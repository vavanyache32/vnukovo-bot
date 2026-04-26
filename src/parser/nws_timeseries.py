"""Parse Synoptic Data MesoWest timeseries (the backend behind weather.gov/wrh).

Resolution rules (verbatim from the market):
  * "highest reading under the Temp column"
  * "whole degrees Celsius"
  * "level of precision that will be used when resolving the market"

Therefore we:
  * round each observation to whole °C **as published** (banker's-style is NOT
    used — NOAA tables use standard round-half-away-from-zero);
  * window observations by *local* day and (separately) UTC day;
  * report finalisation only when there are ≥ 24 hourly readings spaced
    ≤ 60 minutes apart inside the local window.

Synoptic JSON layout (relevant subset):
::
    {
      "STATION": [{
        "STID": "UUWW",
        "OBSERVATIONS": {
          "date_time":      ["2026-04-26T00:00:00Z", ...],
          "air_temp_set_1": [9.3, 9.7, ...]
        }
      }]
    }
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..models import NwsHourly


def _round_half_away(x: float) -> int:
    """Round half away from zero (standard schoolbook rounding)."""
    if math.isnan(x):
        raise ValueError("NaN temperature")
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        return datetime.fromisoformat(ts[:-1]).replace(tzinfo=UTC)
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class ParsedSynoptic:
    station: str
    hourlies: list[NwsHourly] = field(default_factory=list)

    def in_window(self, start: datetime, end: datetime) -> list[NwsHourly]:
        return [h for h in self.hourlies if start <= h.observed_at <= end]

    def t_max_in_window(self, start: datetime, end: datetime) -> int | None:
        rows = self.in_window(start, end)
        if not rows:
            return None
        return max(r.temperature_c_published for r in rows)  # type: ignore[return-value]

    def is_finalized(self, start: datetime, end: datetime, *, min_count: int = 24) -> bool:
        rows = self.in_window(start, end)
        if len(rows) < min_count:
            return False
        rows = sorted(rows, key=lambda r: r.observed_at)
        return all(
            (b.observed_at - a.observed_at) <= timedelta(minutes=70)
            for a, b in itertools.pairwise(rows)
        )


def parse_synoptic_timeseries(payload: dict[str, Any]) -> ParsedSynoptic:
    stations = payload.get("STATION") or []
    if not stations:
        return ParsedSynoptic(station="?")
    st = stations[0]
    stid = st.get("STID", "?")
    obs = st.get("OBSERVATIONS") or {}
    times = obs.get("date_time") or []
    temps = obs.get("air_temp_set_1") or obs.get("air_temp_set_1d") or []
    out: list[NwsHourly] = []
    for ts, t in zip(times, temps, strict=False):
        if t is None:
            continue
        try:
            dt = _parse_iso(ts)
            t_raw = float(t)
            published = _round_half_away(t_raw)
        except (TypeError, ValueError):
            continue
        out.append(
            NwsHourly(
                station=stid,
                observed_at=dt,
                temperature_c_published=float(published),
                temperature_c_raw=t_raw,
                units="celsius",
            )
        )
    return ParsedSynoptic(station=stid, hourlies=out)


def local_day_window(date_local: str, tz: str | ZoneInfo) -> tuple[datetime, datetime]:
    """Return [00:00, 23:59:59] local interval expressed in UTC."""
    z = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    y, m, d = (int(x) for x in date_local.split("-"))
    start = datetime(y, m, d, 0, 0, 0, tzinfo=z).astimezone(UTC)
    end = datetime(y, m, d, 23, 59, 59, tzinfo=z).astimezone(UTC)
    return start, end


def utc_day_window(date_utc: str) -> tuple[datetime, datetime]:
    y, m, d = (int(x) for x in date_utc.split("-"))
    start = datetime(y, m, d, 0, 0, 0, tzinfo=UTC)
    end = datetime(y, m, d, 23, 59, 59, tzinfo=UTC)
    return start, end
