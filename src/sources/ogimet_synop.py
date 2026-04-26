"""Ogimet — SYNOP archive lookup by WMO number. HTML parsing.

Used for medium-latency cross-check, especially to validate that the METAR
T-group hasn't been corrupted in transmission.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from ..http_client import request

URL = "https://www.ogimet.com/cgi-bin/getsynop"


async def fetch_synop(wmo: str, *, lookback_hours: int = 6) -> list[str]:
    now = datetime.now(UTC)
    start = now - timedelta(hours=lookback_hours)
    params = {
        "block": wmo,
        "begin": start.strftime("%Y%m%d%H%M"),
        "end": now.strftime("%Y%m%d%H%M"),
    }
    resp = await request("GET", URL, params=params, timeout_s=20)
    if resp is None or resp.status_code != 200:
        return []
    text = resp.text
    # Each report is one line beginning with the WMO block; reports use the
    # canonical "AAXX YYGGiw IIiii ..." preamble.
    return re.findall(r"AAXX\s+\d+\s+" + wmo + r"\s+[^\n]+", text)
