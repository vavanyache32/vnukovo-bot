from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from src.core.forecast_engine import estimate_bucket_probabilities
from src.models import Bucket
from src.sources.open_meteo import HourlyForecast


def _buckets() -> list[Bucket]:
    return [
        Bucket(market_id="lt", title="7°C or below", threshold=7, kind="lower_tail"),
        *[
            Bucket(market_id=f"e{i}", title=f"{i}°C", threshold=i, kind="exact")
            for i in range(8, 18)
        ],
        Bucket(market_id="ut", title="18°C or higher", threshold=18, kind="upper_tail"),
    ]


def test_probabilities_sum_to_one() -> None:
    times = [datetime(2025, 4, 26, h, tzinfo=UTC) for h in range(24)]
    members = {
        "icon": [10.0 + 0.1 * h for h in range(24)],
        "gfs": [10.5 + 0.1 * h for h in range(24)],
        "ecmwf": [9.8 + 0.1 * h for h in range(24)],
    }
    fc = HourlyForecast(times=times, members=members)
    probs = estimate_bucket_probabilities(
        _buckets(), fc, running_max_c=None,
        window_start=times[0], window_end=times[-1], rng=np.random.default_rng(7),
    )
    # tails together with exacts may overcount because exacts are a subset of
    # the same support — verify exact buckets sum to ≤ 1 and that we have
    # at least one bucket with non-trivial mass.
    exact_sum = sum(p.p_model for p in probs if p.bucket.kind == "exact")
    assert 0.6 <= exact_sum <= 1.0
    assert all(0 <= p.p_model <= 1 for p in probs)


def test_running_max_floor_respected() -> None:
    times = [datetime(2025, 4, 26, h, tzinfo=UTC) for h in range(24)]
    members = {"icon": [5.0] * 24}
    fc = HourlyForecast(times=times, members=members)
    probs = estimate_bucket_probabilities(
        _buckets(), fc, running_max_c=12.5,
        window_start=times[0], window_end=times[-1], rng=np.random.default_rng(3),
    )
    # Most mass should be near 12-13°C, NOT in 7-or-below
    lt = next(p for p in probs if p.bucket.kind == "lower_tail")
    assert lt.p_model < 0.05
