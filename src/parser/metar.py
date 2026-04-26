"""METAR / SPECI parser.

Two-tier accuracy:

* Coarse `TT/DD` group — whole °C with optional `M` for negatives.
* RMK `T1ttt1ddd` group — tenths of °C with sign bit.
  Format: `T<sign><TTT><sign><DDD>` where sign 0 = positive, 1 = negative,
  TTT/DDD encoded as °C * 10.

We always prefer the RMK T-group if present.

The parser is small but careful: it has to survive odd METAR shapes
(double SPECI prefix, AUTO, TEMPO, BECMG sections, multi-line reports).
We deliberately re-implement the temperature extraction instead of relying
on python-metar's `temp.value()` because that library quietly drops
T-group precision in older versions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..models import MetarObservation, Source

_METAR_HEAD = re.compile(r"^(?:METAR|SPECI)\s+(?:COR\s+)?")
_STATION = re.compile(r"\b([A-Z]{4})\b")
_TIME = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")
# Coarse group at end of body (before RMK), e.g. " 17/12 ", "M02/M05"
_TEMP_DEW = re.compile(r"\s(M?\d{2})/(M?\d{2})(?=\s|$)")
_RMK_T = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_QNH = re.compile(r"\b(?:Q(\d{4})|A(\d{4}))\b")
_WIND = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS|KMH)\b")


def _parse_signed_int(s: str) -> int:
    return -int(s[1:]) if s.startswith("M") else int(s)


def _parse_t_group(sign: str, value: str) -> float:
    raw = int(value)
    return -raw / 10.0 if sign == "1" else raw / 10.0


def _resolve_issue_time(day: int, hour: int, minute: int, ref: datetime | None) -> datetime:
    """Combine DDhhmmZ with a reference 'now'.

    METAR carries only day-of-month — we need a real year/month.
    Pick the closest past month to `ref` whose day matches.
    """
    ref = ref or datetime.now(tz=UTC)
    candidates = []
    for delta_months in (0, 1, 2):
        year = ref.year
        month = ref.month - delta_months
        while month <= 0:
            month += 12
            year -= 1
        try:
            cand = datetime(year, month, day, hour, minute, tzinfo=UTC)
        except ValueError:
            continue
        if cand <= ref + timedelta(hours=1):
            candidates.append(cand)
    if not candidates:
        return ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return max(candidates)


@dataclass(frozen=True)
class ParsedMetar:
    station: str
    issue_time: datetime
    raw: str
    temperature_c: float
    temperature_precision_c: float
    dewpoint_c: float | None
    has_rmk_tgroup: bool
    is_speci: bool
    pressure_hpa: float | None
    wind_dir_deg: int | None
    wind_speed_kt: int | None


def parse_metar(raw: str, *, now: datetime | None = None) -> ParsedMetar:
    """Parse a single METAR/SPECI report.

    Raises ValueError on unparseable input. Tolerates leading "METAR" or "SPECI"
    plus COR/AUTO/CCx amendments.
    """
    text = raw.strip().rstrip("=").strip()
    if not text:
        raise ValueError("empty METAR")

    is_speci = text.startswith("SPECI")
    body = _METAR_HEAD.sub("", text)

    # Station
    m_st = _STATION.search(body)
    if not m_st:
        raise ValueError(f"station ICAO not found in: {raw!r}")
    station = m_st.group(1)

    # Time
    m_t = _TIME.search(body)
    if not m_t:
        raise ValueError(f"time group not found in: {raw!r}")
    day, hour, minute = (int(g) for g in m_t.groups())
    issue_time = _resolve_issue_time(day, hour, minute, now)

    # Temperature: prefer RMK T-group
    temp: float | None = None
    dew: float | None = None
    has_t = False
    precision = 1.0
    m_rmk = _RMK_T.search(body)
    if m_rmk:
        t_sign, t_val, d_sign, d_val = m_rmk.groups()
        temp = _parse_t_group(t_sign, t_val)
        dew = _parse_t_group(d_sign, d_val)
        has_t = True
        precision = 0.1
    else:
        m_td = _TEMP_DEW.search(body)
        if m_td:
            t_raw, d_raw = m_td.groups()
            temp = float(_parse_signed_int(t_raw))
            dew = float(_parse_signed_int(d_raw))
            precision = 1.0
    if temp is None:
        raise ValueError(f"temperature not found in: {raw!r}")

    # Pressure (optional)
    pressure: float | None = None
    m_qnh = _QNH.search(body)
    if m_qnh:
        if m_qnh.group(1):
            pressure = float(m_qnh.group(1))
        else:
            # inHg * 100 (e.g. A2992 → 29.92 inHg ≈ 1013.2 hPa)
            inhg = int(m_qnh.group(2)) / 100.0
            pressure = round(inhg * 33.8639, 1)

    # Wind (optional)
    wind_dir: int | None = None
    wind_kt: int | None = None
    m_wind = _WIND.search(body)
    if m_wind:
        d, sp, _gust, unit = m_wind.groups()
        wind_dir = None if d == "VRB" else int(d)
        speed = int(sp)
        if unit == "MPS":
            speed = round(speed * 1.94384)  # m/s → kt
        elif unit == "KMH":
            speed = round(speed / 1.852)
        wind_kt = speed

    return ParsedMetar(
        station=station,
        issue_time=issue_time,
        raw=text,
        temperature_c=temp,
        temperature_precision_c=precision,
        dewpoint_c=dew,
        has_rmk_tgroup=has_t,
        is_speci=is_speci,
        pressure_hpa=pressure,
        wind_dir_deg=wind_dir,
        wind_speed_kt=wind_kt,
    )


def to_observation(p: ParsedMetar, *, source: Source = Source.AWC) -> MetarObservation:
    return MetarObservation(
        station=p.station,
        issue_time=p.issue_time,
        raw=p.raw,
        temperature_c=p.temperature_c,
        temperature_precision_c=p.temperature_precision_c,
        dewpoint_c=p.dewpoint_c,
        wind_dir_deg=p.wind_dir_deg,
        wind_speed_kt=p.wind_speed_kt,
        pressure_hpa=p.pressure_hpa,
        is_speci=p.is_speci,
        has_rmk_tgroup=p.has_rmk_tgroup,
        source=source,
    )
