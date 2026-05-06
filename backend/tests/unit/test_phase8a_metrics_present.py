"""Phase 8a — verify all 12 new metrics are registered + label sets per spec sec 11."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from app.core import metrics as backend_metrics


@pytest.fixture(scope="module")
def sidecar_metrics():
    """Import sidecar_schwab/metrics.py from outside the backend package."""
    sidecar_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(sidecar_root))
    try:
        mod = importlib.import_module("sidecar_schwab.metrics")
    finally:
        sys.path.remove(str(sidecar_root))
    return mod


# Backend capability metrics (from B1)


@pytest.mark.parametrize(
    "name,labels",
    [
        ("order_capability_check_total", {"broker", "result"}),
        ("order_capability_cache_hits_total", {"broker"}),
        ("order_capability_cache_misses_total", {"broker"}),
    ],
)
def test_backend_labelled_counters(name: str, labels: set[str]) -> None:
    m = getattr(backend_metrics, name)
    assert set(m._labelnames) == labels, (
        f"{name}: expected labels {labels}, got {set(m._labelnames)}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "order_capability_admin_writes_total",
        "order_capability_pubsub_invalidations_total",
        "order_capability_pubsub_failures_total",
    ],
)
def test_backend_unlabelled_counters_present(name: str) -> None:
    assert hasattr(backend_metrics, name), f"missing backend metric: {name}"
    m = getattr(backend_metrics, name)
    assert set(m._labelnames) == set(), f"{name}: expected no labels, got {set(m._labelnames)}"


# Sidecar Schwab metrics (from C3 + C4 + D2)


@pytest.mark.parametrize(
    "name,labels",
    [
        ("SCHWAB_ORDER_POLLER_ITERATIONS_TOTAL", {"gateway_label", "account_id", "cadence"}),
        (
            "SCHWAB_ORDER_POLLER_CADENCE_CHANGED_TOTAL",
            {"gateway_label", "account_id", "from_cadence", "to_cadence"},
        ),
        ("SCHWAB_ORDER_EVENT_EMITTED_TOTAL", {"kind"}),
    ],
)
def test_sidecar_labelled_counters(sidecar_metrics, name: str, labels: set[str]) -> None:
    m = getattr(sidecar_metrics, name)
    assert set(m._labelnames) == labels, (
        f"{name}: expected labels {labels}, got {set(m._labelnames)}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "SCHWAB_PLACE_ORDER_DURATION_MS",
        "SCHWAB_CANCEL_ORDER_DURATION_MS",
        "SCHWAB_MODIFY_ORDER_DURATION_MS",
    ],
)
def test_sidecar_histograms_have_extended_buckets(sidecar_metrics, name: str) -> None:
    m = getattr(sidecar_metrics, name)
    buckets = list(m._upper_bounds)
    assert 10000.0 in buckets and 30000.0 in buckets, (
        f"{name}: HIGH-4 requires extended buckets (10s, 30s) for token-refresh tail"
    )


def test_sidecar_unlabelled_metrics_present(sidecar_metrics) -> None:
    assert hasattr(sidecar_metrics, "SCHWAB_FANOUT_SUBSCRIBER_DROPPED_TOTAL")
