"""NOAA Integrated Surface Database — canonical archival data.

Useful for backtesting on historical days. We fetch the per-station yearly
file from `noaa-isd-pds` on S3 (no auth needed via HTTPS).
"""
from __future__ import annotations

import gzip
import io
from collections.abc import Iterable

from ..http_client import request

# https://www.ncei.noaa.gov/pub/data/noaa/<year>/<usaf>-<wban>-<year>.gz
URL = "https://www.ncei.noaa.gov/pub/data/noaa/{year}/{usaf}-{wban}-{year}.gz"


async def fetch_year(usaf: str, wban: str, year: int) -> Iterable[str] | None:
    resp = await request(
        "GET",
        URL.format(year=year, usaf=usaf, wban=wban),
        timeout_s=60,
    )
    if resp is None or resp.status_code != 200:
        return None
    data = gzip.decompress(resp.content) if resp.content[:2] == b"\x1f\x8b" else resp.content
    return io.StringIO(data.decode("latin-1", errors="replace")).read().splitlines()
