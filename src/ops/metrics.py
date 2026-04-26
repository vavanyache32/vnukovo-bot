"""Prometheus metrics. Imported only by ops.health to avoid global state in tests."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

POLL_LATENCY = Histogram(
    "poll_latency_seconds",
    "Latency of source polls",
    labelnames=("source",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
SOURCE_OK = Counter("source_success_total", "Successful source pulls", ("source",))
SOURCE_ERR = Counter("source_failure_total", "Failed source pulls", ("source",))
TIME_SINCE_LAST_METAR = Gauge("time_since_last_metar_seconds", "Seconds since last fresh METAR")
TIME_SINCE_LAST_NWS = Gauge("time_since_last_nws_seconds", "Seconds since last NWS pull")
INFO_VS_RESOLVE = Gauge(
    "info_vs_resolve_disagreement_celsius", "Difference between info and resolve contour"
)
NOTIFICATIONS_SENT = Counter("notifications_sent_total", "Notifications sent", ("severity",))
PROXY_HEALTH = Gauge("proxy_health", "Proxy health, 1=ok 0=failing", ("name",))
TELEGRAM_LATENCY = Histogram("telegram_send_latency_seconds", "Telegram send latency")
FORECAST_EDGE = Gauge("forecast_edge", "Model edge per bucket", ("bucket",))
