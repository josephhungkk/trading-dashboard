from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

cgt_engine_processed_total = Counter(
    "cgt_engine_processed_total",
    "Tax events processed by CGT engine",
    ["cgt_track", "event_type"],
)
cgt_engine_failed_total = Counter(
    "cgt_engine_failed_total",
    "Tax events that failed CGT engine processing",
    ["reason"],
)
cgt_recompute_triggered_total = Counter(
    "cgt_recompute_triggered_total",
    "CGT recompute queue entries pushed",
    ["trigger"],
)
cgt_disposal_inserted_total = Counter(
    "cgt_disposal_inserted_total",
    "CGT disposals inserted",
    ["match_type"],
)
cgt_short_closed_total = Counter(
    "cgt_short_closed_total",
    "Short obligation pairs closed",
)
cgt_importer_runs_total = Counter(
    "cgt_importer_runs_total",
    "CGT importer job runs",
    ["broker", "status"],
)
cgt_importer_records_imported_total = Counter(
    "cgt_importer_records_imported_total",
    "Records imported by CGT importer",
    ["broker", "record_type"],
)
cgt_hmrc_fx_fetch_total = Counter(
    "cgt_hmrc_fx_fetch_total",
    "HMRC FX rate fetch attempts",
    ["status", "period_month"],
)
cgt_bb_gate_fires_total = Counter(
    "cgt_bb_gate_fires_total",
    "Pre-trade b&b gate fires",
    ["outcome"],
)
cgt_recompute_queue_depth = Gauge(
    "cgt_recompute_queue_depth",
    "Items in CGT recompute queue",
)
cgt_short_obligation_open_count = Gauge(
    "cgt_short_obligation_open_count",
    "Open short obligations across all accounts",
)
cgt_hmrc_fx_rates_age_days = Gauge(
    "cgt_hmrc_fx_rates_age_days",
    "Days since last successful HMRC FX rate fetch",
)
cgt_engine_process_seconds = Histogram(
    "cgt_engine_process_seconds",
    "CGT engine process() latency",
    ["cgt_track"],
)
cgt_recompute_seconds = Histogram(
    "cgt_recompute_seconds",
    "CGT recompute() latency",
)
cgt_importer_duration_seconds = Histogram(
    "cgt_importer_duration_seconds",
    "CGT importer job duration",
    ["broker"],
)
