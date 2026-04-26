"""Lightweight Telegram → RU webhook relay (optional)."""
from __future__ import annotations

import hashlib
import hmac
import os

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

UPSTREAM = os.environ["UPSTREAM"]  # e.g. https://your-ru-host:443/tg
SECRET = os.environ["SECRET"].encode()
TG_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
PATH = os.environ["RELAY_PATH"]

app = FastAPI()


@app.post(f"/{PATH}")
async def relay(req: Request, x_telegram_bot_api_secret_token: str | None = Header(None)) -> dict[str, str]:
    if TG_SECRET and x_telegram_bot_api_secret_token != TG_SECRET:
        raise HTTPException(status_code=403, detail="bad telegram secret")
    body = await req.body()
    sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(UPSTREAM, content=body, headers={"X-Relay-Sig": sig, "Content-Type": req.headers.get("content-type", "application/json")})
    return {"status": str(r.status_code)}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
