"""Bucket classifier + Polymarket Gamma title parsing."""
from __future__ import annotations

from src.core.bucket_engine import BucketEngine
from src.models import Bucket
from src.sources.polymarket_gamma import _classify_bucket


def _make_market_buckets() -> list[Bucket]:
    raw_titles = [
        "7°C or below", "8°C", "9°C", "10°C", "11°C", "12°C", "13°C",
        "14°C", "15°C", "16°C", "17°C or higher",
    ]
    out: list[Bucket] = []
    for t in raw_titles:
        threshold, kind, units, threshold_high = _classify_bucket(t)
        out.append(
            Bucket(market_id=f"m_{threshold}_{kind}", title=t, threshold=threshold, kind=kind, units=units, threshold_high=threshold_high)
        )
    return out


def test_classify_bucket_titles() -> None:
    assert _classify_bucket("7°C or below") == (7, "lower_tail", "celsius", None)
    assert _classify_bucket("17°C or higher") == (17, "upper_tail", "celsius", None)
    assert _classify_bucket("12°C") == (12, "exact", "celsius", None)
    assert _classify_bucket("50-51°F") == (50, "exact", "fahrenheit", 51)
    assert _classify_bucket("49°F or below") == (49, "lower_tail", "fahrenheit", None)
    assert _classify_bucket("68°F or higher") == (68, "upper_tail", "fahrenheit", None)


def test_round_for_resolve() -> None:
    assert BucketEngine.round_for_resolve(9.7, units="celsius") == 10
    assert BucketEngine.round_for_resolve(-2.4, units="celsius") == -2
    assert BucketEngine.round_for_resolve(-2.6, units="celsius") == -3
    assert BucketEngine.round_for_resolve(9.5, units="celsius") == 10
    assert BucketEngine.round_for_resolve(50.4, units="fahrenheit") == 50
    assert BucketEngine.round_for_resolve(50.5, units="fahrenheit") == 51


def test_bucket_for_each_threshold() -> None:
    eng = BucketEngine(_make_market_buckets())
    assert eng.bucket_for(10).title == "10°C"
    assert eng.bucket_for(7).title == "7°C or below"
    assert eng.bucket_for(5).title == "7°C or below"
    assert eng.bucket_for(17).title == "17°C or higher"
    assert eng.bucket_for(20).title == "17°C or higher"


def test_exact_overrides_tail_when_overlapping() -> None:
    """If both 'exact 17' and 'upper_tail 17' existed, 'exact' wins for T==17."""
    buckets = [
        Bucket(market_id="x", title="17°C", threshold=17, kind="exact"),
        Bucket(market_id="y", title="17°C or higher", threshold=17, kind="upper_tail"),
    ]
    eng = BucketEngine(buckets)
    assert eng.bucket_for(17).kind == "exact"
    assert eng.bucket_for(18).kind == "upper_tail"


def test_fahrenheit_range_bucket() -> None:
    buckets = [
        Bucket(market_id="r", title="50-51°F", threshold=50, kind="exact", units="fahrenheit", threshold_high=51),
        Bucket(market_id="ut", title="68°F or higher", threshold=68, kind="upper_tail", units="fahrenheit"),
    ]
    eng = BucketEngine(buckets)
    assert eng.bucket_for(50).title == "50-51°F"
    assert eng.bucket_for(51).title == "50-51°F"
    assert eng.bucket_for(49) is None
    assert eng.bucket_for(68).title == "68°F or higher"
