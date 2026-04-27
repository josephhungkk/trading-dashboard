"""Phase 5b Task E3 tests for reconnect resync behavior.

No BUG(E3) deviations are currently documented: the implementation matches the
expected snapshot synthesis, synthetic-first processing, and metric increment
behavior these tests cover.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.brokers.base import Contract, Money, Order, OrderEventMessage, OrderStatus
from app.core import metrics
from app.core.config import settings
from app.core.ids import uuid7
from app.services.order_event_consumer import AccountStream, OrderEventConsumer


class _RecordingRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | bytes]] = []

    async def publish(self, channel: str, message: str | bytes) -> int:
        self.messages.append((channel, message))
        return 1


class _StreamClient:
    def __init__(
        self,
        label: str,
        stream_factory: Callable[[], AsyncIterator[OrderEventMessage]],
    ) -> None:
        self.label = label
        self._stream_factory = stream_factory

    def order_event_stream(self, account_number: str) -> AsyncIterator[OrderEventMessage]:
        return self._stream_factory()


class _Registry:
    def __init__(self, clients: dict[str, _StreamClient] | None = None) -> None:
        self.account_changed: asyncio.Queue[object] = asyncio.Queue()
        self._clients = {
            "isa-live": _StreamClient("isa-live", _blocking_stream),
            "isa-paper": _StreamClient("isa-paper", _blocking_stream),
            "normal-live": _StreamClient("normal-live", _blocking_stream),
            "normal-paper": _StreamClient("normal-paper", _blocking_stream),
        }
        if clients is not None:
            self._clients.update(clients)

    async def get_client(self, label: str) -> _StreamClient:
        return self._clients[label]


class _ResyncStreamClient(_StreamClient):
    def __init__(
        self,
        label: str,
        stream_factory: Callable[[], AsyncIterator[OrderEventMessage]],
        snapshot_orders: list[Order],
    ) -> None:
        super().__init__(label, stream_factory)
        self.get_orders_mock = AsyncMock(return_value=snapshot_orders)

    async def get_orders(self, account_number: str) -> list[Order]:
        return cast(list[Order], await self.get_orders_mock(account_number))


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[Any]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def cleanup(db_engine: AsyncEngine) -> AsyncIterator[None]:
    await _cleanup_rows(db_engine)
    yield
    await _cleanup_rows(db_engine)


async def _cleanup_rows(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM order_events
                 WHERE account_id IN (
                     SELECT id FROM broker_accounts
                      WHERE account_number LIKE 'UTEST_E3_%'
                 )
                """
            )
        )
        await conn.execute(
            text(
                """
                DELETE FROM orders
                 WHERE account_id IN (
                     SELECT id FROM broker_accounts
                      WHERE account_number LIKE 'UTEST_E3_%'
                 )
                """
            )
        )
        await conn.execute(
            text("DELETE FROM broker_accounts WHERE account_number LIKE 'UTEST_E3_%'")
        )


async def _seed_account(
    db_engine: AsyncEngine,
    *,
    account_number: str,
    label: str = "isa-paper",
) -> UUID:
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO broker_accounts
                (broker_id, account_number, mode, gateway_label, currency_base, last_seen_via)
                VALUES ('ibkr', :account_number, 'paper', :label, 'USD', :label)
                RETURNING id
                """
            ),
            {"account_number": account_number, "label": label},
        )
        return UUID(str(result.scalar_one()))


async def _seed_order(
    db_engine: AsyncEngine,
    *,
    account_id: UUID,
    client_order_id: UUID,
    status: str = "pending_submit",
    filled_qty: Decimal = Decimal("0"),
    avg_fill_price: Decimal | None = None,
    notional_filled: Decimal = Decimal("0"),
    last_event_at: datetime | None = None,
) -> UUID:
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO orders (
                    id, account_id, client_order_id, conid, symbol, side, order_type, tif,
                    qty, status, filled_qty, avg_fill_price, notional, notional_filled,
                    last_event_at
                )
                VALUES (
                    :id, :account_id, :client_order_id, '265598', 'AAPL', 'BUY', 'MARKET',
                    'DAY', 10, CAST(:status AS order_status_enum), :filled_qty,
                    :avg_fill_price, 1000, :notional_filled, :last_event_at
                )
                RETURNING id
                """
            ),
            {
                "id": uuid7(),
                "account_id": account_id,
                "client_order_id": client_order_id,
                "status": status,
                "filled_qty": filled_qty,
                "avg_fill_price": avg_fill_price,
                "notional_filled": notional_filled,
                "last_event_at": last_event_at,
            },
        )
        return UUID(str(result.scalar_one()))


def _event(
    *,
    account_id: UUID,
    account_number: str,
    label: str = "isa-paper",
    client_order_id: UUID | str | None,
    status: str = "submitted",
    filled_qty: str = "0",
    avg_fill_price: str = "",
    broker_event_at: datetime | None = None,
    raw_payload_extra: dict[str, object] | None = None,
) -> OrderEventMessage:
    payload: dict[str, object] = {
        "account_id": str(account_id),
        "gateway_label": label,
        "account_number": account_number,
    }
    if raw_payload_extra is not None:
        payload = {**payload, **raw_payload_extra}
    return OrderEventMessage(
        broker_order_id="BRK-1",
        client_order_id="" if client_order_id is None else str(client_order_id),
        status=status,
        filled_qty=filled_qty,
        avg_fill_price=avg_fill_price,
        broker_event_at=broker_event_at or datetime.now(UTC),
        raw_payload=json.dumps(payload),
    )


async def _order_row(db_engine: AsyncEngine, order_id: UUID) -> dict[str, Any]:
    async with db_engine.connect() as conn:
        row = (
            (await conn.execute(text("SELECT * FROM orders WHERE id = :id"), {"id": order_id}))
            .mappings()
            .one()
        )
    return dict(row)


def _money(value: str = "0", currency: str = "USD") -> Money:
    return Money(value=value, currency=currency)


def _contract(symbol: str = "AAPL") -> Contract:
    return Contract(
        symbol=symbol,
        exchange="SMART",
        currency="USD",
        asset_class="STOCK",
        conid="265598",
        local_symbol=symbol,
    )


def _snapshot_order(
    *,
    client_order_id: UUID | str,
    status: OrderStatus = "SUBMITTED",
    quantity_filled: str = "0",
    avg_fill_price: str = "0",
    submitted_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Order:
    return Order(
        order_id=str(client_order_id),
        contract=_contract(),
        side="BUY",
        order_type="MARKET",
        quantity="10",
        limit_price=_money(),
        stop_price=_money(),
        time_in_force="DAY",
        status=status,
        quantity_filled=quantity_filled,
        avg_fill_price=_money(avg_fill_price),
        submitted_at=submitted_at,
        updated_at=updated_at,
    )


async def _stream_from(events: list[OrderEventMessage]) -> AsyncIterator[OrderEventMessage]:
    await asyncio.sleep(0)
    for event in events:
        yield event


async def _blocking_stream() -> AsyncIterator[OrderEventMessage]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")
    yield  # pragma: no cover


async def _last_event_at(db_engine: AsyncEngine, order_id: UUID) -> datetime:
    order = await _order_row(db_engine, order_id)
    value = order["last_event_at"]
    assert isinstance(value, datetime)
    return value


@pytest.mark.asyncio
async def test_reconnect_buffers_then_resyncs_first(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_number = "UTEST_E3_ORDERING"
    account_id = await _seed_account(db_engine, account_number=account_number)
    client_order_id = uuid4()
    await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        status="pending_submit",
    )
    live_events = [
        _event(
            account_id=account_id,
            account_number=account_number,
            client_order_id=uuid4(),
            broker_event_at=datetime(2026, 4, 27, 10, 0, 1, tzinfo=UTC),
        ),
        _event(
            account_id=account_id,
            account_number=account_number,
            client_order_id=uuid4(),
            broker_event_at=datetime(2026, 4, 27, 10, 0, 2, tzinfo=UTC),
        ),
    ]
    snapshot_order = _snapshot_order(
        client_order_id=client_order_id,
        updated_at=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
    )
    client = _ResyncStreamClient("isa-paper", lambda: _stream_from(live_events), [snapshot_order])

    async def get_orders(account_number_arg: str) -> list[Order]:
        await asyncio.sleep(0)
        assert account_number_arg == account_number
        return [snapshot_order]

    client.get_orders_mock = AsyncMock(side_effect=get_orders)
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]
    process_event = AsyncMock(wraps=consumer._process_event)

    with patch.object(consumer, "_process_event", process_event):
        await consumer._run_account_stream_once(
            client,
            "isa-paper",
            account_number,
            AccountStream(account_id, "isa-paper", account_number),
        )

    processed = [call.args[0] for call in process_event.await_args_list]
    assert [event.client_order_id for event in processed] == [
        str(client_order_id),
        str(live_events[0].client_order_id),
        str(live_events[1].client_order_id),
    ]
    assert json.loads(processed[0].raw_payload)["synthetic"] is True


@pytest.mark.asyncio
async def test_resync_synthetic_events_use_broker_event_at_from_snapshot(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_number = "UTEST_E3_TIMESTAMP"
    account_id = await _seed_account(db_engine, account_number=account_number)
    client_order_id = uuid4()
    await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        status="pending_submit",
    )
    snapshot_time = datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC)
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    events = await consumer._synthesize_resync_events(
        AccountStream(account_id, "isa-paper", account_number),
        "isa-paper",
        account_number,
        [_snapshot_order(client_order_id=client_order_id, updated_at=snapshot_time)],
    )

    assert len(events) == 1
    assert events[0].broker_event_at == snapshot_time


@pytest.mark.asyncio
async def test_resync_doesnt_double_count_when_predicate_blocks(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_number = "UTEST_E3_IDEMPOTENT"
    account_id = await _seed_account(db_engine, account_number=account_number)
    client_order_id = uuid4()
    snapshot_time = datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC)
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        status="pending_submit",
    )
    live_event = _event(
        account_id=account_id,
        account_number=account_number,
        client_order_id=client_order_id,
        status="partial",
        filled_qty="1",
        avg_fill_price="100",
        broker_event_at=snapshot_time,
    )
    snapshot_order = _snapshot_order(
        client_order_id=client_order_id,
        quantity_filled="1",
        avg_fill_price="100",
        updated_at=snapshot_time,
    )
    client = _ResyncStreamClient("isa-paper", lambda: _stream_from([live_event]), [snapshot_order])

    async def get_orders(account_number_arg: str) -> list[Order]:
        await asyncio.sleep(0)
        assert account_number_arg == account_number
        return [snapshot_order]

    client.get_orders_mock = AsyncMock(side_effect=get_orders)
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._run_account_stream_once(
        client,
        "isa-paper",
        account_number,
        AccountStream(account_id, "isa-paper", account_number),
    )

    assert await _last_event_at(db_engine, order_id) == snapshot_time


@pytest.mark.asyncio
async def test_resync_emits_metric_count(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_number = "UTEST_E3_METRIC"
    account_id = await _seed_account(db_engine, account_number=account_number)
    client_order_ids = [uuid4(), uuid4(), uuid4()]
    for client_order_id in client_order_ids:
        await _seed_order(
            db_engine,
            account_id=account_id,
            client_order_id=client_order_id,
            status="pending_submit",
        )
    snapshot_orders = [
        _snapshot_order(
            client_order_id=client_order_id,
            updated_at=datetime(2026, 4, 27, 10, index, 0, tzinfo=UTC),
        )
        for index, client_order_id in enumerate(client_order_ids)
    ]
    client = _ResyncStreamClient("isa-paper", lambda: _stream_from([]), snapshot_orders)
    counter = metrics.broker_order_stream_resync_synthetic_events_total.labels(label="isa-paper")
    before = counter._value.get()
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._run_account_stream_once(
        client,
        "isa-paper",
        account_number,
        AccountStream(account_id, "isa-paper", account_number),
    )

    assert counter._value.get() == before + 3
