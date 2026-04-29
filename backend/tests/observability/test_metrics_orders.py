from pathlib import Path

import pytest
import yaml
from prometheus_client import REGISTRY, Counter, Gauge, Histogram

from app.core.metrics import (
    broker_order_event_lag_ms,
    broker_order_events_dropped_total,
    broker_order_events_received_total,
    broker_order_pending_submit_orphan_total,
    broker_order_pending_submit_recovered_total,
    broker_order_stream_reconnects_total,
    broker_order_stream_resync_synthetic_events_total,
    consumer_alive,
    sse_active_connections,
    sse_dropped_clients_total,
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    return None


def test_metrics_orders_registry_includes_all_new_counters() -> None:
    expected = {
        broker_order_events_received_total: (Counter, ("label",)),
        broker_order_events_dropped_total: (Counter, ("label", "reason")),
        broker_order_event_lag_ms: (Histogram, ("label",)),
        broker_order_stream_reconnects_total: (Counter, ("label",)),
        broker_order_stream_resync_synthetic_events_total: (Counter, ("label",)),
        broker_order_pending_submit_recovered_total: (Counter, ("label",)),
        broker_order_pending_submit_orphan_total: (Counter, ("label",)),
        consumer_alive: (Gauge, ("label", "account_id")),
        sse_active_connections: (Gauge, ()),
        sse_dropped_clients_total: (Counter, ()),
    }

    for metric, (metric_type, labelnames) in expected.items():
        assert isinstance(metric, metric_type)
        assert metric._labelnames == labelnames


def test_dropped_rate_alert_fires_at_50_percent() -> None:
    alerts_path = Path(__file__).resolve().parents[3] / "deploy/prometheus/alerts.yml"
    alerts = yaml.safe_load(alerts_path.read_text())
    phase5b_orders = next(group for group in alerts["groups"] if group["name"] == "phase5b_orders")
    alert = next(
        rule for rule in phase5b_orders["rules"] if rule["alert"] == "BrokerOrderEventsHighDropRate"
    )

    assert alert.get("for") == "5m"
    assert alert.get("labels", {}).get("severity") == "page"
    assert "0.5" in alert["expr"]


def _phase5b_rule(name: str) -> dict:
    alerts_path = Path(__file__).resolve().parents[3] / "deploy/prometheus/alerts.yml"
    alerts = yaml.safe_load(alerts_path.read_text())
    group = next(g for g in alerts["groups"] if g["name"] == "phase5b_orders")
    return next(rule for rule in group["rules"] if rule["alert"] == name)


def test_orderevent_stream_down_alert_present() -> None:
    alert = _phase5b_rule("BrokerOrderEventStreamDown")
    assert alert.get("for") == "2m"
    assert alert.get("labels", {}).get("severity") == "page"
    assert "consumer_alive" in alert["expr"]
    assert "== 0" in alert["expr"]


def test_orderevent_stream_flapping_alert_present() -> None:
    alert = _phase5b_rule("BrokerOrderEventStreamFlapping")
    assert alert.get("for") == "5m"
    assert alert.get("labels", {}).get("severity") == "warning"
    assert "broker_order_stream_reconnects_total" in alert["expr"]
    assert "[10m]" in alert["expr"]


def test_consumer_alive_gauge_per_label_account() -> None:
    assert isinstance(consumer_alive, Gauge)
    assert set(consumer_alive._labelnames) == {"label", "account_id"}


def test_sse_active_connections_increments_on_connect_decrements_on_disconnect() -> None:
    before = REGISTRY.get_sample_value("sse_active_connections") or 0.0

    sse_active_connections.inc()
    sse_active_connections.inc()
    sse_active_connections.dec()

    after = REGISTRY.get_sample_value("sse_active_connections") or 0.0
    assert after - before == 1.0
