"""Gateway-label → dial-address resolution (Phase 7c HIGH-4).

Introduces the ``broker_gateway_dial`` config table for the new "labeled
docker sidecar" sub-pattern (alpaca-live, alpaca-paper). Sits between
IBKR's NUC+mTLS dials (resolved via ``SIDECAR_PORTS``/``SIDECAR_HOSTS``)
and Schwab's fixed in-cluster dial — neither is migrated this phase.
Callers fall back to the existing static maps when this helper returns
the sentinel ``default`` value.
"""

from __future__ import annotations

from typing import Any

_TABLE_KEY = "broker_gateway_dial"
_SENTINEL: Any = object()


def resolve_dial(
    config: dict[str, Any],
    gateway_label: str,
    *,
    default: Any = _SENTINEL,
) -> str | None:
    """Resolve a ``gateway_label`` to its dial target via app_config.

    Returns the configured ``"<host>:<port>"`` string when ``gateway_label``
    is present in ``config["broker_gateway_dial"]``. Otherwise:

    * If ``default`` is supplied (e.g. ``default=None``), returns it —
      caller can then fall back to the legacy static-map resolver.
    * If ``default`` is omitted, raises ``KeyError(gateway_label)``.
    """
    table = config.get(_TABLE_KEY, {})
    if gateway_label in table:
        return str(table[gateway_label])
    if default is not _SENTINEL:
        return default if default is None else str(default)
    raise KeyError(gateway_label)
