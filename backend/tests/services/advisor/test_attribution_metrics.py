from __future__ import annotations

from app.services.advisor.metrics import (
    advisor_attribution_bars_unavailable_total,
    advisor_attribution_decisions_processed_total,
    advisor_attribution_poll_latency_seconds,
    advisor_attribution_skipped_total,
    advisor_attribution_unresolvable_total,
)


def test_attribution_metrics_have_correct_labels() -> None:
    advisor_attribution_decisions_processed_total.labels(verdict="veto").inc()
    advisor_attribution_unresolvable_total.labels(reason="no_instrument").inc()
    advisor_attribution_skipped_total.labels(reason="close_position").inc()
    advisor_attribution_poll_latency_seconds.observe(0.5)
    advisor_attribution_bars_unavailable_total.inc()
