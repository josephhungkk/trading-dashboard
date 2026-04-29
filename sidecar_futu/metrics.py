"""Prometheus metrics for the Futu sidecar."""
from __future__ import annotations

from prometheus_client import Counter

account_skip_total = Counter(
    "futu_account_skip_total",
    "Futu account rows skipped while normalizing account list responses.",
    ["reason"],
)
