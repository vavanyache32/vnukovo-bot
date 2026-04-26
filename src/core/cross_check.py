"""Cross-check two contours and neighbouring stations.

Two checks:

1. **Info vs resolve.** Compare running METAR T_max (0.1°C) against the
   NWS Synoptic running maximum (whole °C). If `|info - resolve| > 0.6°C`
   we emit WARNING — usually a sign that NOAA hasn't ingested the latest
   reading yet, but occasionally indicates a rounding / data-feed mismatch.

2. **Neighbours.** Pull METAR from fallback stations (e.g. UUEE/UUDD for
   Moscow). If the primary station deviates by > 4°C from all neighbours,
   we flag an anomaly — likely a sensor malfunction or transcription error.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Severity


@dataclass
class CheckResult:
    severity: Severity
    text: str
    payload: dict[str, float | str | None]


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def info_vs_resolve(
    info_running_max: float | None,
    resolve_running_max: float | None,
    *,
    tolerance: float = 0.6,
    info_units: str = "celsius",
    resolve_units: str = "celsius",
) -> CheckResult | None:
    if info_running_max is None or resolve_running_max is None:
        return None
    # Normalize both to resolve units for comparison
    info_norm = info_running_max
    if info_units == "celsius" and resolve_units == "fahrenheit":
        info_norm = _c_to_f(info_running_max)
    elif info_units == "fahrenheit" and resolve_units == "celsius":
        info_norm = _f_to_c(info_running_max)
    delta = info_norm - resolve_running_max
    if abs(delta) <= tolerance:
        return None
    unit_sym = "°F" if resolve_units == "fahrenheit" else "°C"
    return CheckResult(
        severity=Severity.WARNING,
        text=(
            f"info vs resolve disagreement {delta:+.2f}{unit_sym} "
            f"(info={info_norm:+.2f}, resolve={resolve_running_max:+.0f})"
        ),
        payload={
            "delta": delta,
            "info_max": info_norm,
            "resolve_max": resolve_running_max,
        },
    )


def utc_vs_local(
    t_max_local: int | None,
    t_max_utc: int | None,
    *,
    units: str = "celsius",
) -> CheckResult | None:
    if t_max_local is None or t_max_utc is None:
        return None
    if t_max_local == t_max_utc:
        return None
    unit_sym = "°F" if units == "fahrenheit" else "°C"
    return CheckResult(
        severity=Severity.CRITICAL,
        text=(
            f"T_max disagreement between LOCAL ({t_max_local:+d}{unit_sym}) and UTC "
            f"({t_max_utc:+d}{unit_sym}) day windows — manual review recommended"
        ),
        payload={"local": t_max_local, "utc": t_max_utc},
    )


def neighbours(
    primary_t: float,
    neighbour_ts: list[float],
    *,
    tolerance: float = 4.0,
    units: str = "celsius",
) -> CheckResult | None:
    if not neighbour_ts:
        return None
    if all(abs(primary_t - n) > tolerance for n in neighbour_ts):
        unit_sym = "°F" if units == "fahrenheit" else "°C"
        return CheckResult(
            severity=Severity.WARNING,
            text=(
                f"Primary station deviates from all neighbours by > {tolerance}{unit_sym} "
                f"(primary={primary_t:+.1f}, neighbours={neighbour_ts})"
            ),
            payload={"primary": primary_t},
        )
    return None
