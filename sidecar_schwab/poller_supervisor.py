"""Phase 8a D4 — supervisor + per-account facades for OrderPoller and SimRegistry.

Per-account: each (gateway_label, account_number) gets its own OrderPoller (with its
own _FanOut) and its own SimRegistry. The simulator/poller FACADES expose the same
call signatures the handler uses, but route to the right per-account instance.

Wiring into BrokerServicer.Configure / sidecar lifespan is a deploy-ops concern;
this module just provides the building blocks + tests them in isolation.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sidecar_schwab.order_poller import OrderPoller
from sidecar_schwab.order_state_cache import OrderStateCache
from sidecar_schwab.simulator import SimRegistry

if TYPE_CHECKING:
    from sidecar_schwab.order_poller import _FanOut

_GATEWAY_LABEL = "schwab"  # single-gateway sidecar (Phase 8a scope)
_PER_ACCOUNT_SEMAPHORE = 4  # rate-limit defense per account


class _SimulatorFacade:
    """Routes simulator calls to the right per-account SimRegistry.

    Matches the handler signatures from C3+C4. PlaceOrder routes via account_number;
    Cancel/Modify route via broker_order_id (search all sims since cancel/modify
    requests don't carry account context in proto).
    """

    def __init__(self, sims_by_account: dict[str, SimRegistry]) -> None:
        self._sims = sims_by_account

    def register(
        self, *, account_number: str, client_order_id: str, request: Any
    ) -> str:
        sim = self._sims.get(account_number)
        if sim is None:
            raise ValueError(f"no simulator for account {account_number}")
        return sim.register(
            account_number=account_number,
            client_order_id=client_order_id,
            request=request,
        )

    def cancel(self, *, broker_order_id: str) -> None:
        for sim in self._sims.values():
            if broker_order_id in sim._by_bid:
                sim.cancel(broker_order_id=broker_order_id)
                return
        # Unknown broker_order_id is a no-op (matches SimRegistry.cancel behavior).

    def modify(self, *, broker_order_id: str, request: Any) -> str:
        for sim in self._sims.values():
            if broker_order_id in sim._by_bid:
                return sim.modify(broker_order_id=broker_order_id, request=request)
        return ""


class _PollerFacade:
    """Routes poller calls to the right per-account OrderPoller."""

    def __init__(self, pollers_by_account: dict[str, OrderPoller]) -> None:
        self._pollers = pollers_by_account

    def activate_fast(self, *, account_number: str) -> None:
        p = self._pollers.get(account_number)
        if p is not None:
            p.activate_fast()

    def fan_out_for(self, *, account_number: str) -> "_FanOut | None":
        p = self._pollers.get(account_number)
        return p.fan_out() if p is not None else None


class PollerSupervisor:
    """Supervises per-account OrderPoller + SimRegistry instances."""

    def __init__(
        self, *, client: Any, redis: Any, accounts: list[dict[str, str]]
    ) -> None:
        """accounts: list of {'account_number': str, 'account_hash': str}."""
        self._client = client
        self._redis = redis
        self._accounts = list(accounts)
        self._pollers: dict[str, OrderPoller] = {}
        self._sims: dict[str, SimRegistry] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self.simulator: _SimulatorFacade | None = None
        self.poller: _PollerFacade | None = None

    async def start(self) -> None:
        for acct in self._accounts:
            account_number = acct["account_number"]
            account_hash = acct["account_hash"]
            cache = OrderStateCache(
                redis=self._redis,
                gateway_label=_GATEWAY_LABEL,
                account_id=account_number,
            )
            poller = OrderPoller(
                client=self._client,
                state_cache=cache,
                gateway_label=_GATEWAY_LABEL,
                account_id=account_number,
                account_hash_resolver=lambda h=account_hash: h,
            )
            sim = SimRegistry(fan_out=poller.fan_out())
            self._pollers[account_number] = poller
            self._sims[account_number] = sim
            self._semaphores[account_number] = asyncio.Semaphore(_PER_ACCOUNT_SEMAPHORE)
            await poller.start()

        self.simulator = _SimulatorFacade(self._sims)
        self.poller = _PollerFacade(self._pollers)

    async def stop(self) -> None:
        # Codex pattern B: cancel + gather all pollers in parallel.
        tasks = [p.stop() for p in self._pollers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pollers.clear()
        self._sims.clear()
        self._semaphores.clear()
        self.simulator = None
        self.poller = None

    def get_semaphore(self, account_number: str) -> asyncio.Semaphore | None:
        return self._semaphores.get(account_number)
