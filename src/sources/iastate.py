"""Iowa State ASOS / IEM. Free, no token. Slower, useful as cross-check."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..http_client import request

URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"


async def fetch_latest(icao: str, *, lookback_hours: int = 1) -> list[str]:
    now = datetime.now(UTC)
    start = now - timedelta(hours=lookback_hours)
    params = {
        "station": icao,
        "data": "metar",
        "year1": start.year,
        "month1": start.month,
        "day1": start.day,
        "hour1": start.hour,
        "minute1": start.minute,
        "year2": now.year,
        "month2": now.month,
        "day2": now.day,
        "hour2": now.hour,
        "minute2": now.minute,
        "tz": "Etc/UTC",
        "format": "onlycomma",
        "missing": "M",
    }
    resp = await request("GET", URL, params=params, timeout_s=15)
    if resp is None or resp.status_code != 200:
        return []
    raws: list[str] = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith(("station,", "#")):
            continue
        # CSV: station,valid,metar
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        raws.append(parts[2].strip().strip('"'))
    return raws
