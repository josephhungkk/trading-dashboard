"""gRPC handlers for the IBKR broker sidecar."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

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

from sidecar_ibkr import metrics
from sidecar_ibkr._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar_ibkr.normalize import (
    decimal_str,
    normalize_avg_cost,
    normalize_quote_currency,
    to_money_proto,
)
from sidecar_ibkr.pnl_cache import PnLCache

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
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

    class _IbHistoricalBar(Protocol):
        date: datetime | int | float | str
        open: object
        high: object
        low: object
        close: object
        volume: object


logger = structlog.get_logger(__name__)


def _ib_decimal_str(value: object) -> str:
    """Phase 10a B9 reviewer fix: explicit None-check on ib_async Decimal-or-None.

    ``or "0"`` is unsafe because Decimal("0") is falsy and would also fall
    through to "0" - which happens to be the intended default but is the
    wrong reason. Use ``is not None`` so a real zero stays as "0" and a
    missing field also becomes "0", but the intent is explicit.
    """
    return "0" if value is None else str(value)


async def _abort_rpc(context: object, code: grpc.StatusCode, details: str) -> None:
    abort = getattr(context, "abort", None)
    if abort is not None:
        result = abort(code, details)
        if inspect.isawaitable(result):
            await result
    raise grpc.RpcError(code, details)


type _TickCallback = Callable[[broker_pb2.QuoteMessage], None]

_STREAM_QUEUE_MAX = 2048
_CALL_SUBS_MAX = 500


class _PacingTokenBucket:
    """Per-process token bucket for IBKR historical-data pacing.
    Capacity 50; refill 50 every 600s; reserve 10 for ad-hoc (non-prewarm) callers.
    """

    def __init__(
        self,
        capacity: int = 50,
        refill_window_seconds: int = 600,
        reserved: int = 10,
    ) -> None:
        self._capacity: int = capacity
        self._refill_window_seconds: int = refill_window_seconds
        self._reserved: int = reserved
        self._tokens: int = capacity
        self._last_refill_at: float = time.monotonic()
        self._next_refill_at: float = time.monotonic() + refill_window_seconds
        self._cooldown_until: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self, *, reserve: bool = False) -> None:
        while True:
            wait_seconds: float = 0.0
            async with self._lock:
                now = time.monotonic()
                self._refill_if_due(now)
                if now < self._cooldown_until:
                    wait_seconds = self._cooldown_until - now
                elif self._can_acquire(reserve=reserve):
                    self._tokens -= 1
                    return
                else:
                    wait_seconds = max(self._next_refill_at - now, 0.0)

            await asyncio.sleep(wait_seconds)
            async with self._lock:
                now = time.monotonic()
                if now < self._next_refill_at:
                    self._next_refill_at = now
                self._refill_if_due(now)

    async def release_on_pacing_violation(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = 0
            self._cooldown_until = now + 60.0
            self._next_refill_at = self._cooldown_until

    def _can_acquire(self, *, reserve: bool) -> bool:
        if reserve:
            return self._tokens > 0
        return self._tokens > self._reserved

    def _refill_if_due(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_refill_at)
        tokens_to_add = (elapsed / self._refill_window_seconds) * self._capacity
        if tokens_to_add >= 1.0:
            self._tokens = min(self._capacity, self._tokens + int(tokens_to_add))
            self._last_refill_at = now


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
        started_at: datetime | None = None,
    ) -> None:
        self.ib: IB = ib
        self.pnl_cache: PnLCache = pnl_cache
        self.label: str = label
        self.version: str = version
        self.last_tick_ref: dict[str, datetime] = last_tick_ref
        self._started_at: datetime = started_at or datetime.now(UTC)
        self._place_locks: dict[str, asyncio.Lock] = {}
        self._simulator_only: bool = simulator_only
        # Maps SIM-<uuid> broker_order_id -> {"client_order_id": ..., "account_number": ...}.
        # Required by CancelOrder (SIM branch) to (1) recognize a SIM order without
        # int-parsing the prefix, (2) reconstruct the orderRef + account for the
        # synthetic cancellation event.
        self._sim_orders: dict[str, dict[str, str]] = {}
        # 5c v0.5.5 fix: ib.orderStatusEvent.emit() does NOT trigger externally
        # registered listeners under ib_async's eventkit (cross-loop / IB-callback-
        # only dispatch). SIM echo paths bypass it by writing directly to the
        # OrderEvent gRPC stream's per-account queue list. Keyed by account_number.
        self._order_event_queues: dict[str, list[asyncio.Queue[broker_pb2.OrderEventMessage]]] = {}
        self._pacing_bucket: _PacingTokenBucket = _PacingTokenBucket()
        # Phase 10a C2: PreviewOrder LRU cache (key = idempotency_key,
        # value = (timestamp_seconds, PreviewOrderResponse)). 60s TTL,
        # 1000-entry cap. Spec §5 [M6]: identical preview requests collapse
        # to one whatIf round-trip via content-hash idempotency.
        # B9 reviewer fix: OrderedDict + move_to_end for O(1) LRU eviction
        # (was O(n) dict + min() scan). Per-key asyncio.Lock prevents two
        # concurrent same-key calls from both missing the cache and both
        # issuing whatIf round-trips (HIGH finding from code-quality reviewer).
        self._preview_lru: OrderedDict[
            str, tuple[float, broker_pb2.PreviewOrderResponse]
        ] = OrderedDict()
        self._preview_key_locks: dict[str, asyncio.Lock] = {}

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

        ts = Timestamp()
        ts.FromDatetime(self._started_at)
        response: broker_pb2.HealthResponse = broker_pb2.HealthResponse(
            label=self.label,
            gateway_connected=gateway_connected,
            gateway_version=gateway_version,
            sidecar_version=self.version,
            started_at=ts,
            broker_id="ibkr",
        )
        if last_tick_at is not None:
            response.last_tick_at.CopyFrom(last_tick_at)
        return response

    async def Configure(  # noqa: N802
        self,
        request: broker_pb2.ConfigureRequest,
        context: object,
    ) -> broker_pb2.ConfigureResponse:
        del request, context
        # IBKR sidecars get their config via CLI flags + mTLS material, not the
        # Configure RPC. Return ok=True so the backend's BrokerConfigurer flow
        # treats IBKR labels as already-configured.
        return broker_pb2.ConfigureResponse(ok=True, detail="")

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
        cash_amount = request.cash_amount
        logger.debug("place_order_cash_amount_received", cash_amount=cash_amount)

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

            # Conid qualification resolves the concrete IBKR contract, including
            # secType="FUT" for futures, so PlaceOrder does not need an asset
            # class-specific branch here.
            contract: object = await self._resolve_contract(request.conid)
            ib_order: object = self._build_ib_order(request)
            ib_order.orderRef = request.client_order_id
            ib_order.account = request.account_number
            if request.oco_group_id:
                from sidecar_ibkr.order_builder import attach_oca_group

                attach_oca_group(ib_order, request.oco_group_id)
            trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(contract, ib_order),  # type: ignore[attr-defined, unused-ignore]
            )
            return broker_pb2.PlaceOrderResponse(
                broker_order_id=str(trade.order.permId),
                status=str(trade.orderStatus.status),
            )

    async def PreviewOrder(  # noqa: N802
        self,
        request: broker_pb2.PreviewOrderRequest,
        context: object,
    ) -> broker_pb2.PreviewOrderResponse:
        """Phase 10a C2 (M7): pre-trade margin/risk preview via WhatIf.

        Caches by content-hash idempotency_key (60s TTL, 1000-entry cap)
        so repeated preview requests for the same payload don't re-issue
        whatIf round-trips. On filledEvent timeout (>2.5s, leaving 500ms
        for serialization to caller), aborts with DEADLINE_EXCEEDED so
        the gate's _check_margin can translate per the asymmetric fail
        policy (spec §4 [H4]).
        """
        logger.debug(
            "preview_order_received",
            idempotency_key=request.idempotency_key,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
        )

        key = request.idempotency_key
        # Per-key lock: B9 reviewer HIGH fix - two concurrent same-key calls
        # would both miss the cache + both whatIf at IBKR.
        lock = self._preview_key_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._preview_lru.get(key)
            if cached is not None:
                timestamp, cached_response = cached
                if time.time() - timestamp < 60:
                    self._preview_lru.move_to_end(key)  # LRU touch
                    logger.debug("preview_order_cache_hit", idempotency_key=key)
                    return cached_response

            contract = await self._resolve_contract(request.symbol)
            what_if_order = self._build_what_if_order(request)
            trade = self.ib.placeOrder(contract, what_if_order)  # type: ignore[attr-defined]

            try:
                await asyncio.wait_for(trade.filledEvent.wait(), timeout=2.5)
            except asyncio.TimeoutError:
                logger.warning(
                    "preview_order_whatif_timeout",
                    idempotency_key=key,
                    symbol=request.symbol,
                )
                await context.abort(
                    grpc.StatusCode.DEADLINE_EXCEEDED,
                    "WhatIf timeout (2.5s)",
                )
                return broker_pb2.PreviewOrderResponse()  # unreachable

            # B9 reviewer MED fix: TWS-level rejection signals via
            # orderStatus.status="Rejected" or non-empty warningText.
            # Map authoritative broker reject -> accepted=False so the
            # backend gate's _check_margin can BLOCK with margin_rejected.
            status_str = str(getattr(trade.orderStatus, "status", "") or "")
            warning_text = str(getattr(trade.orderStatus, "warningText", "") or "")
            tws_rejected = status_str == "Rejected"
            response = broker_pb2.PreviewOrderResponse(
                accepted=not tws_rejected,
                reject_reason=warning_text if tws_rejected else "",
                initial_margin=_ib_decimal_str(trade.orderStatus.initMarginAfter),
                maintenance_margin=_ib_decimal_str(trade.orderStatus.maintMarginAfter),
                commission=_ib_decimal_str(trade.orderStatus.commission),
                available_funds_after=_ib_decimal_str(
                    trade.orderStatus.equityWithLoanAfter
                ),
                buying_power_after=_ib_decimal_str(trade.orderStatus.equityWithLoanAfter),
                warnings=[warning_text] if warning_text and not tws_rejected else [],
                raw_provider_payload=json.dumps(
                    {"warningText": warning_text, "status": status_str}
                ),
            )

            # B9 reviewer HIGH fix: OrderedDict + popitem(last=False) for
            # O(1) LRU eviction (was O(n) min() scan).
            if len(self._preview_lru) >= 1000:
                self._preview_lru.popitem(last=False)
            self._preview_lru[key] = (time.time(), response)
            self._preview_lru.move_to_end(key)
            return response

    def _build_what_if_order(self, request: broker_pb2.PreviewOrderRequest) -> object:
        """Build an ib_async Order with whatIf=True from a PreviewOrderRequest."""
        from ib_async import LimitOrder, MarketOrder, StopOrder

        side = "BUY" if request.side == "buy" else "SELL"
        qty = float(request.qty)
        if request.order_type == "MKT":
            order = MarketOrder(side, qty)
        elif request.order_type == "LMT":
            order = LimitOrder(side, qty, float(request.limit_price or "0"))
        elif request.order_type in ("STP", "STOP"):
            order = StopOrder(side, qty, float(request.stop_price or "0"))
        else:
            order = LimitOrder(side, qty, float(request.limit_price or "0"))
        order.whatIf = True
        order.account = request.account_hash
        return order

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

            # 5c v0.5.5 fix: write directly to OrderEvent queues (see ModifyOrder).
            self._dispatch_sim_event(
                account_number=sim_meta["account_number"],
                broker_order_id=broker_order_id,
                client_order_id=sim_meta["client_order_id"],
                status="cancelled",
            )
            metrics.broker_sim_cancel_echo_total.labels(label=self.label).inc()
            logger.info(
                "sim_cancel_echo_emitted",
                label=self.label,
                broker_order_id=broker_order_id,
                client_order_id=sim_meta["client_order_id"],
                account_number=sim_meta["account_number"],
            )
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
        broker_order_id = request.broker_order_id

        if broker_order_id.startswith("SIM-"):
            # 5c v0.5.5 follow-up A: SIM modify echo (mirrors 5b.1 SIM cancel echo).
            # The broker_order_id stays the same — IB-side modify reuses the perm-id;
            # the simulator just emits a synthetic "Modified" status event so the
            # backend consumer transitions the orders row.
            sim_meta = self._sim_orders.get(broker_order_id)
            if sim_meta is None:
                await _abort_rpc(
                    context,
                    grpc.StatusCode.NOT_FOUND,
                    f"sim order {broker_order_id} not registered (sidecar restart drops the map)",
                )
                return broker_pb2.ModifyOrderResponse()

            # 5c v0.5.5 fix: write the synthetic event directly to all OrderEvent
            # queues registered for this account. ib.orderStatusEvent.emit() is
            # one-way (IB → handlers); manual emit doesn't trigger our listeners.
            self._dispatch_sim_event(
                account_number=sim_meta["account_number"],
                broker_order_id=broker_order_id,
                client_order_id=sim_meta["client_order_id"],
                status="modified",
            )
            logger.info(
                "sim_modify_echo_emitted",
                label=self.label,
                broker_order_id=broker_order_id,
                client_order_id=sim_meta["client_order_id"],
                account_number=sim_meta["account_number"],
            )
            return broker_pb2.ModifyOrderResponse(
                broker_order_id=broker_order_id,
                status="Modified",
            )

        try:
            target_perm_id = int(broker_order_id)
        except ValueError as exc:
            await _abort_rpc(
                context, grpc.StatusCode.INVALID_ARGUMENT, f"invalid broker_order_id: {exc}"
            )
            return broker_pb2.ModifyOrderResponse()

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
            await _abort_rpc(
                context, grpc.StatusCode.NOT_FOUND, f"order {broker_order_id} not in openTrades"
            )
            return broker_pb2.ModifyOrderResponse()

        ib_order = target_trade.order
        ib_order.totalQuantity = float(request.qty)
        if request.HasField("limit_price"):
            ib_order.lmtPrice = float(request.limit_price.value)
        if request.HasField("stop_price"):
            ib_order.auxPrice = float(request.stop_price.value)
        if request.tif:
            ib_order.tif = request.tif
        try:
            contract: object = target_trade.contract
            new_trade: _IbTrade = cast(
                "_IbTrade",
                self.ib.placeOrder(contract, ib_order),  # type: ignore[attr-defined, unused-ignore]
            )
        except Exception as exc:
            await _abort_rpc(context, grpc.StatusCode.UNKNOWN, f"placeOrder failed: {exc}")
            return broker_pb2.ModifyOrderResponse()

        return broker_pb2.ModifyOrderResponse(
            broker_order_id=str(new_trade.order.permId),
            status=str(new_trade.orderStatus.status),
        )

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: object,
    ) -> broker_pb2.PlaceBracketResponse:
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
        placed_children: list[_IbTrade] = []
        try:
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

            for i, (child_contract, child_order, leg) in enumerate(children_to_place):
                child_order.transmit = i == len(children_to_place) - 1
                child_trade: _IbTrade = cast(
                    "_IbTrade",
                    self.ib.placeOrder(child_contract, child_order),  # type: ignore[attr-defined, unused-ignore]
                )
                placed_children.append(child_trade)
                if leg == "stop_loss":
                    sl_perm_id = str(child_trade.order.permId)
                else:
                    tp_perm_id = str(child_trade.order.permId)
        except Exception as exc:
            logger.warning(
                "PlaceBracket.child_failure_rolling_back",
                parent_order_id=parent_trade.order.orderId,
                exc=str(exc),
            )
            try:
                self.ib.cancelOrder(parent_trade.order)  # type: ignore[attr-defined, unused-ignore]
            except Exception as cancel_exc:
                logger.error("PlaceBracket.parent_cancel_failed", exc=str(cancel_exc))
            for child in placed_children:
                try:
                    self.ib.cancelOrder(child.order)  # type: ignore[attr-defined, unused-ignore]
                except Exception as cancel_exc:
                    logger.error("PlaceBracket.child_cancel_failed", exc=str(cancel_exc))
            await _abort_rpc(
                context,
                grpc.StatusCode.INTERNAL,
                f"bracket child placement failed: {exc}",
            )
            return broker_pb2.PlaceBracketResponse()

        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=str(parent_trade.order.permId),
            stop_loss_broker_order_id=sl_perm_id,
            take_profit_broker_order_id=tp_perm_id,
            status=str(parent_trade.orderStatus.status),
        )

    async def GetSupportedComboStrategies(  # noqa: N802
        self,
        request: broker_pb2.GetSupportedComboStrategiesRequest,
        context: object,
    ) -> broker_pb2.GetSupportedComboStrategiesResponse:
        del request, context
        return broker_pb2.GetSupportedComboStrategiesResponse(
            strategy_types=["VERTICAL", "CALENDAR", "DIAGONAL", "STRADDLE", "STRANGLE"]
        )

    async def PlaceCombo(  # noqa: N802
        self,
        request: broker_pb2.PlaceComboRequest,
        context: object,
    ) -> broker_pb2.PlaceComboResponse:
        from ib_async import ComboLeg, Contract, LimitOrder

        if not request.legs:
            await _abort_rpc(context, grpc.StatusCode.INVALID_ARGUMENT, "combo legs required")
            return broker_pb2.PlaceComboResponse()

        combo_legs: list[ComboLeg] = []
        for leg in request.legs:
            underlying = leg.symbol.raw_symbol or leg.symbol.canonical_id
            contract = Contract(
                secType="OPT",
                symbol=underlying,
                lastTradeDateOrContractMonth=leg.option_hint.expiry_iso,
                strike=float(leg.option_hint.strike),
                right=leg.option_hint.put_call,
                exchange=leg.symbol.exchange,
                currency=leg.symbol.currency,
                multiplier="100",
            )
            details = await self.ib.reqContractDetailsAsync(contract)
            if not details:
                await _abort_rpc(
                    context,
                    grpc.StatusCode.NOT_FOUND,
                    f"option contract not found: {underlying}",
                )
                return broker_pb2.PlaceComboResponse()
            conid = details[0].contract.conId
            action = "BUY" if leg.side == "buy" else "SELL"
            combo_legs.append(
                ComboLeg(conId=conid, ratio=leg.ratio, action=action, exchange="SMART")
            )

        first_leg = request.legs[0]
        bag = Contract(
            secType="BAG",
            symbol=first_leg.symbol.raw_symbol or first_leg.symbol.canonical_id,
            currency=first_leg.symbol.currency,
            exchange="SMART",
            comboLegs=combo_legs,
        )
        order = LimitOrder(
            action="BUY",
            totalQuantity=1,
            lmtPrice=float(request.limit_price) if request.limit_price else 0,
            tif=request.tif,
            orderRef=request.client_combo_id,
        )
        trade = self.ib.placeOrder(bag, order)
        leg_results = [
            broker_pb2.ComboLegResult(
                leg_idx=i,
                broker_order_id="",
                status="working",
            )
            for i in range(len(request.legs))
        ]
        return broker_pb2.PlaceComboResponse(
            broker_combo_id=str(trade.order.orderId),
            legs=leg_results,
        )

    async def GetFutureContracts(  # noqa: N802
        self,
        request: broker_pb2.GetFutureContractsRequest,
        context: object,
    ) -> broker_pb2.GetFutureContractsResponse:
        from ib_async import Contract

        del context
        ib_contract = Contract(
            secType="FUT",
            symbol=request.root_symbol,
            exchange="SMART",
        )
        details_list = await self.ib.reqContractDetailsAsync(ib_contract)
        contracts: list[broker_pb2.FutureContractMonth] = []
        for details in details_list[:6]:
            c = details.contract
            cd = details.contractDetails if hasattr(details, "contractDetails") else details
            first_notice = getattr(cd, "firstNoticeDate", "") or ""
            contracts.append(
                broker_pb2.FutureContractMonth(
                    conid=str(c.conId),
                    contract_month=getattr(c, "lastTradeDateOrContractMonth", "")[:6],
                    expiry_date=getattr(c, "lastTradeDateOrContractMonth", ""),
                    first_notice=first_notice,
                    exchange=c.exchange or "",
                    tick_size=str(getattr(cd, "minTick", "0")),
                    tick_value=str(getattr(cd, "minTick", "0")),
                    multiplier=str(c.multiplier or "1"),
                    settlement_type="CASH",
                )
            )
        return broker_pb2.GetFutureContractsResponse(contracts=contracts)

    async def StreamSettlementEvents(  # noqa: N802
        self,
        request: broker_pb2.StreamSettlementEventsRequest,
        context: object,
    ) -> None:
        """Streams settlement events. Phase 14: backend settlement_listener handles event subscription directly via ib_async callbacks."""
        del request, context
        pass

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
                queue.put_nowait(self._proto_event_from_trade(ib_trade, kind="status", exec_id=""))
                # 5c v0.5.5 diagnostic: confirm synthetic emits queue properly.
                logger.info(
                    "orderevent_emit_queued",
                    label=self.label,
                    account_number=request.account_number,
                    broker_order_id=str(getattr(ib_trade.order, "permId", "")),
                    status=str(getattr(ib_trade.orderStatus, "status", "")),
                    kind="status",
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
                    self._proto_event_from_trade(ib_trade, kind="exec_details", exec_id=exec_id)
                )
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()

        def _on_commission_report(trade: object, fill: object, commission_report: object) -> None:
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
        # 5c v0.5.5 fix: register this queue so SIM echo paths can put directly
        # (bypassing ib.orderStatusEvent.emit which doesn't trigger our listeners).
        self._order_event_queues.setdefault(request.account_number, []).append(queue)
        # 5c v0.5.5 diagnostic: log subscribe/unsubscribe lifecycle so SIM-echo
        # propagation gaps are visible (paired with backend stream_subscribed log).
        logger.info(
            "orderevent_subscribed",
            label=self.label,
            account_number=request.account_number,
        )
        try:
            while not context.cancelled():  # type: ignore[attr-defined]
                yield await queue.get()
        finally:
            self.ib.orderStatusEvent -= _on_status  # type: ignore[attr-defined, unused-ignore]
            self.ib.execDetailsEvent -= _on_exec_details  # type: ignore[attr-defined, unused-ignore]
            if commission_report_event is not None:
                self.ib.commissionReportEvent -= _on_commission_report  # type: ignore[attr-defined, unused-ignore]
            queues = self._order_event_queues.get(request.account_number)
            if queues is not None:
                try:
                    queues.remove(queue)
                except ValueError:
                    pass
                if not queues:
                    self._order_event_queues.pop(request.account_number, None)
            logger.info(
                "orderevent_unsubscribed",
                label=self.label,
                account_number=request.account_number,
            )

    async def _get_or_init_ibkr_streamer(self) -> Any:
        lock = self.__dict__.setdefault("_streamer_lock", asyncio.Lock())
        async with lock:
            streamer = getattr(self, "_streamer", None)
            if streamer is not None:
                return streamer

            from sidecar_ibkr.streamer import IBKRStreamer

            streamer = IBKRStreamer(self.ib)
            try:
                await streamer.start()
            except Exception:
                with contextlib.suppress(Exception):
                    await streamer.stop()
                raise
            self._streamer = streamer
            return streamer

    async def StreamQuotes(  # noqa: N802
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.QuoteMessage]:
        try:
            streamer = await self._get_or_init_ibkr_streamer()
        except RuntimeError as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return
        except Exception as exc:
            # Surprise during streamer init (AttributeError on a partly-
            # configured IB, network error mid-subscribe, ...) must not
            # propagate to gRPC as UNKNOWN — abort with INTERNAL so the
            # client gets actionable status. The partial streamer was
            # already torn down by _get_or_init_ibkr_streamer's own
            # try/except cleanup path.
            logger.exception("ibkr.stream_quotes.init_unexpected_error")
            await context.abort(grpc.StatusCode.INTERNAL, str(exc))
            return

        queue: asyncio.Queue[broker_pb2.QuoteMessage] = asyncio.Queue(maxsize=_STREAM_QUEUE_MAX)
        call_subs: set[str] = set()

        def tick_callback(message: broker_pb2.QuoteMessage) -> None:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    metrics.ibkr_stream_quote_drops_total.inc()
                    logger.warning("ibkr.stream_quotes.dropped")

        self._add_streamer_tick_callback(streamer, tick_callback)
        consumer_task = asyncio.create_task(
            self._consume_stream_quote_requests(
                request_iterator,
                streamer,
                call_subs,
            ),
            name="ibkr-stream-quotes-consumer",
        )
        try:
            while True:
                yield await queue.get()
        finally:
            # Cancel + await the consumer to release its coroutine frame
            # immediately (B4 lesson). asyncio.shield() AFTER cancel is
            # backwards: the cancel has already taken effect, so shielding
            # only delays the join. A bare suppress(CancelledError) around
            # an await is cleaner.
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await consumer_task
            if call_subs:
                await streamer.on_unsubscribe(_symbol_refs(call_subs))
            self._remove_streamer_tick_callback(streamer, tick_callback)

    async def _consume_stream_quote_requests(
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        streamer: Any,
        call_subs: set[str],
    ) -> None:
        async for request in request_iterator:
            try:
                op = request.WhichOneof("op")
                if op == "subscribe":
                    symbols = list(request.subscribe.symbols)
                    if len(call_subs) + len(symbols) > _CALL_SUBS_MAX:
                        logger.warning(
                            "ibkr.stream_quotes.call_subs_cap_hit",
                            current=len(call_subs),
                            requested=len(symbols),
                            cap=_CALL_SUBS_MAX,
                        )
                        continue
                    await streamer.on_subscribe(symbols)
                    call_subs.update(_canonical_id(symbol) for symbol in symbols)
                elif op == "unsubscribe":
                    symbols = list(request.unsubscribe.symbols)
                    await streamer.on_unsubscribe(symbols)
                    call_subs.difference_update(_canonical_id(symbol) for symbol in symbols)
                elif op == "resync":
                    symbols = list(request.resync.expected)
                    if len(symbols) > _CALL_SUBS_MAX:
                        logger.warning(
                            "ibkr.stream_quotes.resync_cap_hit",
                            requested=len(symbols),
                            cap=_CALL_SUBS_MAX,
                        )
                        continue
                    await streamer.on_resync(symbols)
                    call_subs.clear()
                    call_subs.update(_canonical_id(symbol) for symbol in symbols)
                # heartbeat (and any unknown op) is keep-alive only.
            except Exception as exc:
                # Surface the subscribe-side failure in metrics — the
                # streamer.on_subscribe path also bumps the error metric
                # internally, but that increment never fires when the
                # caller short-circuits before reaching the streamer
                # (e.g. canonical_to_contract ValueError raised inside
                # the streamer call).
                metrics.ibkr_streamer_subscribe_total.labels(result="error").inc()
                logger.warning(
                    "ibkr.stream_quotes.request_dispatch_error",
                    error=str(exc),
                )

    def _add_streamer_tick_callback(
        self,
        streamer: Any,
        callback: _TickCallback,
    ) -> None:
        callbacks = getattr(streamer, "_broker_servicer_tick_callbacks", None)
        if callbacks is None:
            callbacks = set()
            streamer._broker_servicer_tick_callbacks = callbacks
            previous = getattr(streamer, "tick_callback", None)
            streamer._broker_servicer_previous_tick_callback = previous

            def dispatch(message: broker_pb2.QuoteMessage) -> None:
                if previous is not None:
                    try:
                        previous(message)
                    except Exception as exc:
                        logger.warning(
                            "ibkr.stream_quotes.previous_callback_error",
                            error=str(exc),
                        )
                for registered in tuple(callbacks):
                    try:
                        registered(message)
                    except Exception as exc:
                        logger.warning(
                            "ibkr.stream_quotes.tick_callback_error",
                            error=str(exc),
                        )

            streamer.tick_callback = dispatch
        callbacks.add(callback)

    def _remove_streamer_tick_callback(
        self,
        streamer: Any,
        callback: _TickCallback,
    ) -> None:
        callbacks = getattr(streamer, "_broker_servicer_tick_callbacks", None)
        if callbacks is None:
            return
        callbacks.discard(callback)
        if callbacks:
            return
        previous = getattr(streamer, "_broker_servicer_previous_tick_callback", None)
        streamer.tick_callback = previous
        del streamer._broker_servicer_tick_callbacks
        del streamer._broker_servicer_previous_tick_callback

    async def GetHistoricalBars(  # noqa: N802
        self,
        request: broker_pb2.GetHistoricalBarsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.GetHistoricalBarsResponse:
        if request.timeframe != "1m":
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "timeframe must be 1m",
            )
            return broker_pb2.GetHistoricalBarsResponse()
        if not request.canonical_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "canonical_id is required",
            )
            return broker_pb2.GetHistoricalBarsResponse()
        if request.range_start.seconds <= 0 or request.range_end.seconds <= 0:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "range_start and range_end are required",
            )
            return broker_pb2.GetHistoricalBarsResponse()
        if request.range_end.seconds <= request.range_start.seconds:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "range_end must be after range_start",
            )
            return broker_pb2.GetHistoricalBarsResponse()

        try:
            connected = bool(self.ib.isConnected())
        except Exception as exc:
            logger.warning(
                "ibkr.historical_bars.connection_check_failed",
                label=self.label,
                error=str(exc),
            )
            connected = False
        if not connected:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "ibkr client is not connected",
            )
            return broker_pb2.GetHistoricalBarsResponse()

        from sidecar_ibkr.streamer import canonical_to_contract

        try:
            contract = canonical_to_contract(
                broker_pb2.SymbolRef(canonical_id=request.canonical_id)
            )
        except ValueError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            return broker_pb2.GetHistoricalBarsResponse()

        jitter_ms = (_instrument_id_hash(request.canonical_id) % 4) * 50
        if jitter_ms > 0:
            await asyncio.sleep(jitter_ms / 1000)

        reserve = not _historical_bars_is_prewarm(context)
        await self._pacing_bucket.acquire(reserve=reserve)

        end_dt = request.range_end.ToDatetime(tzinfo=UTC)
        duration_seconds = max(
            int(request.range_end.seconds - request.range_start.seconds),
            60,
        )

        try:
            req_historical = self.ib.reqHistoricalDataAsync  # type: ignore[attr-defined, unused-ignore]
            raw_bars: object = await req_historical(
                contract,
                endDateTime=end_dt,
                durationStr=f"{duration_seconds} S",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
            )
        except Exception as exc:
            if _is_ibkr_pacing_violation(exc):
                await self._pacing_bucket.release_on_pacing_violation()
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    "ibkr pacing violation; retry after 60s",
                )
                return broker_pb2.GetHistoricalBarsResponse()
            raise

        bars = list(cast("Sequence[_IbHistoricalBar]", raw_bars))
        truncated = len(bars) >= request.limit if request.limit > 0 else False
        if request.limit > 0:
            bars = bars[: request.limit]

        return broker_pb2.GetHistoricalBarsResponse(
            bars=[_proto_historical_bar(bar) for bar in bars],
            truncated=truncated,
        )

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
            # Phase 15: FOREX->CASH/IDEALPRO, CRYPTO->CRYPTO/PAXOS, default STK
            asset_class = request.asset_class or "STK"
            if asset_class == "FOREX":
                ib_contract = ib_async.Forex(symbol=request.query, exchange="IDEALPRO")
            elif asset_class == "CRYPTO":
                ib_contract = ib_async.Crypto(symbol=request.query, exchange="PAXOS")
            else:
                ib_contract = ib_async.Contract(symbol=request.query, secType=asset_class)
            details = await self.ib.reqContractDetailsAsync(ib_contract)  # type: ignore[attr-defined, unused-ignore]

        contracts = [self._proto_contract_from_details(detail) for detail in details]
        # Phase 4 retro M2: prune _search_cache so noisy queries cannot OOM
        # the sidecar. Evict expired entries first (>300s TTL), then trim
        # the oldest until we are back at MAX 500.
        now = time.monotonic()
        self._search_cache[cache_key] = (now, contracts)
        if len(self._search_cache) > 500:
            expired = [k for k, (ts, _) in self._search_cache.items() if now - ts >= 300]
            for k in expired:
                self._search_cache.pop(k, None)
            while len(self._search_cache) > 500:
                oldest = min(self._search_cache.items(), key=lambda kv: kv[1][0])[0]
                self._search_cache.pop(oldest, None)
        return broker_pb2.SearchContractsResponse(contracts=contracts)

    async def _resolve_contract(self, conid: str) -> object:
        from ib_async import Contract as RuntimeIbContract  # type: ignore[import-untyped]

        contract: object = RuntimeIbContract(conId=int(conid))
        raw_qualified: object = await self.ib.qualifyContractsAsync(contract)  # type: ignore[attr-defined, unused-ignore]
        qualified: list[object] = list(cast("Iterable[object]", raw_qualified))
        return qualified[0] if qualified else contract

    def _build_ib_order(self, request: broker_pb2.PlaceOrderRequest) -> object:
        from sidecar_ibkr.order_builder import build_ib_order

        side: str = "BUY" if request.side == "BUY" else "SELL"
        qty: float = float(request.qty)
        return build_ib_order(request, side, qty)

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

    def _dispatch_sim_event(
        self,
        *,
        account_number: str,
        broker_order_id: str,
        client_order_id: str,
        status: str,
    ) -> None:
        """5c v0.5.5: put a synthetic SIM event into all OrderEvent queues
        registered for ``account_number``. Bypasses ``ib.orderStatusEvent.emit``
        which doesn't dispatch to externally-registered listeners under ib_async.
        """
        message = broker_pb2.OrderEventMessage(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            status=status,
            filled_qty="0",
            avg_fill_price="0",
            raw_payload=json.dumps({"sim_synthetic": True, "account_number": account_number}),
            exec_id="",
            kind="status",
        )
        message.event_at.FromDatetime(datetime.now(UTC))
        queues = self._order_event_queues.get(account_number, [])
        for queue in queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                metrics.broker_order_events_dropped_total.labels(reason="queue_full").inc()
            else:
                logger.info(
                    "orderevent_emit_queued",
                    label=self.label,
                    account_number=account_number,
                    broker_order_id=broker_order_id,
                    status=status,
                    kind="sim_synthetic",
                )

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
        # Phase 9.7: enum values were renamed in proto (MARKET →
        # ORDER_TYPE_MARKET etc.) during Phase 8b. handlers.py wasn't
        # updated, so _order_type raised AttributeError every time an
        # order's type was projected to proto.
        order_types: dict[str, broker_pb2.OrderType] = {
            "MKT": broker_pb2.ORDER_TYPE_MARKET,
            "LMT": broker_pb2.ORDER_TYPE_LIMIT,
            "STP": broker_pb2.ORDER_TYPE_STOP,
            "STP LMT": broker_pb2.ORDER_TYPE_STOP_LIMIT,
        }
        return order_types.get(order_type, broker_pb2.ORDER_TYPE_UNSPECIFIED)

    def _time_in_force(self, time_in_force: str) -> broker_pb2.TimeInForce:
        # Phase 9.7: same proto rename as _order_type — DAY → TIF_DAY etc.
        time_in_forces: dict[str, broker_pb2.TimeInForce] = {
            "DAY": broker_pb2.TIF_DAY,
            "GTC": broker_pb2.TIF_GTC,
            "IOC": broker_pb2.TIF_IOC,
            "FOK": broker_pb2.TIF_FOK,
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


def _canonical_id(symbol: broker_pb2.SymbolRef) -> str:
    return symbol.canonical_id or symbol.raw_symbol


def _instrument_id_hash(canonical_id: str) -> int:
    digest = hashlib.sha256(canonical_id.encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _historical_bars_is_prewarm(context: object) -> bool:
    invocation_metadata = getattr(context, "invocation_metadata", None)
    if not callable(invocation_metadata):
        return False
    metadata: object = invocation_metadata()
    for item in cast("Iterable[object]", metadata):
        if isinstance(item, tuple) and len(item) == 2:
            raw_key, raw_value = item
        else:
            raw_key = getattr(item, "key", "")
            raw_value = getattr(item, "value", "")
        key = str(raw_key).lower()
        value = str(raw_value).lower()
        if key in {"x-ibkr-prewarm", "ibkr-prewarm"} and value in {"1", "true", "yes"}:
            return True
    return False


def _is_ibkr_pacing_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "pacing violation" in message
        or "historical data request pacing violation" in message
        or "error 162" in message
    )


def _proto_historical_bar(bar: _IbHistoricalBar) -> broker_pb2.HistoricalBar:
    bucket_start = Timestamp()
    bucket_start.FromDatetime(_historical_bar_datetime(bar.date))
    return broker_pb2.HistoricalBar(
        bucket_start=bucket_start,
        open=str(bar.open),
        high=str(bar.high),
        low=str(bar.low),
        close=str(bar.close),
        volume=str(bar.volume),
    )


def _historical_bar_datetime(value: datetime | int | float | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    return datetime.fromtimestamp(float(value), tz=UTC)


def _symbol_refs(canonical_ids: set[str]) -> list[broker_pb2.SymbolRef]:
    return [
        broker_pb2.SymbolRef(canonical_id=canonical_id) for canonical_id in sorted(canonical_ids)
    ]
