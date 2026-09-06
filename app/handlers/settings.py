"""Команда /settings — настройки скачивания.

Интерактивная панель: качество (HD/SD), лимит размера, пауза между запросами.
Настройки хранятся в памяти (app/services/user_settings.py).
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services import user_settings

logger = logging.getLogger(__name__)

router = Router()


def format_settings(s: user_settings.UserSettings) -> str:
    """Текст панели настроек."""
    return (
        "⚙️ <b>Настройки скачивания</b>\n\n"
        f"🎥 Качество: <b>{'HD' if s.hd else 'SD'}</b>\n"
        f"🎵 Аудио отдельно: <b>{'Вкл' if s.send_audio else 'Выкл'}</b>\n"
        f"🔊 Улучшить звук TikTok: <b>{'Вкл' if s.improve_audio else 'Выкл'}</b>\n\n"
        "Меняй значение кнопкой ниже 👇"
    )


def build_kb(s: user_settings.UserSettings) -> InlineKeyboardMarkup:
    """Клавиатура с кнопками настроек."""
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"🎥 Качество: {'HD' if s.hd else 'SD'}",
        callback_data="set:hd",
    )
    kb.button(
        text=f"🎵 Аудио: {'Вкл' if s.send_audio else 'Выкл'}",
        callback_data="set:audio",
    )
    kb.button(
        text=f"🔊 Звук TikTok: {'Вкл' if s.improve_audio else 'Выкл'}",
        callback_data="set:improve",
    )
    kb.button(text="❌ Закрыть", callback_data="set:close")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Показывает панель настроек.

    В группе удаляет сообщение и отправляет панель в личку пользователю,
    чтобы её видел только он.
    """
    try:
        await message.delete()
    except Exception:
        pass  # если нет прав на удаление (группа) — просто игнорируем
    s = user_settings.get(message.from_user.id)
    text = format_settings(s)
    kb = build_kb(s)
    if message.chat.type in ("group", "supergroup"):
        try:
            await message.bot.send_message(
                message.from_user.id, text, reply_markup=kb, parse_mode="HTML"
            )
        except Exception:
            # Пользователь не начал чат с ботом — отвечаем в группе
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("set:"))
async def on_setting(callback: CallbackQuery) -> None:
    """Обработка нажатий кнопок настроек."""
    action = callback.data[4:]
    s = user_settings.get(callback.from_user.id)

    if action == "hd":
        s.hd = not s.hd
    elif action == "audio":
        s.send_audio = not s.send_audio
    elif action == "improve":
        s.improve_audio = not s.improve_audio
    elif action == "close":
        await callback.message.delete()
        await callback.answer()
        return
    else:
        await callback.answer()
        return

    await callback.message.edit_text(
        format_settings(s),
        reply_markup=build_kb(s),
        parse_mode="HTML",
    )
    await callback.answer()
