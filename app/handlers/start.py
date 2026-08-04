"""Хендлеры для /start и /help."""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services import stats, user_settings
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
        "• VK Видео — видео\n"
        "• Rutube — видео\n"
        "• Pinterest — фото\n"
        "• Одноклассники — видео\n"
        "• X / Twitter — видео\n"
        "• Dailymotion, Vimeo, Twitch — видео\n"
        "• Bilibili, Xiaohongshu — видео\n\n"
        "🚀 С развитием бота будет появляться больше платформ — "
        "в том числе YouTube.\n\n"
        "💡 <b>Команды:</b>\n"
        "• /settings — настройки скачивания\n"
        "• /clear — сбросить настройки\n"
        "• /help — эта справка\n\n"
        "⚠️ <b>Ограничения:</b>\n"
        f"• Отправляю файлы до {Config.MAX_VIDEO_MB} МБ — большие видео "
        "сожму в 720p\n"
        "• Могу сжать большие видео (~до 6 минут) до 45 МБ — лимит Telegram\n"
        f"• Между запросами пауза {Config.DOWNLOAD_COOLDOWN_SEC} сек\n"
        "• Видео из закрытых аккаунтов скачать нельзя\n\n"
        "🚀 С развитием бота можно будет отправлять и большие видео.\n\n"
        "🔊 <b>Нюансы:</b>\n"
        "• Улучшение звука в TikTok может работать некорректно "
        "(возможна задержка звука в видео)\n"
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Приветствие."""
    u = message.from_user
    stats.register_start(
        u.id, stats.make_name(u.username, u.first_name)
    )
    await message.answer(
        "👋 Привет! Я - пуфик, скачиваю видео и фото с 15+ платформ: "
        "TikTok, Instagram, VK, Rutube, Pinterest и других.\n\n"
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
    # Приветствуем только при реальном добавлении (раньше бота не было),
    # а не при назначении админом — иначе сообщения будут дублироваться
    was_absent = update.old_chat_member.status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
    )
    if was_absent and update.new_chat_member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
    ):
        await update.bot.send_message(
            update.chat.id,
            "👋 Всем привет! Я пуфик — скачиваю видео и фото с TikTok, "
            "Instagram и Pinterest!\n\n"
            "Просто пришли мне ссылку в чат — и я пришлю готовый результат 🎬\n\n"
            "Команды:\n"
            "/help — справка\n"
            "/settings — настройки скачивания",
            parse_mode="HTML",
        )
        # Второе сообщение: напоминание о правах администратора
        await update.bot.send_message(
            update.chat.id,
            "🛡 <b>Важно:</b> для полноценной работы бота требуются права "
            "администратора (для удаления команд).\n\n"
            "Добавь меня в админы: Управление группой → Участники → "
            "@PufikSaverBot → Назначить администратором",
            parse_mode="HTML",
        )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка (удаляя сообщение пользователя).

    В группе отправляет справку в личку, чтобы её видел только запросивший.
    """
    try:
        await message.delete()
    except Exception:
        pass
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Закрыть", callback_data="help:close")
    kb.adjust(1)
    kb_markup = kb.as_markup()
    if message.chat.type in ("group", "supergroup"):
        try:
            await message.bot.send_message(
                message.from_user.id, help_text(),
                reply_markup=kb_markup, parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                help_text(), reply_markup=kb_markup, parse_mode="HTML"
            )
    else:
        await message.answer(
            help_text(), reply_markup=kb_markup, parse_mode="HTML"
        )


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
    user_settings.reset(user_id)
    if message.chat.type in ("group", "supergroup"):
        try:
            await message.bot.send_message(
                user_id, "🧹 Настройки сброшены к дефолту.",
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                "🧹 Настройки сброшены к дефолту.", parse_mode="HTML"
            )
    else:
        await message.answer(
            "🧹 Настройки сброшены к дефолту.", parse_mode="HTML"
        )
