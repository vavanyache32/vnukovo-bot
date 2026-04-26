"""Wunderground backend via api.weather.com — resolution source for °F markets.

Endpoint::
    https://api.weather.com/v1/location/{stid}/observations/historical.json
        ?apiKey={key}&units=e&startDate=YYYYMMDD&endDate=YYYYMMDD

`units=e` means imperial (Fahrenheit, mph, inches).
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from ..config import get_settings
from ..http_client import request
from ..models import NwsHourly

URL_TEMPLATE = "https://api.weather.com/v1/location/{stid}/observations/historical.json"
ARTIFACT_ROOT = Path("data/raw")
DEFAULT_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


@dataclass
class ParsedWunderground:
    station: str
    hourlies: list[NwsHourly] = field(default_factory=list)

    def in_window(self, start: datetime, end: datetime) -> list[NwsHourly]:
        return [h for h in self.hourlies if start <= h.observed_at <= end]

    def t_max_in_window(self, start: datetime, end: datetime) -> int | None:
        rows = self.in_window(start, end)
        if not rows:
            return None
        return int(max(r.temperature_c_published for r in rows))

    def is_finalized(self, start: datetime, end: datetime, *, min_count: int = 20) -> bool:
        """Wunderground observations are not always strictly hourly;
        we relax the gap threshold slightly.
        """
        rows = self.in_window(start, end)
        if len(rows) < min_count:
            return False
        rows = sorted(rows, key=lambda r: r.observed_at)
        return all(
            (b.observed_at - a.observed_at) <= timedelta(minutes=90)
            for a, b in itertools.pairwise(rows)
        )


def parse_wunderground_timeseries(payload: dict[str, Any], station: str) -> ParsedWunderground:
    observations = payload.get("observations") or []
    if not observations:
        return ParsedWunderground(station=station)
    out: list[NwsHourly] = []
    for obs in observations:
        ts = obs.get("valid_time_gmt")
        temp = obs.get("temp")
        if ts is None or temp is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(ts), tz=UTC)
            t_published = float(temp)
        except (TypeError, ValueError):
            continue
        out.append(
            NwsHourly(
                station=station,
                observed_at=dt,
                temperature_c_published=t_published,
                temperature_c_raw=t_published,
                units="fahrenheit",
            )
        )
    return ParsedWunderground(station=station, hourlies=out)


async def fetch_day(
    stid: str,
    start: datetime,
    end: datetime,
    *,
    save_artifact: bool = True,
) -> ParsedWunderground:
    s = get_settings()
    api_key = (s.wunderground_api_key.get_secret_value() if s.wunderground_api_key else None) or DEFAULT_API_KEY
    url = URL_TEMPLATE.format(stid=stid)
    params = {
        "apiKey": api_key,
        "units": "e",
        "startDate": _fmt_date(start),
        "endDate": _fmt_date(end),
    }
    resp = await request("GET", url, params=params, expect_json=True, timeout_s=30)
    if resp is None or resp.status_code != 200:
        logger.warning(
            "wunderground: status {} for {}",
            getattr(resp, "status_code", "?"),
            stid,
        )
        return ParsedWunderground(station=stid)
    payload: dict[str, Any] = resp.json()
    if save_artifact:
        _save_artifact(stid, start, end, payload)
    return parse_wunderground_timeseries(payload, station=stid)


def _save_artifact(stid: str, start: datetime, end: datetime, payload: dict[str, Any]) -> None:
    date_dir = ARTIFACT_ROOT / start.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_stid = stid.replace(":", "_")
    fname = f"wunderground_{safe_stid}_{_fmt_date(start)}_{_fmt_date(end)}.json"
    path = date_dir / fname
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:  # pragma: no cover
        logger.warning("wunderground: failed to write artifact {}: {}", path, e)
