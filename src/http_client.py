"""Single httpx.AsyncClient with per-host proxy mounts, retry & ETag support.

Critical for RU-hosted deployments where Telegram, Polymarket, NOAA may be filtered.
Proxy URLs are NEVER logged with credentials — see :func:`mask_proxy`.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

import httpx
from httpx import AsyncClient, AsyncHTTPTransport, Headers, Response
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import Settings, get_settings


def mask_proxy(url: str) -> str:
    """Strip credentials from a proxy URL for safe logging."""
    if not url:
        return ""
    return re.sub(r"://[^@/]+@", "://***:***@", url)


def _transport(proxy: str | None) -> AsyncHTTPTransport:
    if not proxy:
        return AsyncHTTPTransport(retries=0, http2=True)
    # httpx natively supports http(s):// proxies; for socks5:// httpx-socks plugs in.
    if proxy.startswith("socks"):
        try:
            from httpx_socks import AsyncProxyTransport  # type: ignore[import-untyped]

            return AsyncProxyTransport.from_url(proxy)  # type: ignore[no-any-return]
        except Exception:  # pragma: no cover
            logger.exception("httpx-socks not available for proxy {}", mask_proxy(proxy))
            raise
    return AsyncHTTPTransport(proxy=proxy, retries=0, http2=True)


def build_mounts(settings: Settings) -> dict[str, AsyncHTTPTransport]:
    mounts: dict[str, AsyncHTTPTransport] = {}
    if settings.proxy_telegram:
        mounts["all://api.telegram.org"] = _transport(settings.proxy_telegram)
    if settings.proxy_polymarket:
        mounts["all://*.polymarket.com"] = _transport(settings.proxy_polymarket)
        mounts["all://polymarket.com"] = _transport(settings.proxy_polymarket)
    if settings.proxy_aviation:
        mounts["all://aviationweather.gov"] = _transport(settings.proxy_aviation)
        mounts["all://avwx.rest"] = _transport(settings.proxy_aviation)
        mounts["all://api.synopticdata.com"] = _transport(settings.proxy_aviation)
        mounts["all://api.weather.gov"] = _transport(settings.proxy_aviation)
        mounts["all://api.checkwx.com"] = _transport(settings.proxy_aviation)
        mounts["all://mesonet.agron.iastate.edu"] = _transport(settings.proxy_aviation)
        mounts["all://www.ogimet.com"] = _transport(settings.proxy_aviation)
        mounts["all://api.open-meteo.com"] = _transport(settings.proxy_aviation)
    # final wildcard: per httpx docs, "all://" matches anything not matched above
    mounts["all://"] = _transport(settings.proxy_default or None)
    return mounts


_CLIENT: AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()
_ETAG_CACHE: dict[str, tuple[str | None, str | None]] = {}  # url -> (etag, last-modified)


async def get_client(settings: Settings | None = None) -> AsyncClient:
    """Return the singleton AsyncClient (lazily built)."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    async with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        s = settings or get_settings()
        mounts = build_mounts(s)
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
        limits = httpx.Limits(max_connections=64, max_keepalive_connections=16)
        _CLIENT = AsyncClient(
            mounts=mounts,
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            headers={"User-Agent": "vnukovo-bot/0.1 (+https://github.com/yourorg/vnukovo-bot)"},
            http2=True,
        )
        logger.info(
            "http_client: mounts={}",
            {k: type(v).__name__ for k, v in mounts.items()},
        )
        return _CLIENT


async def close_client() -> None:
    global _CLIENT
    if _CLIENT is not None:
        await _CLIENT.aclose()
        _CLIENT = None


_RETRYABLE = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ConnectTimeout,
)


async def request(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    json: Any | None = None,
    use_etag: bool = False,
    timeout_s: float | None = None,
    expect_json: bool = False,
    max_attempts: int = 3,
) -> Response | None:
    """Resilient HTTP request with retry/backoff and optional ETag handling.

    Returns None when ETag/If-Modified-Since indicates 304 Not Modified.
    """
    client = await get_client()
    req_headers: dict[str, str] = dict(headers or {})
    if use_etag and url in _ETAG_CACHE:
        etag, lm = _ETAG_CACHE[url]
        if etag:
            req_headers["If-None-Match"] = etag
        if lm:
            req_headers["If-Modified-Since"] = lm
    if expect_json:
        req_headers.setdefault("Accept", "application/json")
    timeout = httpx.Timeout(timeout_s) if timeout_s else None

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=0.5, max=8.0),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    ):
        with attempt:
            resp = await client.request(
                method, url, params=params, headers=req_headers, json=json, timeout=timeout
            )
    if use_etag and resp.status_code == 304:
        return None
    if use_etag and resp.status_code == 200:
        h: Headers = resp.headers
        _ETAG_CACHE[url] = (h.get("etag"), h.get("last-modified"))
    return resp
