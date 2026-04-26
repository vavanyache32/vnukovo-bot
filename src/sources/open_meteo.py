"""Open-Meteo forecast API — free, no key. Used for bucket probability model.

Multi-model ensemble lets us approximate forecast uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..http_client import request

URL = "https://api.open-meteo.com/v1/forecast"

ENSEMBLE_MODELS = "icon_seamless,gfs_seamless,ecmwf_ifs04"


@dataclass
class HourlyForecast:
    times: list[datetime]
    members: dict[str, list[float | None]]  # model_name -> hourly °C


async def fetch_forecast(
    lat: float, lon: float, *, days: int = 2, temperature_unit: str = "celsius"
) -> HourlyForecast | None:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m",
        "models": ENSEMBLE_MODELS,
        "forecast_days": days,
        "past_days": 0,
        "timezone": "UTC",
        "temperature_unit": temperature_unit,
    }
    resp = await request("GET", URL, params=params, expect_json=True, timeout_s=20)
    if resp is None or resp.status_code != 200:
        return None
    data = resp.json()
    hourly = data.get("hourly") or {}
    times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in hourly.get("time", [])]
    members: dict[str, list[float | None]] = {}
    for k, v in hourly.items():
        if k.startswith("temperature_2m"):
            members[k] = list(v)
    if not members:
        return None
    return HourlyForecast(times=times, members=members)
