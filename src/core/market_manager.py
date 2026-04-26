"""Dynamic market manager: start/stop monitor loops on demand.

Replaces the static slug-pattern logic. The bot now monitors only markets
that the user has explicitly subscribed to (persisted in DB).
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from ..config import Station, get_settings, get_stations
from ..models import MarketEvent
from ..notifiers.notifier_router import NotifierRouter
from ..storage import load_subscriptions, save_subscription
from .market_discovery import MarketDiscovery, fetch_event_or_raise
from .poller import monitor_loop


class MarketManager:
    def __init__(self, notifier: NotifierRouter) -> None:
        self.notifier = notifier
        self._tasks: dict[str, asyncio.Task] = {}
        self._discovery = MarketDiscovery()
        self._running = False

    async def start(self) -> None:
        """Resume subscriptions from DB and start discovery loop."""
        self._running = True
        slugs = await load_subscriptions()
        if slugs:
            logger.info("market_manager: resuming {} subscribed markets", len(slugs))
            for slug in slugs:
                await self.start_market(slug)
        else:
            logger.info("market_manager: no subscriptions yet; waiting for /markets")
        # Background discovery every 30 min
        asyncio.create_task(self._discovery_loop(), name="discovery")

    async def stop(self) -> None:
        self._running = False
        for slug in list(self._tasks):
            await self.stop_market(slug)

    async def start_market(self, slug: str) -> MarketEvent | None:
        """Start monitoring a single market. Idempotent."""
        if slug in self._tasks and not self._tasks[slug].done():
            logger.debug("market_manager: {} already running", slug)
            return None
        # Fetch event metadata
        try:
            ev = await fetch_event_or_raise(slug)
        except Exception as e:
            logger.warning("market_manager: cannot fetch event for {}: {}", slug, e)
            return None
        # Persist subscription
        await save_subscription(slug)
        cfg = get_stations()
        station = cfg.by_slug(slug) or cfg.by_key(get_settings().default_city)
        if station is None:
            logger.error("market_manager: no station mapping for {}", slug)
            return None
        date_local = self._date_from_slug(slug) or datetime.now(UTC).strftime("%Y-%m-%d")
        task = asyncio.create_task(
            self._run_wrapped(slug, date_local, station),
            name=f"market:{slug}",
        )
        self._tasks[slug] = task
        logger.info("market_manager: started {}", slug)
        await self.notifier.send_info(f"▶️ Started monitoring <b>{slug}</b>")
        return ev

    async def stop_market(self, slug: str) -> None:
        task = self._tasks.pop(slug, None)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("market_manager: stopped {}", slug)

    async def list_active(self) -> list[str]:
        """Return slugs of currently running markets."""
        return [s for s, t in self._tasks.items() if not t.done()]

    async def _run_wrapped(self, slug: str, date_local: str, station: Station) -> None:
        try:
            await monitor_loop(
                slug,
                date_local=date_local,
                notifier=self.notifier,
                station=station,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("market_manager: loop crashed for {}", slug)

    async def _discovery_loop(self) -> None:
        """Optional: auto-discover new markets by pattern and notify."""
        while self._running:
            await asyncio.sleep(1800)  # 30 min
            if not self._running:
                break
            try:
                result = await self._discovery.run_once()
                for ev in result.new_events:
                    await self.notifier.send_info(
                        f"🔎 New market discovered: <b>{ev.slug}</b>\n"
                        f"Use /subscribe to start monitoring."
                    )
            except Exception:
                logger.exception("market_manager: discovery failed")

    @staticmethod
    def _date_from_slug(slug: str) -> str | None:
        import re

        m = re.search(r"on-(\w+)-(\d{1,2})-(\d{4})$", slug)
        if not m:
            return None
        month_word, day_s, year_s = m.groups()
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        }
        month = months.get(month_word.lower())
        if not month:
            return None
        return f"{int(year_s):04d}-{month:02d}-{int(day_s):02d}"
