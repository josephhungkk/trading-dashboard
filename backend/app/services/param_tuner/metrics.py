from prometheus_client import Counter, Gauge, Histogram

param_tuner_trigger_total = Counter(
    "param_tuner_trigger_total",
    "Param tuner triggers",
    ["triggered_by"],
)

param_tuner_trigger_failures_total = Counter(
    "param_tuner_trigger_failures_total",
    "Trigger failures",
    ["reason"],
)

param_tuner_candidates_generated_total = Counter(
    "param_tuner_candidates_generated_total",
    "Total LLM candidates generated",
)

param_tuner_invalid_candidates_total = Counter(
    "param_tuner_invalid_candidates_total",
    "Candidates dropped as invalid",
    ["reason"],
)

param_tuner_backtest_fan_out_total = Counter(
    "param_tuner_backtest_fan_out_total",
    "Backtest fan-out submits",
)

param_tuner_backtest_queue_depth = Gauge(
    "param_tuner_backtest_queue_depth",
    "Current backtest queue depth",
)

param_tuner_ranked_total = Counter(
    "param_tuner_ranked_total",
    "Suggestions ranked",
)

param_tuner_applied_total = Counter(
    "param_tuner_applied_total",
    "Suggestions applied",
    ["triggered_by"],
)

param_tuner_ai_latency_seconds = Histogram(
    "param_tuner_ai_latency_seconds",
    "AI call latency for param tuner",
)

param_tuner_fleet_cost_ceiling_total = Counter(
    "param_tuner_fleet_cost_ceiling_total",
    "Fleet scheduled runs stopped by cost ceiling",
)

param_tuner_cost_reservation_failures_total = Counter(
    "param_tuner_cost_reservation_failures_total",
    "Redis cost reservation failures",
)
