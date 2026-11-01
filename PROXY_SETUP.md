# Прокси для TikTokBot

## Текущая ситуация

На Render (дата-центр) большинство платформ блокируют запросы из-за IP-адресов:
- YouTube — 403 Forbidden
- Instagram — ошибка парсинга
- TikTok — работает через tikwm.com

## Решение: Ручная настройка прокси

### Вариант 1: Купленный прокси (рекомендую)
Покупай SOCKS5 прокси на:
- https://proxy6.com
- https://geonode.com
- https://soax.com

Формат в .env:
```
PROXY_URL=socks5://user:pass@host:port
```

### Вариант 2: Бесплатные прокси (не стабильно)
Ищи на:
- https://free-proxy-list.net
- https://sslproxies.org
- https://spys.one

Проверяй рабочие прокси через:
```
python -c "
import asyncio
import aiohttp

async def test(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://www.tiktok.com/', proxy=url, timeout=10) as r:
                print(f'{url}: {r.status}')
    except:
        print(f'{url}: FAIL')

asyncio.run(test('socks5://IP:PORT'))
"
```

### Вариант 3: Локальный VPN
Запусти бота локально с активным VPN:
```
set PROXY_URL=socks5://127.0.0.1:1080
python bot.py
```

## Статус на Render

Бот сейчас:
- TikTok → работает через tikwm.com (не нужен прокси)
- YouTube → 403 Forbidden (нужен прокси)
- Другие платформы → могут блокироваться (нужен прокси)

## Настройка в Render

1. Зайди в Dashboard → TikTokBot
2. Environment → Add Variable
3. Добавь: `PROXY_URL=socks5://user:pass@host:port`
4. Save
