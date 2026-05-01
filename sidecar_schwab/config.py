"""Phase 7a configuration — env vars only."""
from __future__ import annotations

import logging
import os

DEFAULT_PORT = 9090

log = logging.getLogger(__name__)


def resolve_port() -> int:
    """Read SCHWAB_SIDECAR_PORT from env, fall back to 9090 on error."""
    raw = os.environ.get("SCHWAB_SIDECAR_PORT", "")
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        log.warning(
            "invalid SCHWAB_SIDECAR_PORT %r — falling back to %d",
            raw, DEFAULT_PORT,
        )
        return DEFAULT_PORT
