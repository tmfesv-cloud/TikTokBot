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

from app.services import tiktok_service, user_settings

logger = logging.getLogger(__name__)

router = Router()

# Команды
_DOWNLOAD_CMDS = ("/тикток", "/tt", "/скачать", "/скачай", "/download")


def _extract_audio_sync(video_path: Path, out_dir: Path) -> Path | None:
    """Извлекает аудио-дорожку из видео через ffmpeg.

    Возвращает путь к mp3 или None (если ffmpeg недоступен или нечего извлекать).
    """
    audio_path = out_dir / "audio.mp3"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "4",
                str(audio_path),
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
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


async def _handle_download(message: Message, url: str) -> None:
    """Общий путь: кулдаун → скачивание → отправка → очистка."""
    user_id = message.from_user.id

    # Анти-спам
    left = tiktok_service.cooldown_left(user_id)
    if left > 0:
        await message.answer(
            f"⏳ Не так быстро! Подожди ещё {left} сек.", parse_mode=None
        )
        return

    tiktok_service.mark_used(user_id)
    await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_video")

    result = None
    s = user_settings.get(user_id)
    try:
        result = await tiktok_service.download(url, user_id=user_id)
    except tiktok_service.TiktokError as e:
        await message.answer(str(e), parse_mode=None)
        return
    except Exception as e:
        logger.exception(f"Ошибка скачивания для {user_id}")
        await message.answer(
            "😔 Что-то пошло не так. Попробуй ещё раз позже.", parse_mode=None
        )
        return

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
        await message.answer(
            "😔 Не удалось отправить файл. Попробуй ещё раз.", parse_mode=None
        )
    finally:
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
