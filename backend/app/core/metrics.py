"""prometheus-client counters/gauges for Phase 2 observability."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

registry = REGISTRY


cf_jwt_verification_total = Counter(
    "cf_jwt_verification_total",
    "CF Access JWT verification outcomes",
    labelnames=["result"],
    registry=registry,
)

config_ops_total = Counter(
    "config_ops_total",
    "Config/secret operations",
    labelnames=["op", "kind", "result"],
    registry=registry,
)

config_cache_size = Gauge(
    "config_cache_size",
    "Entries currently in the per-worker cache",
    labelnames=["kind"],
    registry=registry,
)

redis_publish_fail_total = Counter(
    "redis_publish_fail_total",
    "Redis publish errors during config invalidation",
    labelnames=["channel"],
    registry=registry,
)

redis_subscribe_reconnect_total = Counter(
    "redis_subscribe_reconnect_total",
    "Redis subscribe reconnect attempts",
    labelnames=["channel"],
    registry=registry,
)

fernet_prev_key_hits_total = Counter(
    "fernet_prev_key_hits_total",
    "Reveals decrypted via the PREV Fernet key (rotation indicator)",
    registry=registry,
)

admin_secret_reveal_total = Counter(
    "admin_secret_reveal_total",
    "Plaintext reveal operations on /api/admin/secrets/*/reveal",
    labelnames=["actor_kind"],
    registry=registry,
)

avg_cost_unit_suspected_wrong_total = Counter(
    "avg_cost_unit_suspected_wrong_total",
    "Positions where avg_cost appears to be in wrong currency unit (GBX vs GBP)",
    labelnames=["account_id"],
    registry=registry,
)

broker_discover_nlv_update_duration_ms = Histogram(
    "broker_discover_nlv_update_duration_ms",
    "Time to UPDATE all per-account NLV rows in one discover tick (ms).",
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
    registry=registry,
)

broker_discover_nlv_overflow_total = Counter(
    "broker_discover_nlv_overflow_total",
    "Number of NUMERIC(20,8) overflow events on per-account NLV UPDATE.",
    registry=registry,
)

broker_discover_positions_update_duration_ms = Histogram(
    "broker_discover_positions_update_duration_ms",
    "BrokerDiscoverer per-tick GetPositions fan-out + DB upsert duration in ms",
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
    registry=registry,
)

broker_discover_positions_overflow_total = Counter(
    "broker_discover_positions_overflow_total",
    "Per-account NUMERIC(20,8) overflow rejections during positions upsert",
    labelnames=("label",),
    registry=registry,
)

broker_order_events_received_total = Counter(
    "broker_order_events_received_total",
    "Broker order stream events received by the backend consumer.",
    labelnames=["label"],
    registry=registry,
)

broker_order_events_dropped_total = Counter(
    "broker_order_events_dropped_total",
    "Broker order stream events dropped by the backend consumer.",
    labelnames=["label", "reason"],
    registry=registry,
)

broker_order_event_lag_ms = Histogram(
    "broker_order_event_lag_ms",
    "Lag between broker_event_at and backend observation time in milliseconds.",
    labelnames=["label"],
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 15000, 30000, 60000),
    registry=registry,
)

broker_order_stream_reconnects_total = Counter(
    "broker_order_stream_reconnects_total",
    "Broker order stream reconnect attempts by backend consumer.",
    labelnames=["label"],
    registry=registry,
)

consumer_alive = Gauge(
    "consumer_alive",
    "Whether a per-account broker order event consumer task is alive.",
    labelnames=["label", "account_id"],
    registry=registry,
)

broker_order_stream_resync_synthetic_events_total = Counter(
    "broker_order_stream_resync_synthetic_events_total",
    "Synthetic OrderEventMessages emitted during reconnect resync from snapshot.",
    labelnames=["label"],
    registry=registry,
)

sse_active_connections = Gauge(
    "sse_active_connections",
    "Number of currently active SSE /events connections",
    registry=registry,
)

sse_dropped_clients_total = Counter(
    "sse_dropped_clients_total",
    "Total SSE connections dropped due to slow client (queue overflow)",
    registry=registry,
)

broker_order_pending_submit_recovered_total = Counter(
    "broker_order_pending_submit_recovered_total",
    "Orders recovered from pending_submit state by the watchdog (broker match found).",
    labelnames=["label"],
    registry=registry,
)

broker_order_pending_submit_orphan_total = Counter(
    "broker_order_pending_submit_orphan_total",
    "Orders escalated to rejected by watchdog after 5 min with no broker match.",
    labelnames=["label"],
    registry=registry,
)

pending_fills_backlog_count = Gauge(
    "pending_fills_backlog_count",
    "Count of pending_fills rows older than 5 minutes (BrokerPendingFillsBacklog alert).",
    registry=registry,
)

commission_buffer_overflow_total = Counter(
    "commission_buffer_overflow_total",
    "Times the in-memory commission buffer exceeded 1000 entries.",
    registry=registry,
)
