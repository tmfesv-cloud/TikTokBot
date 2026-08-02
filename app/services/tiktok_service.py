"""Скачивание видео из TikTok через yt-dlp.

Весь доступ к yt-dlp живёт здесь. Хендлеры только вызывают download().
"""

import asyncio
import logging
import re
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import yt_dlp

from app.services import user_settings
from config import Config

logger = logging.getLogger(__name__)


def _patch_vk_wallpost_audio_bug() -> None:
    """Обход бага yt-dlp: VKWallPostIE падает, когда в посте data-audio — не объект.

    yt-dlp 2026.07.04: строка `if not audio['url']:` кидает TypeError,
    если ВК отдаёт аудио списком вместо объекта. Точечно заменяем эту строку
    на безопасную проверку. Если структура метода изменится — просто не патчим.
    """
    try:
        import inspect
        import yt_dlp.extractor.vk as vk_mod

        src = inspect.getsource(vk_mod.VKWallPostIE._real_extract)
        new_src = textwrap.dedent(src).replace(
            "if not audio['url']:",
            "if not isinstance(audio, dict) or not audio.get('url'):",
        )
        if new_src == src:
            return  # строка не найдена — версия другая, не трогаем

        exec(compile(new_src, "<vk_patch>", "exec"), vk_mod.__dict__)
        vk_mod.VKWallPostIE._real_extract = vk_mod.__dict__["_real_extract"]
        logger.info("VK: применён патч бага с audio в постах со стены")
    except Exception as e:
        logger.warning(f"VK: не удалось применить патч audio-бага: {e}")


_patch_vk_wallpost_audio_bug()

# Расширения скачанных файлов
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_MEDIA_EXTS = _VIDEO_EXTS | _IMAGE_EXTS

# Поддерживаемые платформы: TikTok, Instagram, YouTube, Pinterest, VK
_MEDIA_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:"
    r"(?:www\.|m\.|vm\.|vt\.|v\.)?(?:tiktok|tik-tok)\.com"
    r"|(?:www\.|m\.)?instagram\.com"
    r"|(?:www\.|m\.)?youtu(?:\.be|be\.com)"
    r"|(?:www\.|m\.)?pinterest\.(?:com|co\.[a-z]{2})"
    r"|pin\.it"
    r"|(?:www\.|m\.)?vk\.(?:com|ru)"
    r"|(?:www\.|m\.)?vkvideo\.ru"
    r")"
    r"/\S+",
    re.IGNORECASE,
)

# Ограничиваем число одновременных скачиваний
_SEMAPHORE = asyncio.Semaphore(3)

# Заголовки для HTTP-запросов к TikTok CDN и tikwm (без UA они отдают 403)
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}


class TiktokError(Exception):
    """Базовая ошибка скачивания TikTok."""


class UnsupportedUrlError(TiktokError):
    """Ссылка не похожа на TikTok."""


class VideoUnavailableError(TiktokError):
    """Видео удалено, приватное или ссылка битая."""


class VideoTooLargeError(TiktokError):
    """Видео больше лимита Telegram."""


class DownloadTimeoutError(TiktokError):
    """Скачивание заняло слишком много времени."""


@dataclass
class DownloadResult:
    """Результат скачивания."""

    files: list[Path]          # локальные файлы (1 видео или несколько фото)
    is_video: bool             # True — видео, False — фотопост
    title: str = ""
    author: str = ""
    duration: int | None = None
    audio_file: Path | None = None  # mp3 музыки (для фотопостов, опционально)
    _dir: Path | None = field(default=None, repr=False)

    def cleanup(self) -> None:
        """Удаляет скачанные файлы и папку запроса."""
        for f in self.files:
            try:
                f.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"Не удалось удалить {f}: {e}")
        if self._dir:
            _rmtree(self._dir)


def _rmtree(path: Path) -> None:
    """Удаляет папку вместе с файлами (без рекурсии вниз)."""
    try:
        for p in path.iterdir():
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        path.rmdir()
    except OSError as e:
        logger.warning(f"Не удалось очистить {path}: {e}")


def _is_image_file(path: Path) -> bool:
    """Проверяет по magic bytes, является ли файл изображением.

    Нужно потому что yt-dlp иногда сохраняет картинки в mp4-контейнер
    (например, пины в Pinterest) — тогда расширение .mp4, а внутри JPEG.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        if header[:3] == b"\xff\xd8\xff":  # JPEG
            return True
        if header[:4] == b"\x89PNG":  # PNG
            return True
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":  # WebP
            return True
        return False
    except OSError:
        return False


# --- Анти-спам ---------------------------------------------------------

# Время последнего скачивания каждого пользователя
_last_used: dict[int, float] = {}


def cooldown_left(user_id: int, secs: int | None = None) -> int:
    """Сколько секунд осталось до следующего скачивания (0 = можно)."""
    last = _last_used.get(user_id, 0.0)
    interval = secs if secs is not None else Config.DOWNLOAD_COOLDOWN_SEC
    left = interval - (time.time() - last)
    return int(max(0, left))


def mark_used(user_id: int) -> None:
    """Отмечает, что пользователь только что скачивал."""
    _last_used[user_id] = time.time()


# --- Поиск ссылки ------------------------------------------------------

def extract_media_url(text: str) -> str | None:
    """Достаёт первую ссылку на видео/фото из поддерживаемых платформ."""
    m = _MEDIA_URL_RE.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,;:!?)]}>\"'")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def detect_platform(url: str) -> str:
    """Определяет платформу по ссылке: tiktok/instagram/youtube/pinterest/vk."""
    if "tiktok" in url or "tik-tok" in url:
        return "tiktok"
    if "instagram" in url:
        return "instagram"
    if "youtu.be" in url or "youtube" in url:
        return "youtube"
    if "pinterest" in url or "pin.it" in url:
        return "pinterest"
    if "vk.com" in url or "vk.ru" in url or "vkvideo.ru" in url:
        return "vk"
    return "other"


# --- Скачивание --------------------------------------------------------

def _build_opts(out_dir: Path, max_bytes: int, hd: bool = True,
                platform: str | None = None) -> dict:
    """Опции yt-dlp для TikTok."""
    # VK отдаёт видео/аудио раздельными DASH-потоками и счётчик размера
    # не заполняет — поэтому ограничиваем качество и склеиваем через ffmpeg,
    # иначе yt-dlp хватает 4K (гигабайты) и упирается в max_filesize.
    if platform == "vk":
        fmt = "bv[height<=720]+ba/b[height<=720]/b"
    else:
        # HD — лучшее качество; SD — не выше 720p.
        # Предпочитаем единый mp4 (видео+аудио) — не требует ffmpeg.
        # `b` = формат с видео И аудио; если такого нет — берём лучшее.
        fmt = "b[ext=mp4]/b/best" if hd else "b[height<=720]/b/best"

    opts = {
        "format": fmt,
        # Автономер, чтобы фотопосты (несколько картинок) не перезаписывали друг друга
        "outtmpl": str(out_dir / "%(id)s_%(autonumber)03d.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes,
        "socket_timeout": 15,
        "retries": 3,
        "noprogress": True,
    }
    # VK: склейка видео+аудио через ffmpeg (установлен в Dockerfile и на Render)
    if platform == "vk":
        opts["merge_output_format"] = "mp4"
    # Если TikTok блокирует запросы — подставляем cookies из браузера или файла
    if Config.COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (Config.COOKIES_FROM_BROWSER,)
    if Config.COOKIES_FILE:
        opts["cookiefile"] = Config.COOKIES_FILE
    return opts


def _classify_error(msg: str) -> TiktokError:
    """Переводит ошибку yt-dlp в понятное исключение."""
    lower = msg.lower()
    if "max-filesize" in lower or ("larger than" in lower and "filesize" in lower):
        return VideoTooLargeError(
            f"📦 Видео больше {Config.MAX_VIDEO_MB} МБ — Telegram не даёт боту "
            "отправлять такие файлы. Попробуй другое видео."
        )
    if any(k in lower for k in ("timed out", "timeout", "timedout")):
        return DownloadTimeoutError(
            "⏱ Скачивание заняло слишком много времени. Попробуй ещё раз."
        )
    if any(k in lower for k in ("blocked", "captcha", "verify")):
        return TiktokError(
            "🛡 Сайт заблокировал запрос с этого IP. Попробуй другое видео "
            "или другую ссылку."
        )
    if any(k in lower for k in ("404", "not found", "unavailable", "removed",
                                "private", "account", "expired", "no longer exists")):
        return VideoUnavailableError(
            "😔 Видео не найдено. Возможно, оно удалено, скрыто или ссылка битая."
        )
    # Всё остальное — общая ошибка без страшного технического текста
    return TiktokError(
        "😔 Не удалось скачать это видео. Проверь ссылку и попробуй ещё раз."
    )


def _download_sync(url: str, out_dir: Path, max_bytes: int, hd: bool = True) -> DownloadResult:
    """Блокирующая загрузка (вызывается в отдельном потоке)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Уникальная папка на каждый запрос — параллельные загрузки не мешают друг другу
    req_dir = out_dir / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    req_dir.mkdir()

    platform = detect_platform(url)
    opts = _build_opts(req_dir, max_bytes, hd, platform)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.UnsupportedError as e:
        _rmtree(req_dir)
        logger.warning(f"Неподдерживаемый URL {url}: {e}")
        raise UnsupportedUrlError(
            "🤔 Неподдерживаемая ссылка. Поддерживаются: TikTok, Instagram, "
            "YouTube, Pinterest, VK."
        ) from e
    except yt_dlp.utils.DownloadError as e:
        _rmtree(req_dir)
        logger.warning(f"Ошибка yt-dlp для {url}: {e}")
        raise _classify_error(str(e)) from e
    except Exception as e:
        _rmtree(req_dir)
        logger.exception(f"Неожиданная ошибка yt-dlp для {url}")
        raise TiktokError(
            "😔 Что-то пошло не так при скачивании. Попробуй ещё раз."
        ) from e

    # Собираем скачанные файлы (1 видео или несколько фото)
    files = sorted(
        p for p in req_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _MEDIA_EXTS
    )
    if not files:
        _rmtree(req_dir)
        logger.warning(f"Ничего не скачалось для {url}")
        raise VideoUnavailableError(
            "😔 Не удалось получить файлы. Возможно, видео недоступно."
        )

    result = DownloadResult(
        files=files,
        # Проверяем magic bytes, потому что yt-dlp может сохранить картинку
        # в mp4-контейнер (Pinterest, иногда Instagram)
        is_video=any(
            p.suffix.lower() in _VIDEO_EXTS and not _is_image_file(p)
            for p in files
        ),
        title=info.get("title") or "",
        author=info.get("uploader") or info.get("creator") or info.get("channel") or "",
        duration=info.get("duration"),
    )
    result._dir = req_dir
    return result


async def _download_file(
    session: aiohttp.ClientSession, file_url: str, dest: Path, max_bytes: int
) -> None:
    """Скачивает файл по URL в dest, следя за лимитом размера."""
    async with session.get(
        file_url, timeout=aiohttp.ClientTimeout(total=120)
    ) as resp:
        if resp.status != 200:
            raise VideoUnavailableError("😔 Не удалось скачать файл с CDN.")
        size = 0
        with open(dest, "wb") as f:
            async for chunk in resp.content.iter_chunked(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise VideoTooLargeError(
                        f"📦 Файл больше {Config.MAX_VIDEO_MB} МБ — Telegram не даёт "
                        "боту отправлять такие файлы."
                    )
                f.write(chunk)
        if size == 0:
            raise VideoUnavailableError("😔 Получился пустой файл.")


def _tikwm_author(data: dict) -> str:
    """Автор из ответа tikwm (словарь author)."""
    a = data.get("author") or {}
    return a.get("nickname") or a.get("unique_id") or ""


async def _improve_tiktok_audio(
    result: DownloadResult, url: str, user_id: int | None = None
) -> DownloadResult:
    """Подмешивает оригинальный трек TikTok вместо слабой дорожки (~64 kbps).

    Улучшение только если у пользователя включена настройка improve_audio
    (панель /settings). Берёт music URL из tikwm, качает mp3 и через ffmpeg
    заменяет аудио-дорожку видео на него. При любой ошибке возвращает исходный
    result — без падений.
    """
    s = user_settings.get(user_id) if user_id else None
    if not (s and s.improve_audio):
        return result
    if not result.is_video or not result.files or not result._dir:
        return result

    video = result.files[0]
    req_dir = result._dir
    music_path = req_dir / "orig_music.mp3"
    improved_path = req_dir / "improved.mp4"

    try:
        # 1. Узнаём URL оригинального трека через tikwm и качаем его
        async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
            async with session.get(
                "https://www.tikwm.com/api/",
                params={"url": url, "hd": 1},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                payload = await resp.json(content_type=None)
            data = payload.get("data") or {}
            music_url = data.get("music")
            if not music_url:
                logger.info("TikTok audio: нет music URL — оставляем как есть")
                return result

            # 2. Качаем трек
            await _download_file(session, music_url, music_path, 50 * 1024 * 1024)

        # 3. Подмешиваем через ffmpeg (видео без перекодирования, звук из трека)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", str(music_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(improved_path),
        ]
        proc = await asyncio.to_thread(
            lambda: __import__("subprocess").run(
                cmd, capture_output=True, timeout=180
            )
        )
        if proc.returncode != 0 or not improved_path.exists() or improved_path.stat().st_size == 0:
            logger.warning("TikTok audio: ffmpeg не сработал, оставляем оригинал")
            return result

        # Заменяем файл видео на улучшенный
        result.files = [improved_path]
        video.unlink(missing_ok=True)
        logger.info("TikTok audio: улучшено до 128 kbps")
    except Exception as e:
        logger.warning(f"TikTok audio: улучшение не удалось ({e}), оставляем оригинал")
    finally:
        music_path.unlink(missing_ok=True)

    return result


def _download_pinterest_video(m3u8_url: str, req_dir: Path, max_bytes: int) -> DownloadResult:
    """Скачивание HLS-видео из Pinterest через yt-dlp + ffmpeg.

    Вызывается в отдельном потоке (yt-dlp блокирующий). Нужен ffmpeg,
    чтобы соединить раздельные видео и аудио из HLS-потока.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(req_dir / "video.%(ext)s"),
        "noplaylist": True,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "max_filesize": max_bytes,
        "socket_timeout": 20,
        "retries": 3,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(m3u8_url, download=True)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "ffmpeg is not installed" in msg.lower():
            raise TiktokError(
                "🎬 Это видео-пин. Для него нужен ffmpeg — он уже есть "
                "на сервере, а локально установи ffmpeg."
            ) from e
        raise _classify_error(msg) from e
    except Exception as e:
        logger.exception(f"Ошибка скачивания видео-пина: {m3u8_url}")
        raise TiktokError(
            "😔 Не удалось скачать видео-пин. Попробуй ещё раз."
        ) from e

    files = sorted(
        p for p in req_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTS
    )
    if not files:
        raise VideoUnavailableError("😔 Не удалось получить видео из пина.")
    if any(p.stat().st_size > max_bytes for p in files):
        raise VideoTooLargeError(
            f"📦 Видео больше {Config.MAX_VIDEO_MB} МБ — Telegram не даёт "
            "боту отправлять такие файлы."
        )
    return DownloadResult(
        files=files,
        is_video=True,
        title="Pinterest",
        _dir=req_dir,
    )


async def _download_pinterest(url: str, out_dir: Path, max_bytes: int) -> DownloadResult:
    """Скачивание картинок из Pinterest через парсинг HTML.

    yt-dlp не умеет извлекать картинки из Pinterest-пинов,
    поэтому парсим страницу и качаем оригиналы с i.pinimg.com.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    req_dir = out_dir / f"{int(time.time() * 1000)}-pin-{uuid.uuid4().hex[:8]}"
    req_dir.mkdir()

    try:
        async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
            # Загружаем страницу пина (pin.it → редирект на полный URL)
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    _rmtree(req_dir)
                    raise VideoUnavailableError(
                        "😔 Не удалось загрузить страницу Pinterest."
                    )
                html = await resp.text()

        # --- Видео или картинка? ---

        # Видео-пины Pinterest хранят HLS-плейлисты (m3u8) в JSON-данных
        m3u8_url = None
        for m in re.finditer(r'"url":"(https://[^"]+\.m3u8[^"]*)"', html):
            m3u8_url = m.group(1)
            break

        if m3u8_url:
            # Видео-пин — скачиваем через yt-dlp + ffmpeg (соединяет видео и аудио)
            try:
                return await asyncio.to_thread(
                    _download_pinterest_video, m3u8_url, req_dir, max_bytes
                )
            except TiktokError:
                _rmtree(req_dir)
                raise

        # --- Изображения пина ---

        img_urls: list[str] = []

        # 1) og:image — основное изображение (самый надёжный путь)
        #    Pinterest ставит name и property в любом порядке
        og_match = re.search(
            r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html
        )
        if not og_match:
            og_match = re.search(
                r'<meta[^>]*content="([^"]+)"[^>]*property="og:image"', html
            )
        if og_match and "pinimg.com" in og_match.group(1):
            og_url = og_match.group(1)
            # Улучшаем качество: 736x → originals (структура путей совпадает)
            if "/736x/" in og_url:
                og_url = og_url.replace("/736x/", "/originals/")
            img_urls.append(og_url)

        # 2) JSON-данные — изображения из images_orig (карусели)
        for m in re.finditer(r'"images_orig":\{[^}]*"url":"([^"]+)"', html):
            u = m.group(1)
            if u not in img_urls:
                img_urls.append(u)

        # 3) Фолбэк: originals, исключая CSS url() (декоративные элементы)
        if not img_urls:
            css_urls = set(
                re.findall(r"url\([^)]*i\.pinimg\.com[^)]*\)", html)
            )
            img_urls = [
                u for u in re.findall(
                    r"https://i\.pinimg\.com/originals/[a-f0-9/]+\.\w+", html
                )
                if u not in css_urls
            ]

        if not img_urls:
            _rmtree(req_dir)
            raise VideoUnavailableError(
                "😔 Не удалось найти изображения в пине."
            )

        # Качаем каждое изображение
        files: list[Path] = []
        async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
            for i, img_url in enumerate(img_urls[:20], 1):
                dest = req_dir / f"{i:02d}.jpg"
                try:
                    await _download_file(session, img_url, dest, max_bytes)
                    files.append(dest)
                except VideoTooLargeError:
                    _rmtree(req_dir)
                    raise
                except VideoUnavailableError:
                    continue  # одно фото не скачалось — пробуем остальные

        if not files:
            _rmtree(req_dir)
            raise VideoUnavailableError(
                "😔 Не удалось скачать изображения из пина."
            )

        return DownloadResult(
            files=files,
            is_video=False,
            title="Pinterest",
            _dir=req_dir,
        )

    except aiohttp.ClientError as e:
        _rmtree(req_dir)
        logger.warning(f"Pinterest недоступен: {e}")
        raise TiktokError(
            "😔 Не удалось загрузить страницу Pinterest."
        ) from e
    except (asyncio.TimeoutError, ValueError) as e:
        _rmtree(req_dir)
        logger.warning(f"Pinterest: ошибка парсинга: {e}")
        raise TiktokError("😔 Pinterest не ответил.") from e


async def _download_via_tikwm(url: str, out_dir: Path, max_bytes: int, hd: bool = True) -> DownloadResult:
    """Запасной путь через tikwm.com — работает даже при блокировке TikTok.

    Бросает VideoTooLargeError / VideoUnavailableError (точные ошибки)
    или TiktokError, если сервис сам недоступен (тогда вызывающий вернётся
    к ошибке yt-dlp).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    req_dir = out_dir / f"{int(time.time() * 1000)}-tikwm-{uuid.uuid4().hex[:8]}"
    req_dir.mkdir()

    try:
        async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
            # 1. Узнаём прямые ссылки через API (hd=1/0 — качество)
            async with session.get(
                "https://www.tikwm.com/api/",
                params={"url": url, "hd": 1 if hd else 0},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                payload = await resp.json(content_type=None)

            if not isinstance(payload, dict) or payload.get("code") not in (0, 200):
                _rmtree(req_dir)
                raise VideoUnavailableError(
                    "😔 Не удалось скачать это видео. Возможно, оно удалено "
                    "или ссылка битая."
                )

            data = payload.get("data") or {}
            images = data.get("images") or []
            video_url = data.get("hdplay") or data.get("play")

            # 2. Фотопост — качаем все фото + музыку
            if images:
                files: list[Path] = []
                for i, img_url in enumerate(images[:20], 1):
                    dest = req_dir / f"{i:02d}.jpg"
                    try:
                        await _download_file(session, img_url, dest, max_bytes)
                        files.append(dest)
                    except VideoTooLargeError:
                        _rmtree(req_dir)
                        raise
                    except VideoUnavailableError:
                        continue  # одно фото не скачалось — пробуем остальные
                if not files:
                    _rmtree(req_dir)
                    raise VideoUnavailableError("😔 Не удалось скачать фотопост.")

                # Качаем аудио музыки (если есть)
                audio_file = None
                music_url = data.get("music")
                if music_url:
                    audio_dest = req_dir / "music.mp3"
                    try:
                        await _download_file(session, music_url, audio_dest, max_bytes)
                        if audio_dest.stat().st_size > 0:
                            audio_file = audio_dest
                    except (VideoTooLargeError, VideoUnavailableError):
                        pass  # аудио необязательно — если не скачалось, просто не отправим

                return DownloadResult(
                    files=files,
                    is_video=False,
                    title=data.get("title") or "",
                    author=_tikwm_author(data),
                    audio_file=audio_file,
                    _dir=req_dir,
                )

            # 3. Видео — качаем один файл
            if video_url:
                dest = req_dir / "video.mp4"
                try:
                    await _download_file(session, video_url, dest, max_bytes)
                except (VideoTooLargeError, VideoUnavailableError):
                    _rmtree(req_dir)
                    raise
                return DownloadResult(
                    files=[dest],
                    is_video=True,
                    title=data.get("title") or "",
                    author=_tikwm_author(data),
                    duration=data.get("duration") or None,
                    _dir=req_dir,
                )

            _rmtree(req_dir)
            raise VideoUnavailableError("😔 Не удалось получить ссылки на файлы.")

    except aiohttp.ClientError as e:
        _rmtree(req_dir)
        logger.warning(f"tikwm недоступен: {e}")
        raise TiktokError("tikwm недоступен") from e
    except (asyncio.TimeoutError, ValueError) as e:
        _rmtree(req_dir)
        logger.warning(f"tikwm ответил неправильно: {e}")
        raise TiktokError("tikwm: плохой ответ") from e


async def download(url: str, user_id: int | None = None) -> DownloadResult:
    """Скачивает видео или фотопост по ссылке.

    user_id — для применения настроек пользователя (качество).
    Бросает TiktokError при любых проблемах.
    """
    # Сразу отсекаем неподдерживаемые ссылки
    normalized = extract_media_url(url)
    if not normalized:
        raise UnsupportedUrlError(
            "🤔 Неподдерживаемая ссылка. Поддерживаются: TikTok, Instagram, "
            "YouTube, Pinterest, VK."
        )

    # vk.ru — тот же сайт, но yt-dlp знает только vk.com
    # (vkvideo.ru не трогаем — у него свой extractor)
    if "vk.ru" in normalized:
        normalized = re.sub(r"vk\.ru", "vk.com", normalized)
        logger.info(f"VK: нормализован домен -> {normalized}")

    # Настройки пользователя (качество)
    s = user_settings.get(user_id) if user_id else None
    hd = s.hd if s else True

    # TikTok-специфичный fallback через tikwm.com
    is_tiktok = bool(re.search(r"(?:tiktok|tik-tok)\.com", normalized))
    # Pinterest — кастомный парсинг (yt-dlp не умеет картинки)
    is_pinterest = bool(re.search(r"pinterest\.(?:com|co\.\w+)|pin\.it", normalized))

    max_bytes = Config.MAX_VIDEO_MB * 1024 * 1024
    out_dir = Path(Config.DOWNLOADS_DIR)
    logger.info(f"Скачивание (hd={hd}): {normalized}")
    async with _SEMAPHORE:
        # Pinterest — сразу кастомный путь (yt-dlp не извлекает картинки)
        if is_pinterest:
            try:
                return await _download_pinterest(normalized, out_dir, max_bytes)
            except TiktokError:
                raise

        # Остальное — через yt-dlp
        try:
            result = await asyncio.to_thread(
                _download_sync, normalized, out_dir, max_bytes, hd
            )
            # TikTok: улучшаем звук (если включено в настройках)
            if is_tiktok and result.is_video:
                return await _improve_tiktok_audio(result, normalized, user_id)
            return result
        except (VideoTooLargeError, UnsupportedUrlError):
            # Эти ошибки уже точные — fallback не нужен
            raise
        except TiktokError as primary:
            # yt-dlp не справился — пробуем tikwm (только для TikTok)
            if is_tiktok:
                logger.info(f"yt-dlp не смог, пробуем tikwm: {normalized}")
                try:
                    result = await _download_via_tikwm(
                        normalized, out_dir, max_bytes, hd
                    )
                    if result.is_video:
                        return await _improve_tiktok_audio(result, normalized, user_id)
                    return result
                except (VideoTooLargeError, VideoUnavailableError):
                    raise
                except TiktokError:
                    raise primary from None
            raise
