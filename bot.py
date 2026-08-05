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
from aiogram.types import BotCommand
from aiogram.utils.token import TokenValidationError
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import Config
from app.handlers import admin, start, settings, tiktok
from app.services import stats

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
    """Действия при запуске: меню команд + вебхук."""
    # Синяя кнопка меню с командами
    await bot.set_my_commands([
        BotCommand(command="help", description="Справка"),
        BotCommand(command="invite", description="Пригласить друзей"),
        BotCommand(command="settings", description="Настройки скачивания"),
        BotCommand(command="clear", description="Сбросить настройки"),
    ])

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

    # Уведомить владельца о запуске/перезапуске (rate-limit против crash-loop спама)
    await stats.notify_owner(
        bot,
        "🔄 Бот перезапустился и работает.",
        rate_key="startup",
        rate_ttl=1800,
    )


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке: ничего не удаляем, чтобы webhook оставался."""
    logger.info("Бот останавливается... webhook сохраняется")


def create_dispatcher() -> Dispatcher:
    """Создаёт и настраивает диспетчер."""
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin.router)
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

    # Health check — чтобы Render не засыпал на бесплатном тарифе
    import time as _time
    _start_time = _time.time()

    async def health(request):
        return web.Response(text="TikTokBot is alive!")

    async def ping(request):
        uptime = int(_time.time() - _start_time)
        return web.json_response({
            "status": "ok",
            "uptime_seconds": uptime,
            "bot": "Pufik",
        })

    # Диагностика: проверка связи с tikwm/TikTok с IP Render + память процесса.
    # Открыть в браузере https://tiktokbot-dxpr.onrender.com/diag
    async def diag(request):
        import aiohttp

        def _rss_mb() -> float:
            """RSS текущего процесса в МБ (Linux /proc)."""
            try:
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            return int(line.split()[1]) / 1024.0
            except Exception:
                pass
            return 0.0

        async def _probe(name: str, url: str) -> dict:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        url, timeout=aiohttp.ClientTimeout(total=15),
                        allow_redirects=True,
                    ) as r:
                        body = await r.read()
                        return {"name": name, "ok": True, "status": r.status,
                                "bytes": len(body)}
            except Exception as e:
                return {"name": name, "ok": False, "error": str(e)[:150]}

        # Реальный цикл tikwm-скачивания (как бот) с замером памяти на каждом шаге.
        # URL из query ?url=... или тестовый.
        url = request.query.get("url", "https://www.tiktok.com/t/ZP8n6mLjT/")
        steps: list[dict] = []
        rss0 = _rss_mb()

        async def _tikwm_steps():
            from app.services import tiktok_service as ts
            import yt_dlp
            steps.append({"step": "start", "rss_mb": round(rss0, 1),
                          "ytdlp": yt_dlp.version.__version__})
            try:
                # ffmpeg есть?
                import subprocess, shutil
                ff = shutil.which("ffmpeg")
                steps.append({"step": "ffmpeg", "path": ff or "NOT FOUND",
                              "rss_mb": round(_rss_mb(), 1)})
            except Exception as e:
                steps.append({"step": "ffmpeg", "error": str(e)[:100]})

            try:
                # tikwm API запрос
                async with aiohttp.ClientSession(headers=ts._HTTP_HEADERS) as s:
                    async with s.get(
                        "https://www.tikwm.com/api/",
                        params={"url": url, "hd": 1},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as r:
                        payload = await r.json(content_type=None)
                    data = payload.get("data") or {}
                    video_url = data.get("hdplay") or data.get("play")
                    steps.append({
                        "step": "tikwm_api",
                        "code": payload.get("code"),
                        "title": (data.get("title") or "")[:60],
                        "hd_size": data.get("hd_size"),
                        "duration": data.get("duration"),
                        "rss_mb": round(_rss_mb(), 1),
                    })
                    if video_url:
                        # Скачивание файла чанками (как в боте)
                        async with s.get(
                            video_url, timeout=aiohttp.ClientTimeout(total=120),
                        ) as fr:
                            size = 0
                            with open("downloads/diag_test.mp4", "wb") as f:
                                async for chunk in fr.content.iter_chunked(65536):
                                    size += len(chunk)
                                    f.write(chunk)
                        steps.append({
                            "step": "download_file",
                            "http": fr.status,
                            "bytes": size,
                            "rss_mb": round(_rss_mb(), 1),
                        })
            except Exception as e:
                steps.append({"step": "tikwm_download_error",
                              "error": str(e)[:200],
                              "rss_mb": round(_rss_mb(), 1)})

        try:
            await _tikwm_steps()
        except Exception as e:
            steps.append({"step": "outer_error", "error": str(e)[:200]})

        # Полный цикл download() (как бот: сжатие + водяной знак через ffmpeg)
        try:
            from app.services import tiktok_service as ts
            steps.append({"step": "full_download_start", "rss_mb": round(_rss_mb(), 1)})
            result = await ts.download(url)
            rss_after = _rss_mb()
            import os
            sizes = [round(os.path.getsize(f) / 1024, 1) for f in result.files]
            steps.append({
                "step": "full_download_done",
                "rss_mb": round(rss_after, 1),
                "delta_mb": round(rss_after - rss0, 1),
                "files_kb": sizes,
                "is_video": result.is_video,
                "duration": result.duration,
            })
            result.cleanup()
            steps.append({"step": "after_cleanup", "rss_mb": round(_rss_mb(), 1)})
        except Exception as e:
            steps.append({"step": "full_download_error",
                          "error": type(e).__name__ + ": " + str(e)[:200],
                          "rss_mb": round(_rss_mb(), 1)})

        tikwm = await _probe("tikwm.com", "https://www.tikwm.com/api/")
        tiktok = await _probe("tiktok.com", "https://www.tiktok.com/")
        return web.json_response({
            "rss_mb": round(rss0, 1),
            "checks": [tikwm, tiktok],
            "tikwm_download": steps,
        })

    app.router.add_get("/", health)
    app.router.add_get("/ping", ping)
    app.router.add_get("/diag", diag)

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
