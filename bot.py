"""
Точка входа TikTokBot.

Поддерживает два режима:
- Polling (локальная разработка)
- Webhook (для Render)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from urllib.request import getproxies

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.token import TokenValidationError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import Config
from app.handlers import start, settings, tiktok

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _get_proxy() -> str | None:
    """Определяет системный прокси для aiohttp (если есть)."""
    proxies = getproxies()
    # Берём HTTPS прокси (или HTTP как запасной)
    return proxies.get("https") or proxies.get("http") or None


def _create_bot() -> Bot:
    """Создаёт бота с учётом системного прокси."""
    proxy = _get_proxy()
    if proxy:
        logger.info(f"Обнаружен системный прокси: {proxy}")
        # Используем aiohttp сессию с прокси
        session = AiohttpSession(proxy=proxy)
        return Bot(
            token=Config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
            session=session,
        )
    return Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )


async def on_startup(bot: Bot) -> None:
    """Действия при запуске: установка вебхука."""
    if Config.USE_WEBHOOK:
        base = Config.WEBHOOK_URL or os.getenv("RENDER_EXTERNAL_URL", "")
        if not base:
            logger.error("WEBHOOK_URL не задан! Не могу установить webhook")
            return
        webhook_url = f"{base}/webhook"
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook установлен: {webhook_url}")
    else:
        # Удаляем вебхук на случай если он был
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Вебхук удалён, работаем в режиме polling")


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке: ничего не удаляем, чтобы webhook оставался."""
    logger.info("Бот останавливается... webhook сохраняется")


def create_dispatcher() -> Dispatcher:
    """Создаёт и настраивает диспетчер."""
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start.router)
    dp.include_router(settings.router)
    dp.include_router(tiktok.router)

    return dp


def _cleanup_old_downloads(max_age_sec: int = 3600) -> None:
    """Удаляет старые временные файлы из папки downloads (если бот упал до отправки)."""
    out_dir = Path(Config.DOWNLOADS_DIR)
    if not out_dir.exists():
        return
    now = time.time()
    cleaned = 0
    for p in out_dir.iterdir():
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
                cleaned += 1
        except OSError:
            pass
    if cleaned:
        logger.info(f"Очистка downloads: удалено {cleaned} старых файлов")


def start_polling() -> None:
    """Запуск в режиме polling."""
    bot = _create_bot()
    dp = create_dispatcher()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("TikTokBot запущен в режиме polling")
    dp.run_polling(bot)


def start_webhook() -> None:
    """Запуск в режиме webhook (для Render/Railway)."""
    bot = _create_bot()
    dp = create_dispatcher()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    # Health check — чтобы Render не засыпал
    async def health(request):
        return web.Response(text="TikTokBot is alive!")

    app.router.add_get("/", health)

    # Настраиваем вебхук
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")

    setup_application(app, dp, bot=bot)

    logger.info(f"TikTokBot запущен в режиме webhook на порту {Config.WEBHOOK_PORT}")
    web.run_app(app, host=Config.WEBHOOK_HOST, port=Config.WEBHOOK_PORT)


def main() -> None:
    """Точка входа."""
    # Проверяем конфигурацию
    errors = Config.validate()
    if errors:
        logger.error("Ошибки конфигурации:")
        for err in errors:
            logger.error(f"  ❌ {err}")
        sys.exit(1)

    # Автоопределение Render: если есть PORT — это Render, включаем webhook
    # даже если USE_WEBHOOK не задан в переменных
    if os.getenv("PORT"):
        Config.USE_WEBHOOK = True
        if not Config.WEBHOOK_URL:
            Config.WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "")

    _cleanup_old_downloads()

    logger.info(f"TikTokBot запускается... Режим: {'webhook' if Config.USE_WEBHOOK else 'polling'}")

    try:
        if Config.USE_WEBHOOK:
            start_webhook()
        else:
            start_polling()
    except TokenValidationError:
        logger.error(
            "❌ Токен бота невалидный! Проверь BOT_TOKEN в файле .env "
            "(создай нового бота или получи токен заново у @BotFather)."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
