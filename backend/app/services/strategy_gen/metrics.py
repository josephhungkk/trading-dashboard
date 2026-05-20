from __future__ import annotations

from prometheus_client import Counter, Histogram

strategy_gen_generated_total = Counter(
    "strategy_gen_generated_total",
    "Strategy generation outcomes",
    ["outcome"],
)
strategy_gen_sandbox_latency_seconds = Histogram(
    "strategy_gen_sandbox_latency_seconds",
    "Sandbox validation latency",
)
strategy_gen_auto_approved_total = Counter(
    "strategy_gen_auto_approved_total",
    "Auto-approved strategies",
)
strategy_gen_veto_window_cancellations_total = Counter(
    "strategy_gen_veto_window_cancellations_total",
    "Strategies vetoed during veto window",
)
strategy_gen_load_hash_mismatch_total = Counter(
    "strategy_gen_load_hash_mismatch_total",
    "Source hash mismatches detected on load",
)
