"""Real-IBKR read-only smoke tests against paper gateway 4002.

Marked @pytest.mark.real_ibkr — included only when the nightly cron
or operator explicitly runs `pytest -m real_ibkr`. CI's normal run
filters with `-m 'not real_ibkr'`. Idempotent: no orders placed, no
state mutated. Spec §9.
"""

from __future__ import annotations

import asyncio

import pytest
from ib_async import IB

PAPER_HOST = "127.0.0.1"
PAPER_PORT = 4002
CLIENT_ID = 999
CONNECT_TIMEOUT = 15


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_connect_paper_gateway() -> None:
    ib = IB()
    try:
        await ib.connectAsync(
            PAPER_HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT
        )
        assert ib.isConnected()
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_managed_accounts_returns_at_least_one() -> None:
    ib = IB()
    try:
        await ib.connectAsync(
            PAPER_HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT
        )
        await asyncio.sleep(0.5)
        accounts = ib.managedAccounts()
        assert isinstance(accounts, list)
        assert len(accounts) >= 1
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_account_summary_carries_currency() -> None:
    """Spec §9 contract test for option-E base currency: prove that
    Summary.net_liquidation rows from a real ib_async paper gateway
    do carry a currency field matching one of the expected ISO-3 codes.
    """
    ib = IB()
    try:
        await ib.connectAsync(
            PAPER_HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT
        )
        accounts = ib.managedAccounts()
        assert accounts
        await ib.reqAccountSummaryAsync()
        await asyncio.sleep(0.5)
        rows = ib.accountSummary(accounts[0])
        nlv_rows = [r for r in rows if r.tag == "NetLiquidation"]
        assert nlv_rows, f"no NetLiquidation row for {accounts[0]}"
        assert nlv_rows[0].currency in {
            "USD",
            "GBP",
            "HKD",
            "JPY",
            "KRW",
            "EUR",
            "CAD",
        }
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_get_positions_round_trips() -> None:
    ib = IB()
    try:
        await ib.connectAsync(
            PAPER_HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT
        )
        positions = await ib.reqPositionsAsync()
        assert isinstance(positions, list)
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_get_orders_empty_list_ok() -> None:
    ib = IB()
    try:
        await ib.connectAsync(
            PAPER_HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT
        )
        trades = ib.openTrades()
        assert isinstance(trades, list)
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_connection_survives_sixty_seconds() -> None:
    ib = IB()
    try:
        await ib.connectAsync(
            PAPER_HOST, PAPER_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT
        )
        await asyncio.sleep(60)
        assert ib.isConnected()
    finally:
        ib.disconnect()
