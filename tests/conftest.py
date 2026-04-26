"""Shared fixtures + isolation hooks."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

FIX_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Force tests to use a tmp DB, no proxies, no tokens."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    monkeypatch.setenv("PROXY_TELEGRAM", "")
    monkeypatch.setenv("PROXY_POLYMARKET", "")
    monkeypatch.setenv("PROXY_AVIATION", "")
    monkeypatch.setenv("PROXY_DEFAULT", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("SYNOPTIC_TOKEN", "")
    monkeypatch.setenv("AVWX_TOKEN", "")
    monkeypatch.setenv("CHECKWX_TOKEN", "")
    monkeypatch.setenv("PROMETHEUS_ENABLED", "false")
    # invalidate caches
    from src import config as cfg

    cfg.get_settings.cache_clear()
    cfg.get_stations.cache_clear()
    yield


@pytest.fixture()
def metar_fixtures() -> list[str]:
    p = FIX_DIR / "metar" / "uuww_corpus.txt"
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


@pytest.fixture()
def synoptic_fixture() -> dict:
    return json.loads((FIX_DIR / "nws" / "uuww_2025-04-26.json").read_text(encoding="utf-8"))
