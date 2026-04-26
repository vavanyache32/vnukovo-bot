"""Test proxy URL masking and per-host mount construction (no real I/O)."""
from __future__ import annotations

import pytest

from src.config import Settings
from src.http_client import build_mounts, mask_proxy


def test_mask_proxy_credentials() -> None:
    assert mask_proxy("socks5://user:pass@proxy:1080") == "socks5://***:***@proxy:1080"
    assert mask_proxy("http://u:p@srv:3128") == "http://***:***@srv:3128"
    assert mask_proxy("http://srv:3128") == "http://srv:3128"
    assert mask_proxy("") == ""


def test_build_mounts_routes_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_TELEGRAM", "http://u:p@example.com:3128")
    monkeypatch.setenv("PROXY_DEFAULT", "")
    monkeypatch.setenv("PROXY_AVIATION", "")
    monkeypatch.setenv("PROXY_POLYMARKET", "")
    s = Settings()
    mounts = build_mounts(s)
    assert "all://api.telegram.org" in mounts
    assert "all://" in mounts
