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

SCHWAB_PLACE_ORDER_DURATION_MS = Histogram(
    "schwab_place_order_duration_ms",
    "Schwab PlaceOrder REST call duration in milliseconds.",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
)

SCHWAB_CANCEL_ORDER_DURATION_MS = Histogram(
    "schwab_cancel_order_duration_ms",
    "Schwab CancelOrder REST call duration in milliseconds.",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
)

SCHWAB_MODIFY_ORDER_DURATION_MS = Histogram(
    "schwab_modify_order_duration_ms",
    "Schwab ModifyOrder REST call duration in milliseconds.",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
)

SCHWAB_ORDER_POLLER_ITERATIONS_TOTAL = Counter(
    "schwab_order_poller_iterations_total",
    "Total number of OrderPoller tick iterations.",
    ["gateway_label", "account_id", "cadence"],
)

SCHWAB_ORDER_POLLER_CADENCE_CHANGED_TOTAL = Counter(
    "schwab_order_poller_cadence_changed_total",
    "Number of cadence transitions (idle <-> fast).",
    ["gateway_label", "account_id", "from_cadence", "to_cadence"],
)

SCHWAB_ORDER_EVENT_EMITTED_TOTAL = Counter(
    "schwab_order_event_emitted_total",
    "Wire events emitted to fan-out subscribers.",
    ["kind"],
)

SCHWAB_FANOUT_SUBSCRIBER_DROPPED_TOTAL = Counter(
    "schwab_fanout_subscriber_dropped_total",
    "Fan-out subscribers dropped due to bounded-queue overflow.",
)
