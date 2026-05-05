"""Alpaca client normalization tests."""

from __future__ import annotations

import os
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.client import AlpacaClient, AlpacaClientError


async def _to_thread_inline(
    fn: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    return fn(*args, **kwargs)


@pytest.mark.asyncio
async def test_alpaca_client_list_accounts_normalizes() -> None:
    account = SimpleNamespace(
        id="account-uuid",
        account_number="PA123",
        currency="USD",
        status="ACTIVE",
    )

    with (
        patch("sidecar_alpaca.client.TradingClient") as trading_client,
        patch("sidecar_alpaca.client.asyncio.to_thread", _to_thread_inline),
    ):
        trading_client.return_value.get_account.return_value = account
        client = AlpacaClient("key", "secret", paper=True)

        rows = await client.list_managed_accounts()

    assert rows == [
        {
            "account_id": "account-uuid",
            "account_number": "PA123",
            "currency": "USD",
            "status": "ACTIVE",
        },
    ]


@pytest.mark.asyncio
async def test_alpaca_client_get_positions_normalizes() -> None:
    positions = [
        SimpleNamespace(
            symbol="AAPL",
            exchange="NASDAQ",
            asset_class="us_equity",
            qty="2",
            avg_entry_price="150.25",
            market_value="310.00",
            unrealized_pl="9.50",
            side="long",
        ),
        SimpleNamespace(
            symbol="TSLA",
            exchange="NASDAQ",
            asset_class="us_equity",
            qty="1",
            avg_entry_price="200.00",
            market_value="190.00",
            unrealized_pl=None,
            side="long",
        ),
    ]

    with (
        patch("sidecar_alpaca.client.TradingClient") as trading_client,
        patch("sidecar_alpaca.client.asyncio.to_thread", _to_thread_inline),
    ):
        trading_client.return_value.get_all_positions.return_value = positions
        client = AlpacaClient("key", "secret", paper=True)

        rows = await client.get_positions()

    assert len(rows) == 2
    assert rows[0] == {
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "asset_class": "STOCK",
        "qty": "2",
        "avg_cost": "150.25",
        "currency": "USD",
        "market_value": "310.00",
        "unrealized_pnl": "9.50",
        "side": "long",
    }
    assert rows[1]["symbol"] == "TSLA"
    assert rows[1]["unrealized_pnl"] == "0"


@pytest.mark.asyncio
async def test_alpaca_client_handles_api_error() -> None:
    with (
        patch("sidecar_alpaca.client.TradingClient") as trading_client,
        patch("sidecar_alpaca.client.asyncio.to_thread", _to_thread_inline),
    ):
        trading_client.return_value.get_account.side_effect = RuntimeError("API down")
        client = AlpacaClient("key", "secret", paper=True)

        with pytest.raises(AlpacaClientError) as exc_info:
            await client.list_managed_accounts()

    assert exc_info.value.endpoint == "get_account"
    assert exc_info.value.message == "API down"
