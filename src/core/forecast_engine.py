"""Bucket probability via Monte-Carlo over an ensemble forecast.

Inputs:
    * running_max — current info-contour T_max (°C, 0.1° precision)
    * forecast    — Open-Meteo ensemble hourly °C for remaining hours of the day

Output:
    P(bucket_i) for each bucket and `edge_i = P_model_i − price_i`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from ..models import Bucket, BucketPrice
from ..sources.open_meteo import HourlyForecast
from .bucket_engine import BucketEngine


@dataclass
class BucketProbability:
    bucket: Bucket
    p_model: float
    price: float | None
    edge: float | None  # p_model - price


def _ensemble_stats(fc: HourlyForecast, units: str = "celsius") -> tuple[np.ndarray, np.ndarray]:
    arr = np.array(
        [[(v if v is not None else np.nan) for v in series] for series in fc.members.values()],
        dtype=float,
    )
    mean = np.nanmean(arr, axis=0)
    ddof = 1 if arr.shape[0] > 1 else 0
    std = np.nanstd(arr, axis=0, ddof=ddof)
    # floor at 0.7°C ≈ 1.26°F
    floor = 1.26 if units == "fahrenheit" else 0.7
    std = np.where(np.isnan(std) | (std < floor), floor, std)
    return mean, std


def estimate_bucket_probabilities(
    buckets: list[Bucket],
    fc: HourlyForecast | None,
    *,
    running_max_c: float | None,
    window_start: datetime,
    window_end: datetime,
    units: str = "celsius",
    n_samples: int = 4000,
    rng: np.random.Generator | None = None,
    prices: dict[str, BucketPrice] | None = None,
) -> list[BucketProbability]:
    rng = rng or np.random.default_rng(42)
    eng = BucketEngine(buckets)

    # running_max_c is a legacy parameter name; value is in the bucket/market units.
    running_max = running_max_c

    samples: np.ndarray
    if fc is None or not fc.times:
        if running_max is None:
            return [BucketProbability(b, 0.0, None, None) for b in eng.buckets]
        samples = np.full(n_samples, running_max, dtype=float)
    else:
        mask = np.array(
            [
                window_start.replace(tzinfo=t.tzinfo) <= t <= window_end.replace(tzinfo=t.tzinfo)
                for t in fc.times
            ]
        ) if fc.times[0].tzinfo else np.array(
            [window_start <= t <= window_end for t in fc.times]
        )
        mean, std = _ensemble_stats(fc, units=units)
        if not mask.any():
            return [BucketProbability(b, 0.0, None, None) for b in eng.buckets]
        m = mean[mask]
        s = std[mask]
        # sample N hourly trajectories, take per-trajectory max
        noise = rng.normal(size=(n_samples, len(m))) * s + m
        traj_max = noise.max(axis=1)
        if running_max is not None:
            traj_max = np.maximum(traj_max, running_max)
        samples = traj_max

    # Round each sample like the resolution source publishes
    rounded = np.where(
        samples >= 0,
        np.floor(samples + 0.5),
        -np.floor(-samples + 0.5),
    ).astype(int)

    out: list[BucketProbability] = []
    total = len(rounded)
    for b in eng.buckets:
        if b.kind == "exact":
            if b.threshold_high is not None:
                count = int(((rounded >= b.threshold) & (rounded <= b.threshold_high)).sum())
            else:
                count = int((rounded == b.threshold).sum())
        elif b.kind == "lower_tail":
            count = int((rounded <= b.threshold).sum())
        else:
            count = int((rounded >= b.threshold).sum())
        p = count / total
        price = None
        if prices and b.market_id in prices:
            price = prices[b.market_id].yes_price
        edge = (p - price) if (price is not None) else None
        out.append(BucketProbability(bucket=b, p_model=p, price=price, edge=edge))
    return out


def render_table(rows: list[BucketProbability]) -> list[dict[str, Any]]:
    return [
        {
            "title": r.bucket.title,
            "threshold": r.bucket.threshold,
            "kind": r.bucket.kind,
            "price": r.price,
            "p_model": round(r.p_model, 3),
            "edge": round(r.edge, 3) if r.edge is not None else None,
        }
        for r in rows
    ]
