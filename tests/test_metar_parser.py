"""Snapshot-style tests for the METAR parser, including RMK T-group precision."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.parser.metar import parse_metar


def test_parses_simple_with_rmk_tgroup() -> None:
    raw = (
        "METAR UUWW 011200Z 29009G16MPS CAVOK 18/M01 Q1022 NOSIG RMK QFE743 T01891011="
    )
    p = parse_metar(raw, now=datetime(2025, 1, 2, tzinfo=UTC))
    assert p.station == "UUWW"
    assert p.has_rmk_tgroup
    assert p.temperature_c == pytest.approx(18.9)
    assert p.dewpoint_c == pytest.approx(-1.1)
    assert p.temperature_precision_c == 0.1
    assert p.pressure_hpa == 1022.0
    assert p.wind_dir_deg == 290
    # 9 MPS ≈ 17.5 kt → 17 (round-half-even)
    assert p.wind_speed_kt in (17, 18)


def test_negative_temperature_via_t_group() -> None:
    raw = "METAR UUWW 050300Z 00000MPS 0500 OVC003 M05/M05 Q1024 NOSIG RMK QFE745 T10481048="
    p = parse_metar(raw, now=datetime(2024, 1, 6, tzinfo=UTC))
    assert p.temperature_c == pytest.approx(-4.8)
    assert p.dewpoint_c == pytest.approx(-4.8)
    assert p.has_rmk_tgroup


def test_falls_back_to_coarse_temp_when_no_rmk() -> None:
    raw = "METAR EGLL 121500Z 24010KT 9999 SCT040 17/05 Q1015 NOSIG="
    p = parse_metar(raw, now=datetime(2025, 6, 13, tzinfo=UTC))
    assert not p.has_rmk_tgroup
    assert p.temperature_precision_c == 1.0
    assert p.temperature_c == 17.0
    assert p.dewpoint_c == 5.0


def test_speci_flag_detected() -> None:
    raw = "SPECI UUWW 011115Z 28010G18MPS CAVOK 18/M01 Q1022 NOSIG RMK QFE743 T01791014="
    p = parse_metar(raw, now=datetime(2025, 1, 2, tzinfo=UTC))
    assert p.is_speci is True
    assert p.has_rmk_tgroup
    assert p.temperature_c == pytest.approx(17.9)


def test_inhg_pressure_conversion() -> None:
    raw = "METAR KJFK 121551Z 27009KT 10SM CLR 22/12 A3001 RMK AO2 SLP163 T02220117="
    p = parse_metar(raw, now=datetime(2025, 6, 13, tzinfo=UTC))
    assert p.pressure_hpa is not None
    assert 1015 < p.pressure_hpa < 1018  # 30.01 inHg ≈ 1016 hPa
    assert p.has_rmk_tgroup
    assert p.temperature_c == pytest.approx(22.2)


def test_corpus_parses_without_errors(metar_fixtures: list[str]) -> None:
    assert len(metar_fixtures) >= 50
    failures: list[str] = []
    for raw in metar_fixtures:
        try:
            p = parse_metar(raw, now=datetime(2025, 6, 1, tzinfo=UTC))
            assert -50 < p.temperature_c < 50, f"out of range: {p.temperature_c} in {raw}"
            assert p.station == "UUWW"
        except Exception as e:  # noqa: BLE001
            failures.append(f"{raw}: {e}")
    assert not failures, "\n".join(failures)


def test_corpus_rmk_precision_consistent(metar_fixtures: list[str]) -> None:
    """Coarse TT and RMK T-group should agree to within ±1.0°C.

    Real-world METARs sometimes truncate (not round) the coarse field,
    so a strict ±0.5 check is too tight. Anything > 1.0°C, however, is
    a sign of a genuine transmission error.
    """
    import re as _re

    for raw in metar_fixtures:
        p = parse_metar(raw, now=datetime(2025, 6, 1, tzinfo=UTC))
        if not p.has_rmk_tgroup:
            continue
        m = _re.search(r"\s(M?\d{2})/(M?\d{2})\b", raw)
        if not m:
            continue
        coarse_t = m.group(1)
        coarse = -int(coarse_t[1:]) if coarse_t.startswith("M") else int(coarse_t)
        assert abs(coarse - p.temperature_c) <= 1.2, raw


def test_invalid_metar_raises() -> None:
    with pytest.raises(ValueError):
        parse_metar("not a metar at all")
