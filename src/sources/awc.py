"""NOAA Aviation Weather Center — primary low-latency METAR source.

Endpoint:
    https://aviationweather.gov/api/data/metar?ids=<ICAO>&format=json
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from ..http_client import request

URL = "https://aviationweather.gov/api/data/metar"


@dataclass
class AwcResult:
    raw: str
    received_at: datetime
    metadata: dict[str, Any]


async def fetch_latest(icao: str, *, hours: int = 1) -> list[AwcResult]:
    resp = await request(
        "GET",
        URL,
        params={"ids": icao, "format": "json", "hours": hours},
        expect_json=True,
        use_etag=True,
    )
    if resp is None:
        return []
    if resp.status_code != 200:
        logger.warning("awc: status {} for {}", resp.status_code, icao)
        return []
    payload = resp.json()
    if not isinstance(payload, list):
        return []
    out: list[AwcResult] = []
    for item in payload:
        raw = item.get("rawOb") or item.get("raw")
        if raw:
            out.append(AwcResult(raw=raw, received_at=datetime.utcnow(), metadata=item))
    return out
