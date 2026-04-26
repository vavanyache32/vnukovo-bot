"""Tests for Wunderground / api.weather.com source."""
from __future__ import annotations

from datetime import UTC, datetime

from src.sources.wunderground import ParsedWunderground, parse_wunderground_timeseries


def _make_payload() -> dict:
    return {
        "observations": [
            {
                "valid_time_gmt": 1777179060,
                "temp": 43,
            },
            {
                "valid_time_gmt": 1777182660,
                "temp": 45,
            },
            {
                "valid_time_gmt": 1777186260,
                "temp": 50,
            },
        ]
    }


def test_parse_wunderground() -> None:
    p = parse_wunderground_timeseries(_make_payload(), station="KLGA")
    assert p.station == "KLGA"
    assert len(p.hourlies) == 3
    assert p.hourlies[0].temperature_c_published == 43.0
    assert p.hourlies[0].units == "fahrenheit"


def test_t_max_in_window() -> None:
    p = parse_wunderground_timeseries(_make_payload(), station="KLGA")
    start = datetime(2026, 4, 26, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 26, 23, 59, 59, tzinfo=UTC)
    assert p.t_max_in_window(start, end) == 50


def test_is_finalized() -> None:
    p = parse_wunderground_timeseries(_make_payload(), station="KLGA")
    start = datetime(2026, 4, 26, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 26, 23, 59, 59, tzinfo=UTC)
    # only 3 obs, less than min_count=20
    assert p.is_finalized(start, end) is False

    # now add enough observations
    payload = {"observations": []}
    base_ts = int(start.timestamp())
    for i in range(24):
        payload["observations"].append({"valid_time_gmt": base_ts + i * 3600, "temp": 40 + i})
    p2 = parse_wunderground_timeseries(payload, station="KLGA")
    assert p2.is_finalized(start, end) is True
