"""Final resolution: NWS Synoptic, whole °C, wait-for-finalisation, lock-once.

Strict adherence to the market rules:

* "highest reading under the Temp column" — we take the **max** of the
  whole-°C published values inside the local-day window.
* "whole degrees Celsius" — :func:`parse_synoptic_timeseries` already
  rounds each observation; we never re-round nor average.
* "can not resolve until data has been finalized" — :class:`ParsedSynoptic`
  exposes :meth:`is_finalized` (≥ 24 hourly points, gap ≤ 70 min).
  We poll up to ``max_wait`` (default 48 h) before giving up.
* "revisions ... will not be considered" — once a resolution has been
  persisted with ``finalized=True``, the storage layer refuses overwrites.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from ..config import Station, get_settings
from ..models import ResolutionReport
from ..parser.nws_timeseries import (
    ParsedSynoptic,
    local_day_window,
    utc_day_window,
)
from ..sources import nws_synoptic, wunderground
from ..storage import save_resolution
from .bucket_engine import BucketEngine
from .cross_check import utc_vs_local


class FinalisationTimeout(RuntimeError):
    pass


async def _pull_until_final(
    station: Station,
    start: datetime,
    end: datetime,
    *,
    max_wait: timedelta = timedelta(hours=48),
    poll_every: timedelta = timedelta(minutes=15),
):
    stid = station.synoptic_stid or station.icao
    deadline = datetime.now(UTC) + max_wait
    last = None
    while True:
        if station.resolve_source == "wunderground":
            last = await wunderground.fetch_day(stid, start, end)
            if last.is_finalized(start, end):
                return last
        else:
            last_syn = await nws_synoptic.fetch_day(stid, start, end)
            if last_syn.is_finalized(start, end):
                return last_syn
        if datetime.now(UTC) >= deadline:
            raise FinalisationTimeout(
                f"Resolution data not finalised for {stid} ({station.resolve_source}) in window {start}..{end}"
            )
        logger.info(
            "resolver: not finalised yet ({} obs in window), sleeping {}",
            len(last.in_window(start, end)) if last else 0,
            poll_every,
        )
        await asyncio.sleep(poll_every.total_seconds())


async def resolve(
    *,
    slug: str,
    event_id: str,
    station: Station,
    date_local: str,
    buckets: list,
    info_t_max: float | None = None,
    max_wait: timedelta = timedelta(hours=48),
) -> ResolutionReport:
    s = get_settings()
    tz = station.tz or s.resolution_timezone
    units = station.units
    local_start, local_end = local_day_window(date_local, tz)
    utc_start, utc_end = utc_day_window(date_local)

    parsed = await _pull_until_final(station, local_start, local_end, max_wait=max_wait)

    t_max_local = parsed.t_max_in_window(local_start, local_end)
    t_max_utc_obj = parsed.t_max_in_window(utc_start, utc_end) if parsed.hourlies else None
    if t_max_local is None:
        raise RuntimeError(f"resolver: no observations in local window for {station.icao}")

    t_max_local_int = BucketEngine.round_for_resolve(float(t_max_local), units=units)
    t_max_utc_int = BucketEngine.round_for_resolve(float(t_max_utc_obj), units=units) if t_max_utc_obj is not None else None

    eng = BucketEngine(buckets)
    winning = eng.bucket_for(t_max_local_int)

    # Critical disagreement check
    disagree = utc_vs_local(t_max_local_int, t_max_utc_int, units=units)
    if disagree is not None:
        logger.warning("resolver: {}", disagree.text)

    artifact_dir = Path("data/raw") / date_local
    source_label = station.resolve_source
    if artifact_dir.exists():
        artifact_path = next(artifact_dir.glob(f"{source_label}_{station.icao}_*.json"), None)
    else:
        artifact_path = None

    report = ResolutionReport(
        slug=slug,
        event_id=event_id,
        station=station.icao,
        date_local=date_local,
        timezone=tz,
        units=units,
        t_max_resolve_whole_c=t_max_local_int,
        t_max_resolve_local=t_max_local_int,
        t_max_resolve_utc=t_max_utc_int,
        t_max_info_metar_c=info_t_max,
        winning_bucket_title=winning.title if winning else None,
        winning_bucket_threshold=winning.threshold if winning else None,
        hourly_count=len(parsed.in_window(local_start, local_end)),
        finalized=True,
        revisions_locked=True,
        source=source_label,
        raw_artifact_path=str(artifact_path) if artifact_path else None,
        generated_at=datetime.now(UTC),
    )
    await save_resolution(report)
    unit_sym = "°F" if units == "fahrenheit" else "°C"
    logger.info(
        "resolver: {} → {}{}, bucket {}",
        slug,
        report.t_max_resolve_whole_c,
        unit_sym,
        report.winning_bucket_title,
    )
    return report


def write_json_report(report: ResolutionReport, dest: Path | str) -> Path:
    p = Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return p
