"""Скачивание видео из TikTok через yt-dlp.

Весь доступ к yt-dlp живёт здесь. Хендлеры только вызывают download().
"""

import asyncio
import base64
import logging
import re
import subprocess
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

# Поддерживаемые платформы: TikTok, Instagram, YouTube, Pinterest, VK,
# Rutube, Одноклассники, X/Twitter, Dailymotion, Likee, Vimeo, Twitch,
# Tumblr, Bilibili, Xiaohongshu
#
# Короткие домены (x.com, ok.ru, dai.ly, pin.it, youtu.be, vk.com) матчим
# с обязательной границей слова + не-буква/цифра перед доменом, чтобы не
# ловить подстроки в чужих URL (example.com/x.com/...).
_MEDIA_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:"
    r"(?:www\.|m\.|vm\.|vt\.|v\.)?(?:tiktok|tik-tok)\.com"
    r"|(?:www\.|m\.)?instagram\.com"
    r"|(?<![A-Za-z0-9])(?:www\.|m\.)?youtu(?:\.be|be\.com)"
    r"|(?<![A-Za-z0-9])(?:www\.|m\.)?pinterest\.(?:com|co\.[a-z]{2})"
    r"|(?<![A-Za-z0-9])pin\.it"
    r"|(?<![A-Za-z0-9])(?:www\.|m\.)?vk\.(?:com|ru)"
    r"|(?:www\.|m\.)?vkvideo\.ru"
    r"|(?:www\.|m\.)?rutube\.ru"
    r"|(?<![A-Za-z0-9])(?:www\.|m\.)?(?:ok|odnoklassniki)\.ru"
    r"|(?<![A-Za-z0-9])(?:www\.|m\.)?(?:x|twitter)\.com"
    r"|(?:www\.|m\.)?dailymotion\.com"
    r"|(?<![A-Za-z0-9])dai\.ly"
    r"|(?:www\.|m\.)?likee\.(?:com|video)"
    r"|(?:www\.|m\.)?vimeo\.com"
    r"|(?:www\.|m\.)?twitch\.tv"
    r"|(?:www\.)?tumblr\.com"
    r"|(?:www\.|m\.)?bilibili\.com"
    r"|(?:www\.)?xiaohongshu\.com"
    r"|(?<![A-Za-z0-9])xhslink\.com"
    r")"
    r"/\S+",
    re.IGNORECASE,
)

# Ограничиваем число одновременных скачиваний
_SEMAPHORE = asyncio.Semaphore(3)

# Потолок размера исходного видео, которое качаем для последующего сжатия (МБ).
# Файлы больше этого — сразу ошибка «слишком большой» (гигабайты не качаем).
_COMPRESS_MAX_MB = 200

# Заголовки для HTTP-запросов к TikTok CDN и tikwm (без UA они отдают 403)
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}

# JS runtime для yt-dlp. YouTube с версии 2025 требует JS для извлечения
# видео; yt-dlp по умолчанию ищет только deno. На Render ставим nodejs,
# локально node тоже почти всегда есть. Указываем оба имени (node / nodejs —
# в Debian-контейнерах бинарник может называться по-разному), чтобы yt-dlp
# не работал в урезанном режиме без JS.
_JS_RUNTIMES = {
    "node": {"executable": "node"},
    "nodejs": {"executable": "nodejs"},
}


def _js_opts(opts: dict) -> dict:
    """Добавляет JS runtime в опции yt-dlp (для YouTube)."""
    opts["js_runtimes"] = _JS_RUNTIMES
    # Если задан YT_CLIENT (android/ios/tv...) — ходить к YouTube этим клиентом.
    # Мобильные/телевизорные клиенты используют другие API, которые иногда
    # не требуют "Sign in to confirm you're not a bot".
    client = Config.YT_CLIENT
    if client:
        opts.setdefault("extractor_args", {})
        opts["extractor_args"]["youtube"] = {"player_client": [client]}
    return opts


# Путь к файлу cookies, который бот сам создаёт из COOKIES_CONTENT
_COOKIES_FILE_PATH = Path("cookies_bot.txt")


def _decode_cookies_content(content: str) -> str:
    """Превращает COOKIES_CONTENT в обычный текст cookies.txt.

    Поддерживает два формата:
    - обычный текст cookies.txt (с переносами строк и табуляциями);
    - base64 от этого текста (так удобно вставлять в переменную окружения
      Render одной строкой — многострочные значения неудобны).
    """
    s = content.strip()
    # Текст cookies содержит переводы строк и табы — это признак, что base64 не нужен
    if "\n" in s or "\t" in s:
        return s
    # Иначе пробуем декодировать base64 и проверяем, что получился cookie-файл
    try:
        decoded = base64.b64decode(s, validate=True).decode("utf-8")
        if "Netscape" in decoded or "\t" in decoded:
            return decoded
    except Exception:
        pass
    # Не похоже ни на то, ни на другое — отдаём как есть
    return s


def _ensure_cookies_file() -> str | None:
    """Создаёт файл cookies из Config.COOKIES_CONTENT (для Render).

    В переменную окружения удобно вставить весь текст cookies.txt
    (или его base64) — бот запишет его в файл при первом скачивании
    и передаст yt-dlp. Возвращает путь к файлу или None (если cookies
    не заданы).
    """
    content = Config.COOKIES_CONTENT.strip()
    if not content:
        return None
    try:
        decoded = _decode_cookies_content(content)
        _COOKIES_FILE_PATH.write_text(decoded, encoding="utf-8")
        # Диагностика: сколько строк cookies и какие домены
        lines = [ln for ln in decoded.splitlines() if ln and not ln.startswith("#")]
        domains = sorted({ln.split("\t")[0] for ln in lines if "\t" in ln})
        logger.info(
            f"Cookies: записано {len(lines)} строк из COOKIES_CONTENT, "
            f"домены: {', '.join(domains[:5]) or 'нет'}"
        )
        return str(_COOKIES_FILE_PATH)
    except OSError as e:
        logger.warning(f"Не удалось записать файл cookies: {e}")
        return None


def _cookies_opts(opts: dict) -> dict:
    """Добавляет cookies в опции yt-dlp: из файла, содержимого или браузера."""
    if Config.COOKIES_FILE:
        opts["cookiefile"] = Config.COOKIES_FILE
    elif Config.COOKIES_CONTENT:
        path = _ensure_cookies_file()
        if path:
            opts["cookiefile"] = path
    elif Config.COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (Config.COOKIES_FROM_BROWSER,)
    return opts


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
    # Короткие домены (x.com, ok.ru, pin.it...) могут встретиться как подстрока
    # в путях чужих ссылок (example.com/x.com/...). Если перед ссылкой стоит
    # буква/цифра/точка/слеш — это не хост, отбрасываем. Схема "//" (https://)
    # перед ссылкой — нормально, это начало хоста.
    if m.start() > 0:
        prev = text[m.start() - 1]
        if prev.isalnum() or prev in "./?&#=_-":
            if not (prev == "/" and m.start() >= 2 and text[m.start() - 2] == "/"):
                return None
    url = m.group(0).rstrip(".,;:!?)]}>\"'")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def detect_platform(url: str) -> str:
    """Определяет платформу по ссылке: tiktok/instagram/youtube/pinterest/vk/..."""
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
    if "rutube.ru" in url:
        return "rutube"
    if "ok.ru" in url or "odnoklassniki.ru" in url:
        return "ok"
    if "twitter.com" in url or "x.com" in url:
        return "twitter"
    if "dailymotion.com" in url or "dai.ly" in url:
        return "dailymotion"
    if "likee.com" in url or "likee.video" in url:
        return "likee"
    if "vimeo.com" in url:
        return "vimeo"
    if "twitch.tv" in url:
        return "twitch"
    if "tumblr.com" in url:
        return "tumblr"
    if "bilibili.com" in url:
        return "bilibili"
    if "xiaohongshu.com" in url or "xhslink.com" in url:
        return "xiaohongshu"
    return "other"


# --- Скачивание --------------------------------------------------------

def _build_opts(out_dir: Path, max_bytes: int, hd: bool = True,
                platform: str | None = None) -> dict:
    """Опции yt-dlp для TikTok."""
    # VK и Rutube отдают через HLS/DASH, где размер неизвестен заранее —
    # max_filesize не может остановить скачивание. Поэтому сразу ограничиваем
    # качество до 720p, иначе yt-dlp хватает гигабайты (а на Render не хватит
    # памяти/диска). VK ещё и склеивает видео+аудио через ffmpeg.
    if platform in ("vk", "rutube"):
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
    # VK и Rutube: склейка видео+аудио через ffmpeg (установлен в Dockerfile и на Render)
    if platform in ("vk", "rutube"):
        opts["merge_output_format"] = "mp4"
    # Cookies: из файла, содержимого (Render) или браузера
    _cookies_opts(opts)
    return _js_opts(opts)


def _classify_error(msg: str) -> TiktokError:
    """Переводит ошибку yt-dlp в понятное исключение."""
    lower = msg.lower()
    if "max-filesize" in lower or ("larger than" in lower and "filesize" in lower):
        return VideoTooLargeError(
            "📦 Видео слишком большое — даже сжатое не поместится в лимит "
            "Telegram. Попробуй другое видео."
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
    # Всё остальное — общая ошибка без страшного технического текста.
    # При DEBUG_ERRORS=True показываем реальный текст yt-dlp (для диагностики).
    if Config.DEBUG_ERRORS:
        return TiktokError(
            "🔧 [debug] Не удалось скачать. Техническая ошибка:\n"
            f"{msg[:500]}"
        )
    return TiktokError(
        "😔 Не удалось скачать это видео. Проверь ссылку и попробуй ещё раз."
    )


async def get_duration(url: str) -> int | None:
    """Быстро определяет длительность видео в секундах (без скачивания).

    Нужно, чтобы решить, показывать ли "Скачиваю видео...".
    Для TikTok идём через tikwm — это быстрее и не создаёт лишней нагрузки
    на сам TikTok (меньше шансов поймать rate-limit перед скачиванием).
    Для остальных — yt-dlp. Фотопосты возвращают 0/None — статус не
    показывается.
    """
    return (await probe(url)).duration


@dataclass
class ProbeResult:
    """Что узнали о ссылке до скачивания (без загрузки файлов)."""

    is_playlist: bool = False          # ссылка ведёт на плейлист
    playlist_count: int | None = None  # сколько роликов в плейлисте
    duration: int | None = None        # длительность (у видео)
    is_tiktok_photo: bool = False      # TikTok-фотопост


async def probe(url: str) -> ProbeResult:
    """Быстро узнаёт тип ссылки: видео / плейлист / TikTok-фото.

    Один лёгкий запрос метаданных. Используется, чтобы решить:
    - показывать ли "Скачиваю видео..." (длительность > 120 сек);
    - спросить ли "Скачать весь плейлист?".
    """
    if detect_platform(url) == "tiktok":
        try:
            async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
                async with session.get(
                    "https://www.tikwm.com/api/",
                    params={"url": url, "hd": 1},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    payload = await resp.json(content_type=None)
            if isinstance(payload, dict) and payload.get("code") in (0, 200):
                data = payload.get("data") or {}
                # Фото: images есть, duration=0. Видео: duration>0.
                if data.get("images"):
                    return ProbeResult(is_tiktok_photo=True)
                return ProbeResult(duration=data.get("duration"))
            return ProbeResult()
        except Exception:
            return ProbeResult()

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,      # не резать плейлисты — узнаём про них
        "extract_flat": True,     # только метаданные, без скачивания роликов
    }
    _cookies_opts(opts)
    _js_opts(opts)
    try:
        info = await asyncio.to_thread(
            lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False)
        )
    except Exception:
        return ProbeResult()
    if not isinstance(info, dict):
        return ProbeResult()

    if info.get("_type") == "playlist":
        return ProbeResult(
            is_playlist=True,
            playlist_count=info.get("playlist_count"),
        )
    return ProbeResult(duration=info.get("duration"))


def _get_playlist_urls_sync(url: str, limit: int) -> list[str] | None:
    """Собирает прямые ссылки на ролики плейлиста (без скачивания)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,  # берём только метаданные, не качаем ролики
    }
    _cookies_opts(opts)
    _js_opts(opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict) or info.get("_type") != "playlist":
        return None
    urls: list[str] = []
    for e in (info.get("entries") or [])[:limit]:
        u = e.get("url") if isinstance(e, dict) else None
        if u and u.startswith("http"):
            urls.append(u)
    return urls or None


async def get_playlist_urls(url: str, limit: int = 25) -> list[str] | None:
    """Прямые ссылки на первые `limit` роликов плейлиста (или None)."""
    return await asyncio.to_thread(_get_playlist_urls_sync, url, limit)


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
            "YouTube, Pinterest, VK, Rutube, Одноклассники, X/Twitter, "
            "Dailymotion, Likee, Vimeo, Twitch, Tumblr, Bilibili, Xiaohongshu."
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
                        "📦 Файл слишком большой — даже сжатое не поместится "
                        "в лимит Telegram."
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
            lambda: subprocess.run(cmd, capture_output=True, timeout=180)
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


def _compress_video_sync(src: Path, out_dir: Path, target_bytes: int) -> Path | None:
    """Сжимает видео через ffmpeg до target_bytes.

    Понижает разрешение до 720p (если выше), затем пробует -crf 28 → 32 → 36,
    пока размер не влезет в лимит. Возвращает путь к сжатому файлу или None,
    если не влезло даже на crf=36 (вызывающий покажет ошибку).
    """
    out = out_dir / "compressed.mp4"

    # Определяем разрешение: если выше 720p — масштабируем, это резко
    # сокращает работу и размер (обычно достаточно одного прохода).
    vf = []
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=height",
                "-of", "csv=p=0",
                str(src),
            ],
            capture_output=True, text=True, timeout=30,
        )
        height = int(probe.stdout.strip().splitlines()[0])
        if height > 720:
            vf = ["-vf", "scale=-2:720"]
    except Exception:
        pass  # не смогли узнать высоту — сжимаем как есть

    for crf in (28, 32, 36):
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(crf),
        ]
        if vf:
            cmd += vf
        cmd += [
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "+faststart",
            str(out),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=600)
        except FileNotFoundError:
            logger.debug("ffmpeg не найден — сжатие видео невозможно")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg timeout при сжатии видео")
            return None
        if proc.returncode != 0:
            logger.warning(f"ffmpeg не сжал видео (crf={crf}): {proc.stderr[:200]}")
            continue
        if out.exists() and out.stat().st_size <= target_bytes:
            logger.info(f"Видео сжато до {out.stat().st_size // 1024 // 1024} МБ (crf={crf})")
            return out
    # Не влезло даже на crf=36 — не отдаём файл больше лимита
    return None


async def _ensure_size(result: DownloadResult) -> DownloadResult:
    """Сжимает видео, если оно больше лимита Telegram (45 МБ).

    Скачивание идёт с потолком _COMPRESS_MAX_MB (200 МБ), поэтому сюда
    попадают файлы 45–200 МБ. Меньше лимита — отдаём как есть, без сжатия.
    """
    if not result.is_video or not result.files or not result._dir:
        return result
    limit = Config.MAX_VIDEO_MB * 1024 * 1024
    if result.files[0].stat().st_size <= limit:
        return result

    compressed = await asyncio.to_thread(
        _compress_video_sync, result.files[0], result._dir, limit
    )
    if compressed:
        logger.info("Большое видео сжато и будет отправлено")
        result.files = [compressed]
        return result

    # Не влезло в лимит даже после сжатия — отдаём понятную ошибку,
    # а не пытаемся отправить файл, который Telegram всё равно отклонит.
    raise VideoTooLargeError(
        "📦 Видео слишком большое — не удалось сжать до лимита Telegram. "
        "Попробуй другое видео."
    )


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
    _js_opts(opts)
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
            "📦 Видео слишком большое — даже сжатое не поместится в лимит "
            "Telegram."
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

    async def _try_once(session: aiohttp.ClientSession) -> DownloadResult:
        """Один цикл: запрос tikwm + загрузка файлов.

        Возвращает результат или бросает VideoUnavailableError, если файлы
        не скачались. Подписанные ссылки tikwm истекают (CDN отдаёт 403),
        поэтому при неудаче вызывающий перезапрашивает свежие ссылки.
        """
        async with session.get(
            "https://www.tikwm.com/api/",
            params={"url": url, "hd": 1 if hd else 0},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            payload = await resp.json(content_type=None)

        if not isinstance(payload, dict) or payload.get("code") not in (0, 200):
            raise VideoUnavailableError(
                "😔 Не удалось скачать это видео. Возможно, оно удалено "
                "или ссылка битая."
            )

        data = payload.get("data") or {}
        images = data.get("images") or []
        video_url = data.get("hdplay") or data.get("play")

        # Фотопост — качаем все фото + музыку
        if images:
            files: list[Path] = []
            for i, img_url in enumerate(images[:20], 1):
                dest = req_dir / f"{i:02d}.jpg"
                try:
                    await _download_file(session, img_url, dest, max_bytes)
                    files.append(dest)
                except VideoTooLargeError:
                    raise
                except VideoUnavailableError:
                    continue  # одно фото не скачалось — пробуем остальные
            if not files:
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

        # Видео — качаем один файл
        if video_url:
            dest = req_dir / "video.mp4"
            try:
                await _download_file(session, video_url, dest, max_bytes)
            except (VideoTooLargeError, VideoUnavailableError):
                raise
            return DownloadResult(
                files=[dest],
                is_video=True,
                title=data.get("title") or "",
                author=_tikwm_author(data),
                duration=data.get("duration") or None,
                _dir=req_dir,
            )

        raise VideoUnavailableError("😔 Не удалось получить ссылки на файлы.")

    try:
        async with aiohttp.ClientSession(headers=_HTTP_HEADERS) as session:
            # Подписанные CDN-ссылки tikwm быстро истекают (403). Если файлы
            # не скачались — перезапрашиваем свежие ссылки, до 3 циклов.
            last_error: Exception | None = None
            for attempt in range(5):
                try:
                    return await _try_once(session)
                except VideoTooLargeError:
                    _rmtree(req_dir)
                    raise
                except VideoUnavailableError as e:
                    last_error = e
                    # tikwm ответил, что видео удалено/битая ссылка — повторять
                    # бессмысленно. Ошибки "Не удалось скачать фотопост/файлы
                    # с CDN" — истёкшие подписанные ссылки, стоит перезапросить.
                    if "удалено" in str(e):
                        break
                    if attempt == 4:
                        break
                    await asyncio.sleep(1.5)
            _rmtree(req_dir)
            raise last_error if last_error else VideoUnavailableError("😔 Не удалось скачать.")

    except aiohttp.ClientError as e:
        _rmtree(req_dir)
        logger.warning(f"tikwm недоступен: {e}")
        raise TiktokError("tikwm недоступен") from e
    except (asyncio.TimeoutError, ValueError) as e:
        _rmtree(req_dir)
        logger.warning(f"tikwm ответил неправильно: {e}")
        raise TiktokError("tikwm: плохой ответ") from e


# --- YouTube fallback через Invidious --------------------------------------

# Публичные Invidious-инстансы — прокси-серверы YouTube, созданные именно
# для обхода блокировок. Используются, когда yt-dlp на Render не может скачать
# YouTube напрямую (YouTube блокирует IP дата-центров: "Sign in to confirm").
# Инстансы иногда умирают — пробуем по очереди.
_INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://invidious.f5.si",
    "https://inv.nadeko.net",
    "https://invidious.tiekoetter.com",
    "https://yt.chocolatemoo53.com",
    "https://yewtu.be",
]

# itag -> приоритет (чем выше качество, тем ниже число = лучше)
_YT_PROGRESSIVE_ITAGS = {22: 0, 18: 1, 43: 2, 44: 2, 17: 3}


def _extract_youtube_id(url: str) -> str | None:
    """Достаёт ID видео из YouTube-ссылки (watch/shorts/youtu.be/embed)."""
    m = re.search(r"(?:youtu\.be/|v=|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


async def _download_via_invidious(
    url: str, out_dir: Path, max_bytes: int
) -> DownloadResult:
    """Запасное скачивание YouTube через публичные Invidious-инстансы.

    Инстанс сам качает видео с YouTube (со своих серверов/IP) и отдаёт
    прямую ссылку на файл. Нам остаётся только скачать её.
    Бросает TiktokError, если ни один инстанс не смог.
    """
    video_id = _extract_youtube_id(url)
    if not video_id:
        raise TiktokError("Не удалось распознать YouTube-ссылку.")

    out_dir.mkdir(parents=True, exist_ok=True)
    req_dir = out_dir / f"{int(time.time() * 1000)}-inv-{uuid.uuid4().hex[:8]}"
    req_dir.mkdir()

    headers = {"User-Agent": _HTTP_HEADERS["User-Agent"], "Accept": "application/json"}

    for host in _INVIDIOUS_INSTANCES:
        try:
            api_url = f"{host}/api/v1/videos/{video_id}?local=true"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    api_url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        continue
                    payload = await resp.json(content_type=None)

            title = payload.get("title") or ""
            author = (payload.get("author") or "") or ""

            # formatStreams — прогрессивные mp4 с видео+аудио (самый простой путь)
            streams = [
                f for f in (payload.get("formatStreams") or [])
                if f.get("url") and "video/mp4" in (f.get("type") or "")
            ]
            # Сортируем по качеству (itag 22 = 720p, 18 = 360p...)
            streams.sort(key=lambda f: _YT_PROGRESSIVE_ITAGS.get(int(f.get("itag") or 0), 99))
            if not streams:
                logger.info(f"Invidious {host}: нет прогрессивных mp4-форматов")
                continue

            stream = streams[0]
            dest = req_dir / "video.mp4"
            async with aiohttp.ClientSession() as session:
                await _download_file(session, stream["url"], dest, max_bytes)

            logger.info(f"Invidious {host}: скачано ({dest.stat().st_size // 1024} КБ)")
            return DownloadResult(
                files=[dest],
                is_video=True,
                title=title,
                author=author,
                _dir=req_dir,
            )
        except VideoTooLargeError:
            _rmtree(req_dir)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.warning(f"Invidious {host}: не сработал ({e})")
            continue

    _rmtree(req_dir)
    raise TiktokError(
        "😔 YouTube заблокировал IP сервера, а запасные сервисы не ответили. "
        "Попробуй позже или другое видео."
    )


async def download(
    url: str,
    user_id: int | None = None,
    is_tiktok_photo: bool | None = None,
) -> DownloadResult:
    """Скачивает видео или фотопост по ссылке.

    user_id — для применения настроек пользователя (качество).
    is_tiktok_photo — если заранее известно, что это TikTok-фотопост
    (хендлер узнал через get_duration), идём сразу в tikwm, минуя
    yt-dlp (он фотопосты TikTok не умеет — только тратит время
    и создаёт лишнюю нагрузку на TikTok). None — не знаем, решаем по факту.
    Бросает TiktokError при любых проблемах.
    """
    # Сразу отсекаем неподдерживаемые ссылки
    normalized = extract_media_url(url)
    if not normalized:
        raise UnsupportedUrlError(
            "🤔 Неподдерживаемая ссылка. Поддерживаются: TikTok, Instagram, "
            "YouTube, Pinterest, VK, Rutube, Одноклассники, X/Twitter, "
            "Dailymotion, Likee, Vimeo, Twitch, Tumblr, Bilibili, Xiaohongshu."
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
    # YouTube — есть запасной путь через Invidious (см. _download_via_invidious)
    is_youtube = bool(re.search(r"(?:youtube\.com|youtu\.be)", normalized))
    # Pinterest — кастомный парсинг (yt-dlp не умеет картинки)
    is_pinterest = bool(re.search(r"pinterest\.(?:com|co\.\w+)|pin\.it", normalized))

    # Потолок скачивания — 200 МБ (чтобы потом можно было сжать до 45 МБ).
    # Файлы больше 200 МБ отсекаются ещё на этапе скачивания.
    max_bytes = _COMPRESS_MAX_MB * 1024 * 1024
    out_dir = Path(Config.DOWNLOADS_DIR)
    logger.info(f"Скачивание (hd={hd}): {normalized}")
    async with _SEMAPHORE:
        # Pinterest — сразу кастомный путь (yt-dlp не извлекает картинки)
        if is_pinterest:
            try:
                result = await _download_pinterest(normalized, out_dir, max_bytes)
                return await _ensure_size(result)
            except TiktokError:
                raise

        # TikTok-фотопост: yt-dlp его не умеет — сразу идём в tikwm,
        # не тратя время на заведомо провальный запрос к yt-dlp.
        if is_tiktok and is_tiktok_photo:
            result = await _download_via_tikwm(normalized, out_dir, max_bytes, hd)
            return await _ensure_size(result)

        # Остальное — через yt-dlp
        try:
            result = await asyncio.to_thread(
                _download_sync, normalized, out_dir, max_bytes, hd
            )
            # TikTok: улучшаем звук (если включено в настройках)
            if is_tiktok and result.is_video:
                result = await _improve_tiktok_audio(result, normalized, user_id)
            return await _ensure_size(result)
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
                        result = await _improve_tiktok_audio(result, normalized, user_id)
                    return await _ensure_size(result)
                except (VideoTooLargeError, VideoUnavailableError):
                    raise
                except TiktokError:
                    raise primary from None
            # YouTube: yt-dlp заблокирован (YouTube не любит IP дата-центров) —
            # пробуем скачать через публичные Invidious-инстансы.
            if is_youtube:
                logger.info(f"yt-dlp не смог YouTube, пробуем Invidious: {normalized}")
                try:
                    result = await _download_via_invidious(
                        normalized, out_dir, max_bytes
                    )
                    return await _ensure_size(result)
                except (VideoTooLargeError, VideoUnavailableError):
                    raise
                except TiktokError:
                    raise primary from None
            raise
