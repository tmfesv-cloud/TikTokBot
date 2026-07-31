"""Админ-панель: /stats — статистика бота (только для владельца)."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services import stats
from config import Config

logger = logging.getLogger(__name__)

router = Router()


def _build_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data="stats:refresh")
    kb.button(text="❌ Закрыть", callback_data="stats:close")
    kb.adjust(2)
    return kb.as_markup()


def _is_owner(user_id: int) -> bool:
    return Config.OWNER_ID > 0 and user_id == Config.OWNER_ID


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Статистика — доступна только владельцу."""
    if not _is_owner(message.from_user.id):
        return  # молча игнорируем чужие запросы
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        stats.get_summary(),
        reply_markup=_build_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("stats:"))
async def on_stats_callback(callback: CallbackQuery) -> None:
    """Кнопки панели статистики."""
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return
    action = callback.data[6:]
    if action == "refresh":
        await callback.message.edit_text(
            stats.get_summary(),
            reply_markup=_build_kb(),
            parse_mode="HTML",
        )
    elif action == "close":
        await callback.message.delete()
    await callback.answer()
