"""Pydantic v2 domain models.

Two temperature contours:
  * METAR/SPECI 0.1°C from RMK T-group → info contour (alerts).
  * NWS Synoptic timeseries, whole °C as published → resolution contour.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(enum.StrEnum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    IMPORTANT = "IMPORTANT"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Source(enum.StrEnum):
    AWC = "awc"
    AVWX = "avwx"
    IASTATE = "iastate"
    CHECKWX = "checkwx"
    OGIMET = "ogimet"
    NOAA_ISD = "noaa_isd"
    NWS_SYNOPTIC = "nws_synoptic"
    OPEN_METEO = "open_meteo"
    POLYMARKET = "polymarket"


class MetarObservation(BaseModel):
    """A single decoded METAR/SPECI observation.

    `temperature_c` is the most precise available temperature for this report:
    if RMK T-group is present (0.1°C), it overrides the coarse TT/DD pair.
    """

    model_config = ConfigDict(frozen=True)

    station: str
    issue_time: datetime  # UTC
    raw: str
    temperature_c: float
    temperature_precision_c: float = 0.1  # 0.1 if T-group, 1.0 otherwise
    dewpoint_c: float | None = None
    wind_dir_deg: int | None = None
    wind_speed_kt: int | None = None
    visibility_m: int | None = None
    pressure_hpa: float | None = None
    is_speci: bool = False
    has_rmk_tgroup: bool = False
    source: Source = Source.AWC

    @property
    def raw_hash(self) -> str:
        import hashlib

        return hashlib.sha1(self.raw.strip().encode()).hexdigest()[:16]

    @property
    def temperature_f(self) -> float:
        return self.temperature_c * 9.0 / 5.0 + 32.0


class NwsHourly(BaseModel):
    """Single hourly observation from NWS WRH timeseries / Synoptic Data / Wunderground."""

    model_config = ConfigDict(frozen=True)

    station: str
    observed_at: datetime  # UTC
    temperature_c_published: float  # whole degrees as displayed by the source (legacy field name)
    temperature_c_raw: float | None = None  # original (possibly fractional) value
    units: str = "celsius"  # "celsius" | "fahrenheit"


class Bucket(BaseModel):
    """A single resolution bucket of a Highest-Temperature market."""

    model_config = ConfigDict(frozen=True)

    market_id: str
    title: str  # e.g. "9°C", "7°C or below", "17°C or higher", "50-51°F"
    threshold: int  # integer threshold in the bucket's units
    threshold_high: int | None = None  # for range buckets like "50-51°F"
    kind: str  # "lower_tail" | "exact" | "upper_tail"
    units: str = "celsius"  # "celsius" | "fahrenheit"
    outcome_yes_token_id: str | None = None
    outcome_no_token_id: str | None = None

    def matches(self, t_max_whole: int) -> bool:
        if self.kind == "exact":
            if self.threshold_high is not None:
                return self.threshold <= t_max_whole <= self.threshold_high
            return t_max_whole == self.threshold
        if self.kind == "lower_tail":
            return t_max_whole <= self.threshold
        if self.kind == "upper_tail":
            return t_max_whole >= self.threshold
        return False


class BucketPrice(BaseModel):
    market_id: str
    yes_price: float | None = None
    no_price: float | None = None
    last_trade_price: float | None = None
    fetched_at: datetime


class MarketEvent(BaseModel):
    """Polymarket event with its bucket markets."""

    event_id: str
    slug: str
    title: str
    end_date: datetime
    neg_risk_market_id: str | None = None
    buckets: list[Bucket] = Field(default_factory=list)


class Notification(BaseModel):
    severity: Severity
    title: str
    body: str
    payload: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str = ""
    created_at: datetime


class ResolutionReport(BaseModel):
    """Final resolution artefact, frozen after publication."""

    slug: str
    event_id: str
    station: str
    date_local: str  # YYYY-MM-DD
    timezone: str
    units: str = "celsius"  # "celsius" | "fahrenheit"
    t_max_resolve_whole_c: int  # whole degrees in the report's units (legacy name)
    t_max_resolve_local: int | None = None  # max in local-day window
    t_max_resolve_utc: int | None = None  # max in UTC-day window
    t_max_info_metar_c: float | None = None  # 0.1°C info contour (always celsius)
    winning_bucket_title: str | None = None
    winning_bucket_threshold: int | None = None
    hourly_count: int
    finalized: bool
    revisions_locked: bool = True
    source: str = "synoptic"
    raw_artifact_path: str | None = None
    generated_at: datetime
