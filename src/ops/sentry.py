"""Sentry initialisation with proxy-URL masking and PII filter."""
from __future__ import annotations

import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.loguru import LoguruIntegration

from ..config import get_settings

_PROXY_PAT = re.compile(r"(?P<scheme>(?:socks5?|https?))://(?P<user>[^:@/]+):(?P<pass>[^@/]+)@")


def _scrub(s: str) -> str:
    return _PROXY_PAT.sub(r"\g<scheme>://***:***@", s)


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    # Drop direct PII keys
    for key in ("user", "request"):
        if key in event:
            event[key] = {}
    # Scrub strings recursively
    def _walk(o: Any) -> Any:
        if isinstance(o, str):
            return _scrub(o)
        if isinstance(o, list):
            return [_walk(x) for x in o]
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        return o
    return _walk(event)


def init() -> None:
    s = get_settings()
    if not s.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=s.sentry_dsn,
        traces_sample_rate=0.05,
        send_default_pii=False,
        before_send=_before_send,
        integrations=[
            AsyncioIntegration(),
            HttpxIntegration(),
            LoguruIntegration(),
        ],
    )
