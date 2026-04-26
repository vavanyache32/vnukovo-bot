# Telegram webhook relay (optional)

Минимальный FastAPI-relay для случая, когда из RU выход на `api.telegram.org`
нестабилен даже через прокси, а у вас есть зарубежный VPS с TLS.

Схема:

```
Telegram → https://relay.example.com/<RELAY_PATH>  (Cloudflare/HTTPS)
       │
       ▼ (mTLS или HMAC)
RU-сервер: vnukovo-bot webhook
```

Запуск:

```bash
docker run --rm -p 8443:8443 -e RELAY_PATH=$(openssl rand -hex 16) \
  -e UPSTREAM=https://your-ru-host:443/tg \
  -e SECRET=$(openssl rand -hex 32) \
  ghcr.io/yourorg/vnukovo-bot-relay:latest
```

`relay.py` — простой proxy с проверкой Telegram secret_token,
HMAC-подписью и форвардом на upstream.

> Не обязательная часть; включается только если webhook надёжнее polling.
