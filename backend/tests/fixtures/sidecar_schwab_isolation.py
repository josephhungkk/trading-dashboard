"""Helper for tests/unit/* tests that import from ``sidecar_schwab``.

Both ``backend.app.core.metrics`` and ``sidecar_schwab.metrics`` define the
same metric names (``broker_normalize_unknown_total``, ``schwab_http_requests_*``,
etc.) against prometheus_client's global REGISTRY. In production these run
in separate processes; in tests both end up in the same Python process, so
the second import collides:

    ValueError: Duplicated timeseries in CollectorRegistry: \
        {'broker_normalize_unknown_total', ...}

Call ``unregister_shared_metrics_then_import_sidecar()`` BEFORE importing
anything from ``sidecar_schwab``; it sweeps the prom-client REGISTRY for the
known shared prefixes and unregisters the backend's copies, letting the
sidecar's import win.
"""

from __future__ import annotations

import pytest

SHARED_PREFIXES: tuple[str, ...] = (
    "broker_normalize_unknown",
    "schwab_http_requests",
    "schwab_account_hash_refresh",
    "schwab_access_token_age",
)


def unregister_shared_metrics() -> None:
    """Unregister the backend's copies of metrics that ``sidecar_schwab``
    will re-declare. Idempotent; safe to call multiple times."""
    from prometheus_client import REGISTRY

    to_unregister: list[object] = []
    for name, collector in list(REGISTRY._names_to_collectors.items()):
        if any(name.startswith(p) for p in SHARED_PREFIXES):
            to_unregister.append(collector)
    for c in set(to_unregister):
        REGISTRY.unregister(c)


def importorskip_sidecar_schwab(
    module: str = "sidecar_schwab._generated.broker.v1.broker_pb2",
) -> None:
    """Combined helper: skip if the sidecar isn't mounted; otherwise
    clear the shared metrics first so the upcoming import won't collide.
    """
    pytest.importorskip(module)
    unregister_shared_metrics()
