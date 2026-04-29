"""gRPC handlers for the IBKR broker sidecar."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, Literal, cast

import grpc
import ib_async
import structlog
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

try:
    import aiolimiter
except ModuleNotFoundError:

    class _FallbackAsyncLimiter:
        def __init__(self, max_rate: int, time_period: float) -> None:
            self._max_rate = max_rate
            self._time_period = time_period
            self._timestamps: list[float] = []
            self._lock = asyncio.Lock()

        async def __aenter__(self) -> None:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    self._timestamps = [
                        timestamp
                        for timestamp in self._timestamps
                        if now - timestamp < self._time_period
                    ]
                    if len(self._timestamps) < self._max_rate:
                        self._timestamps.append(now)
                        return
                    wait_for = self._time_period - (now - self._timestamps[0])
                await asyncio.sleep(max(wait_for, 0.0))

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

    aiolimiter = SimpleNamespace(AsyncLimiter=_FallbackAsyncLimiter)

from sidecar import metrics
from sidecar._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar.normalize import (
    decimal_str,
    normalize_avg_cost,
    normalize_quote_currency,
    to_money_proto,
)
from sidecar.pnl_cache import PnLCache

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    from ib_async import (
        IB,
    )
    from ib_async import (  # type: ignore[import-untyped, unused-ignore]
        Contract as IbContract,
    )

    class _IbContract(Protocol):
        conId: object  # noqa: N815
        currency: object
        exchange: object
        symbol: object
        localSymbol: object  # noqa: N815
        secType: object  # noqa: N815

    class _IbPosition(Protocol):
        account: object
        contract: _IbContract
        marketPrice: object  # noqa: N815
        avgCost: object  # noqa: N815
        position: object

    class _IbOrder(Protocol):
        account: str
        action: str
        auxPrice: float | Decimal | None  # noqa: N815
        lmtPrice: float | Decimal | None  # noqa: N815
        orderId: int  # noqa: N815
        orderRef: str  # noqa: N815
        orderType: str  # noqa: N815
        permId: int  # noqa: N815
        tif: str
        totalQuantity: float  # noqa: N815

    class _IbOrderStatus(Protocol):
        avgFillPrice: float  # noqa: N815
        filled: float
        lastFillPrice: float  # noqa: N815
        remaining: float
        status: str
        whyHeld: str  # noqa: N815

    class _IbTradeLogEntry(Protocol):
        errorCode: int  # noqa: N815
        message: str
        status: str
        time: datetime

    class _IbTrade(Protocol):
        contract: _IbContract
        log: list[_IbTradeLogEntry]
        order: _IbOrder
        orderStatus: _IbOrderStatus  # noqa: N815

    class _IbExecution(Protocol):
        acctNumber: str  # noqa: N815
        avgPrice: float  # noqa: N815
        cumQty: float  # noqa: N815
        permId: int  # noqa: N815
        price: float
        side: str
        time: datetime

    class _IbFill(Protocol):
        contract: _IbContract
        execution: _IbExecution
        time: datetime


logger = structlog.get_logger(__name__)


class BrokerHandlers(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    # The generated BrokerServicer base class is typed Any (proto codegen
    # is not strict-clean). The `misc` ignore documents the intentional
    # subclass-of-Any rather than letting it leak into every caller.
    """Read-only broker service backed by an ib_async IB connection."""

    _search_cache: ClassVar[dict[str, tuple[float, list]]] = {}
    _search_limiter: ClassVar[aiolimiter.AsyncLimiter] = aiolimiter.AsyncLimiter(5, 1.0)

    def __init__(
        self,
        ib: IB,
        pnl_cache: PnLCache,
        label: str,
        version: str,
        last_tick_ref: dict[str, datetime],
        simulator_only: bool = True,
    ) -> None:
        self.ib: IB = ib
        self.pnl_cache: PnLCache = pnl_cache
        self.label: str = label
        self.version: str = version
        self.last_tick_ref: dict[str, datetime] = last_tick_ref
        self._place_locks: dict[str, asyncio.Lock] = {}
        self._simulator_only: bool = simulator_only
        # Maps SIM-<uuid> broker_order_id -> {"client_order_id": ..., "account_number": ...}.
        # Required by CancelOrder (SIM branch) to (1) recognize a SIM order without
        # int-parsing the prefix, (2) reconstruct the orderRef + account for the
        # synthetic cancellation event fired through ib.orderStatusEvent.emit().
        self._sim_orders: dict[str, dict[str, str]] = {}

    async def Health(  # noqa: N802 — gRPC servicer methods mirror proto rpc names
        self,
        request: broker_pb2.HealthRequest,
        context: object,
    ) -> broker_pb2.HealthResponse:
        del request, context

        gateway_connected: bool = False
        gateway_version: str = ""
        last_tick_at: Timestamp | None = self._last_tick_timestamp()

        try:
            gateway_connected = bool(self.ib.isConnected())
        except Exception as exc:
            logger.exception(
                "ibkr_health_connection_check_failed",
                label=self.label,
                error=str(exc),
            )

        if gateway_connected:
            try:
                gateway_version = str(self.ib.client.serverVersion())
            except Exception as exc:
                logger.exception(
                    "ibkr_health_server_version_failed",
                    label=self.label,
                    error=str(exc),
                )
                gateway_version = ""

        response: broker_pb2.HealthResponse = broker_pb2.HealthResponse(
            label=self.label,
            gateway_connected=gateway_connected,
            gateway_version=gateway_version,
            sidecar_version=self.version,
        )
        if last_tick_at is not None:
            response.last_tick_at.CopyFrom(last_tick_at)
        return response

    async def ListManagedAccounts(  # noqa: N802 — gRPC rpc name
        self,
        request: broker_pb2.Empty,
        context: object,
    ) -> broker_pb2.AccountsResponse:
        del request, context

        account_numbers: list[str] = []
        account_values: list[object] = []

        try:
            # ib_async populates managedAccounts() synchronously during
            # connectAsync; modern API has no reqManagedAccountsAsync().
            raw_accounts: object = self.ib.managedAccounts()
            managed_accounts: Iterable[object] = cast("Iterable[object]", raw_accounts)
            account_numbers = [str(account) for account in managed_accounts]
        except Exception as exc:
            logger.exception(
                "ibkr_list_managed_accounts_failed",
                label=self.label,
                error=str(exc),
            )

        try:
            # ibkr_sidecar startup subscribes via reqAccountUpdates(True, acct)
            # for each managed account so ib.accountValues() carries the BASE
            # tag (currency base) - accountSummary doesn't include BASE.
            raw_values: object = self.ib.accountValues()
            values: Iterable[object] = cast("Iterable[object]", raw_values)
            account_values = list(values)
        except Exception as exc:
            logger.exception(
                "ibkr_account_values_failed",
                label=self.label,
                error=str(exc),
            )

        accounts: list[broker_pb2.Account] = []
        for account_number in account_numbers:
            mode: int = broker_pb2.PAPER if account_number.startswith("D") else broker_pb2.LIVE
            currency_base: str = self._base_currency(account_values, account_number)
            account: broker_pb2.Account = broker_pb2.Account(
                account_number=account_number,
                mode=mode,  # type: ignore[arg-type]
                gateway_label=self.label,
                currency_base=currency_base,
            )
            accounts.append(account)

        return broker_pb2.AccountsResponse(accounts=accounts)

    async def GetAccountSummary(  # noqa: N802 — gRPC rpc name
        self,
        request: broker_pb2.AccountRef,
        context: object,
    ) -> broker_pb2.SummaryResponse:
        del context

        account_number: str = str(request.account_number)
        account_values: list[object] = []

        try:
            raw_values: object = self.ib.accountValues()
            values: Iterable[object] = cast("Iterable[object]", raw_values)
            account_values = [
                value for value in values if str(getattr(value, "account", "")) == account_number
            ]
        except Exception as exc:
            logger.exception(
                "ibkr_account_summary_values_failed",
                label=self.label,
                account_number=account_number,
                error=str(exc),
            )

        values_by_tag: dict[str, object] = {
            str(getattr(value, "tag", "")): value for value in account_values
        }

        summary: broker_pb2.Summary = broker_pb2.Summary(
            net_liquidation=self._money_for_tag(values_by_tag, "NetLiquidation"),
            total_cash=self._money_for_tag(values_by_tag, "TotalCashValue"),
            realized_pnl=self._money_for_tag(values_by_tag, "RealizedPnL"),
            unrealized_pnl=self._money_for_tag(values_by_tag, "UnrealizedPnL"),
            buying_power=self._money_for_tag(values_by_tag, "BuyingPower"),
        )
        return broker_pb2.SummaryResponse(summary=summary)

    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: object,
    ) -> broker_pb2.PositionsResponse:
        del context

        account_number: str = str(request.account_number)

        # Read from ib_async's cached positions snapshot. The cache is
        # populated by the connectAsync()-time reqPositions() subscription
        # and kept fresh by position()/positionEnd() callbacks. Calling
        # reqPositionsAsync() again per-RPC is the wrong pattern: the IBKR
        # API only allows one active reqPositions subscription per
        # connection, so the second concurrent call hangs until 5s gRPC
        # deadline (regression seen post-5b.1 deploy when the discoverer
        # fan-out invoked GetPositions every 30s x 22 accounts).
        try:
            raw_positions: object = self.ib.positions()  # type: ignore[attr-defined, unused-ignore]
            positions: list[object] = list(cast("Iterable[object]", raw_positions))
        except Exception as exc:
            logger.exception(
                "ibkr_positions_failed",
                label=self.label,
                account_number=account_number,
                error=str(exc),
            )
            return broker_pb2.PositionsResponse(positions=[])

        account_positions: list[object] = [
            position
            for position in positions
            if str(getattr(position, "account", "")) == account_number
        ]
        dropped_rows: int = len(positions) - len(account_positions)
        if dropped_rows > 0:
            logger.warning(
                "ibkr_positions_filtered_rows",
                account_number=account_number,
                dropped_rows=dropped_rows,
            )

        response_positions: list[broker_pb2.Position] = []
        for position in account_positions:
            ib_position: _IbPosition = cast("_IbPosition", position)
            contract: _IbContract = ib_position.contract
            conid: int = int(str(contract.conId))
            currency: str = str(contract.currency)
            exchange: str = str(contract.exchange)

            unrealized, realized, daily = self.pnl_cache.snapshot(account_number, conid)

            # ib_async.Position (from reqPositions) has no marketPrice;
            # PortfolioItem (from reqAccountUpdates) does. Since the BASE
            # round at startup unsubscribes accountUpdates after caching
            # currency_base, the portfolio cache may or may not be live.
            # Default to 0 when the field is missing - the positions table
            # schema doesn't store marketPrice (the discoverer only writes
            # qty/avg_cost/currency/multiplier/asset_class), and downstream
            # consumers tolerate 0 as "market data not yet snapshotted".
            raw_market_price: Decimal = Decimal(
                str(getattr(ib_position, "marketPrice", "0") or "0")
            )
            market_price: Decimal = normalize_quote_currency(raw_market_price, currency, exchange)
            raw_avg_cost: Decimal = Decimal(str(ib_position.avgCost))
            # TODO(task14): wire ConfigService.get(
            #     "broker", f"{account_number}.avg_cost_unit", default="pounds"
            # ) once sidecar can reach ConfigService
            config_unit: Literal["pounds", "pence"] = "pounds"
            avg_cost: Decimal = normalize_avg_cost(raw_avg_cost, account_number, config_unit)
            quantity_decimal: Decimal = Decimal(str(ib_position.position))

            response_positions.append(
                broker_pb2.Position(
                    contract=self._proto_contract(contract),
                    quantity=decimal_str(quantity_decimal),
                    avg_cost=to_money_proto(avg_cost, currency),
                    market_price=to_money_proto(market_price, currency),
                    market_value=to_money_proto(quantity_decimal * market_price, currency),
                    unrealized_pnl=to_money_proto(unrealized or Decimal("0"), currency),
                    realized_pnl_today=to_money_proto(realized or Decimal("0"), currency),
                    daily_pnl=to_money_proto(daily or Decimal("0"), currency),
                )
            )

        return broker_pb2.PositionsResponse(positions=response_positions)

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: object,
    ) -> broker_pb2.OrdersResponse:
        del context

        account_number: str = str(request.account_number)

        try:
            raw_open_trades: object = self.ib.openTrades()  # type: ignore[attr-defined, unused-ignore]
            raw_fills: object = self.ib.fills()  # type: ignore[attr-defined, unused-ignore]
            open_trades: list[_IbTrade] = [
                cast("_IbTrade", trade)
                for trade in cast("Iterable[object]", raw_open_trades)
                if str(getattr(getattr(trade, "order", None), "account", "")) == account_number
            ]
            today = datetime.now(tz=UTC).date()
            fills: list[_IbFill] = []
            for fill in cast("Iterable[object]", raw_fills):
                ib_fill: _IbFill = cast("_IbFill", fill)
                if ib_fill.time.date() == today and ib_fill.execution.acctNumber == account_number:
                    fills.append(ib_fill)
            open_perm_ids: set[int] = {trade.order.permId for trade in open_trades}

            orders: list[broker_pb2.Order] = [
                self._proto_order_from_trade(trade) for trade in open_trades
            ]
            orders.extend(
                self._proto_order_from_fill(fill)
                for fill in fills
                if fill.execution.permId not in open_perm_ids
            )
        except Exception as exc:
            logger.exception(
                "ibkr_orders_failed",
                label=self.label,
                account_number=account_number,
                error=str(exc),
            )
            return broker_pb2.OrdersResponse(orders=[])

        return broker_pb2.OrdersResponse(orders=orders)

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        context: object,
    ) -> broker_pb2.PlaceOrderResponse:
        del context

        if self._simulator_only:
            from uuid_utils import uuid7

            sim_id: str = f"SIM-{uuid7()}"
            self._sim_orders[sim_id] = {
                "client_order_id": request.client_order_id,
                "account_number": request.account_number,
            }
            logger.info(
                "place_order_simulated",
                client_order_id=request.client_order_id,
                sim_id=sim_id,
            )
            return broker_pb2.PlaceOrderResponse(
                broker_order_id=sim_id,
                status="Submitted",
            )

        lock: asyncio.Lock = self._place_locks.setdefault(request.client_order_id, asyncio.Lock())
        async with lock:
            raw_trades: object = self.ib.trades()  # type: ignore[attr-defined, unused-ignore]
            for trade in cast("Iterable[object]", raw_trades):
                ib_trade: _IbTrade = cast("_IbTrade", trade)
                if str(getattr(ib_trade.order, "orderRef", "")) == request.client_order_id:
                    return broker_pb2.PlaceOrderResponse(
                        broker_order_id=str(ib_trade.order.permId),
                        status=str(ib_trade.orderStatus.status),
                    )

            contract: object = await self._resolve_contract(request.conid)
            ib_order: object = self._build_ib_order(request)
            ib_order.orderRef = request.client_order_id
            ib_order.account = request.account_number
            trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(contract, ib_order),  # type: ignore[attr-defined, unused-ignore]
            )
            return broker_pb2.PlaceOrderResponse(
                broker_order_id=str(trade.order.permId),
                status=str(trade.orderStatus.status),
            )

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        context: object,
    ) -> broker_pb2.CancelOrderResponse:
        del context
        broker_order_id = request.broker_order_id

        if broker_order_id.startswith("SIM-"):
            sim_meta = self._sim_orders.pop(broker_order_id, None)
            if sim_meta is None:
                return broker_pb2.CancelOrderResponse(accepted=False)

            from decimal import Decimal
            from types import SimpleNamespace

            synthetic_trade = SimpleNamespace(
                order=SimpleNamespace(
                    permId=broker_order_id,
                    orderRef=sim_meta["client_order_id"],
                    account=sim_meta["account_number"],
                ),
                orderStatus=SimpleNamespace(
                    status="Cancelled",
                    filled=Decimal("0"),
                    avgFillPrice=Decimal("0"),
                ),
                contract=SimpleNamespace(
                    currency="USD", symbol="", exchange="",
                    conId=0, secType="STK", localSymbol="",
                ),
                fills=[],
                log=[],
            )
            self.ib.orderStatusEvent.emit(synthetic_trade)  # type: ignore[attr-defined, unused-ignore]
            metrics.broker_sim_cancel_echo_total.labels(label=self.label).inc()
            return broker_pb2.CancelOrderResponse(accepted=True)

        raw_trades: object = self.ib.openTrades()  # type: ignore[attr-defined, unused-ignore]
        for trade in cast("Iterable[object]", raw_trades):
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if (
                ib_trade.order.permId == int(broker_order_id)
                and ib_trade.order.account == request.account_number
            ):
                self.ib.cancelOrder(ib_trade.order)  # type: ignore[attr-defined, unused-ignore]
                return broker_pb2.CancelOrderResponse(accepted=True)

        return broker_pb2.CancelOrderResponse(accepted=False)

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: object,
    ) -> broker_pb2.ModifyOrderResponse:
        del context
        broker_order_id = request.broker_order_id

        if broker_order_id.startswith("SIM-"):
            # 5c v0.5.5 follow-up A: SIM modify echo (mirrors 5b.1 SIM cancel echo).
            # The broker_order_id stays the same — IB-side modify reuses the perm-id;
            # the simulator just emits a synthetic "Modified" status event so the
            # backend consumer transitions the orders row.
            sim_meta = self._sim_orders.get(broker_order_id)
            if sim_meta is None:
                raise grpc.RpcError(
                    grpc.StatusCode.NOT_FOUND,
                    f"sim order {broker_order_id} not registered (sidecar restart drops the map)",
                )

            from decimal import Decimal
            from types import SimpleNamespace

            synthetic_trade = SimpleNamespace(
                order=SimpleNamespace(
                    permId=broker_order_id,
                    orderRef=sim_meta["client_order_id"],
                    account=sim_meta["account_number"],
                ),
                orderStatus=SimpleNamespace(
                    status="Modified",
                    filled=Decimal("0"),
                    avgFillPrice=Decimal("0"),
                ),
                contract=SimpleNamespace(
                    currency="USD", symbol="", exchange="",
                    conId=0, secType="STK", localSymbol="",
                ),
                fills=[],
                log=[],
            )
            self.ib.orderStatusEvent.emit(synthetic_trade)  # type: ignore[attr-defined, unused-ignore]
            return broker_pb2.ModifyOrderResponse(
                broker_order_id=broker_order_id,
                status="Modified",
            )

        try:
            target_perm_id = int(broker_order_id)
        except ValueError as exc:
            raise grpc.RpcError(
                grpc.StatusCode.INVALID_ARGUMENT, f"invalid broker_order_id: {exc}"
            ) from exc

        raw_trades: object = self.ib.openTrades()  # type: ignore[attr-defined, unused-ignore]
        target_trade: _IbTrade | None = None
        for trade in cast("Iterable[object]", raw_trades):
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if (
                ib_trade.order.permId == target_perm_id
                and ib_trade.order.account == request.account_number
            ):
                target_trade = ib_trade
                break

        if target_trade is None:
            raise grpc.RpcError(
                grpc.StatusCode.NOT_FOUND, f"order {broker_order_id} not in openTrades"
            )

        ib_order = target_trade.order
        ib_order.totalQuantity = float(request.qty)
        if request.HasField("limit_price"):
            ib_order.lmtPrice = float(request.limit_price.value)
        if request.HasField("stop_price"):
            ib_order.auxPrice = float(request.stop_price.value)
        if request.tif:
            ib_order.tif = request.tif
        try:
            contract: object = await self._resolve_contract(request.contract.conid)
            new_trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(contract, ib_order),  # type: ignore[attr-defined, unused-ignore]
            )
        except Exception as exc:
            raise grpc.RpcError(grpc.StatusCode.UNKNOWN, f"placeOrder failed: {exc}") from exc

        return broker_pb2.ModifyOrderResponse(
            broker_order_id=str(new_trade.order.permId),
            status=str(new_trade.orderStatus.status),
        )

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: object,
    ) -> broker_pb2.PlaceBracketResponse:
        del context
        parent_contract: object = await self._resolve_contract(request.parent.conid)
        parent_order = self._build_ib_order(request.parent)
        parent_order.transmit = False
        parent_order.orderRef = request.parent.client_order_id
        parent_order.account = request.parent.account_number

        if self._simulator_only:
            from uuid_utils import uuid7

            parent_sim = f"SIM-{uuid7()}"
            self._sim_orders[parent_sim] = {
                "client_order_id": request.parent.client_order_id,
                "account_number": request.parent.account_number,
            }
            sl_sim = ""
            tp_sim = ""
            if request.has_stop_loss:
                sl_sim = f"SIM-{uuid7()}"
                self._sim_orders[sl_sim] = {
                    "client_order_id": request.stop_loss.client_order_id,
                    "account_number": request.stop_loss.account_number,
                    "parent_sim_id": parent_sim,
                }
            if request.has_take_profit:
                tp_sim = f"SIM-{uuid7()}"
                self._sim_orders[tp_sim] = {
                    "client_order_id": request.take_profit.client_order_id,
                    "account_number": request.take_profit.account_number,
                    "parent_sim_id": parent_sim,
                }
            return broker_pb2.PlaceBracketResponse(
                parent_broker_order_id=parent_sim,
                stop_loss_broker_order_id=sl_sim,
                take_profit_broker_order_id=tp_sim,
                status="Submitted",
            )

        # Real broker path. Place parent first (transmit=False) so ib_async
        # assigns parent.orderId synchronously; then wire children's
        # parentId/ocaGroup before placing them. Last child gets transmit=True
        # to atomically submit the entire bracket per IBKR docs (OCA type 1
        # = "cancel all remaining orders with block").
        parent_trade: _IbTrade = cast(
            "_IbTrade",
            self.ib.placeOrder(parent_contract, parent_order),  # type: ignore[attr-defined, unused-ignore]
        )
        parent_order_id_int = parent_order.orderId

        sl_perm_id = ""
        tp_perm_id = ""
        children_to_place: list[tuple[object, object, str]] = []
        if request.has_stop_loss:
            sl_contract = await self._resolve_contract(request.stop_loss.conid)
            sl_order = self._build_ib_order(request.stop_loss)
            sl_order.parentId = parent_order_id_int
            sl_order.ocaGroup = request.oca_group
            sl_order.ocaType = 1
            sl_order.orderRef = request.stop_loss.client_order_id
            sl_order.account = request.stop_loss.account_number
            children_to_place.append((sl_contract, sl_order, "stop_loss"))
        if request.has_take_profit:
            tp_contract = await self._resolve_contract(request.take_profit.conid)
            tp_order = self._build_ib_order(request.take_profit)
            tp_order.parentId = parent_order_id_int
            tp_order.ocaGroup = request.oca_group
            tp_order.ocaType = 1
            tp_order.orderRef = request.take_profit.client_order_id
            tp_order.account = request.take_profit.account_number
            children_to_place.append((tp_contract, tp_order, "take_profit"))

        for i, (_c, child_order, _leg) in enumerate(children_to_place):
            child_order.transmit = (i == len(children_to_place) - 1)

        for child_contract, child_order, leg in children_to_place:
            child_trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(child_contract, child_order),  # type: ignore[attr-defined, unused-ignore]
            )
            if leg == "stop_loss":
                sl_perm_id = str(child_trade.order.permId)
            else:
                tp_perm_id = str(child_trade.order.permId)

        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=str(parent_trade.order.permId),
            stop_loss_broker_order_id=sl_perm_id,
            take_profit_broker_order_id=tp_perm_id,
            status=str(parent_trade.orderStatus.status),
        )

    async def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: object,
    ) -> object:
        queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue(maxsize=10_000)

        def _on_status(trade: object) -> None:
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if ib_trade.order.account != request.account_number:
                return
            try:
                queue.put_nowait(
                    self._proto_event_from_trade(ib_trade, kind="status", exec_id="")
                )
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        def _on_exec_details(trade: object, fill: object = None) -> None:
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if ib_trade.order.account != request.account_number:
                return
            try:
                exec_id = str(getattr(getattr(fill, "execution", None), "execId", ""))
                queue.put_nowait(
                    self._proto_event_from_trade(
                        ib_trade, kind="exec_details", exec_id=exec_id
                    )
                )
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        def _on_commission_report(
            trade: object, fill: object, commission_report: object
        ) -> None:
            ib_trade: _IbTrade = cast("_IbTrade", trade)
            if ib_trade.order.account != request.account_number:
                return
            try:
                exec_id = str(getattr(commission_report, "execId", ""))
                commission = str(getattr(commission_report, "commission", "0"))
                currency = str(getattr(commission_report, "currency", ""))
                msg = self._proto_event_from_trade(
                    ib_trade, kind="commission_report", exec_id=exec_id
                )
                payload = json.loads(msg.raw_payload) if msg.raw_payload else {}
                payload["commission"] = commission
                payload["commission_currency"] = currency
                msg.raw_payload = json.dumps(payload)
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        commission_report_event = getattr(self.ib, "commissionReportEvent", None)
        self.ib.orderStatusEvent += _on_status  # type: ignore[attr-defined, unused-ignore]
        self.ib.execDetailsEvent += _on_exec_details  # type: ignore[attr-defined, unused-ignore]
        if commission_report_event is not None:
            self.ib.commissionReportEvent += _on_commission_report  # type: ignore[attr-defined, unused-ignore]
        try:
            while not context.cancelled():  # type: ignore[attr-defined]
                yield await queue.get()
        finally:
            self.ib.orderStatusEvent -= _on_status  # type: ignore[attr-defined, unused-ignore]
            self.ib.execDetailsEvent -= _on_exec_details  # type: ignore[attr-defined, unused-ignore]
            if commission_report_event is not None:
                self.ib.commissionReportEvent -= _on_commission_report  # type: ignore[attr-defined, unused-ignore]

    async def GetContract(  # noqa: N802
        self,
        request: broker_pb2.ContractRef,
        context: object,
    ) -> broker_pb2.ContractResponse:
        del context

        try:
            from ib_async import (
                Contract as RuntimeIbContract,  # type: ignore[import-untyped, unused-ignore]
            )

            contract: IbContract = RuntimeIbContract(conId=int(request.conid))
            raw_qualified: object = await self.ib.qualifyContractsAsync(contract)  # type: ignore[attr-defined, unused-ignore]
            qualified: list[object] = list(cast("Iterable[object]", raw_qualified))
            proto_contract: broker_pb2.Contract = self._proto_contract(
                cast("_IbContract", qualified[0])
            )
        except Exception as exc:
            logger.exception(
                "ibkr_contract_failed",
                label=self.label,
                conid=str(request.conid),
                error=str(exc),
            )
            return broker_pb2.ContractResponse()

        return broker_pb2.ContractResponse(contract=proto_contract)

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: object,
    ) -> broker_pb2.SearchContractsResponse:
        del context

        cache_key = hashlib.sha256(f"{request.query}|{request.asset_class}".encode()).hexdigest()
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            mtime, contracts = cached
            if time.monotonic() - mtime < 300:
                return broker_pb2.SearchContractsResponse(contracts=contracts)

        async with self._search_limiter:
            ib_contract = ib_async.Contract(
                symbol=request.query,
                secType=request.asset_class or "STK",
            )
            details = await self.ib.reqContractDetailsAsync(ib_contract)  # type: ignore[attr-defined, unused-ignore]

        contracts = [self._proto_contract_from_details(detail) for detail in details]
        self._search_cache[cache_key] = (time.monotonic(), contracts)
        return broker_pb2.SearchContractsResponse(contracts=contracts)

    async def _resolve_contract(self, conid: str) -> object:
        from ib_async import Contract as RuntimeIbContract  # type: ignore[import-untyped]

        contract: object = RuntimeIbContract(conId=int(conid))
        raw_qualified: object = await self.ib.qualifyContractsAsync(contract)  # type: ignore[attr-defined, unused-ignore]
        qualified: list[object] = list(cast("Iterable[object]", raw_qualified))
        return qualified[0] if qualified else contract

    def _build_ib_order(self, request: broker_pb2.PlaceOrderRequest) -> object:
        side: str = "BUY" if request.side == "BUY" else "SELL"
        qty: float = float(request.qty)
        order_type: str = request.order_type

        if order_type == "MARKET":
            from ib_async import MarketOrder  # type: ignore[import-untyped]

            order: object = MarketOrder(side, qty)
        elif order_type == "LIMIT":
            from ib_async import LimitOrder  # type: ignore[import-untyped]

            order = LimitOrder(side, qty, float(request.limit_price))
        elif order_type == "STOP":
            from ib_async import StopOrder  # type: ignore[import-untyped]

            order = StopOrder(side, qty, float(request.stop_price))
        else:
            raise ValueError(f"Unsupported order_type: {order_type}")

        if request.tif:
            order.tif = request.tif
        return order

    @staticmethod
    def _serialize_trade(trade: _IbTrade) -> dict[str, object]:
        return {
            "perm_id": trade.order.permId,
            "order_ref": trade.order.orderRef,
            "account": trade.order.account,
            "status": trade.orderStatus.status,
            "filled": str(trade.orderStatus.filled),
            "remaining": str(trade.orderStatus.remaining),
            "avg_fill_price": str(trade.orderStatus.avgFillPrice or 0),
            "last_fill_price": str(trade.orderStatus.lastFillPrice or 0),
            "why_held": trade.orderStatus.whyHeld or "",
            "log": [
                {
                    "time": entry.time.isoformat(),
                    "status": entry.status,
                    "message": entry.message,
                    "error_code": entry.errorCode,
                }
                for entry in trade.log
            ],
        }

    def _proto_event_from_trade(
        self,
        trade: _IbTrade,
        *,
        kind: str = "status",
        exec_id: str = "",
    ) -> broker_pb2.OrderEventMessage:
        raw: dict[str, object] = self._serialize_trade(trade)
        message = broker_pb2.OrderEventMessage(
            broker_order_id=str(trade.order.permId),
            client_order_id=trade.order.orderRef or "",
            status=trade.orderStatus.status,
            filled_qty=str(trade.orderStatus.filled),
            avg_fill_price=str(trade.orderStatus.avgFillPrice or 0),
            raw_payload=json.dumps(raw),
            exec_id=exec_id,
            kind=kind,
        )
        message.event_at.FromDatetime(datetime.now(UTC))
        return message

    def _proto_contract(self, ib_contract: _IbContract) -> broker_pb2.Contract:
        raw_mult = getattr(ib_contract, "multiplier", "")
        return broker_pb2.Contract(
            symbol=str(ib_contract.symbol),
            exchange=str(ib_contract.exchange),
            currency=str(ib_contract.currency),
            asset_class=self._asset_class(str(ib_contract.secType)),
            conid=str(ib_contract.conId),
            local_symbol=str(ib_contract.localSymbol),
            multiplier=str(raw_mult) if raw_mult else "1",
        )

    def _proto_contract_from_details(self, details: object) -> broker_pb2.Contract:
        contract = details.contract
        raw_mult = getattr(contract, "multiplier", "")
        return broker_pb2.Contract(
            conid=str(contract.conId),
            symbol=contract.symbol,
            exchange=contract.primaryExchange or contract.exchange,
            currency=contract.currency,
            asset_class=self._asset_class(contract.secType),
            multiplier=str(raw_mult) if raw_mult else "1",
        )

    def _proto_order_from_trade(self, trade: _IbTrade) -> broker_pb2.Order:
        currency: str = str(trade.contract.currency)
        order: broker_pb2.Order = broker_pb2.Order(
            order_id=str(trade.order.permId or trade.order.orderId or ""),
            contract=self._proto_contract(trade.contract),
            side=self._order_side(trade.order.action),
            order_type=self._order_type(trade.order.orderType),
            quantity=decimal_str(trade.order.totalQuantity),
            limit_price=broker_pb2.Money(value=str(trade.order.lmtPrice), currency=currency),
            stop_price=broker_pb2.Money(value=str(trade.order.auxPrice), currency=currency),
            time_in_force=self._time_in_force(trade.order.tif),
            status=self._order_status(trade.orderStatus.status),
            quantity_filled=decimal_str(trade.orderStatus.filled),
            avg_fill_price=broker_pb2.Money(
                value=str(trade.orderStatus.avgFillPrice),
                currency=currency,
            ),
        )
        if trade.log:
            order.submitted_at.FromDatetime(trade.log[0].time)
            order.updated_at.FromDatetime(trade.log[-1].time)
        return order

    def _proto_order_from_fill(self, fill: _IbFill) -> broker_pb2.Order:
        currency: str = str(fill.contract.currency)
        order: broker_pb2.Order = broker_pb2.Order(
            order_id=str(fill.execution.permId or ""),
            contract=self._proto_contract(fill.contract),
            side=self._order_side(fill.execution.side),
            quantity=decimal_str(fill.execution.cumQty),
            limit_price=broker_pb2.Money(value=str(fill.execution.price), currency=currency),
            status=broker_pb2.FILLED,
            quantity_filled=decimal_str(fill.execution.cumQty),
            avg_fill_price=broker_pb2.Money(
                value=str(fill.execution.avgPrice),
                currency=currency,
            ),
        )
        order.submitted_at.FromDatetime(fill.execution.time)
        order.updated_at.FromDatetime(fill.execution.time)
        return order

    def _last_tick_timestamp(self) -> Timestamp | None:
        tick_at: datetime | None = self.last_tick_ref.get("t")
        if tick_at is None:
            tick_at = self.last_tick_ref.get(self.label)
        if tick_at is None:
            return None

        timestamp: Timestamp = Timestamp()
        try:
            timestamp.FromDatetime(tick_at)
        except Exception as exc:
            logger.exception("ibkr_last_tick_timestamp_failed", label=self.label, error=str(exc))
            return None
        return timestamp

    def _base_currency(self, account_values: Iterable[object], account_number: str) -> str:
        # Per the IBKR TWS API: BASE is a CURRENCY meta-marker, not a tag.
        # Each per-currency tag (NetLiquidation, CashBalance, etc.) ships a
        # row with currency='BASE' (meta) and a row with currency='<X>' where
        # <X> is a real ISO code. The account's actual base currency is the
        # `<X>` on the NetLiquidation row (IBKR reports NLV in account base
        # currency only). See sidecar/scripts/base_round_preflight.py for the
        # empirical evidence (commit 97efe0f).
        #
        # Proto contract (broker/v1/broker.proto §Account.currency_base): "NOT
        # defaulted." Return empty string if no NetLiquidation row exists yet
        # so the backend can distinguish "not loaded" from a real currency.
        for value in account_values:
            tag: str = str(getattr(value, "tag", ""))
            account: str = str(getattr(value, "account", ""))
            if tag != "NetLiquidation" or account != account_number:
                continue
            currency: str = str(getattr(value, "currency", ""))
            if currency and currency != "BASE":
                return currency
        return ""

    def _money_for_tag(self, values_by_tag: dict[str, object], tag: str) -> broker_pb2.Money:
        account_value: object | None = values_by_tag.get(tag)
        if account_value is None:
            return to_money_proto(Decimal("0"), "USD")

        raw_value: str = str(getattr(account_value, "value", "0"))
        currency: str = str(getattr(account_value, "currency", "")) or "USD"
        try:
            value: Decimal = Decimal(decimal_str(Decimal(raw_value)))
        except (InvalidOperation, ValueError) as exc:
            logger.exception(
                "ibkr_money_decimal_parse_failed",
                tag=tag,
                value=raw_value,
                error=str(exc),
            )
            value = Decimal("0")

        return to_money_proto(value, currency)

    def _order_side(self, side: str) -> broker_pb2.OrderSide:
        sides: dict[str, broker_pb2.OrderSide] = {
            "BUY": broker_pb2.BUY,
            "SELL": broker_pb2.SELL,
        }
        return sides.get(side, broker_pb2.SIDE_UNSPECIFIED)

    def _order_type(self, order_type: str) -> broker_pb2.OrderType:
        order_types: dict[str, broker_pb2.OrderType] = {
            "MKT": broker_pb2.MARKET,
            "LMT": broker_pb2.LIMIT,
            "STP": broker_pb2.STOP,
            "STP LMT": broker_pb2.STOP_LIMIT,
        }
        return order_types.get(order_type, broker_pb2.TYPE_UNSPECIFIED)

    def _time_in_force(self, time_in_force: str) -> broker_pb2.TimeInForce:
        time_in_forces: dict[str, broker_pb2.TimeInForce] = {
            "DAY": broker_pb2.DAY,
            "GTC": broker_pb2.GTC,
            "IOC": broker_pb2.IOC,
            "FOK": broker_pb2.FOK,
        }
        return time_in_forces.get(time_in_force, broker_pb2.TIF_UNSPECIFIED)

    def _order_status(self, status: str) -> broker_pb2.OrderStatus:
        statuses: dict[str, broker_pb2.OrderStatus] = {
            "Submitted": broker_pb2.SUBMITTED,
            "PendingSubmit": broker_pb2.SUBMITTED,
            "PreSubmitted": broker_pb2.PENDING,
            "Filled": broker_pb2.FILLED,
            "Cancelled": broker_pb2.CANCELLED,
            "ApiCancelled": broker_pb2.CANCELLED,
            "Inactive": broker_pb2.REJECTED,
        }
        return statuses.get(status, broker_pb2.STATUS_UNSPECIFIED)

    def _asset_class(self, sec_type: str) -> broker_pb2.AssetClass:
        asset_classes: dict[str, broker_pb2.AssetClass] = {
            "STK": broker_pb2.STOCK,
            "ETF": broker_pb2.ETF,
            "OPT": broker_pb2.OPTION,
            "FUT": broker_pb2.FUTURE,
            "CASH": broker_pb2.FOREX,
            "CRYPTO": broker_pb2.CRYPTO,
            "BOND": broker_pb2.BOND,
            "FUND": broker_pb2.MUTUAL_FUND,
            "WAR": broker_pb2.WARRANT,
        }
        return asset_classes.get(sec_type, broker_pb2.ASSET_UNSPECIFIED)
