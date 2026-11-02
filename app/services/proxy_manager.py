"""Менеджер прокси для TikTokBot.

Этот модуль позволяет использовать прокси для обхода блокировок платформ.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

# Кэш файл для сохранения рабочих прокси
_CACHE_FILE = Path("downloads/proxy_cache.json")

# Публичные прокси — проверены и рабочие (2026-11-01)
# Источники: free-proxy-list.net, sslproxies.org
# Формат: "socks5://IP:PORT" или "http://IP:PORT"
_PUBLIC_PROXIES = [
    # SOCKS5 прокси (рабочие)
    "socks5://37.193.95.9:10808",      # RU — проверено: 200
    "socks5://47.253.242.75:8080",     # US — проверено: 200
    "socks5://185.220.101.10:50183",   # fallback
    "socks5://185.220.101.34:50183",   # fallback
    "socks5://185.220.100.99:50183",   # fallback
]


class ProxyManager:
    """Менеджер прокси с автоматическим выбором и ротацией."""

    def __init__(self):
        self._proxies: list[str] = []
        self._current_index: int = 0
        self._last_change_time: float = 0
        self._change_interval: int = 300  # 5 минут между ротацией
        self._failed_proxies: dict[str, float] = {}
        self._failure_threshold: int = 2

    async def load_proxies(self) -> bool:
        """Загружает прокси из кэша или проверяет новые."""
        # Сначала пробуем из кэша
        if await self._load_from_cache():
            logger.info(f"Proxy: загружено {len(self._proxies)} из кэша")
            return True

        # Если кэш пустой — проверяем новые
        await self._test_and_load_proxies()
        await self._save_to_cache()

        if self._proxies:
            logger.info(f"Proxy: найдено {len(self._proxies)} рабочих прокси")
            return True

        logger.warning("Proxy: не найдено рабочих прокси")
        return False

    async def _load_from_cache(self) -> bool:
        """Загружает прокси из кэша файла."""
        if not _CACHE_FILE.exists():
            return False

        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)

            proxies = cache.get("proxies", [])
            if not proxies:
                return False

            current_time = time.time()
            if current_time - cache.get("timestamp", 0) > 3600:
                return False

            for proxy in proxies:
                if await self._test_proxy(proxy, timeout=10):
                    self._proxies.append(proxy)

            return len(self._proxies) > 0
        except Exception:
            return False

    async def _save_to_cache(self) -> None:
        """Сохраняет рабочие прокси в кэш."""
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cache = {
                "timestamp": int(time.time()),
                "proxies": self._proxies.copy(),
            }
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
            logger.debug(f"Proxy: кэш сохранён ({len(self._proxies)} прокси)")
        except Exception:
            pass

    async def _test_and_load_proxies(self) -> None:
        """Проверяет все прокси и загружает рабочие."""
        self._proxies = []
        self._failed_proxies = {}

        # Проверяем ручной PROXY_URL
        if Config.PROXY_URL:
            if await self._test_proxy(Config.PROXY_URL, timeout=10):
                self._proxies.append(Config.PROXY_URL)
                logger.info(f"Proxy: добавлен ручной {Config.PROXY_URL}")
            else:
                logger.warning(f"Proxy: ручной прокси не отвечает")

        # Проверяем публичные прокси
        for proxy in _PUBLIC_PROXIES:
            if await self._test_proxy(proxy, timeout=10):
                self._proxies.append(proxy)
                logger.info(f"Proxy: рабочий {proxy}")

    async def _test_proxy(self, proxy_url: str, timeout: int = 10) -> bool:
        """Проверяет доступность прокси."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.tiktok.com/",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as r:
                    return r.status == 200
        except Exception:
            return False

    def rotate_proxy(self) -> Optional[str]:
        """Переключает на следующий прокси."""
        if not self._proxies:
            return None

        self._current_index = (self._current_index + 1) % len(self._proxies)
        self._last_change_time = time.time()
        logger.info(f"Proxy: ротация -> {self._proxies[self._current_index]}")
        return self._proxies[self._current_index]

    def get_current_proxy(self) -> Optional[str]:
        """Возвращает текущий прокси."""
        if not self._proxies:
            return None
        return self._proxies[self._current_index]

    def record_failure(self, proxy_url: str) -> None:
        """Записывает неудачу для прокси."""
        if proxy_url not in self._failed_proxies:
            self._failed_proxies[proxy_url] = 1
        else:
            self._failed_proxies[proxy_url] += 1

        if self._failed_proxies[proxy_url] >= self._failure_threshold:
            logger.warning(f"Proxy: {proxy_url} провалился, ротация...")
            self.rotate_proxy()

    def get_working_proxy(self) -> Optional[str]:
        """Возвращает рабочий прокси."""
        if not self._proxies:
            return None

        current = self.get_current_proxy()
        if current and current in self._failed_proxies:
            if self._failed_proxies[current] >= self._failure_threshold:
                return self.rotate_proxy()
        return current

    def get_proxy_url(self) -> Optional[str]:
        """Возвращает прокси для aiohttp."""
        return self.get_working_proxy()


# Глобальный менеджер
_proxy_manager: Optional[ProxyManager] = None


async def init_proxy_manager() -> ProxyManager:
    """Инициализирует глобальный менеджер прокси."""
    global _proxy_manager
    _proxy_manager = ProxyManager()
    await _proxy_manager.load_proxies()
    return _proxy_manager


def get_proxy_manager() -> Optional[ProxyManager]:
    """Возвращает глобальный менеджер прокси."""
    return _proxy_manager
