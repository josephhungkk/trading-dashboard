from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

advisor_decisions_total = Counter(
    "advisor_decisions_total",
    "Total advisor decisions by mode, verdict, capability",
    ["mode", "verdict", "capability"],
)

advisor_latency_seconds = Histogram(
    "advisor_latency_seconds",
    "Advisor AI call latency",
    ["mode", "capability"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0],
)

advisor_fail_open_total = Counter(
    "advisor_fail_open_total",
    "Fail-open events by reason",
    ["reason"],
)

advisor_audit_insert_failures_total = Counter(
    "advisor_audit_insert_failures_total",
    "Failures to persist advisor audit row",
)

advisor_publish_failures_total = Counter(
    "advisor_publish_failures_total",
    "Failures to publish advisor WS frame to Redis",
)

advisor_budget_exceeded_total = Counter(
    "advisor_budget_exceeded_total",
    "Daily budget exceeded events per bot",
    ["bot_id"],
)

advisor_auto_pause_triggered_total = Counter(
    "advisor_auto_pause_triggered_total",
    "Auto-pause threshold breaches per bot",
    ["bot_id"],
)

advisor_auto_pause_errors_total = Counter(
    "advisor_auto_pause_errors_total",
    "Redis errors in AutoPauseService",
)

advisor_unexpected_errors_total = Counter(
    "advisor_unexpected_errors_total",
    "Unexpected errors in AdvisorService.review — closed error_class taxonomy",
    ["error_class"],
)

advisor_in_flight_skips_total = Counter(
    "advisor_in_flight_skips_total",
    "In-flight cap exceeded — second concurrent request failed-open per bot",
    ["bot_id"],
)

advisor_unknown_tags_total = Counter(
    "advisor_unknown_tags_total",
    "Unknown advice_tags replaced with 'other'",
    ["tag"],
)

advisor_budget_reconcile_delta_usd = Gauge(
    "advisor_budget_reconcile_delta_usd",
    "Last reconcile delta between optimistic Redis counter and actual AI spend (USD)",
)

advisor_approve_then_account_block_total = Counter(
    "advisor_approve_then_account_block_total",
    "Advisor approved but account-level risk gate blocked",
    ["reason"],
)

advisor_state_drift_skips_total = Counter(
    "advisor_state_drift_skips_total",
    "VETO approve downgraded to fail_open due to post-verdict state drift",
    ["bot_id"],
)

advisor_config_reloads_total = Counter(
    "advisor_config_reloads_total",
    "Advisor config hot-reloads via UPDATE_ADVISOR_CONFIG",
    ["bot_id"],
)

advisor_hook_errors_total = Counter(
    "advisor_hook_errors_total",
    "Exceptions raised in strategy.on_advisor_reject hook",
)

advisor_overrides_total = Counter(
    "advisor_overrides_total",
    "Human veto overrides applied",
    ["override_action"],
)

advisor_concurrent_calls = Gauge(
    "advisor_concurrent_calls",
    "Live concurrent advisor calls per bot",
    ["bot_id"],
)

advisor_shadow_context_build_seconds = Histogram(
    "advisor_shadow_context_build_seconds",
    "Context-build latency in SHADOW mode (AI call excluded)",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

advisor_semaphore_resize_deferred_total = Counter(
    "advisor_semaphore_resize_deferred_total",
    "max_concurrent config changes deferred because old semaphore did not drain in time",
)

advisor_account_config_writes_total = Counter(
    "advisor_account_config_writes_total",
    "Per-account advisor config writes (action=set|clear)",
    ["action"],
)
