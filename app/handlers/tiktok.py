"""Скачивание видео из TikTok.

Команды: /тикток <ссылка>, /tt <ссылка>, /скачать <ссылка>
А также авто-реакция на любую ссылку TikTok в обычном сообщении.
"""

import asyncio
import logging
import subprocess
from pathlib import Path

from aiogram import Router
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from app.services import stats, tiktok_service, user_settings

logger = logging.getLogger(__name__)

router = Router()

# Команды
_DOWNLOAD_CMDS = ("/тикток", "/tt", "/скачать", "/скачай", "/download")

# Очереди скачиваний по пользователям
_queues: dict[int, asyncio.Queue] = {}
_workers: dict[int, asyncio.Task] = {}


def _extract_audio_sync(video_path: Path, out_dir: Path) -> Path | None:
    """Извлекает аудио-дорожку из видео через ffmpeg.

    Сначала пробуем скопировать дорожку как есть в .m4a — без потерь
    (исходный битрейт сохраняется). Если кодек несовместим с .m4a
    (например Opus у YouTube) — перекодируем в AAC 192k.

    Возвращает путь к файлу или None (если ffmpeg недоступен или нечего извлекать).
    """
    m4a_path = out_dir / "audio.m4a"
    try:
        # 1) Копия без перекодирования — качество как в источнике
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",
                "-c:a", "copy",
                str(m4a_path),
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0 and m4a_path.exists() and m4a_path.stat().st_size > 0:
            return m4a_path

        # 2) Кодек не влез в .m4a — перекодируем в AAC 192k (без потерь не вышло)
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",
                "-acodec", "aac",
                "-b:a", "192k",
                str(m4a_path),
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0 and m4a_path.exists() and m4a_path.stat().st_size > 0:
            return m4a_path
    except FileNotFoundError:
        logger.debug("ffmpeg не найден — извлечение аудио невозможно")
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timeout при извлечении аудио")
    except Exception as e:
        logger.warning(f"Ошибка ffmpeg: {e}")
    return None


async def _extract_audio(video_path: Path, out_dir: Path) -> Path | None:
    """Асинхронная обёртка для ffmpeg (не блокирует event loop)."""
    return await asyncio.to_thread(_extract_audio_sync, video_path, out_dir)


def is_tiktok_cmd(message: Message) -> bool:
    """Фильтр: сообщение начинается с команды скачивания (с или без @имябота)."""
    text = message.text or ""
    if not text:
        return False
    first = text.split(maxsplit=1)[0]
    cmd = first.split("@")[0].lower()
    return cmd in _DOWNLOAD_CMDS


def has_media_url(message: Message) -> bool:
    """Фильтр: обычное сообщение (не команда) со ссылкой на видео/фото."""
    text = message.text or ""
    if not text or text.startswith("/"):
        return False
    return tiktok_service.extract_media_url(text) is not None


def _extract_from_command(message: Message) -> str | None:
    """Достаёт ссылку из текста команды вида: /тикток https://..."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return tiktok_service.extract_media_url(parts[1])


async def _enqueue(message: Message, url: str) -> None:
    """Добавить скачивание в очередь пользователя."""
    user_id = message.from_user.id
    if user_id not in _queues:
        _queues[user_id] = asyncio.Queue()
    await _queues[user_id].put((message, url))

    # Запустить воркер, если не запущен
    if user_id not in _workers or _workers[user_id].done():
        _workers[user_id] = asyncio.create_task(_process_queue(user_id))

    left = tiktok_service.cooldown_left(user_id)
    qsize = _queues[user_id].qsize()
    if qsize > 1:
        await message.answer(
            f"⏳ В очереди: {qsize} видео. Подожди ~{left * qsize} сек.",
            parse_mode=None,
        )
    else:
        await message.answer(
            f"⏳ Подожди {left} сек...", parse_mode=None,
        )


async def _process_queue(user_id: int) -> None:
    """Обработать очередь скачиваний пользователя."""
    queue = _queues.get(user_id)
    if not queue:
        return
    while not queue.empty():
        message, url = await queue.get()
        left = tiktok_service.cooldown_left(user_id)
        if left > 0:
            await asyncio.sleep(left)
        try:
            await _handle_download_inner(message, url)
        except Exception as e:
            logger.error(f"Ошибка в очереди для {user_id}: {e}")
        queue.task_done()


async def _handle_download(message: Message, url: str) -> None:
    """Общий путь: кулдаун → прямое скачивание или очередь."""
    user_id = message.from_user.id

    # Анти-спам — ставим в очередь вместо игнорирования
    left = tiktok_service.cooldown_left(user_id)
    if left > 0:
        await _enqueue(message, url)
        return

    await _handle_download_inner(message, url)


async def _handle_download_inner(message: Message, url: str) -> None:
    """Скачивание и отправка (без проверки кулдауна)."""
    user_id = message.from_user.id
    tiktok_service.mark_used(user_id)
    await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_video")

    # Фидбек — чтобы пользователь видел, что бот работает (большие видео качаются долго)
    status_msg = await message.answer(
        "⏳ Скачиваю видео...", parse_mode=None
    )

    result = None
    s = user_settings.get(user_id)
    try:
        result = await tiktok_service.download(url, user_id=user_id)
    except tiktok_service.TiktokError as e:
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(str(e), parse_mode=None)
        return
    except Exception as e:
        logger.exception(f"Ошибка скачивания для {user_id}")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(
            "😔 Что-то пошло не так. Попробуй ещё раз позже.", parse_mode=None
        )
        return

    stats.register_download(
        user_id,
        tiktok_service.detect_platform(url),
        stats.make_name(message.from_user.username, message.from_user.first_name),
    )

    try:
        if result.is_video:
            await message.reply_video(
                FSInputFile(str(result.files[0])),
                supports_streaming=True,
                parse_mode=None,
            )
            # Аудио из видео (если включено в настройках)
            if s.send_audio and result.files and result._dir:
                audio_path = await _extract_audio(result.files[0], result._dir)
                if audio_path:
                    try:
                        await message.reply_audio(
                            FSInputFile(str(audio_path)), parse_mode=None
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось отправить аудио: {e}")
        else:
            # Фотопост: одно фото — reply_photo, несколько — media group
            if len(result.files) == 1:
                await message.reply_photo(
                    FSInputFile(str(result.files[0])),
                    parse_mode=None,
                )
            else:
                media = [
                    InputMediaPhoto(media=FSInputFile(str(p)), parse_mode=None)
                    for p in result.files[:10]
                ]
                await message.reply_media_group(media=media)
            # Аудио с музыкой (фотопосты — берётся из tikwm)
            if s.send_audio and result.audio_file:
                try:
                    await message.reply_audio(
                        FSInputFile(str(result.audio_file)),
                        parse_mode=None,
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить аудио: {e}")
    except Exception as e:
        logger.exception(f"Ошибка отправки файла для {user_id}")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(
            "😔 Не удалось отправить файл. Попробуй ещё раз.", parse_mode=None
        )
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass
        if result:
            result.cleanup()


@router.message(is_tiktok_cmd)
async def cmd_download(message: Message) -> None:
    """Обработчик /тикток <ссылка>."""
    url = _extract_from_command(message)
    if not url:
        await message.answer(
            "🎬 Отправь ссылку на видео из TikTok после команды, например:\n"
            "/тикток https://www.tiktok.com/@user/video/123",
            parse_mode=None,
        )
        return
    await _handle_download(message, url)


@router.message(has_media_url)
async def auto_download(message: Message) -> None:
    """Если в сообщении есть ссылка на видео/фото — скачиваем автоматически."""
    url = tiktok_service.extract_media_url(message.text)
    if not url:
        return
    await _handle_download(message, url)
