"""Running daily-max aggregator on the **info** contour (METAR 0.1°C).

Emits high-level events that downstream notifier_router converts to messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import Settings, get_settings
from ..models import MetarObservation, Severity
from .bucket_engine import Bucket, BucketEngine


@dataclass
class AggEvent:
    kind: str
    severity: Severity
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class State:
    last_temp_c: float | None = None
    last_issue_time: datetime | None = None
    daily_max_info: float | None = None
    last_bucket_threshold: int | None = None
    near_boundary_alerted_for: int | None = None  # threshold last alerted
    raw_hashes: list[tuple[str, str]] = field(default_factory=list)


def _c_to_f(val: float) -> float:
    return val * 9.0 / 5.0 + 32.0


class Aggregator:
    def __init__(
        self,
        buckets: list[Bucket],
        *,
        date_local: str,
        tz: str,
        end_local: datetime,
        units: str = "celsius",
        settings: Settings | None = None,
    ) -> None:
        self.s = settings or get_settings()
        self.buckets = buckets
        self.engine = BucketEngine(buckets)
        self.date_local = date_local
        self.tz = tz
        self.end_local = end_local
        self.units = units
        self.state = State()

    def _to_units(self, celsius: float) -> float:
        return _c_to_f(celsius) if self.units == "fahrenheit" else celsius

    def _threshold_in_units(self, celsius_threshold: float) -> float:
        return _c_to_f(celsius_threshold) if self.units == "fahrenheit" else celsius_threshold

    # ---------- main step ----------
    def update(self, obs: MetarObservation) -> list[AggEvent]:
        events: list[AggEvent] = []
        prev_c = self.state.last_temp_c
        cur_c = obs.temperature_c
        prev = self._to_units(prev_c) if prev_c is not None else None
        cur = self._to_units(cur_c)
        unit_sym = "°F" if self.units == "fahrenheit" else "°C"

        events.append(
            AggEvent(
                kind="NewObservation",
                severity=Severity.INFO,
                text=f"{obs.station} new METAR {obs.issue_time:%H:%MZ}: {cur:+.1f}{unit_sym}",
                payload={"obs": obs.model_dump(mode="json")},
            )
        )

        delta_thr = self._threshold_in_units(self.s.delta_notify_threshold_c)
        if prev is not None and abs(cur - prev) >= delta_thr:
            events.append(
                AggEvent(
                    kind="TempDelta",
                    severity=Severity.NOTICE,
                    text=f"ΔT={cur - prev:+.1f}{unit_sym} → {cur:+.1f}{unit_sym}",
                    payload={"prev": prev, "cur": cur},
                )
            )

        # Daily max (info) — stored in bucket units for easy comparison
        daily_max = self.state.daily_max_info
        if daily_max is None or cur > daily_max:
            self.state.daily_max_info = cur
            events.append(
                AggEvent(
                    kind="NewDailyMax",
                    severity=Severity.IMPORTANT,
                    text=f"NEW DAILY MAX (info): {cur:+.1f}{unit_sym}",
                    payload={"daily_max_info": cur},
                )
            )

        # Bucket detection (using the units the market resolves in)
        rounded = self.engine.round_for_resolve(cur, units=self.units)
        bucket_now = self.engine.bucket_for(rounded)
        if bucket_now is not None and (
            self.state.last_bucket_threshold is None
            or self.state.last_bucket_threshold != bucket_now.threshold
        ):
            if self.state.last_bucket_threshold is not None:
                events.append(
                    AggEvent(
                        kind="BucketCrossed",
                        severity=Severity.CRITICAL,
                        text=f"BUCKET CROSSED → {bucket_now.title}",
                        payload={
                            "from": self.state.last_bucket_threshold,
                            "to": bucket_now.threshold,
                            "title": bucket_now.title,
                        },
                    )
                )
            self.state.last_bucket_threshold = bucket_now.threshold

        # Near-boundary: only when close to end of window (< 3h)
        time_left = (self.end_local - obs.issue_time).total_seconds() / 3600.0
        if 0 < time_left < 3:
            lower_off = self._threshold_in_units(self.s.near_boundary_lower_c)
            upper_off = self._threshold_in_units(self.s.near_boundary_upper_c)
            for b in self.buckets:
                if b.kind != "exact":
                    continue
                lower = b.threshold + lower_off
                upper = b.threshold + upper_off
                if lower <= cur <= upper and self.state.near_boundary_alerted_for != b.threshold:
                    events.append(
                        AggEvent(
                            kind="NearBoundary",
                            severity=Severity.IMPORTANT,
                            text=(
                                f"NEAR INTEGER BOUNDARY: {cur:+.1f}{unit_sym} in "
                                f"[{lower:+.1f}, {upper:+.1f}] for bucket {b.title}"
                            ),
                            payload={"bucket": b.title, "cur": cur},
                        )
                    )
                    self.state.near_boundary_alerted_for = b.threshold

        self.state.last_temp_c = cur_c
        self.state.last_issue_time = obs.issue_time
        return events

    # ---------- helpers ----------
    def serialise_state(self) -> dict[str, Any]:
        return {
            "last_temp_c": self.state.last_temp_c,
            "last_issue_time": self.state.last_issue_time.isoformat()
            if self.state.last_issue_time
            else None,
            "daily_max_info": self.state.daily_max_info,
            "last_bucket_threshold": self.state.last_bucket_threshold,
            "near_boundary_alerted_for": self.state.near_boundary_alerted_for,
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.state.last_temp_c = data.get("last_temp_c")
        ts = data.get("last_issue_time")
        self.state.last_issue_time = datetime.fromisoformat(ts) if ts else None
        self.state.daily_max_info = data.get("daily_max_info")
        self.state.last_bucket_threshold = data.get("last_bucket_threshold")
        self.state.near_boundary_alerted_for = data.get("near_boundary_alerted_for")

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
