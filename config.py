import os
from dotenv import load_dotenv

load_dotenv()


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

    # Водяной знак на видео (@username бота) — для распространения бота.
    # BRAND_TEXT — текст знака; BRAND_MAX_SEC — видео длиннее не брендируем;
    # BRAND_FONT — путь к TTF-шрифту (если пусто — ищется сам);
    # BRAND_ALPHA — непрозрачность текста (0.0-1.0; 0.2 = 80% прозрачность);
    # BRAND_SIZE_DIV — высота_видео / делитель = размер шрифта (96 ≈ мелкий);
    # BRAND_BOX — чёрный фон за текстом (1 = вкл); BRAND_BOX_ALPHA — прозрачность фона.
    # Временный откат водяного знака: ffmpeg drawtext на Render ест 548MB+
    # (лимит контейнера 512MB) → OOM. Знак отключён, пока не сделаем его лёгким.
    BRAND_TEXT: str = os.getenv("BRAND_TEXT", "")
    BRAND_MAX_SEC: int = int(os.getenv("BRAND_MAX_SEC", "300"))
    BRAND_FONT: str = os.getenv("BRAND_FONT", "")
    BRAND_ALPHA: float = float(os.getenv("BRAND_ALPHA", "0.2"))
    BRAND_SIZE_DIV: int = int(os.getenv("BRAND_SIZE_DIV", "96"))
    BRAND_BOX: bool = os.getenv("BRAND_BOX", "0") == "1"
    BRAND_BOX_ALPHA: float = float(os.getenv("BRAND_BOX_ALPHA", "0.45"))

    @classmethod
    def validate(cls) -> list[str]:
        """Проверяет, что все необходимые переменные заданы."""
        errors: list[str] = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN не задан! Получи его у @BotFather")
        return errors
