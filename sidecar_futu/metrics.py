"""Sidecar-local Prometheus counters."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter

# Local registry isolates sidecar metrics from any process-wide default that
# tests or PyInstaller-bundled deps might touch.
registry = CollectorRegistry()

broker_normalize_unknown_total = Counter(
    "broker_normalize_unknown_total",
    "Sidecar normalize layer received an unknown enum value from broker SDK.",
    labelnames=["label", "field"],
    registry=registry,
)

futu_streamer_ticks_total = Counter(
    "futu_streamer_ticks_total",
    "Futu quote streamer ticks emitted by raw Futu symbol.",
    labelnames=["symbol"],
    registry=registry,
)

futu_streamer_subscribe_total = Counter(
    "futu_streamer_subscribe_total",
    "Futu quote streamer subscribe attempts by result.",
    labelnames=["result"],
    registry=registry,
)
