"""Менеджер прокси для автоматической смены при ошибках.

Использует кэш рабочих прокси из файла для ускорения загрузки.
"""

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

# Кэш файл для сохранения рабочих прокси
_CACHE_FILE = Path("downloads/proxy_cache.json")

# Список публичных SOCKS5 прокси (из разных источников)
_SOCKS5_PROXY_SOURCES = [
    # Proxyscrape
    "socks5://185.220.101.10:50183",
    "socks5://185.220.101.34:50183",
    "socks5://185.220.100.99:50183",
    # Geonode
    "socks5://194.26.29.10:55555",
    "socks5://194.26.29.11:55555",
    # Дополнительные
    "socks5://45.94.46.150:50183",
    "socks5://103.145.82.23:50183",
    "socks5://185.156.73.54:50183",
    "socks5://185.156.73.70:50183",
    "socks5://209.127.188.29:50183",
    "socks5://178.128.23.50:50183",
    "socks5://178.128.23.51:50183",
    # Openproxylist
    "socks5://159.89.177.225:50183",
    "socks5://167.99.82.241:50183",
]

# Список публичных HTTP прокси
_HTTP_PROXY_SOURCES = [
    "http://185.220.101.10:50182",
    "http://185.220.101.34:50182",
    "http://185.220.100.99:50182",
    "http://194.26.29.10:55554",
    "http://194.26.29.11:55554",
    "http://45.94.46.150:50182",
    "http://103.145.82.23:50182",
]


class ProxyManager:
    """Менеджер прокси с автоматическим fallback и ротацией."""

    def __init__(self):
        self._proxies: list[str] = []
        self._current_index: int = 0
        self._last_change_time: float = 0
        self._change_interval: int = 300  # 5 минут между сменой
        self._failed_proxies: dict[str, float] = {}
        self._failure_threshold: int = 2  # сколько раз можно провалиться до исключения

    async def load_proxies(self) -> bool:
        """Загружает прокси из кэша или проверяет новые."""
        # Сначала пробуем загрузить из кэша
        if await self._load_from_cache():
            logger.info(f"Proxy: загружено {len(self._proxies)} прокси из кэша")
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
            current_time = time.time()

            # Проверяем что прокси не устарели (1 час)
            if current_time - cache.get("timestamp", 0) > 3600:
                return False

            # Проверяем каждый прокси
            for proxy in proxies:
                if await self._test_proxy(proxy, timeout=8):
                    self._proxies.append(proxy)

            return len(self._proxies) > 0
        except Exception as e:
            logger.debug(f"Cache load failed: {e}")
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
        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

    async def _test_and_load_proxies(self) -> None:
        """Проверяет все прокси и загружает рабочие."""
        self._proxies = []
        self._failed_proxies = {}

        # Сначала проверяем ручной PROXY_URL
        if Config.PROXY_URL:
            if await self._test_proxy(Config.PROXY_URL, timeout=8):
                self._proxies.append(Config.PROXY_URL)
                logger.info(f"Proxy: добавлен ручной {Config.PROXY_URL}")
            else:
                logger.warning(f"Proxy: ручной прокси не отвечает")

        # Проверяем SOCKS5
        for proxy in _SOCKS5_PROXY_SOURCES:
            if await self._test_proxy(proxy, timeout=8):
                self._proxies.append(proxy)
                logger.info(f"Proxy: SOCKS5 {proxy}")

        # Если SOCKS5 нет, пробуем HTTP
        if not self._proxies:
            for proxy in _HTTP_PROXY_SOURCES:
                if await self._test_proxy(proxy, timeout=8):
                    self._proxies.append(proxy)
                    logger.info(f"Proxy: HTTP {proxy}")

    async def _test_proxy(self, proxy_url: str, timeout: int = 8) -> bool:
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
