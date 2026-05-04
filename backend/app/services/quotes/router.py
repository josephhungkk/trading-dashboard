"""SourceRouter — config-driven priority + sliding-window health (HIGH-7).

Owns the routing decision for "which upstream source serves this canonical_id?".
Reads :class:`SourceHealthMap` (updated by SidecarStream tick callbacks +
operator overrides) and the ``quote_source_priority`` table from
``app_config``. Health-flip events emit ``quote_route_changes_total{from,to,
asset_class}`` so the alert pack can spot route churn.

Sliding-window health: an UP source whose last-tick is older than
``max(5 * min_stale_threshold, 60s)`` is downgraded to ``DEGRADED``. The 60 s
floor protects quiet symbols (after-hours, idle warrants) from false-down
flips. ``DEGRADED`` does NOT take a source out of rotation by itself —
``DOWN`` does. Engine logic in Task B5 promotes DEGRADED → DOWN if no ticks
arrive across the entire subscribed set in ``health_window``.
"""

from __future__ import annotations

import time
from enum import IntEnum
from typing import Any

from app.core.metrics import QUOTE_ROUTE_CHANGES_TOTAL, QUOTE_SOURCE_HEALTH_STATE
from app.models.instruments import Instrument
from app.services.quotes.base import (
    SourceId,
    canonical_id_components,
    country_for_exchange,
)


class SourceHealthState(IntEnum):
    """Three-state health enum mirroring the Prometheus gauge values."""

    DOWN = 0
    DEGRADED = 1
    HEALTHY = 2


_HEALTH_WINDOW_FLOOR_SECONDS: float = 60.0


class SourceHealthMap:
    """Per-source health + last-tick wall clock.

    Sources may be plain ids (``schwab``, ``ibkr``, ``futu``) or
    ``ibkr:<gateway>`` for per-gateway entries (MED-6). Setting a state also
    bumps ``quote_source_health_state{source}``.
    """

    def __init__(self) -> None:
        self._state: dict[str, SourceHealthState] = {}
        self._last_tick: dict[str, float] = {}

    def set_state(self, source: str, state: SourceHealthState) -> None:
        self._state[source] = state
        QUOTE_SOURCE_HEALTH_STATE.labels(source=source).set(int(state))

    def get_state(self, source: str) -> SourceHealthState | None:
        return self._state.get(source)

    def update_last_tick(self, source: str, ts: float) -> None:
        """Record the monotonic timestamp of the most recent tick from
        ``source``. Called by SidecarStream tick callbacks (Task B4)."""
        self._last_tick[source] = ts

    def time_since_last_tick(self, source: str) -> float | None:
        ts = self._last_tick.get(source)
        if ts is None:
            return None
        return time.monotonic() - ts

    def is_up(self, source: str) -> bool:
        """``True`` iff the source is not explicitly DOWN. Sources never
        registered are treated as up (boot grace) so a fresh deploy doesn't
        nuke routing before the first tick lands."""
        return self._state.get(source, SourceHealthState.HEALTHY) != SourceHealthState.DOWN


class SourceRouter:
    """Decides ``canonical_id → SourceId`` from config + health."""

    def __init__(self, config: dict[str, Any], health: SourceHealthMap) -> None:
        self._config = config
        self._health = health

    # ── core routing ──────────────────────────────────────────────────────

    async def route(self, instrument: Instrument) -> str | None:
        """Return the highest-priority healthy source for ``instrument``,
        or ``None`` if no source serves this (asset_class, country) or all
        configured sources are DOWN."""
        priority = self._priority_list_for(instrument)
        for src in priority:
            if self._health.is_up(src):
                return src
        return None

    async def route_with_gateway(self, instrument: Instrument) -> tuple[str | None, str | None]:
        """Variant of :meth:`route` that also picks the IBKR gateway when
        the chosen source is IBKR. Returns ``(source, gateway | None)``;
        ``gateway`` is ``None`` for non-IBKR sources.
        """
        src = await self.route(instrument)
        if src != SourceId.IBKR.value:
            return (src, None)

        country = self._derive_country(instrument)
        if country is None:
            return (src, None)
        key = f"{instrument.asset_class.value.lower()}.{country}"

        assignment = self._config.get("ibkr_gateway_quote_assignment", {})
        gw = assignment.get(key, assignment.get("_default", "isa-live"))

        if not self._health.is_up(f"ibkr:{gw}"):
            for fallback in self._config.get("ibkr_gateway_quote_fallback", []):
                if self._health.is_up(f"ibkr:{fallback}"):
                    return (src, fallback)
            return (None, None)

        return (src, gw)

    async def reroute(
        self,
        instrument: Instrument,
        *,
        current: str,
        reason: str,
    ) -> str | None:
        """Pick a new source (skipping ``current``) and emit
        ``quote_route_changes_total`` on success. Caller (engine) writes the
        result via :meth:`SubscriptionRegistry.set_route`. ``reason`` is
        logged but not a metric label (cardinality)."""
        del reason  # logged by caller; not part of metric labels
        priority = self._priority_list_for(instrument)
        for src in priority:
            if src == current:
                continue
            if self._health.is_up(src):
                QUOTE_ROUTE_CHANGES_TOTAL.labels(
                    from_source=current,
                    to_source=src,
                    asset_class=instrument.asset_class.value,
                ).inc()
                return src
        return None

    # ── health window ────────────────────────────────────────────────────

    def compute_health_state(self, source: str, *, min_threshold: float) -> SourceHealthState:
        """Compute the effective health state for ``source``.

        * DOWN if explicitly marked DOWN.
        * DEGRADED if no tick has ever arrived (boot grace) OR the last tick
          is older than ``max(5 * min_threshold, 60 s)``.
        * HEALTHY otherwise.

        The 60 s floor matches spec §5.2.2 — protects quiet symbols (idle
        warrants, after-hours US equity) from false-down flips.
        """
        if not self._health.is_up(source):
            return SourceHealthState.DOWN

        since = self._health.time_since_last_tick(source)
        if since is None:
            return SourceHealthState.DEGRADED

        window = max(5.0 * min_threshold, _HEALTH_WINDOW_FLOOR_SECONDS)
        if since > window:
            return SourceHealthState.DEGRADED
        return SourceHealthState.HEALTHY

    # ── helpers ──────────────────────────────────────────────────────────

    def _priority_list_for(self, instrument: Instrument) -> list[str]:
        country = self._derive_country(instrument)
        if country is None:
            return []
        key = f"{instrument.asset_class.value.lower()}.{country}"
        result = self._config.get("quote_source_priority", {}).get(key, [])
        return list(result)

    def _derive_country(self, instrument: Instrument) -> str | None:
        """Country lives in canonical_id position 3; fall back to the
        exchange→country map if the canonical_id is malformed (defence in
        depth — shouldn't happen post-A4)."""
        try:
            _, _, country = canonical_id_components(instrument.canonical_id)
            return country
        except ValueError:
            return country_for_exchange(instrument.primary_exchange)
