"""Top-level CLI: monitor / resolve / replay / backtest / discover / proxy-check."""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import click
from loguru import logger

from .config import get_settings, get_stations
from .core.market_discovery import fetch_event_or_raise
from .core.market_manager import MarketManager
from .core.poller import monitor_loop
from .core.resolver import resolve as run_resolve
from .core.resolver import write_json_report
from .http_client import close_client, get_client, mask_proxy
from .notifiers.notifier_router import NotifierRouter
from .notifiers.telegram_bot import TelegramBot
from .ops import sentry
from .ops.health import run_in_background as start_health
from .storage import init_db


def _setup_logging() -> None:
    s = get_settings()
    logger.remove()
    if s.log_json:
        logger.add(sys.stderr, level=s.log_level, serialize=True, backtrace=False, diagnose=False)
    else:
        logger.add(sys.stderr, level=s.log_level)


@click.group()
def main() -> None:
    """vnukovo-bot — Polymarket weather-market bot."""
    _setup_logging()
    sentry.init()


# ---------------- monitor ----------------
@main.command()
@click.option("--slug", default=None, help="Polymarket event slug (auto-detect if omitted)")
@click.option("--date", "date_local", default=None, help="Local date YYYY-MM-DD")
@click.option("--no-telegram", is_flag=True, help="Disable Telegram notifier")
def monitor(slug: str | None, date_local: str | None, no_telegram: bool) -> None:
    """Run the live monitoring loop for one or more markets."""
    asyncio.run(_run_monitor(slug=slug, date_local=date_local, no_telegram=no_telegram))


async def _run_monitor(*, slug: str | None, date_local: str | None, no_telegram: bool) -> None:
    await init_db()
    s = get_settings()

    telegram_bot: TelegramBot | None = None
    manager = MarketManager(NotifierRouter())
    if not no_telegram and s.telegram_bot_token:
        telegram_bot = TelegramBot(market_manager=manager)
        manager.notifier = NotifierRouter(telegram=telegram_bot)
    else:
        manager.notifier = NotifierRouter()

    health_task = start_health() if s.prometheus_enabled else None
    tg_task: asyncio.Task | None = None
    if telegram_bot is not None:
        tg_task = asyncio.create_task(telegram_bot.run_polling(), name="telegram")

    # Start manager (restores DB subscriptions or starts explicit slug)
    if slug is not None:
        await manager.start_market(slug)
    await manager.start()

    # Run forever until Ctrl+C
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass

    logger.info("monitor: shutting down")
    await manager.stop()
    if tg_task is not None:
        tg_task.cancel()
    if health_task is not None:
        health_task.cancel()
    await close_client()


def _date_from_slug(slug: str) -> str | None:
    """Best-effort: parse 'highest-temperature-in-<city>-on-<month>-<d>-<yyyy>' tail."""
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


# ---------------- resolve ----------------
@main.command()
@click.option("--date", "date_local", required=True)
@click.option("--slug", required=True)
@click.option("--out", "out_path", default=None, help="Write JSON report to this path")
def resolve(date_local: str, slug: str, out_path: str | None) -> None:
    """Run the resolver for a (date, slug) pair (waits up to 48 h for finalisation)."""
    asyncio.run(_run_resolve(date_local=date_local, slug=slug, out_path=out_path))


async def _run_resolve(*, date_local: str, slug: str, out_path: str | None) -> None:
    await init_db()
    cfg = get_stations()
    station = cfg.by_slug(slug)
    if station is None:
        raise click.ClickException(f"No station mapping for slug {slug}")
    ev = await fetch_event_or_raise(slug)
    report = await run_resolve(
        slug=slug, event_id=ev.event_id, station=station,
        date_local=date_local, buckets=ev.buckets,
    )
    out = Path(out_path or f"data/raw/{date_local}/resolution_{slug}.json")
    p = write_json_report(report, out)
    click.echo(f"Wrote report → {p}")
    await close_client()


# ---------------- replay / backtest ----------------
@main.command()
@click.option("--date", "date_local", required=True)
@click.option("--slug", required=True)
@click.option("--speed", default=60, help="Replay speed multiplier")
def replay(date_local: str, slug: str, speed: int) -> None:
    """Replay a historical day from archived data (no real notifications sent)."""
    from .core.replay import run_replay

    asyncio.run(run_replay(date_local=date_local, slug=slug, speed=speed))


@main.command()
@click.option("--from", "date_from", required=True)
@click.option("--to", "date_to", required=True)
def backtest(date_from: str, date_to: str) -> None:
    """Run backtest over a range of historical days."""
    from .core.replay import run_backtest

    asyncio.run(run_backtest(date_from=date_from, date_to=date_to))


# ---------------- discover ----------------
@main.command()
def discover() -> None:
    """Run market discovery once and print results."""

    async def _run() -> None:
        await init_db()
        result = await MarketDiscovery().run_once()
        for ev in result.new_events + result.known_events:
            click.echo(f"{ev.slug}  buckets={len(ev.buckets)}  end={ev.end_date.isoformat()}")
        await close_client()

    asyncio.run(_run())


# ---------------- proxy-check ----------------
@main.command("proxy-check")
def proxy_check() -> None:
    """Test all configured proxies (RU-host pre-deploy sanity check)."""
    asyncio.run(_run_proxy_check())


async def _run_proxy_check() -> None:
    s = get_settings()
    awc_url = "https://aviationweather.gov/api/data/metar?ids=UUWW&format=json&hours=1"
    om_url = "https://api.open-meteo.com/v1/forecast?latitude=55&longitude=37&hourly=temperature_2m"
    targets: list[tuple[str, str, str]] = [
        ("Telegram", "https://api.telegram.org/bot/getMe", s.proxy_telegram),
        ("Polymarket Gamma", "https://gamma-api.polymarket.com/events?limit=1", s.proxy_polymarket),
        ("Polymarket CLOB", "https://clob.polymarket.com/markets", s.proxy_polymarket),
        ("AviationWeather", awc_url, s.proxy_aviation),
        ("AVWX", "https://avwx.rest", s.proxy_aviation),
        ("Synoptic", "https://api.synopticdata.com/v2/", s.proxy_aviation),
        ("Open-Meteo", om_url, s.proxy_aviation),
    ]
    client = await get_client()
    width = max(len(name) for name, _, _ in targets)
    click.echo(f"{'host'.ljust(width)}  {'proxy':35s}  status   latency")
    click.echo("-" * (width + 60))
    for name, url, proxy in targets:
        t0 = time.monotonic()
        ok = False
        status = "?"
        try:
            r = await client.get(url, timeout=15)
            status = str(r.status_code)
            ok = r.status_code < 500
        except Exception as e:
            status = type(e).__name__
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        click.echo(
            f"{name.ljust(width)}  {mask_proxy(proxy or '-'):35s}  "
            f"{'✓' if ok else '✗'} {status:6s}  {elapsed_ms} ms"
        )
    await close_client()


if __name__ == "__main__":
    main()
