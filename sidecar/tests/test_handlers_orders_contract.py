"""Tests for sidecar.handlers GetOrders + GetContract (Task 13)."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from ib_async import LimitOrder, MarketOrder, StopOrder

from sidecar._generated.broker.v1 import broker_pb2
from sidecar.handlers import BrokerHandlers, aiolimiter
from sidecar.pnl_cache import PnLCache

# ---------- ib_async-shaped fakes ----------


@dataclass
class FakeContract:
    conId: int  # noqa: N815
    symbol: str
    exchange: str
    currency: str
    secType: str = "STK"  # noqa: N815
    localSymbol: str = ""  # noqa: N815
    primaryExchange: str = ""  # noqa: N815


@dataclass
class FakeOrder:
    permId: int  # noqa: N815
    orderId: int  # noqa: N815
    account: str
    action: str
    orderType: str  # noqa: N815
    totalQuantity: Decimal  # noqa: N815
    lmtPrice: Decimal = Decimal("0")  # noqa: N815
    auxPrice: Decimal = Decimal("0")  # noqa: N815
    tif: str = "DAY"
    orderRef: str = ""  # noqa: N815


@dataclass
class FakeOrderStatus:
    status: str
    filled: Decimal = Decimal("0")
    remaining: Decimal = Decimal("0")
    avgFillPrice: Decimal = Decimal("0")  # noqa: N815
    lastFillPrice: Decimal = Decimal("0")  # noqa: N815
    whyHeld: str = ""  # noqa: N815


@dataclass
class FakeLogEntry:
    time: datetime
    status: str = ""
    message: str = ""
    errorCode: int = 0  # noqa: N815


@dataclass
class FakeTrade:
    contract: FakeContract
    order: FakeOrder
    orderStatus: FakeOrderStatus  # noqa: N815
    log: list[FakeLogEntry] = field(default_factory=list)


@dataclass
class FakeExecution:
    permId: int  # noqa: N815
    acctNumber: str  # noqa: N815
    side: str
    cumQty: Decimal  # noqa: N815
    price: Decimal
    avgPrice: Decimal  # noqa: N815
    time: datetime


@dataclass
class FakeFill:
    contract: FakeContract
    execution: FakeExecution
    time: datetime


@dataclass
class FakeContractDetails:
    contract: FakeContract


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def __iadd__(self, handler: object) -> FakeEvent:
        self.handlers.append(handler)
        return self

    def __isub__(self, handler: object) -> FakeEvent:
        self.handlers.remove(handler)
        return self

    def fire(self, trade: FakeTrade) -> None:
        for handler in list(self.handlers):
            handler(trade)  # type: ignore[operator]


@dataclass
class FakeIB:
    open_trades_list: list[FakeTrade] = field(default_factory=list)
    fills_list: list[FakeFill] = field(default_factory=list)
    qualified: list[FakeContract] = field(default_factory=list)
    contract_details: list[FakeContractDetails] = field(default_factory=list)
    contract_details_calls: list[object] = field(default_factory=list)
    place_order_calls: list[tuple[object, object]] = field(default_factory=list)
    cancel_order_calls: list[object] = field(default_factory=list)
    placed_trades: list[FakeTrade] = field(default_factory=list)
    raise_on_open: bool = False
    raise_on_qualify: bool = False
    orderStatusEvent: FakeEvent = field(default_factory=FakeEvent)  # noqa: N815
    execDetailsEvent: FakeEvent = field(default_factory=FakeEvent)  # noqa: N815

    def openTrades(self) -> list[FakeTrade]:  # noqa: N802
        if self.raise_on_open:
            raise RuntimeError("api timeout")
        return list(self.open_trades_list)

    def fills(self) -> list[FakeFill]:
        return list(self.fills_list)

    async def qualifyContractsAsync(self, contract: object) -> list[FakeContract]:  # noqa: N802
        del contract
        if self.raise_on_qualify:
            raise RuntimeError("contract not found")
        return list(self.qualified)

    async def reqContractDetailsAsync(self, contract: object) -> list[FakeContractDetails]:  # noqa: N802
        self.contract_details_calls.append(contract)
        sec_type = str(getattr(contract, "secType", ""))
        return [
            detail
            for detail in self.contract_details
            if not sec_type or detail.contract.secType == sec_type
        ]

    def placeOrder(self, contract: object, order: object) -> FakeTrade:  # noqa: N802
        self.place_order_calls.append((contract, order))
        order.permId = 12345
        trade = FakeTrade(
            contract=contract,  # type: ignore[arg-type]
            order=order,  # type: ignore[arg-type]
            orderStatus=FakeOrderStatus(status="Submitted"),
        )
        self.placed_trades.append(trade)
        return trade

    def cancelOrder(self, order: object) -> None:  # noqa: N802
        self.cancel_order_calls.append(order)

    def trades(self) -> list[FakeTrade]:
        return [*self.open_trades_list, *self.placed_trades]

    def fire_status(self, trade: FakeTrade) -> None:
        self.orderStatusEvent.fire(trade)

    def fire_exec(self, trade: FakeTrade) -> None:
        self.execDetailsEvent.fire(trade)


def _handlers(ib: FakeIB, *, simulator_only: bool = False) -> BrokerHandlers:
    return BrokerHandlers(
        ib=ib,  # type: ignore[arg-type]
        pnl_cache=PnLCache(ib),  # type: ignore[arg-type]
        label="ibgw_live_us",
        version="0.4.0+test",
        last_tick_ref={},
        simulator_only=simulator_only,
    )


class FakeContext:
    def __init__(self) -> None:
        self._cancelled = False

    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


def _place_order_request(
    *,
    client_order_id: str = "client-order-1",
    order_type: str = "MARKET",
    side: str = "BUY",
    qty: str = "10",
    limit_price: str = "",
    stop_price: str = "",
) -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="U1111111",
        client_order_id=client_order_id,
        conid="265598",
        side=side,
        order_type=order_type,
        tif="DAY",
        qty=qty,
        limit_price=limit_price,
        stop_price=stop_price,
    )


def _event_trade(
    *,
    account: str,
    perm_id: int = 77777,
    order_ref: str = "client-order-1",
    status: str = "Submitted",
    filled: Decimal = Decimal("0"),
    avg_fill_price: Decimal = Decimal("0"),
) -> FakeTrade:
    return FakeTrade(
        contract=FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD"),
        order=FakeOrder(
            permId=perm_id,
            orderId=1,
            account=account,
            action="BUY",
            orderType="LMT",
            totalQuantity=Decimal("10"),
            orderRef=order_ref,
        ),
        orderStatus=FakeOrderStatus(
            status=status,
            filled=filled,
            remaining=Decimal("10") - filled,
            avgFillPrice=avg_fill_price,
            lastFillPrice=avg_fill_price,
        ),
        log=[
            FakeLogEntry(
                time=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
                status=status,
                message="event",
                errorCode=0,
            )
        ],
    )


# ---------- GetOrders ----------


@pytest.mark.asyncio
async def test_order_event_filters_on_trade_order_account() -> None:
    ib = FakeIB()
    h = _handlers(ib)
    context = FakeContext()
    stream = h.OrderEvent(broker_pb2.AccountRef(account_number="U1111111"), context)
    first = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    ib.fire_status(_event_trade(account="U1111111", perm_id=1))
    ib.fire_status(_event_trade(account="U2222222", perm_id=2))
    ib.fire_status(_event_trade(account="U1111111", perm_id=3))

    first_event = await first
    second_event = await anext(stream)
    assert [first_event.broker_order_id, second_event.broker_order_id] == ["1", "3"]

    context.cancel()
    await stream.aclose()


@pytest.mark.asyncio
async def test_order_event_does_not_leak_cross_account() -> None:
    ib = FakeIB()
    h = _handlers(ib)
    context = FakeContext()
    stream = h.OrderEvent(broker_pb2.AccountRef(account_number="U1111111"), context)
    first = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    for perm_id in range(10):
        ib.fire_exec(_event_trade(account="U2222222", perm_id=perm_id))
    ib.fire_exec(_event_trade(account="U1111111", perm_id=99))

    event = await first
    assert event.broker_order_id == "99"
    assert event.raw_payload
    assert json.loads(event.raw_payload)["account"] == "U1111111"

    context.cancel()
    await stream.aclose()


@pytest.mark.asyncio
async def test_order_event_queue_bounded_drops_on_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidecar import handlers, metrics

    gate = asyncio.Event()
    real_queue = asyncio.Queue

    class BlockingGetQueue(real_queue):
        async def get(self) -> object:
            await gate.wait()
            return await super().get()

    monkeypatch.setattr(handlers.asyncio, "Queue", BlockingGetQueue)
    dropped = metrics.broker_order_events_dropped_total.labels(reason="queue_full")
    before = dropped._value.get()  # type: ignore[attr-defined]

    ib = FakeIB()
    h = _handlers(ib)
    context = FakeContext()
    stream = h.OrderEvent(broker_pb2.AccountRef(account_number="U1111111"), context)
    first = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    for perm_id in range(10_001):
        ib.fire_status(_event_trade(account="U1111111", perm_id=perm_id))

    after = dropped._value.get()  # type: ignore[attr-defined]
    assert after - before == 1

    gate.set()
    yielded = [await first]
    for _ in range(9_999):
        yielded.append(await anext(stream))
    assert len(yielded) == 10_000
    assert yielded[0].broker_order_id == "0"
    assert yielded[-1].broker_order_id == "9999"

    context.cancel()
    await stream.aclose()


def test_serialize_trade_handles_circular_refs() -> None:
    trade = _event_trade(
        account="U1111111",
        perm_id=123,
        order_ref="client-123",
        status="Filled",
        filled=Decimal("2.5"),
        avg_fill_price=Decimal("181.25"),
    )
    trade.self_ref = trade  # type: ignore[attr-defined]

    payload = BrokerHandlers._serialize_trade(trade)

    assert payload == {
        "perm_id": 123,
        "order_ref": "client-123",
        "account": "U1111111",
        "status": "Filled",
        "filled": "2.5",
        "remaining": "7.5",
        "avg_fill_price": "181.25",
        "last_fill_price": "181.25",
        "why_held": "",
        "log": [
            {
                "time": "2026-04-27T12:00:00+00:00",
                "status": "Filled",
                "message": "event",
                "error_code": 0,
            }
        ],
    }
    json.dumps(payload)


@pytest.mark.asyncio
async def test_order_event_emits_status_and_fill() -> None:
    ib = FakeIB()
    h = _handlers(ib)
    context = FakeContext()
    stream = h.OrderEvent(broker_pb2.AccountRef(account_number="U1111111"), context)
    first = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    ib.fire_status(
        _event_trade(
            account="U1111111",
            perm_id=123,
            status="Submitted",
            filled=Decimal("1"),
            avg_fill_price=Decimal("180.50"),
        )
    )
    ib.fire_exec(
        _event_trade(
            account="U1111111",
            perm_id=123,
            status="Filled",
            filled=Decimal("10"),
            avg_fill_price=Decimal("181.25"),
        )
    )

    status_event = await first
    fill_event = await anext(stream)
    assert status_event.status == "Submitted"
    assert status_event.filled_qty == "1"
    assert status_event.avg_fill_price == "180.50"
    assert fill_event.status == "Filled"
    assert fill_event.filled_qty == "10"
    assert fill_event.avg_fill_price == "181.25"

    context.cancel()
    await stream.aclose()


@pytest.mark.asyncio
async def test_place_order_market_builds_correct_ib_order() -> None:
    ib = FakeIB(
        qualified=[FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")]
    )
    h = _handlers(ib, simulator_only=False)
    request = _place_order_request(order_type="MARKET", side="BUY", qty="10")

    response = await h.PlaceOrder(request, context=object())

    assert response.broker_order_id == "12345"
    assert len(ib.place_order_calls) == 1
    _, ib_order = ib.place_order_calls[0]
    assert isinstance(ib_order, MarketOrder)
    assert ib_order.action == "BUY"
    assert ib_order.totalQuantity == 10.0
    assert ib_order.orderRef == request.client_order_id
    assert ib_order.account == request.account_number


@pytest.mark.asyncio
async def test_place_order_limit_includes_limit_price() -> None:
    ib = FakeIB(
        qualified=[FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")]
    )
    h = _handlers(ib, simulator_only=False)
    request = _place_order_request(
        client_order_id="limit-client-order",
        order_type="LIMIT",
        side="SELL",
        qty="7",
        limit_price="180.5",
    )

    await h.PlaceOrder(request, context=object())

    _, ib_order = ib.place_order_calls[0]
    assert isinstance(ib_order, LimitOrder)
    assert ib_order.action == "SELL"
    assert ib_order.totalQuantity == 7.0
    assert ib_order.lmtPrice == 180.5
    assert ib_order.orderRef == request.client_order_id


@pytest.mark.asyncio
async def test_place_order_stop_includes_stop_price() -> None:
    ib = FakeIB(
        qualified=[FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")]
    )
    h = _handlers(ib, simulator_only=False)
    request = _place_order_request(
        client_order_id="stop-client-order",
        order_type="STOP",
        side="SELL",
        qty="3",
        stop_price="175.25",
    )

    await h.PlaceOrder(request, context=object())

    _, ib_order = ib.place_order_calls[0]
    assert isinstance(ib_order, StopOrder)
    assert ib_order.action == "SELL"
    assert ib_order.totalQuantity == 3.0
    assert ib_order.auxPrice == 175.25
    assert ib_order.orderRef == request.client_order_id


@pytest.mark.asyncio
async def test_place_order_per_client_id_lock_prevents_double_place() -> None:
    ib = FakeIB(
        qualified=[FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")]
    )
    h = _handlers(ib, simulator_only=False)
    request = _place_order_request(client_order_id="same-client-order")

    first, second = await asyncio.gather(
        h.PlaceOrder(request, context=object()),
        h.PlaceOrder(request, context=object()),
    )

    assert len(ib.place_order_calls) == 1
    assert first.broker_order_id == second.broker_order_id == "12345"


@pytest.mark.asyncio
async def test_place_order_simulator_mode_returns_sim_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uuid_utils = types.ModuleType("uuid_utils")
    uuid_utils.uuid7 = lambda: "018f8f97-7b4a-7000-8000-123456789abc"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uuid_utils", uuid_utils)
    ib = FakeIB()
    h = _handlers(ib, simulator_only=True)
    request = _place_order_request()

    response = await h.PlaceOrder(request, context=object())

    assert re.match(r"^SIM-[0-9a-f-]{36}$", response.broker_order_id)
    assert response.status == "Submitted"
    assert ib.place_order_calls == []


# ---------- CancelOrder ----------


@pytest.mark.asyncio
async def test_cancel_order_filters_by_account_and_perm_id() -> None:
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    matching_order = FakeOrder(
        permId=77777,
        orderId=7,
        account="U1111111",
        action="BUY",
        orderType="LMT",
        totalQuantity=Decimal("10"),
    )
    other_account_order = FakeOrder(
        permId=77777,
        orderId=8,
        account="U2222222",
        action="BUY",
        orderType="LMT",
        totalQuantity=Decimal("10"),
    )
    ib = FakeIB(
        open_trades_list=[
            FakeTrade(
                contract=contract,
                order=other_account_order,
                orderStatus=FakeOrderStatus(status="Submitted"),
            ),
            FakeTrade(
                contract=contract,
                order=matching_order,
                orderStatus=FakeOrderStatus(status="Submitted"),
            ),
        ]
    )
    h = _handlers(ib)

    response = await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U1111111",
            broker_order_id="77777",
        ),
        context=object(),
    )

    assert response.accepted is True
    assert ib.cancel_order_calls == [matching_order]


@pytest.mark.asyncio
async def test_cancel_order_returns_accepted_false_when_not_found() -> None:
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    ib = FakeIB(
        open_trades_list=[
            FakeTrade(
                contract=contract,
                order=FakeOrder(
                    permId=88888,
                    orderId=9,
                    account="U1111111",
                    action="BUY",
                    orderType="LMT",
                    totalQuantity=Decimal("10"),
                ),
                orderStatus=FakeOrderStatus(status="Submitted"),
            )
        ]
    )
    h = _handlers(ib)

    response = await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U1111111",
            broker_order_id="99999",
        ),
        context=object(),
    )

    assert response.accepted is False
    assert ib.cancel_order_calls == []


@pytest.mark.asyncio
async def test_cancel_order_returns_accepted_true_when_found() -> None:
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    order = FakeOrder(
        permId=99999,
        orderId=10,
        account="U1111111",
        action="SELL",
        orderType="MKT",
        totalQuantity=Decimal("5"),
    )
    ib = FakeIB(
        open_trades_list=[
            FakeTrade(
                contract=contract,
                order=order,
                orderStatus=FakeOrderStatus(status="Submitted"),
            )
        ]
    )
    h = _handlers(ib)

    response = await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U1111111",
            broker_order_id="99999",
        ),
        context=object(),
    )

    assert response.accepted is True
    assert ib.cancel_order_calls == [order]


@pytest.mark.asyncio
async def test_get_orders_maps_open_limit_order() -> None:
    """Plan §13.2: one open limit order — full Trade → proto Order mapping."""
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    order = FakeOrder(
        permId=11111,
        orderId=1,
        account="U1111111",
        action="BUY",
        orderType="LMT",
        totalQuantity=Decimal("100"),
        lmtPrice=Decimal("180.50"),
        tif="GTC",
    )
    submitted_at = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)
    trade = FakeTrade(
        contract=contract,
        order=order,
        orderStatus=FakeOrderStatus(status="Submitted"),
        log=[FakeLogEntry(time=submitted_at)],
    )
    ib = FakeIB(open_trades_list=[trade])
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert len(response.orders) == 1
    o = response.orders[0]
    assert o.order_id == "11111"
    assert o.contract.symbol == "AAPL"
    assert o.side == broker_pb2.BUY
    assert o.order_type == broker_pb2.LIMIT
    assert o.quantity == "100"
    assert o.limit_price.value == "180.50"
    assert o.limit_price.currency == "USD"
    assert o.time_in_force == broker_pb2.GTC
    assert o.status == broker_pb2.SUBMITTED
    assert o.submitted_at.seconds == int(submitted_at.timestamp())


@pytest.mark.asyncio
async def test_get_orders_maps_filled_today_market_order() -> None:
    """Plan §13.2: one filled-today market order — Fill-derived path."""
    contract = FakeContract(conId=272093, symbol="MSFT", exchange="NASDAQ", currency="USD")
    fill_time = datetime.now(tz=UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    fill = FakeFill(
        contract=contract,
        execution=FakeExecution(
            permId=22222,
            acctNumber="U1111111",
            side="SELL",
            cumQty=Decimal("50"),
            price=Decimal("420.25"),
            avgPrice=Decimal("420.10"),
            time=fill_time,
        ),
        time=fill_time,
    )
    ib = FakeIB(fills_list=[fill])
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert len(response.orders) == 1
    o = response.orders[0]
    assert o.order_id == "22222"
    assert o.contract.symbol == "MSFT"
    assert o.side == broker_pb2.SELL
    assert o.status == broker_pb2.FILLED
    assert o.quantity_filled == "50"
    assert o.avg_fill_price.value == "420.10"


@pytest.mark.asyncio
async def test_get_orders_filters_out_other_account_orders() -> None:
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    other_account_trade = FakeTrade(
        contract=contract,
        order=FakeOrder(
            permId=33333,
            orderId=2,
            account="U2222222",
            action="BUY",
            orderType="MKT",
            totalQuantity=Decimal("10"),
        ),
        orderStatus=FakeOrderStatus(status="Filled", filled=Decimal("10")),
    )
    ib = FakeIB(open_trades_list=[other_account_trade])
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert list(response.orders) == []


@pytest.mark.asyncio
async def test_get_orders_excludes_yesterdays_fills() -> None:
    """Only today's fills count — yesterday's must be silently dropped."""
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    yesterday = datetime.now(tz=UTC) - timedelta(days=1)
    fill = FakeFill(
        contract=contract,
        execution=FakeExecution(
            permId=44444,
            acctNumber="U1111111",
            side="BUY",
            cumQty=Decimal("10"),
            price=Decimal("180"),
            avgPrice=Decimal("180"),
            time=yesterday,
        ),
        time=yesterday,
    )
    ib = FakeIB(fills_list=[fill])
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert list(response.orders) == []


@pytest.mark.asyncio
async def test_get_orders_dedups_fill_when_open_trade_shares_perm_id() -> None:
    """An openTrades row + a fills row with the same permId must NOT double-count."""
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    perm_id = 55555
    now = datetime.now(tz=UTC)
    trade = FakeTrade(
        contract=contract,
        order=FakeOrder(
            permId=perm_id,
            orderId=3,
            account="U1111111",
            action="BUY",
            orderType="MKT",
            totalQuantity=Decimal("10"),
        ),
        orderStatus=FakeOrderStatus(status="Filled", filled=Decimal("10")),
    )
    fill = FakeFill(
        contract=contract,
        execution=FakeExecution(
            permId=perm_id,
            acctNumber="U1111111",
            side="BUY",
            cumQty=Decimal("10"),
            price=Decimal("180"),
            avgPrice=Decimal("180"),
            time=now,
        ),
        time=now,
    )
    ib = FakeIB(open_trades_list=[trade], fills_list=[fill])
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert len(response.orders) == 1
    assert response.orders[0].order_id == str(perm_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ibkr_status", "proto_status"),
    [
        ("Submitted", broker_pb2.SUBMITTED),
        ("PendingSubmit", broker_pb2.SUBMITTED),
        ("PreSubmitted", broker_pb2.PENDING),
        ("Filled", broker_pb2.FILLED),
        ("Cancelled", broker_pb2.CANCELLED),
        ("ApiCancelled", broker_pb2.CANCELLED),
        ("Inactive", broker_pb2.REJECTED),
        ("UnknownState", broker_pb2.STATUS_UNSPECIFIED),
    ],
)
async def test_get_orders_status_mapping(
    ibkr_status: str, proto_status: broker_pb2.OrderStatus
) -> None:
    contract = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    trade = FakeTrade(
        contract=contract,
        order=FakeOrder(
            permId=66666,
            orderId=4,
            account="U1111111",
            action="BUY",
            orderType="LMT",
            totalQuantity=Decimal("1"),
        ),
        orderStatus=FakeOrderStatus(status=ibkr_status),
    )
    ib = FakeIB(open_trades_list=[trade])
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert response.orders[0].status == proto_status


@pytest.mark.asyncio
async def test_get_orders_returns_empty_when_api_throws() -> None:
    ib = FakeIB(raise_on_open=True)
    h = _handlers(ib)
    response = await h.GetOrders(broker_pb2.AccountRef(account_number="U1111111"), context=object())
    assert list(response.orders) == []


# ---------- GetContract ----------


@pytest.mark.asyncio
async def test_get_contract_resolves_by_conid() -> None:
    """Plan §13.2: conId-only contract resolution via qualifyContractsAsync."""
    qualified = FakeContract(
        conId=265598,
        symbol="AAPL",
        exchange="NASDAQ",
        currency="USD",
        secType="STK",
        localSymbol="AAPL",
    )
    ib = FakeIB(qualified=[qualified])
    h = _handlers(ib)
    response = await h.GetContract(broker_pb2.ContractRef(conid="265598"), context=object())
    c = response.contract
    assert c.symbol == "AAPL"
    assert c.exchange == "NASDAQ"
    assert c.currency == "USD"
    assert c.conid == "265598"
    assert c.asset_class == broker_pb2.STOCK


@pytest.mark.asyncio
async def test_get_contract_returns_default_when_qualify_throws() -> None:
    """Unknown conId / network error must surface as default Contract proto."""
    ib = FakeIB(raise_on_qualify=True)
    h = _handlers(ib)
    response = await h.GetContract(broker_pb2.ContractRef(conid="9999999"), context=object())
    # Default proto: empty fields. Caller distinguishes via empty conid.
    assert response.contract.symbol == ""
    assert response.contract.conid == ""


@pytest.mark.asyncio
async def test_get_contract_returns_default_when_qualify_returns_empty() -> None:
    """qualifyContractsAsync returning [] (unrecognized conId) must not crash."""
    ib = FakeIB(qualified=[])
    h = _handlers(ib)
    response = await h.GetContract(broker_pb2.ContractRef(conid="9999999"), context=object())
    assert response.contract.symbol == ""


# ---------- SearchContracts ----------


@pytest.fixture(autouse=True)
def _clear_search_cache() -> Iterator[None]:
    """Reset the class-level _search_cache between tests
    (architect-review M1: ClassVar leaks across tests; rebinding instance
    attribute doesn't reach the class dict). Limiter is left intact —
    R20 mandates process-wide 5/sec."""
    from sidecar.handlers import BrokerHandlers

    BrokerHandlers._search_cache.clear()
    yield
    BrokerHandlers._search_cache.clear()


@pytest.mark.asyncio
async def test_search_contracts_caches_results() -> None:
    ib = FakeIB(
        contract_details=[
            FakeContractDetails(
                FakeContract(
                    conId=265598,
                    symbol="AAPL",
                    primaryExchange="NASDAQ",
                    exchange="SMART",
                    currency="USD",
                )
            ),
            FakeContractDetails(
                FakeContract(
                    conId=38708077,
                    symbol="AAPL",
                    primaryExchange="LSE",
                    exchange="SMART",
                    currency="GBP",
                )
            ),
        ]
    )
    h = _handlers(ib)
    h._search_cache = {}

    request = broker_pb2.SearchContractsRequest(query="AAPL", asset_class="STK")
    first = await h.SearchContracts(request, context=object())
    second = await h.SearchContracts(request, context=object())

    assert len(ib.contract_details_calls) == 1
    assert list(first.contracts) == list(second.contracts)
    assert len(first.contracts) == 2


@pytest.mark.asyncio
async def test_search_contracts_rate_limits_5_per_sec_process_wide() -> None:
    ib = FakeIB(
        contract_details=[
            FakeContractDetails(
                FakeContract(
                    conId=265598,
                    symbol="AAPL",
                    primaryExchange="NASDAQ",
                    exchange="SMART",
                    currency="USD",
                )
            )
        ]
    )
    h = _handlers(ib)
    h._search_cache = {}
    h._search_limiter = aiolimiter.AsyncLimiter(5, 1.0)

    await asyncio.gather(
        *[
            h.SearchContracts(
                broker_pb2.SearchContractsRequest(query=f"SYM{index}", asset_class="STK"),
                context=object(),
            )
            for index in range(5)
        ]
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            h.SearchContracts(
                broker_pb2.SearchContractsRequest(query="SYM5", asset_class="STK"),
                context=object(),
            ),
            timeout=0.2,
        )


@pytest.mark.asyncio
async def test_search_contracts_forwards_asset_class_filter() -> None:
    ib = FakeIB(
        contract_details=[
            FakeContractDetails(
                FakeContract(
                    conId=265598,
                    symbol="AAPL",
                    primaryExchange="NASDAQ",
                    exchange="SMART",
                    currency="USD",
                    secType="STK",
                )
            ),
            FakeContractDetails(
                FakeContract(
                    conId=650242895,
                    symbol="AAPL",
                    primaryExchange="CME",
                    exchange="CME",
                    currency="USD",
                    secType="FUT",
                )
            ),
        ]
    )
    h = _handlers(ib)
    h._search_cache = {}

    response = await h.SearchContracts(
        broker_pb2.SearchContractsRequest(query="AAPL", asset_class="STK"),
        context=object(),
    )

    assert ib.contract_details_calls[0].secType == "STK"
    assert [contract.asset_class for contract in response.contracts] == [broker_pb2.STOCK]
