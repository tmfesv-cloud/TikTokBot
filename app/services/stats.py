"""Статистика использования бота.

Хранится в Upstash Redis (переживает перезапуски сервера).
Если REDIS_URL не задан (локальная разработка) — работает в памяти.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape

from config import Config

logger = logging.getLogger(__name__)

# Порядок платформ для вывода
PLATFORMS = (
    "tiktok", "instagram", "youtube", "pinterest", "vk",
    "rutube", "ok", "twitter", "dailymotion", "likee",
    "vimeo", "twitch", "tumblr", "bilibili", "xiaohongshu",
)
PLATFORM_EMOJI = {
    "tiktok": "🎵",
    "instagram": "📸",
    "youtube": "▶️",
    "pinterest": "📌",
    "vk": "🅥",
    "rutube": "🎬",
    "ok": "🔵",
    "twitter": "🐦",
    "dailymotion": "🎞️",
    "likee": "✨",
    "vimeo": "🎥",
    "twitch": "🟣",
    "tumblr": "📓",
    "bilibili": "🅱️",
    "xiaohongshu": "🔴",
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
    names: dict[int, str] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


_mem = _MemoryStats()


def make_name(username: str | None, first_name: str | None) -> str:
    """Человекочитаемое имя пользователя: @юзернейм или имя."""
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    return "?"


def _ensure_started() -> None:
    """Запоминаем время первого запуска (SETNX — сохранится между рестартами)."""
    if _redis:
        try:
            _redis.setnx("stats:started_at", str(time.time()))
        except Exception:
            pass


def register_start(user_id: int, name: str = "") -> None:
    """Отметить, что пользователь запустил бота."""
    if _redis:
        try:
            _redis.sadd("stats:users", str(user_id))
            _redis.incr("stats:starts")
            if name:
                _redis.hset("stats:names", str(user_id), name)
            _ensure_started()
            return
        except Exception:
            pass  # Redis упал — fallback в память
    _mem.users.add(user_id)
    _mem.starts += 1
    if name:
        _mem.names[user_id] = name


def register_download(user_id: int, platform: str, name: str = "") -> None:
    """Отметить успешное скачивание."""
    if _redis:
        try:
            _redis.sadd("stats:users", str(user_id))
            _redis.incr("stats:downloads")
            _redis.incr(f"stats:platform:{platform}")
            _redis.zincrby("stats:by_user", 1, str(user_id))
            if name:
                _redis.hset("stats:names", str(user_id), name)
            _ensure_started()
            return
        except Exception:
            pass
    _mem.users.add(user_id)
    _mem.downloads += 1
    _mem.by_platform[platform] += 1
    _mem.by_user[user_id] += 1
    if name:
        _mem.names[user_id] = name


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
        uids = [str(uid) for uid, _ in top]
        names = _redis.hmget("stats:names", *uids) if uids else []
        lines.append("\nТоп пользователей:")
        for i, (uid, n) in enumerate(top, 1):
            name = names[i - 1] if names and names[i - 1] else str(uid)
            lines.append(f"  {i}. <b>{escape(str(name))}</b> — {int(n)} скач.")
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
            name = _mem.names.get(uid) or str(uid)
            lines.append(f"  {i}. <b>{escape(name)}</b> — {n} скач.")
    else:
        lines.append("\nТоп пользователей:\n  Пока нет данных")

    lines.append(
        f"\n🕐 Бот работает: <b>{_fmt_duration(time.time() - _mem.started_at)}</b>"
    )
    return "\n".join(lines)


# --- Фидбек от пользователей ---
_mem_feedback: list[dict] = []  # fallback в память


def save_feedback(user_id: int, username: str | None, text: str) -> None:
    """Сохраняет фидбек пользователя."""
    timestamp = int(time.time())
    if _redis:
        try:
            # Храним как JSON в Redis-списке (новые в конце)
            import json
            data = json.dumps({
                "user_id": user_id,
                "username": username or "",
                "text": text,
                "ts": timestamp,
            }, ensure_ascii=False)
            _redis.rpush("feedback:list", data)
            return
        except Exception as e:
            logger.warning(f"Не удалось сохранить фидбек в Redis: {e}")
    # Fallback в память (последние 100)
    _mem_feedback.append({
        "user_id": user_id,
        "username": username or "",
        "text": text,
        "ts": timestamp,
    })
    if len(_mem_feedback) > 100:
        _mem_feedback.pop(0)


def get_feedback(limit: int = 20) -> list[dict]:
    """Возвращает последние N фидбеков (новые первыми)."""
    if _redis:
        try:
            import json
            items = _redis.lrange("feedback:list", -limit, -1)
            result = [json.loads(item) for item in items]
            return list(reversed(result))  # новые первыми
        except Exception as e:
            logger.warning(f"Не удалось получить фидбек из Redis: {e}")
    # Fallback из памяти
    return list(reversed(_mem_feedback[-limit:]))


def clear_feedback() -> int:
    """Очищает все фидбеки, возвращает количество удалённых."""
    if _redis:
        try:
            count = _redis.llen("feedback:list") or 0
            _redis.delete("feedback:list")
            return count
        except Exception:
            pass
    count = len(_mem_feedback)
    _mem_feedback.clear()
    return count


# --- Уведомления владельцу о сбоях ---
_mem_alerts: dict[str, float] = {}  # rate-limit в памяти (без Redis)


async def notify_owner(
    bot,
    text: str,
    rate_key: str | None = None,
    rate_ttl: int = 1800,
) -> bool:
    """Отправляет сообщение владельцу (OWNER_ID) в личку.

    rate_key — если задан, уведомление с этим ключом уходит не чаще раза
    в rate_ttl секунд. Нужно, чтобы не спамить владельцу, когда платформа
    массово не отвечает (например, юзеры шлют YouTube-ссылки).

    Возвращает True, если сообщение отправлено.
    """
    owner_id = Config.OWNER_ID
    if not owner_id:
        return False

    if rate_key:
        if _redis:
            try:
                ok = _redis.setnx(f"alerts:{rate_key}", "1")
                if not ok:
                    return False
                _redis.expire(f"alerts:{rate_key}", rate_ttl)
            except Exception:
                pass  # Redis упал — не блокируем отправку
        else:
            now = time.time()
            last = _mem_alerts.get(rate_key, 0.0)
            if now - last < rate_ttl:
                return False
            _mem_alerts[rate_key] = now

    try:
        await bot.send_message(owner_id, text, parse_mode=None)
        return True
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление владельцу: {e}")
        return False
