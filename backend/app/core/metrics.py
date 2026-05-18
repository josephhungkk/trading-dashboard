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


# ──────────────── Phase 9.7 G1-G2 — capability + order-flow metrics ──────────
# G1: broker capability-check observability
# TODO(app-side): increment broker_capability_mismatch_total in
#   backend/app/services/order_capability_service.py when is_supported()
#   returns False AND the DB row exists (row is not None but is_supported=False).
#   That is the "mismatch" case: a combo is in the table but flagged unsupported,
#   yet the operator is still submitting it.  Suggested call site: inside
#   is_supported() after `supported = bool(row is not None and row["is_supported"])`
#   — add an `elif row is not None:` branch that increments this counter before
#   returning False.
broker_capability_mismatch_total = Counter(
    "broker_capability_mismatch_total",
    "Capability checks that returned unsupported despite a DB row existing "
    "(is_supported=False rows). Sustained increments indicate broker-capability "
    "table drift vs the order types actually being submitted. "
    "Feeds BrokerCapabilityMismatchSustained alert.",
    labelnames=["broker"],
    registry=registry,
)

# G1: poller drift gauge — seconds since the last successful poller tick per
# (gateway_label, account_id).  The schwab sidecar already emits
# schwab_order_poller_iterations_total; this backend Gauge is the companion
# staleness signal intended to be set by the backend's order-poller health
# probe or a BrokerDiscoverer extension.
# TODO(app-side): set broker_poller_drift_seconds in
#   backend/app/services/brokers.py (BrokerDiscoverer tick or a dedicated
#   HealthPoller) by recording monotonic timestamps per (gateway_label,
#   account_id) and computing elapsed since the last successful iteration.
#   Alternatively, feed it from the schwab sidecar's OrderPoller heartbeat RPC
#   if one is added in a future phase.  Until then this Gauge stays at its
#   initial 0 and the alert will not fire.
broker_poller_drift_seconds = Gauge(
    "broker_poller_drift_seconds",
    "Seconds since the last successful order-poller iteration per "
    "(gateway_label, account_id). Initialises at 0 (no tick yet observed). "
    "Feeds BrokerPollerDriftHigh alert (threshold >60 s sustained 2 m).",
    labelnames=["gateway_label", "account_id"],
    registry=registry,
)

# G2: per-broker order-operation outcome counters (place / cancel / modify).
# These are backend-side operation totals, distinct from the sidecar-side
# schwab_http_requests_total.  The BrokerPlaceModifyCancelErrors alert uses
# the union of all three to compute a multi-broker error rate.
# TODO(app-side): increment broker_order_place_total in
#   backend/app/services/orders_service.py — place_order():
#     result="success"  → after _mark_order_submitted() succeeds
#     result="error"    → in the bare `except Exception` block (~line 352)
#     result="timeout"  → in the BrokerSidecarTimeout catch block (~line 349)
#   label=account.gateway_label
# TODO(app-side): increment broker_order_cancel_total in
#   backend/app/services/orders_service.py — cancel_order():
#     result="success"  → on CancelOrderResult(status="cancel_requested")
#     result="error"    → on CancelUnavailable 422 (broker rejected)
#     result="timeout"  → on BrokerSidecarTimeout/BrokerSidecarUnavailable catch
#   label=str(row["gateway_label"])
# TODO(app-side): increment broker_order_modify_total in
#   backend/app/api/orders.py — PUT /api/orders/{id} handler:
#     result="success"  → on 200 response path
#     result="error"    → on broker_modify_rejected JSONResponse 422
#     result="timeout"  → on BrokerSidecarUnavailable 503 response
#   label extracted from the order row's gateway_label
broker_order_place_total = Counter(
    "broker_order_place_total",
    "Place-order attempts by gateway label and result (success|error|timeout). "
    "Error rate denominator for BrokerPlaceModifyCancelErrors alert.",
    labelnames=["label", "result"],
    registry=registry,
)

# Phase 10a D4: risk_decisions audit insert failures (fail-OPEN per spec §4
# row "risk_decisions INSERT fails: fail-OPEN for the order, alert").
risk_audit_insert_failures_total = Counter(
    "risk_audit_insert_failures_total",
    "Risk gate audit row insert failures by attempt_kind. fail-OPEN per spec §4.",
    labelnames=["attempt_kind"],
    registry=registry,
)

broker_order_cancel_total = Counter(
    "broker_order_cancel_total",
    "Cancel-order attempts by gateway label and result (success|error|timeout). "
    "Error rate denominator for BrokerPlaceModifyCancelErrors alert.",
    labelnames=["label", "result"],
    registry=registry,
)

broker_order_modify_total = Counter(
    "broker_order_modify_total",
    "Modify-order attempts by gateway label and result (success|error|timeout). "
    "Error rate denominator for BrokerPlaceModifyCancelErrors alert.",
    labelnames=["label", "result"],
    registry=registry,
)

# ─── Phase 10a.5 A2 metrics — pnl_intraday pipeline ─────────────────────

pnl_intraday_upsert_failures_total = Counter(
    "pnl_intraday_upsert_failures_total",
    "PnlIntradayWriter upsert failures (logged + dropped, not raised).",
    registry=registry,
)

pnl_intraday_last_update_seconds = Gauge(
    "pnl_intraday_last_update_seconds",
    "Age (seconds) of newest pnl_intraday row per account; alert >90s.",
    labelnames=["account_id"],
    registry=registry,
)

pnl_intraday_currency_skip_total = Counter(
    "pnl_intraday_currency_skip_total",
    "Position rows dropped at writer due to currency mismatch vs currency_base.",
    labelnames=["broker_id"],
    registry=registry,
)


# ─── Phase 10a.5 A5 metrics — audit dedupe ──────────────────────────────

risk_audit_dedupe_skipped_total = Counter(
    "risk_audit_dedupe_skipped_total",
    "ALLOW audit rows skipped due to 30s SETNX dedupe at place/modify path.",
    labelnames=["attempt_kind"],
    registry=registry,
)


# ─── Phase 10a.5 B1 metrics — instrument resolution ─────────────────────

risk_gate_concentration_skipped_unresolved_total = Counter(
    "risk_gate_concentration_skipped_unresolved_total",
    "Concentration check skipped due to unresolved instrument_id (B1 cold path).",
    labelnames=["reason"],
    registry=registry,
)


# ─── Phase 10a.5 A4 metrics — risk-counter tokens ───────────────────────

risk_counter_orphan_tokens_total = Gauge(
    "risk_counter_orphan_tokens_total",
    "Count of orphan risk-counter tokens unlinked at last discoverer sweep "
    "(PDT + BP combined). Sustained non-zero indicates a leak.",
    registry=registry,
)

risk_counter_cleanup_failures_total = Counter(
    "risk_counter_cleanup_failures_total",
    "risk_counters commit/revert/orphan-sweep failure count. Alert on rate > 0.",
    registry=registry,
)


# ─── Phase 11a — margin/evaluator skip telemetry ────────────────────────

risk_margin_skip_total = Counter(
    "risk_margin_skip_total",
    "Margin check skipped because symbol/asset_class was unavailable. "
    "preview mode is benign; place_order is a fail-CLOSED block.",
    labelnames=["mode", "outcome"],
    registry=registry,
)

risk_evaluator_degraded_total = Counter(
    "risk_evaluator_degraded_total",
    "A fast check raised AttributeError and degraded to WARN. Test stubs "
    "expected; sustained non-zero in prod indicates a typo'd method call.",
    labelnames=["check", "mode"],
    registry=registry,
)


# ── Phase 10b.1 — position-sizing metrics ──────────────────────────────────
position_sizing_compute_total = Counter(
    "position_sizing_compute_total",
    "Position-sizing requests, labelled by method, account currency, verdict.",
    ["method", "account_currency", "verdict"],
    registry=registry,
)
position_sizing_latency_seconds = Histogram(
    "position_sizing_latency_seconds",
    "End-to-end /api/risk/position-size latency including risk-gate eval.",
    ["method"],
    buckets=(0.005, 0.010, 0.025, 0.050, 0.100, 0.250, 0.500, 1.0),
    registry=registry,
)
position_sizing_vol_unavailable_total = Counter(
    "position_sizing_vol_unavailable_total",
    "vol-targeted requests rejected because realized_vol14 was unavailable and no override.",
    registry=registry,
)
volatility_cache_hits_total = Counter(
    "volatility_cache_hits_total",
    "Redis vol14:* cache hits.",
    registry=registry,
)
volatility_cache_misses_total = Counter(
    "volatility_cache_misses_total",
    "Redis vol14:* cache misses (fell through to bars_1d).",
    registry=registry,
)
position_sizing_admin_writes_total = Counter(
    "position_sizing_admin_writes_total",
    "PUT /api/admin/sizing-defaults calls, labelled by edited field.",
    ["field"],
    registry=registry,
)

# Phase 10b.2 — portfolio rollup (multi-account NLV / exposure / drill)
portfolio_rollup_compute_total = Counter(
    "portfolio_rollup_compute_total",
    "Successful portfolio rollup compute requests.",
    labelnames=["endpoint", "base_currency"],
    registry=registry,
)
portfolio_rollup_compute_latency_seconds = Histogram(
    "portfolio_rollup_compute_latency_seconds",
    "Latency of portfolio rollup compute paths.",
    labelnames=["endpoint"],
    registry=registry,
)
portfolio_rollup_fx_unavailable_total = Counter(
    "portfolio_rollup_fx_unavailable_total",
    "Count of fx_rate_unavailable raises during rollup compute.",
    labelnames=["pair"],
    registry=registry,
)
portfolio_rollup_snapshot_writes_total = Counter(
    "portfolio_rollup_snapshot_writes_total",
    "Successful account_balance_snapshots INSERTs from BalanceSnapshotWriter.",
    registry=registry,
)
portfolio_rollup_snapshot_write_errors_total = Counter(
    "portfolio_rollup_snapshot_write_errors_total",
    "Failed account_balance_snapshots INSERTs (fail-OPEN; outer NLV UPDATE still commits).",
    registry=registry,
)
portfolio_rollup_ws_publish_total = Counter(
    "portfolio_rollup_ws_publish_total",
    "Successful redis.publish on portfolio.rollup.dirty channel.",
    registry=registry,
)
portfolio_rollup_publish_failures_total = Counter(
    "portfolio_rollup_publish_failures_total",
    "Failed redis.publish on portfolio.rollup.dirty channel.",
    registry=registry,
)
portfolio_rollup_ws_connections = Gauge(
    "portfolio_rollup_ws_connections",
    "Current open /ws/portfolio/rollup connections.",
    registry=registry,
)
portfolio_rollup_ws_send_timeout_total = Counter(
    "portfolio_rollup_ws_send_timeout_total",
    "WS send timeouts on /ws/portfolio/rollup; connection closed on timeout.",
    registry=registry,
)

# Phase 11a-A2 — AI router WoL + Ollama health metrics
AI_ROUTER_WOL_WAKE_TOTAL = Counter(
    "ai_router_wol_wake_total",
    "Heavy-box wake attempts by host and outcome.",
    labelnames=["host", "outcome"],
    registry=registry,
)
AI_ROUTER_WOL_WAKE_LATENCY_SECONDS = Histogram(
    "ai_router_wol_wake_latency_seconds",
    "Elapsed seconds from wake start until heavy-box Ollama TCP/API responds.",
    labelnames=["host"],
    buckets=(1, 2.5, 5, 10, 15, 30, 45, 60, 90, 120, 180, 300),
    registry=registry,
)
AI_ROUTER_WOL_WARM_TO_READY_SECONDS = Histogram(
    "ai_router_wol_warm_to_ready_seconds",
    "Elapsed seconds from wake start until requested model is listed by Ollama.",
    labelnames=["host"],
    buckets=(1, 2.5, 5, 10, 15, 30, 45, 60, 90, 120, 180, 300),
    registry=registry,
)
AI_ROUTER_WOL_WAKE_FAILURES_TOTAL = Counter(
    "ai_router_wol_wake_failures_total",
    "Heavy-box wake failures by host and reason.",
    labelnames=["host", "reason"],
    registry=registry,
)
AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE = Gauge(
    "ai_router_wol_circuit_breaker_state",
    "Heavy-box WoL circuit breaker state: 0=closed, 1=half-open, 2=open.",
    labelnames=["host"],
    registry=registry,
)
AI_ROUTER_OLLAMA_HEALTH_FAILURES_TOTAL = Counter(
    "ai_router_ollama_health_failures_total",
    "Raw Ollama health-check failures by host.",
    labelnames=["host"],
    registry=registry,
)
AI_ROUTER_OLLAMA_HEALTH_ALERT_PUBLISH_FAILURES_TOTAL = Counter(
    "ai_router_ollama_health_alert_publish_failures_total",
    "Redis pubsub publish failures while emitting Ollama health alerts.",
    labelnames=["host"],
    registry=registry,
)
AI_ROUTER_RATE_LIMITED_TOTAL = Counter(
    "ai_router_rate_limited_total",
    "AI router rate-limit rejections.",
    labelnames=["capability"],
    registry=registry,
)
AI_COST_LEDGER_DROPS_TOTAL = Counter(
    "ai_cost_ledger_drops_total",
    "Cost-ledger queue drops (queue full).",
    registry=registry,
)
AI_COST_LEDGER_INSERT_FAILURES_TOTAL = Counter(
    "ai_cost_ledger_insert_failures_total",
    "Cost-ledger batched INSERT failures (rows dropped, fail-OPEN).",
    registry=registry,
)
ai_jobs_in_flight = Gauge(
    "ai_jobs_in_flight",
    "AI jobs not in terminal state.",
    registry=registry,
)
ai_jobs_orphan_recovered_total = Counter(
    "ai_jobs_orphan_recovered_total",
    "Jobs failed via orphan-recovery sweep (started before crash).",
    ["phase"],
    registry=registry,
)
ai_jobs_orphan_sweep_failures_total = Counter(
    "ai_jobs_orphan_sweep_failures_total",
    "Failures in the orphan-recovery sweep loop (transient DB errors etc).",
    registry=registry,
)
ai_ws_chat_stream_errors_total = Counter(
    "ai_ws_chat_stream_errors_total",
    "Unhandled exceptions during /ws/ai/chat stream forwarding.",
    ["error_class"],
    registry=registry,
)
ai_ws_jobs_send_timeout_total = Counter(
    "ai_ws_jobs_send_timeout_total",
    "Send timeouts on /ws/ai/jobs/{id} (client too slow or disconnected).",
    registry=registry,
)

# Phase 11a-B7 — AI router completion/fallback metrics
AI_ROUTER_COMPLETIONS_TOTAL = Counter(
    "ai_router_completions_total",
    "AI router completion outcomes.",
    labelnames=["provider", "model", "capability", "outcome"],
    registry=registry,
)
AI_ROUTER_LATENCY_SECONDS = Histogram(
    "ai_router_latency_seconds",
    "AI router completion latency in seconds.",
    labelnames=["provider", "capability"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30),
    registry=registry,
)
AI_ROUTER_TOKENS_PROMPT_TOTAL = Counter(
    "ai_router_tokens_prompt_total",
    "AI router prompt tokens by provider/model.",
    labelnames=["provider", "model"],
    registry=registry,
)
AI_ROUTER_TOKENS_COMPLETION_TOTAL = Counter(
    "ai_router_tokens_completion_total",
    "AI router completion tokens by provider/model.",
    labelnames=["provider", "model"],
    registry=registry,
)
AI_ROUTER_FALLBACK_CHAIN_TOTAL = Counter(
    "ai_router_fallback_chain_total",
    "AI router fallback hops by provider and reason.",
    labelnames=["from_provider", "to_provider", "reason"],
    registry=registry,
)
AI_ROUTER_PROXY_UNAVAILABLE_TOTAL = Counter(
    "ai_router_proxy_unavailable_total",
    "AI router requests where all retryable providers were exhausted.",
    registry=registry,
)
AI_JOBS_PUBLISH_FAILURES_TOTAL = Counter(
    "ai_jobs_publish_failures_total",
    "AI job state-transition pubsub publish failures (PG committed, WS missed).",
    registry=registry,
)

TELEGRAM_ORDER_ATTEMPTS_TOTAL = Counter(
    "telegram_order_attempts_total",
    "Telegram /place_order attempts by result.",
    labelnames=["result"],
    registry=registry,
)

TELEGRAM_ORDER_PREVIEWS_TOTAL = Counter(
    "telegram_order_previews_total",
    "Telegram order preview outcomes.",
    labelnames=["result"],
    registry=registry,
)

TELEGRAM_ORDER_CONFIRMS_TOTAL = Counter(
    "telegram_order_confirms_total",
    "Telegram /confirm outcomes.",
    labelnames=["result"],
    registry=registry,
)

TELEGRAM_ORDER_CANCELS_TOTAL = Counter(
    "telegram_order_cancels_total",
    "Telegram /cancel_order executions by stage.",
    labelnames=["stage"],
    registry=registry,
)

TELEGRAM_RATE_LIMITER_TRADE_BLOCK_TOTAL = Counter(
    "telegram_rate_limiter_trade_block_total",
    "Times the Telegram trade rate-limit bucket blocked a request.",
    registry=registry,
)

TELEGRAM_ORDER_E2E_SECONDS = Histogram(
    "telegram_order_e2e_seconds",
    "Telegram order flow end-to-end latency.",
    labelnames=["stage"],
    registry=registry,
)

# Phase 12: Options metrics
OPTION_CHAIN_FETCH_SECONDS = Histogram(
    "option_chain_fetch_seconds",
    "Option chain fetch latency.",
    labelnames=["source"],
    registry=registry,
)

OPTION_CHAIN_FETCH_TOTAL = Counter(
    "option_chain_fetch_total",
    "Option chain fetch outcomes.",
    labelnames=["source", "outcome"],
    registry=registry,
)

OPTION_EXPIRATIONS_FETCH_TOTAL = Counter(
    "option_expirations_fetch_total",
    "Option expirations fetch outcomes.",
    labelnames=["source", "outcome"],
    registry=registry,
)

OPTION_GREEKS_STREAM_UPDATES_TOTAL = Counter(
    "option_greeks_stream_updates_total",
    "Greeks stream updates received.",
    labelnames=["source"],
    registry=registry,
)

OPTION_GREEKS_STREAM_DROPS_TOTAL = Counter(
    "option_greeks_stream_drops_total",
    "Greeks stream messages dropped (backpressure).",
    labelnames=["source"],
    registry=registry,
)

OPTION_EXERCISE_TOTAL = Counter(
    "option_exercise_total",
    "Exercise elections submitted.",
    labelnames=["broker", "action", "outcome"],
    registry=registry,
)

OPTION_GREEKS_ROWS_TOTAL = Gauge(
    "option_greeks_rows_total",
    "Current rows in option_greeks table.",
    registry=registry,
)

OPTION_GREEKS_CLAMPED_TOTAL = Counter(
    "option_greeks_clamped_total",
    "Greeks values clamped to valid range.",
    labelnames=["field"],
    registry=registry,
)

QUOTE_OPTIONS_CHAIN_SUBS_ACTIVE = Gauge(
    "quote_options_chain_subs_active",
    "Active options chain subscriptions.",
    labelnames=["source"],
    registry=registry,
)

OPTION_RISK_CHECK_TOTAL = Counter(
    "option_risk_check_total",
    "Options risk check outcomes.",
    labelnames=["check", "verdict"],
    registry=registry,
)

OPTION_CHAIN_SOURCES_INVALID_TOTAL = Counter(
    "option_chain_sources_invalid_total",
    "Invalid sources on chain config load/reload.",
    labelnames=["source"],
    registry=registry,
)

# Phase 14 — Futures
FUTURES_ROLL_NOTIFICATIONS_TOTAL = Counter(
    "futures_roll_notifications_total",
    "Roll notifications sent per exchange.",
    labelnames=["exchange"],
    registry=registry,
)

FUTURES_ROLL_CONFIRMS_TOTAL = Counter(
    "futures_roll_confirms_total",
    "Roll confirmations by outcome.",
    labelnames=["outcome"],
    registry=registry,
)

FUTURES_ROLL_NONCE_EXPIRED_TOTAL = Counter(
    "futures_roll_nonce_expired_total",
    "Roll nonces consumed but expired (GETDEL returned None).",
    registry=registry,
)

FUTURES_SETTLEMENT_EVENTS_TOTAL = Counter(
    "futures_settlement_events_total",
    "Settlement events recorded by broker and type.",
    labelnames=["broker", "settlement_type"],
    registry=registry,
)

FUTURES_CONTRACT_RESOLVER_CACHE_HITS_TOTAL = Counter(
    "futures_contract_resolver_cache_hits_total",
    "ContractResolver Redis singleflight cache hits by root symbol.",
    labelnames=["root_symbol"],
    registry=registry,
)

FUTURES_CONTRACT_RESOLVER_FETCH_TOTAL = Counter(
    "futures_contract_resolver_fetch_total",
    "ContractResolver broker fetches by root symbol and outcome.",
    labelnames=["root_symbol", "outcome"],
    registry=registry,
)

# Phase 15a: FX metrics
forex_risk_check_failures_total = Counter(
    "forex_risk_check_failures_total",
    "FX risk gate infrastructure errors (fail-open)",
    registry=registry,
)

forex_rfq_requests_total = Counter(
    "forex_rfq_requests_total",
    "FX RFQ requests by currency pair.",
    labelnames=["pair"],
    registry=registry,
)

forex_rfq_accepts_total = Counter(
    "forex_rfq_accepts_total",
    "FX RFQ accept attempts by currency pair and outcome.",
    labelnames=["pair", "outcome"],
    registry=registry,
)

forex_rfq_expired_total = Counter(
    "forex_rfq_expired_total",
    "FX RFQ quotes swept as expired by the TTL sweeper.",
    registry=registry,
)

forex_quote_stream_updates_total = Counter(
    "forex_quote_stream_updates_total",
    "FX mid-rate stream ticks received by currency pair.",
    labelnames=["pair"],
    registry=registry,
)

forex_risk_blocks_total = Counter(
    "forex_risk_blocks_total",
    "FX risk gate BLOCK verdicts by check code.",
    labelnames=["code"],
    registry=registry,
)

forex_rfq_latency_seconds = Histogram(
    "forex_rfq_latency_seconds",
    "End-to-end RFQ request latency in seconds.",
    registry=registry,
)

# Phase 15b — Crypto / Coinbase metrics
coinbase_ws_messages_total = Counter(
    "coinbase_ws_messages_total",
    "Coinbase WS messages received by channel and outcome.",
    labelnames=["channel", "outcome"],
    registry=registry,
)

coinbase_ws_reconnects_total = Counter(
    "coinbase_ws_reconnects_total",
    "Coinbase WS reconnection attempts.",
    registry=registry,
)

coinbase_book_publish_total = Counter(
    "coinbase_book_publish_total",
    "Order book deltas published to Redis stream by canonical_id.",
    labelnames=["canonical_id"],
    registry=registry,
)

coinbase_book_sequence_gap_total = Counter(
    "coinbase_book_sequence_gap_total",
    "Sequence gaps detected in Coinbase L2 feed by canonical_id.",
    labelnames=["canonical_id"],
    registry=registry,
)

coinbase_book_lag_seconds = Histogram(
    "coinbase_book_lag_seconds",
    "Latency from Coinbase message receipt to Redis XADD in seconds.",
    registry=registry,
)

crypto_risk_check_failures_total = Counter(
    "crypto_risk_check_failures_total",
    "Crypto risk check infrastructure failures (fail-open events).",
    registry=registry,
)

ws_crypto_book_connections = Gauge(
    "ws_crypto_book_connections_total",
    "Active crypto book WS connections",
    registry=registry,
)

ws_crypto_book_messages_total = Counter(
    "ws_crypto_book_messages_total",
    "Crypto book WS messages sent",
    labelnames=["canonical_id"],
    registry=registry,
)
