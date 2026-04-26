"""Tests for the NWS Synoptic timeseries parser & resolver-style window logic."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from src.parser.nws_timeseries import (
    _round_half_away,
    local_day_window,
    parse_synoptic_timeseries,
)


def test_round_half_away() -> None:
    assert _round_half_away(9.7) == 10
    assert _round_half_away(9.5) == 10
    assert _round_half_away(-9.5) == -10
    assert _round_half_away(-2.3) == -2
    assert _round_half_away(-2.6) == -3
    assert _round_half_away(0.0) == 0


def test_parse_corpus(synoptic_fixture: dict) -> None:
    p = parse_synoptic_timeseries(synoptic_fixture)
    assert p.station == "UUWW"
    assert len(p.hourlies) == 25
    # 17.8 → 18 °C, the highest published whole-°C reading in the day
    publishes = [h.temperature_c_published for h in p.hourlies]
    assert int(max(publishes)) == 18


def test_local_window_max_uses_local_day(synoptic_fixture: dict) -> None:
    p = parse_synoptic_timeseries(synoptic_fixture)
    start, end = local_day_window("2025-04-26", "Europe/Moscow")
    assert start.tzinfo == UTC
    assert (end - start).total_seconds() > 23 * 3600
    t_max = p.t_max_in_window(start, end)
    assert int(t_max) == 18


def test_finalised_after_24_hourlies(synoptic_fixture: dict) -> None:
    p = parse_synoptic_timeseries(synoptic_fixture)
    start, end = local_day_window("2025-04-26", "Europe/Moscow")
    assert p.is_finalized(start, end)


def test_local_vs_utc_disagreement_demo() -> None:
    # Build a payload where the daily peak of LOCAL (UTC+3) day is 17,
    # but the UTC-day peak is the next day's spike of 22.
    payload = {
        "STATION": [
            {
                "STID": "TEST",
                "OBSERVATIONS": {
                    "date_time": [
                        # Local 2025-04-26 peak at 17.8 (12:00Z = 15:00 MSK)
                        "2025-04-26T12:00:00Z",
                        # Local 2025-04-27 spike but inside UTC 2025-04-26 (23:30Z = 02:30 MSK)
                        "2025-04-26T23:30:00Z",
                    ],
                    "air_temp_set_1": [17.8, 22.0],
                },
            }
        ]
    }
    p = parse_synoptic_timeseries(payload)
    local_start, local_end = local_day_window("2025-04-26", "Europe/Moscow")
    utc_start = datetime(2025, 4, 26, 0, 0, 0, tzinfo=UTC)
    utc_end = datetime(2025, 4, 26, 23, 59, 59, tzinfo=UTC)

    local_max = p.t_max_in_window(local_start, local_end)
    utc_max = p.t_max_in_window(utc_start, utc_end)
    # 23:30Z = 02:30 MSK on 2025-04-27, so it's NOT in the local 26th window
    assert int(local_max) == 18
    # but it IS inside UTC-26
    assert int(utc_max) == 22


def test_parses_local_timezone_ok() -> None:
    z = ZoneInfo("Europe/Moscow")
    s, e = local_day_window("2025-01-01", z)
    assert s.utcoffset().total_seconds() == 0  # UTC-anchored
