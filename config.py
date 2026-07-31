import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Конфигурация бота из переменных окружения."""

    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # ID владельца бота (для доступа к /stats). Узнай свой ID у @userinfobot
    OWNER_ID: int = int(os.getenv("OWNER_ID") or "0")

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

    @classmethod
    def validate(cls) -> list[str]:
        """Проверяет, что все необходимые переменные заданы."""
        errors: list[str] = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN не задан! Получи его у @BotFather")
        return errors
