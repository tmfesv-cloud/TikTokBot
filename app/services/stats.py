"""Статистика использования бота (хранится в памяти)."""

import time
from collections import defaultdict
from dataclasses import dataclass, field

# Порядок платформ для вывода
PLATFORMS = ("tiktok", "instagram", "youtube", "pinterest")
PLATFORM_EMOJI = {
    "tiktok": "🎵",
    "instagram": "📸",
    "youtube": "▶️",
    "pinterest": "📌",
}


@dataclass
class Stats:
    """Аккумулятор статистики."""

    users: set[int] = field(default_factory=set)      # уникальные пользователи
    starts: int = 0                                   # всего запусков /start
    downloads: int = 0                                # всего скачиваний
    by_platform: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_user: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    started_at: float = field(default_factory=time.time)
    last_download_at: float = 0.0


_stats = Stats()


def register_start(user_id: int) -> None:
    """Отметить, что пользователь запустил бота."""
    _stats.users.add(user_id)
    _stats.starts += 1


def register_download(user_id: int, platform: str) -> None:
    """Отметить успешное скачивание."""
    _stats.users.add(user_id)
    _stats.downloads += 1
    _stats.by_platform[platform] += 1
    _stats.by_user[user_id] += 1
    _stats.last_download_at = time.time()


def _fmt_duration(seconds: float) -> str:
    """Форматирует секунды в 'Xд Yч'."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes or not parts:
        parts.append(f"{minutes}м")
    return " ".join(parts)


def get_summary() -> str:
    """Текстовая сводка для админ-панели."""
    lines = [
        "📊 <b>Статистика бота</b>\n",
        f"👥 Пользователей: <b>{len(_stats.users)}</b>",
        f"🚀 Запусков /start: <b>{_stats.starts}</b>",
        f"📥 Скачиваний: <b>{_stats.downloads}</b>\n",
        "По платформам:",
    ]
    if _stats.downloads:
        for p in PLATFORMS:
            n = _stats.by_platform.get(p, 0)
            emoji = PLATFORM_EMOJI.get(p, "•")
            lines.append(f"  {emoji} {p.capitalize()}: <b>{n}</b>")
    else:
        lines.append("  Пока нет скачиваний 🤷")

    # Топ пользователей (до 5)
    if _stats.by_user:
        top = sorted(_stats.by_user.items(), key=lambda kv: kv[1], reverse=True)[:5]
        lines.append("\nТоп пользователей:")
        for i, (uid, n) in enumerate(top, 1):
            lines.append(f"  {i}. <code>{uid}</code> — {n} скач.")
    else:
        lines.append("\nТоп пользователей:\n  Пока нет данных")

    lines.append(f"\n🕐 Бот работает: <b>{_fmt_duration(time.time() - _stats.started_at)}</b>")
    return "\n".join(lines)
