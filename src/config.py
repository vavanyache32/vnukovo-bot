"""Centralized configuration: pydantic-settings + stations.yaml."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Station(BaseModel):
    key: str
    icao: str
    name: str
    wmo: str | None = None
    lat: float
    lon: float
    tz: str = "UTC"
    fallback_icao: list[str] = Field(default_factory=list)
    resolve_source: str = "synoptic"
    synoptic_stid: str | None = None
    slug_pattern: str = ""
    units: str = "celsius"  # "celsius" | "fahrenheit"

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def slug_matches(self, slug: str) -> bool:
        if not self.slug_pattern:
            return False
        regex = "^" + re.escape(self.slug_pattern).replace("\\*", ".*") + "$"
        return re.match(regex, slug) is not None


class StationsConfig(BaseModel):
    stations: dict[str, Station]

    def by_slug(self, slug: str) -> Station | None:
        for st in self.stations.values():
            if st.slug_matches(slug):
                return st
        return None

    def by_key(self, key: str) -> Station | None:
        return self.stations.get(key)


def load_stations(path: Path | str | None = None) -> StationsConfig:
    p = Path(path) if path else ROOT / "stations.yaml"
    if not p.exists():
        return StationsConfig(stations={})
    with p.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    raw_stations = data.get("stations", {}) or {}
    stations: dict[str, Station] = {}
    for key, payload in raw_stations.items():
        stations[key] = Station(key=key, **payload)
    return StationsConfig(stations=stations)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # events
    default_city: str = "moscow"
    event_discovery_enabled: bool = True
    event_slug_patterns: str = "highest-temperature-in-moscow-on-*"
    resolution_timezone: str = "Europe/Moscow"

    # polling
    poll_interval_seconds: int = 25
    poll_jitter_seconds: int = 5
    delta_notify_threshold_c: float = 0.5
    near_boundary_lower_c: float = 0.4
    near_boundary_upper_c: float = 0.6

    # storage
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"

    # telegram
    telegram_bot_token: SecretStr | None = None
    telegram_admin_ids: str = ""
    telegram_mode: str = "polling"  # polling | webhook
    telegram_webhook_url: str = ""
    telegram_webhook_secret: SecretStr | None = None

    # proxies (per-host)
    proxy_telegram: str = ""
    proxy_telegram_fallback: str = ""
    proxy_polymarket: str = ""
    proxy_aviation: str = ""
    proxy_default: str = ""
    doh_resolver: str = "https://1.1.1.1/dns-query"

    # api tokens
    synoptic_token: SecretStr | None = None
    avwx_token: SecretStr | None = None
    checkwx_token: SecretStr | None = None
    wunderground_api_key: SecretStr | None = None

    # observability
    sentry_dsn: str = ""
    prometheus_enabled: bool = True
    health_port: int = 8080
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("event_slug_patterns")
    @classmethod
    def _split_patterns(cls, v: str) -> str:
        return v.strip()

    @property
    def slug_patterns(self) -> list[str]:
        return [p.strip() for p in self.event_slug_patterns.split(",") if p.strip()]

    @property
    def admin_ids(self) -> list[int]:
        return [int(x) for x in self.telegram_admin_ids.split(",") if x.strip().isdigit()]

    @property
    def resolution_zone(self) -> ZoneInfo:
        return ZoneInfo(self.resolution_timezone)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_stations() -> StationsConfig:
    return load_stations()
