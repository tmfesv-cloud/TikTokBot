"""Хендлеры для /start и /help."""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services import user_settings
from config import Config

logger = logging.getLogger(__name__)

router = Router()


def help_text() -> str:
    """Текст справки."""
    return (
        "🎬 <b>Как скачать видео:</b>\n\n"
        "Просто <b>пришли мне ссылку</b> — и я скачаю видео или фото\n"
        "без водяного знака!\n\n"
        "📱 <b>Поддерживаемые платформы:</b>\n"
        "• TikTok — видео и фотопосты\n"
        "• Instagram Reels — видео и карусели\n"
        "• YouTube Shorts — видео\n"
        "• Pinterest — фото\n\n"
        "💡 <b>Команды:</b>\n"
        "• /settings — настройки скачивания\n"
        "• /clear — сбросить настройки\n"
        "• /help — эта справка\n\n"
        "⚠️ <b>Ограничения:</b>\n"
        f"• Файлы до {Config.MAX_VIDEO_MB} МБ (лимит Telegram)\n"
        f"• Между запросами пауза {Config.DOWNLOAD_COOLDOWN_SEC} сек\n"
        "• Видео из закрытых аккаунтов скачать нельзя\n"
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Приветствие."""
    await message.answer(
        "👋 Привет! Я - пуфик, скачиваю видео и фото c TikTok, Instagram, Pinterest и YouTube.\n\n"
        "Просто пришли мне ссылку на видео — и я скачаю его без водяного знака 🎬\n\n"
        "Команды:\n"
        "/help — справка\n"
        "/settings — настройки скачивания",
        parse_mode="HTML",
    )
    # Отправляем стикер приветствия (указывается в .env, STICKER_FILE_ID)
    if Config.STICKER_FILE_ID:
        try:
            await message.answer_sticker(Config.STICKER_FILE_ID)
        except Exception as e:
            logger.warning(f"Не удалось отправить стикер: {e}")


@router.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated) -> None:
    """Приветствие при добавлении бота в группу."""
    if update.chat.type not in ("group", "supergroup"):
        return
    if update.new_chat_member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
    ):
        await update.bot.send_message(
            update.chat.id,
            "👋 Всем привет! Я пуфик — скачиваю видео и фото с TikTok, "
            "Instagram, Pinterest и YouTube!\n\n"
            "Просто пришли мне ссылку в чат — и я пришлю готовый результат 🎬",
            parse_mode="HTML",
        )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка (удаляя сообщение пользователя)."""
    try:
        await message.delete()
    except Exception:
        pass
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Закрыть", callback_data="help:close")
    kb.adjust(1)
    await message.answer(help_text(), reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "help:close")
async def on_help_close(callback: CallbackQuery) -> None:
    """Закрытие справки."""
    await callback.message.delete()
    await callback.answer()


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    """Сброс настроек пользователя к дефолту."""
    try:
        await message.delete()
    except Exception:
        pass
    user_id = message.from_user.id
    _store = user_settings._store
    _store.pop(user_id, None)
    await message.answer("🧹 Настройки сброшены к дефолту.", parse_mode="HTML")
