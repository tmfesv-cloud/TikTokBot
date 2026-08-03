"""Извлечение cookies YouTube из локальных браузеров для Render.

Скрипт:
1. Проходит по установленным браузерам (Chrome, Edge, Firefox, Opera, Yandex).
2. Находит тот, где есть залогиненный аккаунт Google/YouTube (спец-куки SID/LOGIN_INFO и т.п.).
3. Записывает cookies доменов youtube.com/google.com в файл cookies_youtube.txt (формат Netscape).
4. Записывает этот файл ещё и в base64 в cookies_youtube_base64.txt — одну строку,
   которую удобно вставить в переменную окружения COOKIES_CONTENT на Render.

Запуск (из каталога TikTokBot):
    .venv\\Scripts\\python.exe extract_youtube_cookies.py

Потом: Render -> Environment -> COOKIES_CONTENT = <строка из cookies_youtube_base64.txt>
"""

from __future__ import annotations

import base64
from pathlib import Path

from yt_dlp.cookies import extract_cookies_from_browser

# Куки, которые есть только у залогиненного аккаунта Google
LOGIN_COOKIES = {
    "SID", "__Secure-1PSID", "__Secure-3PSID",
    "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "LOGIN_INFO", "HSID", "SSID", "APISID",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
}

# Домены, которые нужны для YouTube/Google авторизации
ALLOWED_DOMAINS = ("youtube.com", "google.com")

OUT_TXT = Path("cookies_youtube.txt")
OUT_B64 = Path("cookies_youtube_base64.txt")


def _is_allowed(cookie) -> bool:
    d = cookie.domain.lstrip(".").lower()
    return d == "youtube.com" or d.endswith(".youtube.com") \
        or d == "google.com" or d.endswith(".google.com")


def _netscape_line(cookie) -> str:
    expires = int(cookie.expires) if cookie.expires else 0
    include_sub = "TRUE" if cookie.domain.startswith(".") else "FALSE"
    secure = "TRUE" if cookie.secure else "FALSE"
    # В value не бывает табуляций; если вдруг есть — не портим формат
    value = cookie.value.replace("\t", "%09")
    return (
        f"{cookie.domain}\t{include_sub}\t{cookie.path or '/'}\t{secure}\t"
        f"{expires}\t{cookie.name}\t{value}"
    )


def main() -> None:
    print("Ищу браузер с залогиненным YouTube-аккаунтом...")

    best = None
    best_login = []
    for browser in ("chrome", "edge", "firefox", "opera", "yandex"):
        try:
            jar = extract_cookies_from_browser(browser)
            allowed = [c for c in jar if _is_allowed(c)]
            login = sorted({c.name for c in allowed if c.name in LOGIN_COOKIES})
            print(f"  {browser}: куки youtube/google={len(allowed)}, логин-куки={login[:5]}")
            if login and len(login) > len(best_login):
                best, best_login = browser, login
        except Exception as e:
            print(f"  {browser}: ошибка {type(e).__name__}: {str(e)[:120]}")

    if not best:
        print("\nНЕ НАЙДЕН залогиненный аккаунт в браузерах.")
        print("Открой youtube.com в браузере, войди в аккаунт и запусти скрипт снова.")
        return

    print(f"\nБерём cookies из: {best}")

    jar = extract_cookies_from_browser(best)
    lines = [_netscape_line(c) for c in jar if _is_allowed(c)]
    # Сортируем, чтобы файл был стабильным
    lines.sort()
    content = "# Netscape HTTP Cookie File\n# Generated for TikTokBot\n" + "\n".join(lines) + "\n"

    OUT_TXT.write_text(content, encoding="utf-8")
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    OUT_B64.write_text(b64, encoding="ascii")

    print(f"Записано строк: {len(lines)}")
    print(f"Файл: {OUT_TXT}  ({OUT_TXT.stat().st_size // 1024} КБ)")
    print(f"base64: {OUT_B64}  ({OUT_B64.stat().st_size // 1024} КБ)")
    print("\nГотово! Теперь: Render -> Environment -> COOKIES_CONTENT =")
    print("   содержимое файла cookies_youtube_base64.txt (одной строкой)")


if __name__ == "__main__":
    main()
