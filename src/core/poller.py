"""Async polling loop.

Architecture:

* `monitor_loop(slug)` runs forever for a given slug; multiple loops can
  coexist in one process (one per active market).
* Inside the loop we orchestrate three contours at different rates:
  fast info pull (METAR every 20–30 s with jitter), Synoptic pull
  (every 15 min) and forecast refresh (every 15 min).
"""
from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from ..config import Station, get_settings, get_stations
from ..models import MetarObservation, Severity, Source
from ..notifiers.notifier_router import NotifierRouter
from ..parser.metar import parse_metar, to_observation
from ..parser.nws_timeseries import local_day_window
from ..sources import avwx, awc, checkwx, iastate, nws_synoptic, open_meteo, wunderground
from ..storage import load_state, save_observation, save_state
from .aggregator import AggEvent, Aggregator
from .cross_check import info_vs_resolve, neighbours
from .deduper import Deduper
from .forecast_engine import estimate_bucket_probabilities
from .market_discovery import fetch_event_or_raise
from .resolver import resolve


@dataclass
class LoopConfig:
    slug: str
    date_local: str
    station: Station
    grace_hours: int = 2
    nws_every: timedelta = timedelta(minutes=15)
    forecast_every: timedelta = timedelta(minutes=15)
    neighbour_every: timedelta = timedelta(minutes=10)


def _jittered_sleep_seconds() -> float:
    s = get_settings()
    return s.poll_interval_seconds + random.uniform(0, s.poll_jitter_seconds)


async def _fetch_metars(station: Station) -> list[tuple[str, Source]]:
    """Pull latest METAR(s) from all configured sources concurrently."""
    awc_task = awc.fetch_latest(station.icao)
    avwx_task = avwx.fetch_latest(station.icao)
    iastate_task = iastate.fetch_latest(station.icao)
    checkwx_task = checkwx.fetch_latest(station.icao)
    awc_r, avwx_r, ia_r, cw_r = await asyncio.gather(
        awc_task, avwx_task, iastate_task, checkwx_task, return_exceptions=True
    )
    raws: list[tuple[str, Source]] = []
    if isinstance(awc_r, list):
        raws.extend((r.raw, Source.AWC) for r in awc_r)
    if isinstance(avwx_r, str) and avwx_r:
        raws.append((avwx_r, Source.AVWX))
    if isinstance(ia_r, list):
        raws.extend((r, Source.IASTATE) for r in ia_r if r)
    if isinstance(cw_r, str) and cw_r:
        raws.append((cw_r, Source.CHECKWX))
    return raws


async def _fetch_neighbours(station: Station) -> list[float]:
    out: list[float] = []
    for icao in station.fallback_icao:
        try:
            results = await awc.fetch_latest(icao)
        except Exception:
            logger.exception("neighbours: awc fetch failed for {}", icao)
            continue
        for r in results[:1]:
            try:
                p = parse_metar(r.raw)
                out.append(p.temperature_c)
            except Exception:
                continue
    return out


async def monitor_loop(
    slug: str,
    *,
    date_local: str,
    notifier: NotifierRouter,
    station: Station | None = None,
    grace_hours: int = 2,
) -> None:
    s = get_settings()
    if station is None:
        cfg = get_stations()
        station = cfg.by_slug(slug) or cfg.by_key(s.default_city)
    if station is None:
        raise RuntimeError(f"No station mapping for slug {slug}")

    event = await fetch_event_or_raise(slug)
    tz = station.tz or s.resolution_timezone
    local_start, local_end = local_day_window(date_local, tz)
    end_window = local_end + timedelta(hours=grace_hours)

    agg = Aggregator(
        buckets=event.buckets, date_local=date_local, tz=tz, end_local=local_end, units=station.units
    )
    state = await load_state(slug)
    if state:
        agg.restore(state)

    deduper = Deduper()
    last_nws: datetime | None = None
    last_fc: datetime | None = None
    last_neigh: datetime | None = None
    nws_running_max: float | None = None

    logger.info(
        "poller: starting slug={} station={} window_local=[{}..{}] (tz={})",
        slug, station.icao, local_start, local_end, tz,
    )
    await notifier.send_info(f"🚀 Monitor started for <b>{slug}</b> (station {station.icao})")

    while datetime.now(UTC) <= end_window:
        try:
            raws = await _fetch_metars(station)
        except Exception:
            logger.exception("poller: metar fetch cycle failed")
            await asyncio.sleep(_jittered_sleep_seconds())
            continue

        events: list[AggEvent] = []
        for raw, src in raws:
            try:
                parsed = parse_metar(raw)
            except Exception:
                logger.debug("poller: bad METAR skipped: {!r}", raw[:80])
                continue
            obs: MetarObservation = to_observation(parsed, source=src)
            if not deduper.is_new(obs):
                continue
            await save_observation(obs)
            events.extend(agg.update(obs))

        # ---- Resolve-contour refresh ----
        if last_nws is None or (datetime.now(UTC) - last_nws) >= LoopConfig.nws_every:
            last_nws = datetime.now(UTC)
            try:
                if station.resolve_source == "wunderground":
                    parsed_synop = await wunderground.fetch_day(
                        station.synoptic_stid or station.icao, local_start, local_end
                    )
                else:
                    parsed_synop = await nws_synoptic.fetch_day(
                        station.synoptic_stid or station.icao, local_start, local_end
                    )
                nws_max = parsed_synop.t_max_in_window(local_start, local_end)
                if nws_max is not None:
                    nws_running_max = float(nws_max)
                    cr = info_vs_resolve(
                        agg.state.daily_max_info,
                        nws_running_max,
                        info_units="celsius",
                        resolve_units=station.units,
                        tolerance=0.6 if station.units == "celsius" else 1.1,
                    )
                    if cr is not None:
                        events.append(
                            AggEvent(kind="SourceDisagreement", severity=cr.severity, text=cr.text, payload=cr.payload)  # type: ignore[arg-type]
                        )
            except Exception:
                logger.exception("poller: resolve-contour refresh failed")

        # ---- Forecast / probabilities (logged, not always notified) ----
        if last_fc is None or (datetime.now(UTC) - last_fc) >= LoopConfig.forecast_every:
            last_fc = datetime.now(UTC)
            try:
                fc = await open_meteo.fetch_forecast(station.lat, station.lon, temperature_unit=station.units)
                probs = estimate_bucket_probabilities(
                    event.buckets,
                    fc,
                    running_max_c=agg.state.daily_max_info,
                    window_start=local_start,
                    window_end=local_end,
                    units=station.units,
                )
                # Persist into state for /buckets command
                cur_state = agg.serialise_state()
                cur_state["bucket_probabilities"] = [
                    {
                        "title": p.bucket.title,
                        "threshold": p.bucket.threshold,
                        "kind": p.bucket.kind,
                        "p_model": p.p_model,
                    }
                    for p in probs
                ]
                await save_state(slug, cur_state)
            except Exception:
                logger.exception("poller: forecast refresh failed")

        # ---- Neighbour cross-check ----
        if station.fallback_icao and (
            last_neigh is None or (datetime.now(UTC) - last_neigh) >= LoopConfig.neighbour_every
        ):
            last_neigh = datetime.now(UTC)
            try:
                ns = await _fetch_neighbours(station)
                cur = agg.state.last_temp_c
                if cur is not None:
                    primary_u = cur if station.units == "celsius" else cur * 9.0 / 5.0 + 32.0
                    ns_u = [n if station.units == "celsius" else n * 9.0 / 5.0 + 32.0 for n in ns]
                    cr = neighbours(primary_u, ns_u, units=station.units)
                    if cr is not None:
                        events.append(
                            AggEvent(kind="AnomalyDetected", severity=cr.severity, text=cr.text, payload=cr.payload)  # type: ignore[arg-type]
                        )
            except Exception:
                logger.exception("poller: neighbours cross-check failed")

        # ---- Stale data warning ----
        if (
            agg.state.last_issue_time is not None
            and (datetime.now(UTC) - agg.state.last_issue_time) > timedelta(minutes=45)
        ):
            events.append(
                AggEvent(
                    kind="MissingData",
                    severity=Severity.WARNING,
                    text=f"No new METAR for {station.icao} > 45 min",
                )
            )

        # ---- Persist + notify ----
        await save_state(slug, agg.serialise_state())
        for ev in events:
            await notifier.send_event(slug, ev, station=station, buckets=event.buckets)

        await asyncio.sleep(_jittered_sleep_seconds())

    # window closed → run resolver
    logger.info("poller: window closed, starting resolver for {}", slug)
    try:
        report = await resolve(
            slug=slug,
            event_id=event.event_id,
            station=station,
            date_local=date_local,
            buckets=event.buckets,
            info_t_max=agg.state.daily_max_info,
        )
        await notifier.send_resolution(report)
    except Exception:
        logger.exception("poller: resolver failed for {}", slug)


async def run_loops_for_known(notifier: NotifierRouter, slugs_dates: Iterable[tuple[str, str]]) -> None:
    """Convenience: run multiple monitor loops in parallel."""
    tasks = [monitor_loop(slug, date_local=date, notifier=notifier) for slug, date in slugs_dates]
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks, return_exceptions=True)


def now_payload() -> dict[str, Any]:
    return {"now": datetime.now(UTC).isoformat()}
