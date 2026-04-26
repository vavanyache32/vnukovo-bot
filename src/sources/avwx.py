"""AVWX — independent METAR backup. Token in AVWX_TOKEN."""
from __future__ import annotations

from loguru import logger

from ..config import get_settings
from ..http_client import request

URL = "https://avwx.rest/api/metar/{icao}"


async def fetch_latest(icao: str) -> str | None:
    s = get_settings()
    if not s.avwx_token:
        return None
    headers = {"Authorization": f"BEARER {s.avwx_token.get_secret_value()}"}
    resp = await request("GET", URL.format(icao=icao), headers=headers, expect_json=True)
    if resp is None or resp.status_code != 200:
        return None
    data = resp.json()
    raw = data.get("raw") or data.get("sanitized")
    if not raw:
        logger.debug("avwx: empty payload for {}", icao)
    return raw
