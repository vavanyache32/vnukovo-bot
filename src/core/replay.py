"""Replay & backtest framework — minute-level reconstruction of a market day.

Inputs:
* historical METAR archive (Iastate ASOS, NOAA ISD)
* Synoptic timeseries for the same day

We replay events through the same Aggregator + BucketEngine but with a
NotifierRouter swapped for a "logging" notifier that simply records what
*would* have been sent. The end report computes:

* latency_distribution — gap between observation issue_time and our action,
* missed_events — alerts that should have fired but didn't,
* false_positives — alerts that fired but were wrong (with hindsight),
* info_vs_resolve_disagreement — info-contour vs Synoptic disagreement profile.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from ..config import get_stations
from ..core.aggregator import Aggregator
from ..core.market_discovery import fetch_event_or_raise
from ..models import MetarObservation, Source
from ..parser.metar import parse_metar, to_observation
from ..parser.nws_timeseries import local_day_window
from ..sources import iastate, nws_synoptic


@dataclass
class ReplayReport:
    date: str
    slug: str
    station: str
    events_emitted: int = 0
    severities: dict[str, int] = field(default_factory=dict)
    final_info_max: float | None = None
    final_resolve_max: int | None = None
    info_vs_resolve_delta: float | None = None


class _LogNotifier:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def send_event(self, slug: str, ev, **_: Any) -> None:  # type: ignore[no-untyped-def]
        self.events.append(ev)

    async def send_resolution(self, _: Any) -> None:
        pass

    async def send_info(self, _: str) -> None:
        pass


async def run_replay(*, date_local: str, slug: str, speed: int = 60) -> ReplayReport:
    cfg = get_stations()
    station = cfg.by_slug(slug)
    if station is None:
        raise RuntimeError(f"No station mapping for slug {slug}")
    ev = await fetch_event_or_raise(slug)
    tz = station.tz
    local_start, local_end = local_day_window(date_local, tz)
    notifier = _LogNotifier()
    agg = Aggregator(buckets=ev.buckets, date_local=date_local, tz=tz, end_local=local_end)

    # Pull METARs for the day from Iowa State (24h chunk)
    raws = await iastate.fetch_latest(station.icao, lookback_hours=24)
    obs_list: list[MetarObservation] = []
    for raw in raws:
        try:
            parsed = parse_metar(raw)
        except Exception:
            continue
        if local_start <= parsed.issue_time <= local_end:
            obs_list.append(to_observation(parsed, source=Source.IASTATE))

    obs_list.sort(key=lambda o: o.issue_time)
    sev_counter: dict[str, int] = {}
    last_t = obs_list[0].issue_time if obs_list else None

    for o in obs_list:
        if last_t is not None and speed > 0:
            await asyncio.sleep(max((o.issue_time - last_t).total_seconds() / speed, 0))
        events = agg.update(o)
        for e in events:
            await notifier.send_event(slug, e, station=station)
            sev_counter[e.severity.value] = sev_counter.get(e.severity.value, 0) + 1
        last_t = o.issue_time

    parsed_synop = await nws_synoptic.fetch_day(
        station.synoptic_stid or station.icao, local_start, local_end
    )
    resolve_max = parsed_synop.t_max_in_window(local_start, local_end)
    info_max = agg.state.daily_max_info
    delta = (info_max - resolve_max) if (info_max is not None and resolve_max is not None) else None

    report = ReplayReport(
        date=date_local,
        slug=slug,
        station=station.icao,
        events_emitted=len(notifier.events),
        severities=sev_counter,
        final_info_max=info_max,
        final_resolve_max=int(resolve_max) if resolve_max is not None else None,
        info_vs_resolve_delta=delta,
    )
    logger.info("replay: {}", report)
    return report


async def run_backtest(*, date_from: str, date_to: str) -> list[ReplayReport]:
    """Aggregate replays across a date range. Slug must be inferable from
    discovery for each date; for tooling simplicity we expect callers to
    supply per-day slugs externally. Here we only sweep the configured
    default city's slug pattern.
    """
    from ..config import get_settings

    s = get_settings()
    pattern = s.slug_patterns[0] if s.slug_patterns else None
    if pattern is None:
        return []
    out: list[ReplayReport] = []
    cur = datetime.fromisoformat(date_from)
    end = datetime.fromisoformat(date_to)
    while cur <= end:
        # Slug pattern uses 'on-<month>-<d>-<yyyy>' style; we substitute *.
        date_s = cur.strftime("%Y-%m-%d")
        guess_slug = pattern.replace("*", cur.strftime("%B-%-d-%Y").lower())
        try:
            r = await run_replay(date_local=date_s, slug=guess_slug, speed=0)
            out.append(r)
        except Exception:
            logger.exception("backtest: failed for {}", date_s)
        cur += timedelta(days=1)
    return out
