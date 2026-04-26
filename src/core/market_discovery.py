"""Hourly Polymarket discovery loop.

For each configured slug pattern, fetch active events, register new ones,
deregister those that have produced a finalised resolution.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ..config import Settings, get_settings
from ..models import MarketEvent
from ..sources import polymarket_gamma
from ..storage import save_event


@dataclass
class DiscoveryResult:
    new_events: list[MarketEvent]
    known_events: list[MarketEvent]


class MarketDiscovery:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self._known: dict[str, MarketEvent] = {}

    async def run_once(self, *, station_patterns: list[str] | None = None) -> DiscoveryResult:
        patterns = station_patterns or self.s.slug_patterns
        if not patterns:
            return DiscoveryResult([], list(self._known.values()))
        results: list[list[MarketEvent]] = await asyncio.gather(
            *(polymarket_gamma.search_events(p) for p in patterns), return_exceptions=False
        )
        new: list[MarketEvent] = []
        for batch in results:
            for ev in batch:
                if ev.slug not in self._known:
                    self._known[ev.slug] = ev
                    new.append(ev)
                    await save_event(ev.event_id, ev.slug, ev.model_dump(mode="json"))
                    logger.info("discovery: new market {} ({} buckets)", ev.slug, len(ev.buckets))
                else:
                    self._known[ev.slug] = ev  # refresh
        return DiscoveryResult(new_events=new, known_events=list(self._known.values()))

    def known(self) -> dict[str, MarketEvent]:
        return dict(self._known)

    def forget(self, slug: str) -> None:
        self._known.pop(slug, None)


async def fetch_event_or_raise(slug: str) -> MarketEvent:
    ev = await polymarket_gamma.fetch_event_by_slug(slug)
    if ev is None:
        raise RuntimeError(f"Polymarket event not found: {slug}")
    return ev


def event_summary(ev: MarketEvent) -> dict[str, Any]:
    return {
        "slug": ev.slug,
        "title": ev.title,
        "buckets": [b.title for b in ev.buckets],
        "neg_risk_id": ev.neg_risk_market_id,
        "end_date": ev.end_date.isoformat(),
    }
