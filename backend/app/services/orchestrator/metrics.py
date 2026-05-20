from prometheus_client import Counter, Gauge, Histogram

orchestrator_exposure_checks_total = Counter(
    "orchestrator_exposure_checks_total",
    "Portfolio exposure gate check outcomes",
    ["outcome", "limit_type"],
)
orchestrator_exposure_gate_latency_seconds = Histogram(
    "orchestrator_exposure_gate_latency_seconds",
    "Portfolio exposure gate check latency",
    ["path"],
)
orchestrator_exposure_gate_pg_fallback_total = Counter(
    "orchestrator_exposure_gate_pg_fallback_total",
    "Exposure gate PG fallback events",
    ["outcome"],
)
orchestrator_correlation_matrix_age_seconds = Gauge(
    "orchestrator_correlation_matrix_age_seconds",
    "Age of correlation matrix in Redis",
    ["account_id"],
)
orchestrator_auto_promote_total = Counter(
    "orchestrator_auto_promote_total",
    "Auto-promote evaluation outcomes",
    ["outcome"],
)
orchestrator_retrain_bots_total = Counter(
    "orchestrator_retrain_bots_total",
    "Total bots processed by NightlyRetrainJob",
)
orchestrator_retrain_latency_seconds = Histogram(
    "orchestrator_retrain_latency_seconds",
    "NightlyRetrainJob total latency",
)
orchestrator_sector_ingestion_total = Counter(
    "orchestrator_sector_ingestion_total",
    "Sector ingestion outcomes",
    ["outcome", "source"],
)
orchestrator_marginal_variance_fallback_total = Counter(
    "orchestrator_marginal_variance_fallback_total",
    "MV gate fallback events",
    ["reason"],
)
orchestrator_digest_runs_total = Counter(
    "orchestrator_digest_runs_total",
    "Total HealthDigestService run completions",
)
