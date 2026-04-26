"""Polymarket Gamma API — events, slugs, bucket markets (negRisk-aware)."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from ..http_client import request
from ..models import Bucket, MarketEvent

GAMMA_BASE = "https://gamma-api.polymarket.com"
EVENTS_URL = f"{GAMMA_BASE}/events"

# Bucket title parsers — support both °C and °F, including ranges like "50-51°F"
_LOWER = re.compile(r"(-?\d+)\s*°?\s*[CF]\s*or\s*below", re.IGNORECASE)
_UPPER = re.compile(r"(-?\d+)\s*°?\s*[CF]\s*or\s*higher", re.IGNORECASE)
_EXACT = re.compile(r"^\s*(-?\d+)\s*°?\s*[CF]\s*$", re.IGNORECASE)
_RANGE = re.compile(r"(-?\d+)\s*-\s*(-?\d+)\s*°?\s*[CF]", re.IGNORECASE)
_RANGE_BETWEEN = re.compile(r"between\s+(-?\d+)\s*-\s*(-?\d+)\s*°?\s*[CF]", re.IGNORECASE)


def _detect_units(title: str) -> str:
    if re.search(r"°\s*F\b|\bFahrenheit\b", title, re.IGNORECASE):
        return "fahrenheit"
    return "celsius"


def _classify_bucket(title: str, threshold_hint: int | None = None) -> tuple[int, str, str, int | None]:
    """Return (threshold, kind, units, threshold_high)."""
    units = _detect_units(title)

    # Range patterns: "50-51°F" or "between 50-51°F"
    if (m := _RANGE_BETWEEN.search(title)):
        return int(m.group(1)), "exact", units, int(m.group(2))
    if (m := _RANGE.search(title)):
        return int(m.group(1)), "exact", units, int(m.group(2))

    if (m := _LOWER.search(title)):
        return int(m.group(1)), "lower_tail", units, None
    if (m := _UPPER.search(title)):
        return int(m.group(1)), "upper_tail", units, None
    if (m := _EXACT.match(title)):
        return int(m.group(1)), "exact", units, None

    # Fallback: groupItemThreshold is sometimes an index (0,1,2...) for negRisk groups.
    # Only trust it if it matches the first literal number in the title.
    first_num = re.search(r"(-?\d+)", title)
    if threshold_hint is not None and first_num is not None and int(threshold_hint) == int(first_num.group(1)):
        return int(threshold_hint), "exact", units, None

    raise ValueError(f"Cannot classify bucket title: {title!r}")


async def fetch_event_by_slug(slug: str) -> MarketEvent | None:
    resp = await request("GET", EVENTS_URL, params={"slug": slug}, expect_json=True)
    if resp is None or resp.status_code != 200:
        return None
    items = resp.json() or []
    if not isinstance(items, list) or not items:
        return None
    return _build_event(items[0])


async def search_events(slug_pattern: str, *, limit: int = 100) -> list[MarketEvent]:
    """Best-effort search by slug pattern. Gamma supports `slug` exact-match
    and a few filter knobs; we use ``q`` (free-text) as a fallback.
    """
    q = slug_pattern.replace("*", "").replace("-", " ").strip()
    resp = await request(
        "GET",
        EVENTS_URL,
        params={"limit": limit, "active": "true", "closed": "false", "q": q},
        expect_json=True,
    )
    if resp is None or resp.status_code != 200:
        return []
    items = resp.json() or []
    re_pattern = re.compile("^" + re.escape(slug_pattern).replace("\\*", ".*") + "$")
    out: list[MarketEvent] = []
    for it in items:
        if re_pattern.match(it.get("slug", "")):
            try:
                out.append(_build_event(it))
            except Exception:
                logger.exception("gamma: failed to build event for slug={}", it.get("slug"))
    return out


def _build_event(payload: dict[str, Any]) -> MarketEvent:
    end_iso = payload.get("endDate") or payload.get("end_date") or ""
    try:
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00")) if end_iso else datetime.now(UTC)
    except ValueError:
        end_dt = datetime.now(UTC)
    buckets: list[Bucket] = []
    for m in payload.get("markets", []) or []:
        title = m.get("groupItemTitle") or m.get("question") or ""
        thr = m.get("groupItemThreshold")
        try:
            threshold, kind, units, threshold_high = _classify_bucket(title, thr)
        except ValueError:
            logger.warning("gamma: skipping bucket {!r}", title)
            continue
        tokens = m.get("clobTokenIds") or []
        if isinstance(tokens, str):
            try:
                import json as _json

                tokens = _json.loads(tokens)
            except Exception:
                tokens = []
        yes_id = tokens[0] if len(tokens) > 0 else None
        no_id = tokens[1] if len(tokens) > 1 else None
        buckets.append(
            Bucket(
                market_id=str(m.get("id") or m.get("marketId") or m.get("conditionId") or ""),
                title=title,
                threshold=threshold,
                threshold_high=threshold_high,
                kind=kind,
                units=units,
                outcome_yes_token_id=yes_id,
                outcome_no_token_id=no_id,
            )
        )
    buckets.sort(key=lambda b: (b.threshold, {"lower_tail": 0, "exact": 1, "upper_tail": 2}[b.kind]))
    return MarketEvent(
        event_id=str(payload.get("id") or payload.get("event_id") or payload.get("slug")),
        slug=payload.get("slug", ""),
        title=payload.get("title") or payload.get("question") or "",
        end_date=end_dt,
        neg_risk_market_id=payload.get("negRiskMarketID"),
        buckets=buckets,
    )
