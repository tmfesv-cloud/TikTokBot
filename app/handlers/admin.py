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


def _feedback_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Очистить", callback_data="feedback:clear")
    kb.button(text="❌ Закрыть", callback_data="feedback:close")
    kb.adjust(2)
    return kb.as_markup()


@router.message(Command("feedback"))
async def cmd_feedback(message: Message) -> None:
    """Фидбек от пользователя: /feedback текст идеи."""
    text = message.text or ""
    # Убираем команду и пробелы
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "💡 Напиши свою идею или предложение после команды:\n\n"
            "/feedback твоя идея для бота",
            parse_mode=None,
        )
        return

    feedback_text = parts[1].strip()[:2000]  # лимит на длину
    user = message.from_user
    stats.save_feedback(user.id, user.username, feedback_text)

    await message.answer(
        "✅ Спасибо! Твоя идея сохранена.",
        parse_mode=None,
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Админ-панель: /admin feedback — просмотр фидбека."""
    if not _is_owner(message.from_user.id):
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    subcmd = parts[1].lower() if len(parts) > 1 else ""

    if subcmd == "feedback":
        await _show_feedback(message)
    else:
        await message.answer(
            "🔐 Админ-команды:\n\n"
            "/admin feedback — просмотреть фидбек\n"
            "/stats — статистика бота",
            parse_mode=None,
        )


async def _show_feedback(message: Message) -> None:
    """Показывает список фидбека."""
    items = stats.get_feedback(limit=20)

    if not items:
        await message.answer(
            "📭 Фидбек пока пуст.",
            reply_markup=_feedback_kb(),
            parse_mode=None,
        )
        return

    lines = ["📬 <b>Последний фидбек:</b>\n"]
    for i, item in enumerate(items, 1):
        username = item.get("username", "")
        user_id = item.get("user_id", "?")
        ts = item.get("ts", 0)
        text = item.get("text", "")

        # Форматируем дату
        from datetime import datetime
        dt = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")

        user_display = f"@{username}" if username else f"id:{user_id}"
        lines.append(f"<b>{i}.</b> {user_display} ({dt}):\n{text}\n")

    await message.answer(
        "\n".join(lines),
        reply_markup=_feedback_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("feedback:"))
async def on_feedback_callback(callback: CallbackQuery) -> None:
    """Кнопки панели фидбека."""
    if not _is_owner(callback.from_user.id):
        await callback.answer()
        return

    action = callback.data[9:]  # после "feedback:"
    if action == "clear":
        count = stats.clear_feedback()
        await callback.message.edit_text(
            f"🗑 Очищено {count} записей.",
            parse_mode=None,
        )
    elif action == "close":
        await callback.message.delete()

    await callback.answer()
