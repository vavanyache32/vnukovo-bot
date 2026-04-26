"""Bucket engine: classify whole-°C T_max → winning bucket, emulate
NOAA-style rounding, expose helpers for forecasting.
"""
from __future__ import annotations

import math

from ..models import Bucket


class BucketEngine:
    def __init__(self, buckets: list[Bucket]) -> None:
        # Sort canonical: lower_tail | exact (asc) | upper_tail
        order = {"lower_tail": 0, "exact": 1, "upper_tail": 2}
        self.buckets = sorted(buckets, key=lambda b: (order[b.kind], b.threshold))

    @staticmethod
    def round_for_resolve(t: float, units: str = "celsius") -> int:
        """Standard rounding (half away from zero) to whole degrees.

        Works identically for Celsius and Fahrenheit; units parameter is
        kept for API clarity and future-proofing.
        """
        _ = units  # noqa: F841
        return math.floor(t + 0.5) if t >= 0 else -math.floor(-t + 0.5)

    def bucket_for(self, t_whole: int) -> Bucket | None:
        # exact wins over tails when both nominally match
        exacts = [b for b in self.buckets if b.kind == "exact" and b.matches(t_whole)]
        if exacts:
            return exacts[0]
        for b in self.buckets:
            if b.matches(t_whole):
                return b
        return None

    def low_tail(self) -> Bucket | None:
        for b in self.buckets:
            if b.kind == "lower_tail":
                return b
        return None

    def high_tail(self) -> Bucket | None:
        for b in reversed(self.buckets):
            if b.kind == "upper_tail":
                return b
        return None

    def all_thresholds(self) -> list[int]:
        return sorted({b.threshold for b in self.buckets})
