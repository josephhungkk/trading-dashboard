"""Sidecar-local Prometheus counters."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

SCHWAB_NORMALIZE_UNKNOWN_TOTAL = Counter(
    "broker_normalize_unknown_total",
    "Schwab JSON normalize unknown enum encounters.",
    ["field", "value"],
)

SCHWAB_HTTP_REQUESTS_TOTAL = Counter(
    "schwab_http_requests_total",
    "Schwab REST request count by endpoint + status.",
    ["endpoint", "status"],
)

SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL = Counter(
    "schwab_account_hash_refresh_total",
    "account_hash cache refreshes by reason.",
    ["reason"],
)

SCHWAB_ACCESS_TOKEN_AGE_SECONDS = Gauge(
    "schwab_access_token_age_seconds",
    "Age of the current access_token.",
)

SCHWAB_STREAMER_TICKS_TOTAL = Counter(
    "schwab_streamer_ticks_total",
    "Schwab streamer ticks received by raw upstream symbol.",
    ["symbol"],
)

SCHWAB_STREAMER_RECONNECT_TOTAL = Counter(
    "schwab_streamer_reconnect_total",
    "Schwab streamer reconnects by reason.",
    ["reason"],
)

SCHWAB_STREAMER_TOKEN_ROTATION_GAP_SECONDS = Histogram(
    "schwab_streamer_token_rotation_gap_seconds",
    "Seconds between token rotation signal and websocket close.",
)
