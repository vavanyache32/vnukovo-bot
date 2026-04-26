"""Polymarket CLOB read-only price snapshots.

We only need *prices*, never trade. Endpoints:

* GET https://clob.polymarket.com/price?token_id=...&side=BUY|SELL
* GET https://clob.polymarket.com/midpoint?token_id=...

The CLOB API is rate-limited and IP-fenced; route through PROXY_POLYMARKET.
"""
from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from ..http_client import request
from ..models import Bucket, BucketPrice

CLOB_BASE = "https://clob.polymarket.com"


async def fetch_midpoint(token_id: str) -> float | None:
    resp = await request("GET", f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
    if resp is None or resp.status_code != 200:
        return None
    try:
        return float((resp.json() or {}).get("mid"))
    except (TypeError, ValueError):
        return None


async def fetch_prices_for_buckets(buckets: list[Bucket]) -> dict[str, BucketPrice]:
    out: dict[str, BucketPrice] = {}
    now = datetime.now(UTC)
    for b in buckets:
        if not b.outcome_yes_token_id:
            continue
        try:
            mid = await fetch_midpoint(b.outcome_yes_token_id)
        except Exception:
            logger.exception("clob: midpoint failed for bucket {}", b.title)
            mid = None
        out[b.market_id] = BucketPrice(market_id=b.market_id, yes_price=mid, fetched_at=now)
    return out
