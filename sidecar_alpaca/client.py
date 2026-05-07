"""Alpaca SDK isolation surface (M3 from Phase 7a)."""

from __future__ import annotations

import asyncio
import atexit
import signal
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
TradingStream: Any | None = None
APIError: Any | None = None
MarketOrderRequest: Any | None = None
LimitOrderRequest: Any | None = None
StopOrderRequest: Any | None = None
StopLimitOrderRequest: Any | None = None
TrailingStopOrderRequest: Any | None = None
MarketOnCloseOrderRequest: Any | None = None
MarketOnOpenOrderRequest: Any | None = None
LimitOnCloseOrderRequest: Any | None = None
LimitOnOpenOrderRequest: Any | None = None
ReplaceOrderRequest: Any | None = None
OrderClass: Any | None = None
TakeProfitRequest: Any | None = None
StopLossRequest: Any | None = None


_TRADING_CLIENTS: dict[tuple[str, str], Any] = {}
_TRADING_CLIENT_CREDENTIALS: dict[tuple[str, str], tuple[str, str, bool]] = {}


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
        except (Exception,) as exc:
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


def configure_trading_client(
    *,
    account_id: str,
    mode: str,
    api_key: str,
    api_secret: str,
    paper: bool,
) -> Any:
    """Create or refresh the lazily shared trading client for an account/mode."""
    key = (account_id, mode)
    credentials = (api_key, api_secret, paper)
    if (
        key not in _TRADING_CLIENTS
        or _TRADING_CLIENT_CREDENTIALS.get(key) != credentials
    ):
        trading_client = _load_trading_client_class()
        _TRADING_CLIENTS[key] = trading_client(
            api_key,
            api_secret,
            paper=paper,
            raw_data=False,
        )
        _TRADING_CLIENT_CREDENTIALS[key] = credentials
    return _TRADING_CLIENTS[key]


def get_trading_client(*, account_id: str, mode: str) -> Any | None:
    return _TRADING_CLIENTS.get((account_id, mode))


def clear_trading_clients() -> None:
    for client in list(_TRADING_CLIENTS.values()):
        close = getattr(client, "close", None)
        if close is None:
            continue
        try:
            close()
        except (RuntimeError, ConnectionError) as exc:
            log.warning("alpaca_trading_client_close_failed", exc_info=exc)
    _TRADING_CLIENTS.clear()
    _TRADING_CLIENT_CREDENTIALS.clear()


def load_order_request_classes() -> dict[str, Any]:
    global MarketOrderRequest
    global LimitOrderRequest
    global StopOrderRequest
    global StopLimitOrderRequest
    global TrailingStopOrderRequest
    global MarketOnCloseOrderRequest
    global MarketOnOpenOrderRequest
    global LimitOnCloseOrderRequest
    global LimitOnOpenOrderRequest
    global ReplaceOrderRequest
    global OrderClass
    global TakeProfitRequest
    global StopLossRequest

    if MarketOrderRequest is None or OrderClass is None:
        from alpaca.trading.enums import OrderClass as _OrderClass
        from alpaca.trading.requests import (
            LimitOnCloseOrderRequest as _LimitOnCloseOrderRequest,
            LimitOnOpenOrderRequest as _LimitOnOpenOrderRequest,
            LimitOrderRequest as _LimitOrderRequest,
            MarketOnCloseOrderRequest as _MarketOnCloseOrderRequest,
            MarketOnOpenOrderRequest as _MarketOnOpenOrderRequest,
            MarketOrderRequest as _MarketOrderRequest,
            ReplaceOrderRequest as _ReplaceOrderRequest,
            StopLossRequest as _StopLossRequest,
            StopLimitOrderRequest as _StopLimitOrderRequest,
            StopOrderRequest as _StopOrderRequest,
            TakeProfitRequest as _TakeProfitRequest,
            TrailingStopOrderRequest as _TrailingStopOrderRequest,
        )

        MarketOrderRequest = _MarketOrderRequest
        LimitOrderRequest = _LimitOrderRequest
        StopOrderRequest = _StopOrderRequest
        StopLimitOrderRequest = _StopLimitOrderRequest
        TrailingStopOrderRequest = _TrailingStopOrderRequest
        MarketOnCloseOrderRequest = _MarketOnCloseOrderRequest
        MarketOnOpenOrderRequest = _MarketOnOpenOrderRequest
        LimitOnCloseOrderRequest = _LimitOnCloseOrderRequest
        LimitOnOpenOrderRequest = _LimitOnOpenOrderRequest
        ReplaceOrderRequest = _ReplaceOrderRequest
        OrderClass = _OrderClass
        TakeProfitRequest = _TakeProfitRequest
        StopLossRequest = _StopLossRequest
    return {
        "MARKET": MarketOrderRequest,
        "LIMIT": LimitOrderRequest,
        "STOP": StopOrderRequest,
        "STOP_LIMIT": StopLimitOrderRequest,
        "TRAIL": TrailingStopOrderRequest,
        "TRAIL_LIMIT": TrailingStopOrderRequest,
        "MOC": MarketOnCloseOrderRequest,
        "MOO": MarketOnOpenOrderRequest,
        "LOC": LimitOnCloseOrderRequest,
        "LOO": LimitOnOpenOrderRequest,
        "REPLACE": ReplaceOrderRequest,
        "ORDER_CLASS": OrderClass,
        "BRACKET_TP": TakeProfitRequest,
        "BRACKET_SL": StopLossRequest,
    }


def load_trading_stream_class() -> Any:
    global TradingStream
    if TradingStream is None:
        from alpaca.trading.stream import TradingStream as _TradingStream

        TradingStream = _TradingStream
    return TradingStream


def load_api_error_class() -> Any:
    global APIError
    if APIError is None:
        from alpaca.common.exceptions import APIError as _APIError

        APIError = _APIError
    return APIError


def _load_trading_client_class() -> Any:
    global TradingClient
    if TradingClient is None:
        from alpaca.trading.client import TradingClient as _TradingClient

        TradingClient = _TradingClient
    return TradingClient


atexit.register(clear_trading_clients)

try:
    signal.signal(signal.SIGTERM, lambda _signum, _frame: clear_trading_clients())
except (ValueError, RuntimeError) as exc:
    log.warning("alpaca_trading_client_sigterm_hook_failed", exc_info=exc)
