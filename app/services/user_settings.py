"""Настройки скачивания каждого пользователя (хранятся в памяти)."""

from dataclasses import dataclass


@dataclass
class UserSettings:
    """Настройки одного пользователя."""

    hd: bool = True       # HD-качество
    send_audio: bool = False  # отправлять аудио отдельно
    improve_audio: bool = False  # улучшать звук TikTok (подмешивать оригинальный трек)


# user_id -> настройки
_store: dict[int, UserSettings] = {}


def get(user_id: int) -> UserSettings:
    """Возвращает настройки пользователя (создаёт дефолтные, если их нет)."""
    return _store.setdefault(user_id, UserSettings())


def reset(user_id: int) -> None:
    """Сбрасывает настройки пользователя к дефолту."""
    _store.pop(user_id, None)
