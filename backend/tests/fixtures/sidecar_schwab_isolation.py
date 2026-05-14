"""Helper for tests/unit/* tests that import from ``sidecar_schwab``.

Two collision classes exist between the backend and ``sidecar_schwab`` when
both run in the same Python process:

1. **Prometheus metric collision** — ``backend.app.core.metrics`` and
   ``sidecar_schwab.metrics`` declare identical metric names against the
   global REGISTRY.  Fix: unregister the backend's copies first.

2. **Protobuf descriptor-pool collision** — ``backend/app/_generated`` and
   ``sidecar_schwab/_generated`` both compile ``broker.v1.*`` messages but
   under different file-path keys (``v1/broker.proto`` vs
   ``broker/v1/broker.proto``).  The C-extension descriptor pool has no
   eviction API, so loading the second copy raises
   ``TypeError: duplicate symbol 'broker.v1.BrokerId'``.
   Fix: if the backend's proto is already in the pool, alias the sidecar's
   ``sys.modules`` entries to the backend modules before ``importorskip``
   triggers the file import.
"""

from __future__ import annotations

import sys

import pytest

SHARED_PREFIXES: tuple[str, ...] = (
    "broker_normalize_unknown",
    "schwab_http_requests",
    "schwab_account_hash_refresh",
    "schwab_access_token_age",
)

# Backend-generated proto path that may already be in sys.modules.
_BACKEND_PB2 = "app._generated.broker.v1.broker_pb2"
_BACKEND_PB2_GRPC = "app._generated.broker.v1.broker_pb2_grpc"
# Sidecar module paths that would duplicate the backend symbols.
_SIDECAR_PB2 = "sidecar_schwab._generated.broker.v1.broker_pb2"
_SIDECAR_PB2_GRPC = "sidecar_schwab._generated.broker.v1.broker_pb2_grpc"


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


def _alias_proto_modules() -> None:
    """If the backend's broker_pb2 is already loaded (descriptor pool
    already owns the symbols), inject aliases so that sidecar_schwab's
    _generated package resolves to the same module objects instead of
    attempting a second AddSerializedFile call.

    This is safe because both protos compile identical .proto source —
    they share package, message names, field numbers, and enum values.
    The only difference is the file-path key used during code generation.

    Only the _generated pb2 modules are aliased. The rest of the
    sidecar_schwab package (handlers, order_poller, etc.) imports normally
    from the real package on sys.path; we don't create stub modules for
    those so that subsequent ``from sidecar_schwab.handlers import ...``
    still resolves through the real file system.
    """
    if _BACKEND_PB2 not in sys.modules:
        return  # backend proto not loaded yet; normal import path is fine

    sys.modules.setdefault(_SIDECAR_PB2, sys.modules[_BACKEND_PB2])
    if _BACKEND_PB2_GRPC in sys.modules:
        sys.modules.setdefault(_SIDECAR_PB2_GRPC, sys.modules[_BACKEND_PB2_GRPC])


def importorskip_sidecar_schwab(
    module: str = "sidecar_schwab._generated.broker.v1.broker_pb2",
) -> None:
    """Skip if sidecar_schwab isn't on sys.path; otherwise resolve the
    Prometheus and protobuf descriptor-pool collisions before importing.
    """
    # Resolve proto collision BEFORE importorskip triggers the file import.
    _alias_proto_modules()
    unregister_shared_metrics()
    pytest.importorskip(module)
