"""Статистика использования бота.

Хранится в Upstash Redis (переживает перезапуски сервера).
Если REDIS_URL не задан (локальная разработка) — работает в памяти.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from config import Config

logger = logging.getLogger(__name__)

# Порядок платформ для вывода
PLATFORMS = ("tiktok", "instagram", "youtube", "pinterest")
PLATFORM_EMOJI = {
    "tiktok": "🎵",
    "instagram": "📸",
    "youtube": "▶️",
    "pinterest": "📌",
}

# --- Подключение к Redis (если настроен) ---
_redis = None
if Config.REDIS_URL and Config.REDIS_TOKEN:
    try:
        from upstash_redis import Redis

        _redis = Redis(url=Config.REDIS_URL, token=Config.REDIS_TOKEN)
        # Проверяем подключение
        _redis.ping()
        logger.info("Статистика: подключено к Upstash Redis")
    except Exception as e:
        _redis = None
        logger.warning(f"Статистика: Redis недоступен, работаю в памяти — {e}")


# --- Фолбэк в память (без Redis) ---
@dataclass
class _MemoryStats:
    users: set[int] = field(default_factory=set)
    starts: int = 0
    downloads: int = 0
    by_platform: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_user: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    started_at: float = field(default_factory=time.time)


_mem = _MemoryStats()


def _ensure_started() -> None:
    """Запоминаем время первого запуска (SETNX — сохранится между рестартами)."""
    if _redis:
        try:
            _redis.setnx("stats:started_at", str(time.time()))
        except Exception:
            pass


def register_start(user_id: int) -> None:
    """Отметить, что пользователь запустил бота."""
    if _redis:
        try:
            _redis.sadd("stats:users", str(user_id))
            _redis.incr("stats:starts")
            _ensure_started()
            return
        except Exception:
            pass  # Redis упал — fallback в память
    _mem.users.add(user_id)
    _mem.starts += 1


def register_download(user_id: int, platform: str) -> None:
    """Отметить успешное скачивание."""
    if _redis:
        try:
            _redis.sadd("stats:users", str(user_id))
            _redis.incr("stats:downloads")
            _redis.incr(f"stats:platform:{platform}")
            _redis.zincrby("stats:by_user", 1, str(user_id))
            _ensure_started()
            return
        except Exception:
            pass
    _mem.users.add(user_id)
    _mem.downloads += 1
    _mem.by_platform[platform] += 1
    _mem.by_user[user_id] += 1


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
    if _redis:
        try:
            return _summary_from_redis()
        except Exception:
            pass  # Redis упал — показываем из памяти
    return _summary_from_memory()


def _summary_from_redis() -> str:
    users = int(_redis.scard("stats:users") or 0)
    starts = int(_redis.get("stats:starts") or 0)
    downloads = int(_redis.get("stats:downloads") or 0)
    started_at = float(_redis.get("stats:started_at") or time.time())

    lines = [
        "📊 <b>Статистика бота</b>\n",
        f"👥 Пользователей: <b>{users}</b>",
        f"🚀 Запусков /start: <b>{starts}</b>",
        f"📥 Скачиваний: <b>{downloads}</b>\n",
        "По платформам:",
    ]
    if downloads:
        for p in PLATFORMS:
            n = int(_redis.get(f"stats:platform:{p}") or 0)
            emoji = PLATFORM_EMOJI.get(p, "•")
            lines.append(f"  {emoji} {p.capitalize()}: <b>{n}</b>")
    else:
        lines.append("  Пока нет скачиваний 🤷")

    top = _redis.zrevrange("stats:by_user", 0, 4, withscores=True)
    if top:
        lines.append("\nТоп пользователей:")
        for i, (uid, n) in enumerate(top, 1):
            lines.append(f"  {i}. <code>{uid}</code> — {int(n)} скач.")
    else:
        lines.append("\nТоп пользователей:\n  Пока нет данных")

    lines.append(
        f"\n🕐 Бот работает: <b>{_fmt_duration(time.time() - started_at)}</b>"
    )
    return "\n".join(lines)


def _summary_from_memory() -> str:
    lines = [
        "📊 <b>Статистика бота</b>\n",
        f"👥 Пользователей: <b>{len(_mem.users)}</b>",
        f"🚀 Запусков /start: <b>{_mem.starts}</b>",
        f"📥 Скачиваний: <b>{_mem.downloads}</b>\n",
        "По платформам:",
    ]
    if _mem.downloads:
        for p in PLATFORMS:
            n = _mem.by_platform.get(p, 0)
            emoji = PLATFORM_EMOJI.get(p, "•")
            lines.append(f"  {emoji} {p.capitalize()}: <b>{n}</b>")
    else:
        lines.append("  Пока нет скачиваний 🤷")

    if _mem.by_user:
        top = sorted(
            _mem.by_user.items(), key=lambda kv: kv[1], reverse=True
        )[:5]
        lines.append("\nТоп пользователей:")
        for i, (uid, n) in enumerate(top, 1):
            lines.append(f"  {i}. <code>{uid}</code> — {n} скач.")
    else:
        lines.append("\nТоп пользователей:\n  Пока нет данных")

    lines.append(
        f"\n🕐 Бот работает: <b>{_fmt_duration(time.time() - _mem.started_at)}</b>"
    )
    return "\n".join(lines)
