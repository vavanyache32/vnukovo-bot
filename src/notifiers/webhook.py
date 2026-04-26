"""Generic JSON webhook notifier (Slack-compatible 'text' payload)."""
from __future__ import annotations

from typing import Any

from loguru import logger

from ..http_client import request


async def send(url: str, payload: dict[str, Any]) -> None:
    if not url:
        return
    resp = await request("POST", url, json=payload)
    if resp is None or resp.status_code >= 400:
        logger.warning("webhook: send failed status={}", getattr(resp, "status_code", "?"))
