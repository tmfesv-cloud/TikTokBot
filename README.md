# 🎬 TikTokBot — скачивает видео из TikTok

Telegram-бот, который скачивает видео из TikTok **без водяного знака** и отправляет прямо в чат.

## ✨ Возможности

- 📥 Скачивание видео по ссылке (обычные и короткие ссылки `vm.`/`vt.`)
- 🚫 Видео без водяного знака
- 🖼 Фотопосты отправляются как группа фото
- ⏳ Анти-спам: пауза между запросами
- 📦 Ограничение размера (по умолчанию до 45 МБ — лимит Telegram)

## 🛠 Как запустить

### 1. Создать бота
У [@BotFather](https://t.me/BotFather): `/newbot` → имя + username (заканчивается на `bot`) → сохрани токен.

### 2. Настроить
```bash
copy .env.example .env
```
Открой `.env` в блокноте и вставь токен вместо `BOT_TOKEN=`.

### 3. Запустить локально
Просто запусти:
```
start_bot.bat
```
Или вручную:
```
.venv\Scripts\python.exe bot.py
```

### 4. Задеплоить на Render (24/7)
1. Создай репозиторий на GitHub (например `TikTokBot`) и склонируй его.
2. Зайди на [render.com](https://render.com) → **New → Web Service** → подключи репозиторий.
3. Настройки:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`
4. В **Environment** добавь `BOT_TOKEN` (и при желании `MAX_VIDEO_MB`, `DOWNLOAD_COOLDOWN_SEC`).
5. Запусти `deploy_render.bat` (путь к репозиторию указан внутри файла).
6. (Необязательно) Настрой Deploy Hook в Render (Settings → Deploy Hook), сохрани URL в `render_hook.txt` рядом с батником — деплой станет полностью автоматическим.

## 📝 Команды

- `/start` — приветствие
- `/help` — справка
- `/тикток ссылка` или `/tt ссылка` — скачать по ссылке
- *Просто ссылка* — бот скачает сам

## ⚠️ Заметка

TikTok иногда блокирует запросы с IP дата-центров (например, на Render) или с подозрительных IP. Если бот отвечает «TikTok заблокировал запрос» — есть два варианта:

1. Запусти бота локально (там почти всегда работает).
2. Добавь cookies в `.env`:
   - `COOKIES_FROM_BROWSER=chrome` — браузер, в котором ты открывал TikTok (chrome / edge / firefox), или
   - `COOKIES_FILE=cookies.txt` — файл cookies, сгенерированный расширением **Get cookies.txt LOCALLY** (можно положить и на Render).
