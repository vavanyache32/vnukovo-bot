"""CheckWX — independent METAR API. Token in CHECKWX_TOKEN."""
from __future__ import annotations

from ..config import get_settings
from ..http_client import request

URL = "https://api.checkwx.com/metar/{icao}"


async def fetch_latest(icao: str) -> str | None:
    s = get_settings()
    if not s.checkwx_token:
        return None
    headers = {"X-API-Key": s.checkwx_token.get_secret_value()}
    resp = await request("GET", URL.format(icao=icao), headers=headers, expect_json=True)
    if resp is None or resp.status_code != 200:
        return None
    data = resp.json() or {}
    items = data.get("data") or []
    return items[0] if items else None
