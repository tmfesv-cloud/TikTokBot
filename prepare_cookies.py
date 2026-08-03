"""Подготовка cookies из скачанного cookies.txt (Export All) для Render.

Читает файл, оставляет только куки youtube.com/google.com,
проверяет наличие логина, пишет:
- cookies_youtube.txt          — текст (для перепроверки/обновления)
- cookies_youtube_base64.txt   — одна строка base64 для COOKIES_CONTENT на Render

Запуск:
    .venv\\Scripts\\python.exe prepare_cookies.py
"""

from __future__ import annotations

import base64
from pathlib import Path

# Путь к скачанному через расширение файлу
SRC = Path(r"C:\Users\times\Downloads\cookies.txt")
OUT_TXT = Path("cookies_youtube.txt")
OUT_B64 = Path("cookies_youtube_base64.txt")

# Куки, которые есть только у залогиненного аккаунта Google
LOGIN_COOKIES = {
    "SID", "__Secure-1PSID", "__Secure-3PSID",
    "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "LOGIN_INFO", "HSID", "SSID", "APISID",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
}


def main() -> None:
    if not SRC.exists():
        print(f"Файл не найден: {SRC}")
        print("Скачай cookies.txt через расширение и повтори.")
        return

    raw = SRC.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in raw.splitlines() if ln and not ln.startswith("#")]

    # Оставляем ТОЛЬКО авторизационные куки youtube/google.
    # (Опыт: полный набор куки ломает yt-dlp — YouTube отдаёт только
    # storyboards без форматов видео. Auth-куки работают и дают вход.)
    allowed = []
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) < 7:
            continue
        if parts[5] not in LOGIN_COOKIES:
            continue
        domain = parts[0].lstrip(".").lower()
        if (domain == "youtube.com" or domain.endswith(".youtube.com")
                or domain == "google.com" or domain.endswith(".google.com")):
            allowed.append(ln)

    # Проверяем логин
    login_found = sorted({p[5] for p in (ln.split("\t") for ln in lines) if len(p) >= 7 and p[5] in LOGIN_COOKIES})

    print(f"Всего строк в файле: {len(lines)}")
    print(f"Строк youtube/google: {len(allowed)}")
    print(f"Логин-куки: {login_found[:8]}")

    if not login_found:
        print("\nВНИМАНИЕ: ЛОГИНА НЕТ — это cookies без входа в аккаунт.")
        print("Такие cookies не обойдут блокировку YouTube. Войди в аккаунт в браузере,")
        print("обнови страницу YouTube и экспортируй cookies заново.")
        return

    content = "# Netscape HTTP Cookie File\n# Generated for TikTokBot\n" + "\n".join(allowed) + "\n"
    OUT_TXT.write_text(content, encoding="utf-8")
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    OUT_B64.write_text(b64, encoding="ascii")

    print("\nГОТОВО! Логин подтверждён.")
    print(f"Текст:    {OUT_TXT}  ({OUT_TXT.stat().st_size // 1024} КБ)")
    print(f"base64:   {OUT_B64}  ({OUT_B64.stat().st_size // 1024} КБ)")
    print("\nДальше: Render -> Environment -> COOKIES_CONTENT =")
    print("   скопировать ВСЁ содержимое файла cookies_youtube_base64.txt (одной строкой)")


if __name__ == "__main__":
    main()
