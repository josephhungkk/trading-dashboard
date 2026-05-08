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

config_cache_payload_decode_errors = Counter(
    "config_cache_payload_decode_errors_total",
    "Unparseable invalidation payloads received on a config cache pub/sub channel",
    labelnames=["channel"],
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

commission_db_errors_total = Counter(
    "commission_db_errors_total",
    "DB errors while applying commissionReport events to fills.",
    registry=registry,
)

broker_bracket_cancel_cascade_seconds = Histogram(
    "broker_bracket_cancel_cascade_seconds",
    "Latency from parent.cancel_requested_at to child cancelled-event for OCA cascade.",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=registry,
)

broker_order_modify_duration_ms = Histogram(
    "broker_order_modify_duration_ms",
    "Time to process PUT /api/orders/{id} from request entry to response (ms).",
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
    registry=registry,
)

broker_fills_write_failed_total = Counter(
    "broker_fills_write_failed_total",
    "Times the consumer's fills INSERT raised an unexpected DB error.",
    labelnames=("reason",),
    registry=registry,
)

broker_registry_label_mismatch_total = Counter(
    "broker_registry_label_mismatch_total",
    "Health.broker_id from sidecar disagreed with SIDECAR_BROKERS map.",
    labelnames=["label"],
    registry=registry,
)

broker_normalize_unknown_total = Counter(
    "broker_normalize_unknown_total",
    "Sidecar normalize layer received an unknown enum value from broker SDK.",
    labelnames=["label", "field"],
    registry=registry,
)

order_capability_check_total = Counter(
    "order_capability_check_total",
    "Capability check outcomes",
    labelnames=["broker", "result"],
    registry=registry,
)

order_capability_cache_hits_total = Counter(
    "order_capability_cache_hits_total",
    "Capability LRU cache hits",
    labelnames=["broker"],
    registry=registry,
)

order_capability_cache_misses_total = Counter(
    "order_capability_cache_misses_total",
    "Capability LRU cache misses",
    labelnames=["broker"],
    registry=registry,
)

order_capability_legacy_3tuple_calls_total = Counter(
    "order_capability_legacy_3tuple_calls_total",
    "Deprecated 3-tuple order capability checks.",
    labelnames=["broker_id"],
    registry=registry,
)

order_capability_cache_evictions_total = Counter(
    "order_capability_cache_evictions_total",
    "LRU evictions from the broker order capability cache.",
    labelnames=["broker_id"],
    registry=registry,
)

order_capability_pubsub_invalidations_total = Counter(
    "order_capability_pubsub_invalidations_total",
    "Capability cache invalidations triggered by Redis pubsub",
    registry=registry,
)

order_capability_pubsub_failures_total = Counter(
    "order_capability_pubsub_failures_total",
    "Redis pubsub publish failures (MED-5: silent cache-inconsistency canary)",
    registry=registry,
)

order_capability_admin_writes_total = Counter(
    "order_capability_admin_writes_total",
    "Admin writes to broker_order_capability",
    registry=registry,
)


# ──────────────────────── Phase 7a Schwab metrics ───────────────────────────
# Per spec §8.1 — see docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md

# v3 — net-new (Phase 6 never registered this despite spec §8.1 saying "extends").
BROKER_CONFIGURE_TOTAL = Counter(
    "broker_configure_total",
    "Sidecar Configure RPC outcomes by label + reason.",
    labelnames=["label", "reason"],
    registry=registry,
)

SCHWAB_OAUTH_START_TOTAL = Counter(
    "schwab_oauth_start_total",
    "Number of Schwab OAuth flow initiations (Tier-1 path).",
    registry=registry,
)

SCHWAB_OAUTH_CALLBACK_TOTAL = Counter(
    "schwab_oauth_callback_total",
    "Schwab OAuth callback outcomes by path + result.",
    labelnames=["path", "result"],
    registry=registry,
)

SCHWAB_OAUTH_NO_STATE_TOTAL = Counter(
    "schwab_oauth_no_state_total",
    "Schwab OAuth callbacks received without a state parameter (CSRF-unverified path).",
    registry=registry,
)

SCHWAB_ACCESS_TOKEN_AGE_SECONDS = Gauge(
    "schwab_access_token_age_seconds",
    "Age of the current access_token in seconds.",
    registry=registry,
)

SCHWAB_REFRESH_TOKEN_AGE_HOURS = Gauge(
    "schwab_refresh_token_age_hours",
    "Age of the current refresh_token in hours.",
    registry=registry,
)

SCHWAB_REFRESH_TOKEN_USES_PER_24H = Gauge(
    "schwab_refresh_token_uses_per_24h",
    "Refresh-token uses in a rolling 24h window (H4 — restart-flapping detector).",
    registry=registry,
)

SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL = Counter(
    "schwab_account_hash_refresh_total",
    "account_hash cache refreshes by reason.",
    labelnames=["reason"],
    registry=registry,
)

SCHWAB_HTTP_REQUESTS_TOTAL = Counter(
    "schwab_http_requests_total",
    "Schwab REST request count by endpoint + status code.",
    labelnames=["endpoint", "status"],
    registry=registry,
)

SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS = Gauge(
    "schwab_sidecar_token_drift_seconds",
    "Seconds since the last Configure call after a known token write (C3 invariant).",
    registry=registry,
)

SCHWAB_TIER2_REFRESH_TOTAL = Counter(
    "schwab_tier2_refresh_total",
    "Tier-2 Playwright auto-refresh outcomes.",
    labelnames=["result"],
    registry=registry,
)

SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS = Gauge(
    "schwab_tier2_last_run_timestamp_seconds",
    "Unix timestamp of the most recent Tier-2 refresh attempt (any outcome).",
    registry=registry,
)

# ──────────────────────── Phase 7b.1 streaming-quotes metrics ───────────────
QUOTE_INSTRUMENTS_CREATED_TOTAL = Counter(
    "quote_instruments_created_total",
    "Net-new instrument rows written by InstrumentResolver, by asset_class.",
    labelnames=["asset_class"],
    registry=registry,
)

QUOTE_ALIASES_CREATED_TOTAL = Counter(
    "quote_aliases_created_total",
    "Net-new symbol_aliases rows written by InstrumentResolver, by source.",
    labelnames=["source"],
    registry=registry,
)

QUOTE_POSITION_CANONICAL_RESOLVED_TOTAL = Counter(
    "quote_position_canonical_resolved_total",
    "Positions upserted with a derived canonical_id.",
    labelnames=["broker_id"],
    registry=registry,
)

QUOTE_POSITION_CANONICAL_UNRESOLVED_TOTAL = Counter(
    "quote_position_canonical_unresolved_total",
    "Positions upserted without a derived canonical_id.",
    labelnames=["broker_id", "reason"],
    registry=registry,
)

QUOTE_SEED_SKIPPED_TOTAL = Counter(
    "quote_seed_skipped_total",
    "Position rows skipped during instruments seed.",
    labelnames=["reason"],
    registry=registry,
)

QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL = Counter(
    "quote_subscription_cap_rejected_total",
    "SubscriptionRegistry rejections by cap kind (HIGH-6 + Phase 7c CRIT-1). "
    "cap_kind ∈ {per_ws, global, rate_limit, per_source}. Phase 7c F1 widened "
    "the label set with `source` (resolved upstream id, e.g. 'alpaca') and "
    "`asset_class` (the canonical_id prefix, e.g. 'crypto'). Pre-7c rejections "
    "carry empty strings for those two labels.",
    labelnames=["cap_kind", "source", "asset_class"],
    registry=registry,
)

ALPACA_MODE_MISMATCH_TOTAL = Counter(
    "alpaca_mode_mismatch_total",
    "Backend refused to send Configure to a sidecar whose Health-reported "
    "label-suffix did not match the gateway_label-implied mode (Phase 7c "
    "HIGH-5 cross-mode pollution probe). Should always be 0 in steady state.",
    labelnames=["label"],
    registry=registry,
)

QUOTE_SOURCE_HEALTH_STATE = Gauge(
    "quote_source_health_state",
    "SourceRouter health gauge per upstream (HIGH-7). "
    "Values: 0=down, 1=degraded, 2=healthy. Health-flip events drive "
    "quote_route_changes_total.",
    labelnames=["source"],
    registry=registry,
)

QUOTE_ROUTE_CHANGES_TOTAL = Counter(
    "quote_route_changes_total",
    "Route reassignments by SourceRouter — fires on primary-down fallback "
    "or operator override. Labels: from / to source ids + asset_class.",
    labelnames=["from_source", "to_source", "asset_class"],
    registry=registry,
)

QUOTE_SIDECAR_RECONNECT_TOTAL = Counter(
    "quote_sidecar_reconnect_total",
    "SidecarStream gRPC reconnects by source + reason (HIGH-1). "
    "reason ∈ {aio_rpc_error, token_rotation, sidecar_restart, idle_timeout}.",
    labelnames=["source", "reason"],
    registry=registry,
)

STREAM_QUEUE_DROPPED_TOTAL = Counter(
    "stream_queue_dropped_total",
    "SidecarStream pending-queue drop-oldest events (Phase 9.5 HIGH fix). "
    "Non-zero rate indicates a slow/stalled sidecar gRPC connection.",
    labelnames=["source"],
    registry=registry,
)

QUOTE_SIDECAR_FIRST_FRAME_TOTAL = Counter(
    "quote_sidecar_first_frame_total",
    "First-frame kind sent on (re)connect: subscribe (cold/sidecar-restart) "
    "vs resync (warm/gRPC-only reconnect). HIGH-1 mitigation observability.",
    labelnames=["source", "kind"],
    registry=registry,
)

QUOTE_ENGINE_TICKS_TOTAL = Counter(
    "quote_engine_ticks_total",
    "Ticks processed by QuoteEngine._on_quote, by source.",
    labelnames=["source"],
    registry=registry,
)

QUOTE_CACHE_SIZE = Gauge(
    "quote_cache_size",
    "Active entries in the engine's last-tick cache.",
    registry=registry,
)

QUOTE_CONFLATOR_NOTIFY_FAILURES_TOTAL = Counter(
    "quote_conflator_notify_failures_total",
    "Per-conflator notify exceptions in QuoteEngine._notify_conflators. "
    "Surfaces silent failures from misbehaving WS conflator callbacks.",
    registry=registry,
)

QUOTE_REDIS_PUBLISH_FAILURES_TOTAL = Counter(
    "quote_redis_publish_failures_total",
    "Redis publish exceptions in QuoteEngine._on_quote. Distinct from "
    "redis_publish_fail_total (config invalidation channel).",
    registry=registry,
)

QUOTE_WS_CONNECTIONS = Gauge(
    "quote_ws_connections",
    "Active WS quote connections",
    registry=registry,
)

QUOTE_WS_SEND_TOTAL = Counter(
    "quote_ws_send_total",
    "Quote WebSocket frames sent by operation.",
    labelnames=["op"],
    registry=registry,
)

QUOTE_WS_SEND_TIMEOUT_TOTAL = Counter(
    "quote_ws_send_timeout_total",
    "Quote WebSocket send attempts that timed out and closed the connection.",
    registry=registry,
)

QUOTE_WS_RECV_INVALID_TOTAL = Counter(
    "quote_ws_recv_invalid_total",
    "Invalid quote WebSocket frames received by reason.",
    labelnames=["reason"],
    registry=registry,
)


# ──────────────────────── Phase 9 — bar service metrics ─────────────────────

bar_service_cross_worker_wait_seconds = Histogram(
    "bar_service_cross_worker_wait_seconds",
    "Time spent waiting for another worker to complete a bar backfill via pg_notify.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 16.0),
    registry=registry,
)

bar_service_backfill_total = Counter(
    "bar_service_backfill_total",
    "Bar backfill jobs handled by this worker.",
    labelnames=("source", "timeframe", "outcome"),  # done|failed|coalesced_wait
    registry=registry,
)
