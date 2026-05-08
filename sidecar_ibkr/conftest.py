"""Pytest fixtures shared across the sidecar test suite.

The `golden_fake_ib` factory replays the JSON fixtures recorded against the
paper IB Gateway (sidecar/tests/golden/*.json — Phase 4 Task 17.3) so the
proto-contract regression tests in tests/test_golden_replay.py can exercise
the BrokerHandlers wrapping path without needing a live gateway in CI.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import sidecar_ibkr

sys.modules.setdefault("sidecar", sidecar_ibkr)

GOLDEN_DIR = Path(__file__).parent / "tests" / "golden"

# Fields the handlers expect as Decimal (Position/Pnl numerics). Excludes
# AccountValue.value because that one carries heterogeneous strings — e.g.
# tag=AccountType yields value="INDIVIDUAL", not a number.
_DECIMAL_FIELDS = frozenset(
    {"position", "avgCost", "realizedPnL", "unrealizedPnL", "marketValue"}
)


def _hydrate(value: Any) -> Any:
    """Rebuild SimpleNamespace objects from recorded JSON dicts so handlers'
    getattr() calls keep working without reconstructing ib_async typed classes
    (some of which raise on null nested fields like deltaNeutralContract)."""

    if isinstance(value, dict):
        ns = SimpleNamespace()
        for key, item in value.items():
            if key in _DECIMAL_FIELDS and isinstance(item, str) and item:
                try:
                    setattr(ns, key, Decimal(item))
                    continue
                except InvalidOperation:
                    pass  # fall through to plain hydrate
            setattr(ns, key, _hydrate(item))
        return ns
    if isinstance(value, list):
        return [_hydrate(item) for item in value]
    return value


def _load_fixture(name: str) -> Any:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


class _GoldenFakeIB:
    """Drop-in for ib_async.IB whose read-only methods replay recorded
    paper-gateway responses. Method names mirror ib_async's mixedCase API."""

    def __init__(self) -> None:
        self._managed = _load_fixture("managed_accounts")
        self._values = [_hydrate(d) for d in _load_fixture("account_summary")]
        self._positions = [_hydrate(d) for d in _load_fixture("positions")]
        self._trades = [_hydrate(d) for d in _load_fixture("open_trades")]
        self._fills = [_hydrate(d) for d in _load_fixture("fills")]
        self._qualified = [_hydrate(d) for d in _load_fixture("qualify_aapl")]

    def isConnected(self) -> bool:  # noqa: N802
        return True

    @property
    def client(self) -> SimpleNamespace:
        return SimpleNamespace(serverVersion=lambda: 999)

    async def reqManagedAccountsAsync(self) -> Sequence[str]:  # noqa: N802
        return list(self._managed)

    def managedAccounts(self) -> Sequence[str]:  # noqa: N802
        return list(self._managed)

    def accountValues(self, account: str = "") -> list[object]:  # noqa: N802
        if not account:
            return list(self._values)
        return [v for v in self._values if getattr(v, "account", "") == account]

    def accountSummary(self, account: str = "") -> list[object]:  # noqa: N802
        # ib_async exposes accountSummary as a separate cache populated by
        # reqAccountSummary; the sidecar's startup subscribes to it for the
        # BASE tag. The golden fixture's account_summary.json carries those
        # rows already, so alias to the same list for replay-test parity.
        if not account:
            return list(self._values)
        return [v for v in self._values if getattr(v, "account", "") == account]

    async def reqPositionsAsync(self) -> list[object]:  # noqa: N802
        return list(self._positions)

    def openTrades(self) -> list[object]:  # noqa: N802
        return list(self._trades)

    def fills(self) -> list[object]:
        return list(self._fills)

    async def qualifyContractsAsync(  # noqa: N802
        self, contract: object
    ) -> list[object]:
        return list(self._qualified)


class _ZeroPnLCache:
    """Stub PnLCache that returns Decimal('0') for any key. Recorded positions
    fixture is empty, so the cache is never consulted in replay — but
    BrokerHandlers expects .snapshot() to exist regardless, and a real
    PnLCache requires a live ib_async.IB it can subscribe to."""

    def snapshot(
        self,
        account: str,
        model_code: str,
        conid: int,
    ) -> tuple[Decimal, Decimal, Decimal]:
        zero = Decimal("0")
        return zero, zero, zero

    async def cancel_all(self) -> None:
        return None


@pytest.fixture
def golden_fake_ib() -> Callable[[], _GoldenFakeIB]:
    """Factory: each call returns a fresh _GoldenFakeIB so tests that mutate
    state stay isolated. Snapshot source: Task 17.3 / commit 00d8381."""

    def _factory() -> _GoldenFakeIB:
        return _GoldenFakeIB()

    return _factory


@pytest.fixture
def zero_pnl_cache() -> _ZeroPnLCache:
    return _ZeroPnLCache()
