"""Synoptic Data MesoWest — primary RESOLUTION source.

This is the API behind https://www.weather.gov/wrh/timeseries.

Endpoint::
    https://api.synopticdata.com/v2/stations/timeseries
        ?stid=UUWW&start=YYYYMMDDhhmm&end=YYYYMMDDhhmm
        &vars=air_temp&units=temp|c&token=<TOKEN>

Response is JSON; we hand it off to :func:`parser.nws_timeseries.parse_synoptic_timeseries`.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from ..config import get_settings
from ..http_client import request
from ..parser.nws_timeseries import ParsedSynoptic, parse_synoptic_timeseries

URL = "https://api.synopticdata.com/v2/stations/timeseries"

ARTIFACT_ROOT = Path("data/raw")


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M")


async def fetch_day(
    stid: str,
    start: datetime,
    end: datetime,
    *,
    save_artifact: bool = True,
) -> ParsedSynoptic:
    s = get_settings()
    if not s.synoptic_token:
        logger.warning("nws_synoptic: SYNOPTIC_TOKEN not set; resolution contour disabled")
        return ParsedSynoptic(station=stid)
    params = {
        "stid": stid,
        "start": _fmt_time(start),
        "end": _fmt_time(end),
        "vars": "air_temp",
        "units": "temp|c",
        "token": s.synoptic_token.get_secret_value(),
    }
    resp = await request("GET", URL, params=params, expect_json=True, timeout_s=30)
    if resp is None or resp.status_code != 200:
        logger.warning(
            "nws_synoptic: status {} for {}",
            getattr(resp, "status_code", "?"),
            stid,
        )
        return ParsedSynoptic(station=stid)
    payload: dict[str, Any] = resp.json()
    if save_artifact:
        _save_artifact(stid, start, end, payload)
    return parse_synoptic_timeseries(payload)


def _save_artifact(stid: str, start: datetime, end: datetime, payload: dict[str, Any]) -> None:
    date_dir = ARTIFACT_ROOT / start.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    fname = f"synoptic_{stid}_{_fmt_time(start)}_{_fmt_time(end)}.json"
    path = date_dir / fname
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:  # pragma: no cover
        logger.warning("nws_synoptic: failed to write artifact {}: {}", path, e)
