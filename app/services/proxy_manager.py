"""Менеджер прокси для автоматической смены при ошибках."""

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

# Список публичных SOCKS5 прокси
# Источники: proxyscrape, openproxylist, geonode, github free lists
_SOCKS5_PROXY_LIST = [
    "socks5://185.220.101.10:50183",
    "socks5://185.220.101.34:50183",
    "socks5://185.220.100.99:50183",
    "socks5://194.26.29.10:55555",
    "socks5://194.26.29.11:55555",
    "socks5://45.94.46.150:50183",
    "socks5://103.145.82.23:50183",
    "socks5://185.156.73.54:50183",
    "socks5://185.156.73.70:50183",
    "socks5://209.127.188.29:50183",
    "socks5://178.128.23.50:50183",
    "socks5://178.128.23.51:50183",
]

# Список публичных HTTP прокси
_HTTP_PROXY_LIST = [
    "http://185.220.101.10:50182",
    "http://185.220.101.34:50182",
    "http://185.220.100.99:50182",
    "http://194.26.29.10:55554",
    "http://194.26.29.11:55554",
]


class ProxyManager:
    """Менеджер прокси с автоматическим fallback и ротацией."""

    def __init__(self):
        self._proxies: list[str] = []
        self._current_index: int = 0
        self._last_change_time: float = 0
        self._change_interval: int = 300  # 5 минут между сменой
        self._failed_proxies: dict[str, float] = {}
        self._failure_threshold: int = 3  # сколько раз можно провалиться до исключения

    async def load_proxies(self) -> None:
        """Загружает прокси из конфига и проверяет доступность."""
        self._proxies = []
        self._failed_proxies = {}

        # Сначала проверяем ручной PROXY_URL
        if Config.PROXY_URL:
            if await self._test_proxy(Config.PROXY_URL):
                self._proxies.append(Config.PROXY_URL)
                logger.info(f"Proxy: добавлен ручной прокси {Config.PROXY_URL}")
            else:
                logger.warning(f"Proxy: ручной прокси {Config.PROXY_URL} не отвечает")

        # Добавляем SOCKS5 прокси из списка (предпочтительнее)
        for proxy in _SOCKS5_PROXY_LIST:
            if await self._test_proxy(proxy):
                self._proxies.append(proxy)
                logger.info(f"Proxy: найден рабочий SOCKS5 {proxy}")
            else:
                logger.debug(f"Proxy: SOCKS5 {proxy} не отвечает")

        # Если SOCKS5 нет, пробуем HTTP
        if not self._proxies:
            for proxy in _HTTP_PROXY_LIST:
                if await self._test_proxy(proxy):
                    self._proxies.append(proxy)
                    logger.info(f"Proxy: найден рабочий HTTP {proxy}")
                else:
                    logger.debug(f"Proxy: HTTP {proxy} не отвечает")

        if self._proxies:
            logger.info(f"Proxy: всего найдено {len(self._proxies)} рабочих прокси")
        else:
            logger.warning("Proxy: не найдено рабочих прокси")

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
        except Exception as e:
            logger.debug(f"Proxy test failed for {proxy_url}: {e}")
            return False

    def rotate_proxy(self) -> Optional[str]:
        """Переключает на следующий прокси в списке."""
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
        """Записывает неудачу для прокси и ротирует при threshold."""
        if proxy_url not in self._failed_proxies:
            self._failed_proxies[proxy_url] = 1
        else:
            self._failed_proxies[proxy_url] += 1

        # Если прокси провалился слишком много раз — ротируем
        if self._failed_proxies[proxy_url] >= self._failure_threshold:
            logger.warning(f"Proxy: прокси {proxy_url} провалился, ротация...")
            self.rotate_proxy()

    def get_working_proxy(self) -> Optional[str]:
        """Возвращает рабочий прокси, ротируя при необходимости."""
        if not self._proxies:
            return None

        current = self.get_current_proxy()
        if current:
            # Проверяем, не провалился ли он недавно
            if current in self._failed_proxies:
                failed_count = self._failed_proxies[current]
                if failed_count >= self._failure_threshold:
                    # Ротируем
                    return self.rotate_proxy()
        return current

    def get_proxy_url(self) -> Optional[str]:
        """Возвращает прокси в формате для aiohttp."""
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
