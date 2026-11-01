import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    """Конфигурация бота из переменных окружения."""

    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # ID владельца бота (для доступа к /stats). Узнай свой ID у @userinfobot
    OWNER_ID: int = int((os.getenv("OWNER_ID") or "0").lstrip("="))

    # Upstash Redis — постоянное хранение статистики (URL + токен из дашборда)
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    REDIS_TOKEN: str = os.getenv("REDIS_TOKEN", "")

    # Webhook / Polling
    USE_WEBHOOK: bool = os.getenv("USE_WEBHOOK", "False").lower() == "true"
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_PORT: int = int(os.getenv("PORT", os.getenv("WEBHOOK_PORT", "8080")))

    # Хост для вебхука (0.0.0.0 для Render)
    WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")

    # Лимиты скачивания
    MAX_VIDEO_MB: int = int(os.getenv("MAX_VIDEO_MB", "45"))
    DOWNLOAD_COOLDOWN_SEC: int = int(os.getenv("DOWNLOAD_COOLDOWN_SEC", "10"))

    # Папка для временных скачанных файлов
    DOWNLOADS_DIR: str = os.getenv("DOWNLOADS_DIR", "downloads")

    # Стикер для приветствия (file_id; бот запоминает его сам, когда ему присылают стикер)
    STICKER_FILE_ID: str = os.getenv("STICKER_FILE_ID", "")

    # Cookies для TikTok, если он блокирует скачивание.
    # COOKIES_FROM_BROWSER — имя браузера: chrome, edge, firefox (локально).
    COOKIES_FROM_BROWSER: str = os.getenv("COOKIES_FROM_BROWSER", "")
    # COOKIES_FILE — путь к файлу cookies.txt (например, на Render).
    COOKIES_FILE: str = os.getenv("COOKIES_FILE", "")

    # Прокси для yt-dlp (например, на Render для обхода блокировок).
    # Формат: http://user:pass@host:port или socks5://user:pass@host:port
    PROXY_URL: str = os.getenv("PROXY_URL", "")

    # Invidious instance для YouTube (если основной YouTube недоступен).
    # Публичные инстансы: invidious.snopyta.org, invidious.kabi.tk, etc.
    INVIDIOUS_INSTANCE: str = os.getenv("INVIDIOUS_INSTANCE", "")

    # Публичные Invidious instance (fallback если INVIDIOUS_INSTANCE не задан)
    # Список публичных инстансов, которые можно использовать
    _INVIDIOUS_PUBLIC: list[str] = [
        "https://invidious.snopyta.org",
        "https://invidious.kabi.tk",
        "https://invidious.pimpmymovie.org",
        "https://invidious.perennialdev.com",
        "https://invidious.fdn.fr",
        "https://vid.puffyan.us",
    ]

    # Список бесплатных SOCKS5 прокси (может меняться — публичные сервера)
    _SOCKS5_PROXIES: list[str] = [
        "socks5://185.220.101.10:50183",  # Russia
        "socks5://185.220.101.34:50183",  # Russia
        "socks5://185.220.100.99:50183",  # Russia
        "socks5://194.26.29.10:55555",    # France
        "socks5://194.26.29.11:55555",    # France
    ]

    # Список бесплатных HTTP прокси
    _HTTP_PROXIES: list[str] = [
        "http://185.220.101.10:50182",
        "http://185.220.101.34:50182",
        "http://185.220.100.99:50182",
    ]

    @classmethod
    def get_working_proxy(cls) -> str | None:
        """Возвращает рабочий прокси из списка или None."""
        # Если прокси задан вручную — используем его
        if cls.PROXY_URL:
            return cls.PROXY_URL

        # Проверяем SOCKS5 прокси (предпочтительнее для YouTube/TikTok)
        for proxy in cls._SOCKS5_PROXIES:
            if _test_proxy_sync(proxy):
                logger.info(f"Proxy: выбран SOCKS5 {proxy}")
                return proxy

        # Проверяем HTTP прокси
        for proxy in cls._HTTP_PROXIES:
            if _test_proxy_sync(proxy):
                logger.info(f"Proxy: выбран HTTP {proxy}")
                return proxy

        return None

    @classmethod
    def validate(cls) -> list[str]:
        """Проверяет, что все необходимые переменные заданы."""
        errors: list[str] = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN не задан! Получи его у @BotFather")
        return errors


async def _test_proxy(proxy_url: str, timeout: int = 10) -> bool:
    """Проверяет доступность прокси асинхронно."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Проверка через test-сайт
            async with session.get(
                "https://www.tiktok.com/",
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                return r.status == 200
    except Exception:
        return False


def _test_proxy_sync(proxy_url: str, timeout: int = 10) -> bool:
    """Проверяет доступность прокси синхронно."""
    import asyncio
    return asyncio.run(_test_proxy(proxy_url, timeout))
