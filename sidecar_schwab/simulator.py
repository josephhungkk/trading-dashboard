"""Phase 8a — SIM mode echo for Schwab sidecar (mirrors IBKR 5b.1 pattern).

When client_order_id starts with 'SIM-', PlaceOrder/CancelOrder/ModifyOrder route
through SimRegistry instead of hitting Schwab REST. SimRegistry emits synthetic
WireEvents through the same _FanOut as the real OrderPoller (so subscribers see
identical wire events for SIM and live orders).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidecar_schwab.order_poller import _FanOut

_SIM_TTL_SECONDS = 3600.0
_SYNTHETIC_DELAY_S = 0.05


def _new_sim_id() -> str:
    return f"SIM-{uuid.uuid4()}"


@dataclass
class _SimEntry:
    client_order_id: str
    broker_order_id: str
    created_at: float


class SimRegistry:
    def __init__(
        self,
        *,
        fan_out: "_FanOut",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fan_out = fan_out
        self._clock = clock
        self._entries: dict[str, _SimEntry] = {}
        self._by_bid: dict[str, _SimEntry] = {}

    def register(
        self, *, account_number: str, client_order_id: str, request: object
    ) -> str:
        broker_order_id = _new_sim_id()
        entry = _SimEntry(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            created_at=self._clock(),
        )
        self._entries[client_order_id] = entry
        self._by_bid[broker_order_id] = entry
        self._schedule_emit(broker_order_id, client_order_id, "submitted")
        return broker_order_id

    def cancel(self, *, broker_order_id: str) -> None:
        entry = self._by_bid.get(broker_order_id)
        if entry is None:
            return
        self._schedule_emit(broker_order_id, entry.client_order_id, "cancelled")

    def modify(self, *, broker_order_id: str, request: object) -> str:
        old = self._by_bid.get(broker_order_id)
        if old is None:
            return ""
        new_broker_order_id = _new_sim_id()
        new_entry = _SimEntry(
            client_order_id=old.client_order_id,
            broker_order_id=new_broker_order_id,
            created_at=self._clock(),
        )
        self._entries[old.client_order_id] = new_entry
        self._by_bid[new_broker_order_id] = new_entry
        self._schedule_emit(broker_order_id, old.client_order_id, "modified")
        self._schedule_emit(new_broker_order_id, old.client_order_id, "submitted")
        return new_broker_order_id

    def gc(self) -> None:
        cutoff = self._clock() - _SIM_TTL_SECONDS
        stale = [k for k, e in self._entries.items() if e.created_at < cutoff]
        for k in stale:
            entry = self._entries.pop(k)
            self._by_bid.pop(entry.broker_order_id, None)

    def _schedule_emit(
        self, broker_order_id: str, client_order_id: str, status: str
    ) -> None:
        from sidecar_schwab.order_poller import WireEvent

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        ev = WireEvent(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            kind="status",
            status=status,
        )
        loop.call_later(
            _SYNTHETIC_DELAY_S,
            lambda: loop.create_task(self._fan_out.publish(ev)),
        )
