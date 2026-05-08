"""Integration test: Phase 9.7 — one-shot BASE-tag refresh for mid-run accounts.

Covers BrokerDiscoverer._known_accounts logic in brokers.py:
  - A (broker_id, account_number) pair NOT in _known_accounts triggers
    list_managed_accounts() + get_account_summary() with a 15 s timeout.
  - The pair is added to _known_accounts so the trigger is one-shot.
  - On the second tick the pair is already known; no extra refresh RPC fires.
  - The broker_account_first_seen log event is emitted exactly once.

These tests are pure in-memory (no Postgres needed): the discoverer's DB
session is replaced with an AsyncMock, and the fake registry tracks call
counts so assertions are deterministic.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brokers import base
from app.services.brokers import BrokerDiscoverer

# ---------------------------------------------------------------------------
# Fake registry / client helpers
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal fake that records calls to list_managed_accounts / get_account_summary."""

    def __init__(self, label: str, accounts: list[base.Account]) -> None:
        self.label = label
        self._accounts = accounts
        self.list_managed_calls: int = 0
        self.summary_calls: list[str] = []

    async def list_managed_accounts(self) -> list[base.Account]:
        self.list_managed_calls += 1
        return self._accounts

    async def get_account_summary(self, account_number: str) -> base.Summary:
        self.summary_calls.append(account_number)
        return base.Summary(
            net_liquidation=base.Money(value="10000", currency="GBP"),
            total_cash=base.Money(value="0", currency="GBP"),
            realized_pnl=base.Money(value="0", currency="GBP"),
            unrealized_pnl=base.Money(value="0", currency="GBP"),
            buying_power=base.Money(value="0", currency="GBP"),
            updated_at=None,
        )

    async def get_positions(self, account_number: str) -> list[base.Position]:
        return []


class _FakeRegistry:
    def __init__(self, clients: list[_FakeClient]) -> None:
        self._clients = {c.label: c for c in clients}

    async def healthy_clients(self) -> list[_FakeClient]:
        return list(self._clients.values())

    async def get_client(self, label: str) -> _FakeClient:
        return self._clients[label]


# ---------------------------------------------------------------------------
# Helpers to build a no-op session factory (skips real DB)
# ---------------------------------------------------------------------------


def _make_no_op_session_factory() -> Any:
    """Return a session factory whose sessions silently no-op all execute calls.

    The discoverer's _discover_once uses the session for:
      1. resurrect-check SELECT (returns empty)
      2. upsert INSERT ... ON CONFLICT (no-op)
      3. soft-delete UPDATE (returns empty)
      4. NLV UPDATE (no-op)
      5. positions SELECT + upsert (delegated to _discover_positions)

    We mock all of these to return empty results so the first-seen logic
    (which runs AFTER the DB block) can be exercised in isolation.
    """
    cursor = MagicMock()
    cursor.mappings.return_value.all.return_value = []
    cursor.all.return_value = []
    cursor.one_or_none.return_value = None
    cursor.scalar_one.return_value = 0

    session = AsyncMock()
    session.execute = AsyncMock(return_value=cursor)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=session)
    session.begin_nested = MagicMock(return_value=session)

    factory = MagicMock()
    factory.return_value = session
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_seen_account_triggers_base_refresh() -> None:
    """On the first tick a new account triggers list_managed_accounts + get_account_summary."""
    account = base.Account(
        account_number="U9997771",
        mode="PAPER",
        gateway_label="isa-paper",
        currency_base="GBP",
    )
    client = _FakeClient("isa-paper", [account])
    registry = _FakeRegistry([client])
    session_factory = _make_no_op_session_factory()

    discoverer = BrokerDiscoverer(registry, session_factory)  # type: ignore[arg-type]
    assert discoverer._known_accounts == set()

    with patch("app.services.broker_registry_factory.SIDECAR_BROKERS", {"isa-paper": "ibkr"}):
        await discoverer._discover_once()

    # The BASE-tag refresh path calls list_managed_accounts once (ping) and
    # get_account_summary once with the 15 s timeout.
    assert client.list_managed_calls >= 1, "list_managed_accounts must be called for refresh"
    assert "U9997771" in client.summary_calls, "get_account_summary must be called for new account"

    # Pair must now be in _known_accounts.
    assert ("ibkr", "U9997771") in discoverer._known_accounts


@pytest.mark.asyncio
async def test_second_tick_does_not_retrigger_refresh() -> None:
    """On the second tick the pair is already known; no extra refresh RPC fires."""
    account = base.Account(
        account_number="U9997772",
        mode="PAPER",
        gateway_label="isa-paper",
        currency_base="GBP",
    )
    client = _FakeClient("isa-paper", [account])
    registry = _FakeRegistry([client])
    session_factory = _make_no_op_session_factory()

    discoverer = BrokerDiscoverer(registry, session_factory)  # type: ignore[arg-type]

    with patch("app.services.broker_registry_factory.SIDECAR_BROKERS", {"isa-paper": "ibkr"}):
        await discoverer._discover_once()  # tick 1 — triggers refresh

    calls_after_tick1 = len(client.summary_calls)
    assert calls_after_tick1 >= 1

    with patch("app.services.broker_registry_factory.SIDECAR_BROKERS", {"isa-paper": "ibkr"}):
        await discoverer._discover_once()  # tick 2 — must NOT re-trigger

    # The NLV fan-out (Phase 5a) also calls get_account_summary; we assert
    # that no extra call beyond the NLV fan-out was made for the refresh path
    # (i.e. the count grows by at most 1 from NLV fan-out, not 2).
    calls_after_tick2 = len(client.summary_calls)
    assert calls_after_tick2 - calls_after_tick1 <= 1, (
        "second tick must not re-trigger the one-shot BASE refresh; "
        f"got {calls_after_tick2 - calls_after_tick1} extra summary calls"
    )


@pytest.mark.asyncio
async def test_known_accounts_grows_with_each_new_account() -> None:
    """_known_accounts accumulates all seen pairs across multiple accounts."""
    accounts = [
        base.Account(
            account_number=f"U999800{i}",
            mode="PAPER",
            gateway_label="isa-paper",
            currency_base="GBP",
        )
        for i in range(3)
    ]
    client = _FakeClient("isa-paper", accounts)
    registry = _FakeRegistry([client])
    session_factory = _make_no_op_session_factory()

    discoverer = BrokerDiscoverer(registry, session_factory)  # type: ignore[arg-type]

    with patch("app.services.broker_registry_factory.SIDECAR_BROKERS", {"isa-paper": "ibkr"}):
        await discoverer._discover_once()

    assert len(discoverer._known_accounts) == 3
    for acct in accounts:
        assert ("ibkr", acct.account_number) in discoverer._known_accounts


@pytest.mark.asyncio
async def test_first_seen_log_event_emitted(capsys: Any) -> None:
    """broker_account_first_seen structlog event fires for each new account.

    structlog is configured with JSONRenderer writing to stdout; caplog does
    not capture structlog output, so we use capsys instead.
    """
    account = base.Account(
        account_number="U9997773",
        mode="PAPER",
        gateway_label="isa-paper",
        currency_base="GBP",
    )
    client = _FakeClient("isa-paper", [account])
    registry = _FakeRegistry([client])
    session_factory = _make_no_op_session_factory()

    discoverer = BrokerDiscoverer(registry, session_factory)  # type: ignore[arg-type]

    with patch("app.services.broker_registry_factory.SIDECAR_BROKERS", {"isa-paper": "ibkr"}):
        await discoverer._discover_once()

    captured = capsys.readouterr()
    assert "broker_account_first_seen" in captured.out, (
        "broker_account_first_seen log event must appear in structlog output"
    )


@pytest.mark.asyncio
async def test_refresh_rpc_timeout_does_not_crash_discoverer() -> None:
    """A timeout during the BASE-tag refresh must be swallowed; tick must complete."""

    class _SlowClient(_FakeClient):
        async def get_account_summary(self, account_number: str) -> base.Summary:
            await asyncio.sleep(0)  # yield so gather sees the coroutine
            raise TimeoutError("simulated timeout")

    account = base.Account(
        account_number="U9997774",
        mode="PAPER",
        gateway_label="isa-paper",
        currency_base="GBP",
    )
    slow_client = _SlowClient("isa-paper", [account])
    registry = _FakeRegistry([slow_client])
    session_factory = _make_no_op_session_factory()

    discoverer = BrokerDiscoverer(registry, session_factory)  # type: ignore[arg-type]

    # Must not raise despite the timeout inside _base_refresh.
    with patch("app.services.broker_registry_factory.SIDECAR_BROKERS", {"isa-paper": "ibkr"}):
        await discoverer._discover_once()

    # Pair still added to _known_accounts even when refresh fails — prevents
    # retrying every single tick against a persistently-unresponsive sidecar.
    assert ("ibkr", "U9997774") in discoverer._known_accounts
