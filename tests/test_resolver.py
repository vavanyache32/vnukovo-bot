"""Resolver flow tests with mocked Synoptic source."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.config import Station
from src.core.resolver import resolve as run_resolve
from src.models import Bucket
from src.parser.nws_timeseries import parse_synoptic_timeseries
from src.storage import init_db


def _buckets() -> list[Bucket]:
    return [
        Bucket(market_id=f"b{i}", title=f"{i}°C", threshold=i, kind="exact")
        for i in range(7, 19)
    ] + [
        Bucket(market_id="lt", title="6°C or below", threshold=6, kind="lower_tail"),
        Bucket(market_id="ut", title="19°C or higher", threshold=19, kind="upper_tail"),
    ]


@pytest.mark.asyncio
async def test_resolver_returns_winning_bucket(synoptic_fixture: dict) -> None:
    await init_db()
    parsed = parse_synoptic_timeseries(synoptic_fixture)

    async def fake_fetch(stid: str, start: datetime, end: datetime, **_):  # type: ignore[no-untyped-def]
        return parsed

    station = Station(
        key="moscow", icao="UUWW", name="Vnukovo", lat=55.59, lon=37.26,
        tz="Europe/Moscow", resolve_source="synoptic", synoptic_stid="UUWW",
        slug_pattern="highest-temperature-in-moscow-on-*",
    )
    with patch("src.core.resolver.nws_synoptic.fetch_day", side_effect=fake_fetch):
        report = await run_resolve(
            slug="highest-temperature-in-moscow-on-april-26-2025",
            event_id="evt-1",
            station=station,
            date_local="2025-04-26",
            buckets=_buckets(),
            max_wait=timedelta(seconds=1),
        )
    assert report.t_max_resolve_whole_c == 18
    assert report.winning_bucket_title == "18°C"
    assert report.finalized is True
    assert report.revisions_locked is True


@pytest.mark.asyncio
async def test_resolver_locks_after_publication(synoptic_fixture: dict) -> None:
    await init_db()
    parsed = parse_synoptic_timeseries(synoptic_fixture)

    async def fake_fetch(stid: str, start: datetime, end: datetime, **_):  # type: ignore[no-untyped-def]
        return parsed

    station = Station(
        key="moscow", icao="UUWW", name="Vnukovo", lat=55.59, lon=37.26,
        tz="Europe/Moscow", resolve_source="synoptic", synoptic_stid="UUWW",
        slug_pattern="highest-temperature-in-moscow-on-*",
    )
    with patch("src.core.resolver.nws_synoptic.fetch_day", side_effect=fake_fetch):
        first = await run_resolve(
            slug="highest-temperature-in-moscow-on-april-26-2025",
            event_id="evt-1",
            station=station,
            date_local="2025-04-26",
            buckets=_buckets(),
            max_wait=timedelta(seconds=1),
        )
    # Tamper with the fixture (simulating a NOAA post-finalisation revision)
    synoptic_fixture["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][20] = 25.0
    parsed2 = parse_synoptic_timeseries(synoptic_fixture)

    async def fake_fetch_2(stid: str, start: datetime, end: datetime, **_):  # type: ignore[no-untyped-def]
        return parsed2

    with patch("src.core.resolver.nws_synoptic.fetch_day", side_effect=fake_fetch_2):
        second = await run_resolve(
            slug="highest-temperature-in-moscow-on-april-26-2025",
            event_id="evt-1",
            station=station,
            date_local="2025-04-26",
            buckets=_buckets(),
            max_wait=timedelta(seconds=1),
        )
    # The in-memory ResolutionReport reflects the new pull, but storage layer
    # will have refused to overwrite — so the *persisted* report is unchanged.
    from sqlalchemy import select

    from src.storage import get_db
    from src.storage.db import ResolutionRow

    db = get_db()
    async with db.session() as s:
        row = (await s.execute(select(ResolutionRow).where(ResolutionRow.slug == first.slug))).scalar_one()
    assert row.payload["t_max_resolve_whole_c"] == 18
    assert second.t_max_resolve_whole_c >= 18  # the live computation is consistent
