"""Alpaca sidecar Prometheus metrics."""

from __future__ import annotations

from prometheus_client import Counter, Gauge

ALPACA_SIDECAR_UPTIME_SECONDS = Gauge(
    "alpaca_sidecar_uptime_seconds",
    "Alpaca sidecar process start time.",
    labelnames=["mode"],
)

ALPACA_QUOTE_TICKS_TOTAL = Counter(
    "alpaca_quote_ticks_total",
    "Alpaca quote ticks received.",
    labelnames=["endpoint", "mode"],
)

ALPACA_WS_RECONNECT_TOTAL = Counter(
    "alpaca_ws_reconnect_total",
    "Alpaca websocket reconnect count.",
    labelnames=["endpoint", "reason"],
)

ALPACA_SUBSCRIPTION_ACTIVE = Gauge(
    "alpaca_subscription_active",
    "Active Alpaca upstream subscriptions.",
    labelnames=["endpoint", "mode"],
)

ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL = Counter(
    "alpaca_upstream_subscribe_rejected_total",
    "Alpaca upstream subscription rejections.",
    labelnames=["endpoint", "reason"],
)

ALPACA_HTTP_REQUESTS_TOTAL = Counter(
    "alpaca_http_requests_total",
    "Alpaca HTTP request count.",
    labelnames=["endpoint", "status"],
)

ALPACA_HTTP_RATE_LIMIT_WINDOW_SECONDS = Gauge(
    "alpaca_http_rate_limit_window_seconds",
    "Alpaca HTTP rate limit window seconds.",
    labelnames=[],
)

ALPACA_HTTP_RATE_LIMIT_REMAINING = Gauge(
    "alpaca_http_rate_limit_remaining",
    "Alpaca HTTP rate limit remaining requests.",
    labelnames=[],
)

ALPACA_ACCOUNT_READ_FAILURES_TOTAL = Counter(
    "alpaca_account_read_failures_total",
    "Alpaca account read failures.",
    labelnames=["kind"],
)

ALPACA_ENDPOINT_ISOLATION_VIOLATIONS_TOTAL = Counter(
    "alpaca_endpoint_isolation_violations_total",
    "Alpaca live/paper endpoint isolation violations.",
    labelnames=[],
)
