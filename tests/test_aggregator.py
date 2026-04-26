"""Aggregator events: NewObservation, TempDelta, NewDailyMax, BucketCrossed, NearBoundary."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.aggregator import Aggregator
from src.models import Bucket, MetarObservation, Severity, Source


def _o(t_c: float, *, minutes: int = 0) -> MetarObservation:
    return MetarObservation(
        station="UUWW",
        issue_time=datetime(2025, 4, 26, 14, minutes, tzinfo=UTC),
        raw=f"FAKE T={t_c}",
        temperature_c=t_c,
        source=Source.AWC,
    )


def _buckets() -> list[Bucket]:
    return [
        Bucket(market_id="t7", title="7°C or below", threshold=7, kind="lower_tail"),
        Bucket(market_id="b8", title="8°C", threshold=8, kind="exact"),
        Bucket(market_id="b9", title="9°C", threshold=9, kind="exact"),
        Bucket(market_id="b10", title="10°C", threshold=10, kind="exact"),
        Bucket(market_id="t11", title="11°C or higher", threshold=11, kind="upper_tail"),
    ]


def test_new_observation_and_daily_max() -> None:
    agg = Aggregator(
        buckets=_buckets(), date_local="2025-04-26", tz="Europe/Moscow",
        end_local=datetime(2025, 4, 26, 23, 59, tzinfo=UTC),
    )
    e1 = agg.update(_o(8.2, minutes=0))
    e2 = agg.update(_o(8.4, minutes=15))
    e3 = agg.update(_o(9.7, minutes=30))

    kinds_1 = [e.kind for e in e1]
    kinds_3 = [e.kind for e in e3]
    assert "NewObservation" in kinds_1
    assert "NewDailyMax" in kinds_1
    # 9.7 → 10°C bucket (exact 10) vs initial 8 → BucketCrossed
    assert "BucketCrossed" in kinds_3


def test_temp_delta_threshold() -> None:
    agg = Aggregator(
        buckets=_buckets(), date_local="2025-04-26", tz="Europe/Moscow",
        end_local=datetime(2025, 4, 26, 23, 59, tzinfo=UTC),
    )
    agg.update(_o(8.0, minutes=0))
    e = agg.update(_o(9.0, minutes=15))
    assert any(ev.kind == "TempDelta" for ev in e)


def test_near_boundary_emits_only_in_last_3h() -> None:
    end = datetime(2025, 4, 26, 21, 0, tzinfo=UTC)
    agg = Aggregator(
        buckets=_buckets(), date_local="2025-04-26", tz="Europe/Moscow", end_local=end,
    )
    # plenty of time → no near-boundary
    far = MetarObservation(
        station="UUWW",
        issue_time=end - timedelta(hours=10),
        raw="FAKE",
        temperature_c=10.5,
        source=Source.AWC,
    )
    e_far = agg.update(far)
    assert not any(ev.kind == "NearBoundary" for ev in e_far)

    # 1h before close, t=10.5 lies in [10.4, 10.6] → NearBoundary for bucket 10
    near = MetarObservation(
        station="UUWW",
        issue_time=end - timedelta(hours=1),
        raw="FAKE2",
        temperature_c=10.5,
        source=Source.AWC,
    )
    e_near = agg.update(near)
    assert any(ev.severity == Severity.IMPORTANT and ev.kind == "NearBoundary" for ev in e_near)
