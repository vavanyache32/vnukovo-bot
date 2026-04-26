"""Optional Discord webhook (no bot account needed)."""
from __future__ import annotations

from loguru import logger

from ..http_client import request


async def send_webhook(url: str, content: str) -> None:
    if not url:
        return
    resp = await request("POST", url, json={"content": content[:1900]})
    if resp is None or resp.status_code >= 400:
        logger.warning("discord: send failed status={}", getattr(resp, "status_code", "?"))
