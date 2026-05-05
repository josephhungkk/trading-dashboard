"""Alpaca SDK isolation surface (M3 from Phase 7a)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from sidecar_alpaca.metrics import (
    ALPACA_ACCOUNT_READ_FAILURES_TOTAL,
    ALPACA_HTTP_REQUESTS_TOTAL,
)

log = structlog.get_logger(__name__)

TradingClient: Any | None = None


@dataclass
class AlpacaClientError(Exception):
    """Raised when an Alpaca REST call fails. Caller maps to gRPC status."""

    endpoint: str
    message: str
    status: str = "unknown"


class AlpacaClient:
    """ONLY this file imports `alpaca.*`. Bumps to alpaca-py only touch here.

    Wraps TradingClient with async methods (alpaca-py is sync).
    """

    def __init__(self, api_key: str, api_secret: str, *, paper: bool) -> None:
        trading_client = TradingClient
        if trading_client is None:
            # Late-import alpaca to keep the rest of the package mockable in tests.
            from alpaca.trading.client import TradingClient as _TradingClient

            trading_client = _TradingClient
        self._client = trading_client(api_key, api_secret, paper=paper, raw_data=False)
        self._paper = paper

    async def list_managed_accounts(self) -> list[dict[str, Any]]:
        """Returns [{account_id, account_number, mode, currency_base}]."""
        account = await self._http_call(
            "get_account",
            lambda: self._client.get_account(),
        )
        return [self._account_to_dict(account)]

    async def get_account_summary(self) -> dict[str, Any]:
        account = await self._http_call(
            "get_account_summary",
            lambda: self._client.get_account(),
        )
        return {
            "account_id": str(account.id),
            "account_number": str(account.account_number),
            "currency": str(account.currency or "USD"),
            "net_liquidation_value": str(account.equity),
            "cash": str(account.cash),
            "buying_power": str(account.buying_power),
            "portfolio_value": str(account.portfolio_value),
        }

    async def get_positions(self) -> list[dict[str, Any]]:
        positions = await self._http_call(
            "get_positions",
            lambda: self._client.get_all_positions(),
        )
        return [self._position_to_dict(position) for position in positions]

    async def get_orders(self) -> list[dict[str, Any]]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = await self._http_call(
            "get_orders",
            lambda: self._client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL),
            ),
        )
        return [self._order_to_dict(order) for order in orders]

    async def _http_call(self, endpoint: str, fn: Callable[[], Any]) -> Any:
        try:
            result = await asyncio.to_thread(fn)
            ALPACA_HTTP_REQUESTS_TOTAL.labels(endpoint=endpoint, status="2xx").inc()
            return result
        except (RuntimeError, ConnectionError) as exc:
            ALPACA_HTTP_REQUESTS_TOTAL.labels(endpoint=endpoint, status="error").inc()
            ALPACA_ACCOUNT_READ_FAILURES_TOTAL.labels(kind=endpoint).inc()
            raise AlpacaClientError(endpoint=endpoint, message=str(exc)) from exc
        except Exception as exc:
            ALPACA_HTTP_REQUESTS_TOTAL.labels(endpoint=endpoint, status="error").inc()
            ALPACA_ACCOUNT_READ_FAILURES_TOTAL.labels(kind=endpoint).inc()
            raise AlpacaClientError(endpoint=endpoint, message=str(exc)) from exc

    def _account_to_dict(self, account: Any) -> dict[str, Any]:
        return {
            "account_id": str(account.id),
            "account_number": str(account.account_number),
            "currency": str(account.currency or "USD"),
            "status": str(account.status),
        }

    def _position_to_dict(self, position: Any) -> dict[str, Any]:
        exchange = str(position.exchange) if hasattr(position, "exchange") else ""
        return {
            "symbol": str(position.symbol),
            "exchange": exchange,
            "asset_class": str(position.asset_class)
            .upper()
            .replace("US_EQUITY", "STOCK"),
            "qty": str(position.qty),
            "avg_cost": str(position.avg_entry_price),
            "currency": "USD",
            "market_value": str(position.market_value),
            "unrealized_pnl": str(position.unrealized_pl or 0),
            "side": str(position.side),
        }

    def _order_to_dict(self, order: Any) -> dict[str, Any]:
        return {
            "id": str(order.id),
            "client_order_id": str(order.client_order_id or ""),
            "symbol": str(order.symbol),
            "qty": str(order.qty),
            "filled_qty": str(order.filled_qty or 0),
            "side": str(order.side).upper(),
            "type": str(order.type).upper(),
            "tif": str(order.time_in_force).upper(),
            "status": str(order.status).upper(),
            "limit_price": str(order.limit_price) if order.limit_price else "",
            "stop_price": str(order.stop_price) if order.stop_price else "",
        }
