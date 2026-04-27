from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from app.brokers.base import OrderEventMessage
from app.core import metrics
from app.core.config import settings
from app.core.ids import uuid7
from app.core.logging import _redact_secrets
from app.services.order_event_consumer import AccountChangedEvent, OrderEventConsumer


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
                      WHERE account_number LIKE 'UTEST_E1_%'
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
                      WHERE account_number LIKE 'UTEST_E1_%'
                 )
                """
            )
        )
        await conn.execute(
            text("DELETE FROM broker_accounts WHERE account_number LIKE 'UTEST_E1_%'")
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
    payload = {
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


async def _event_rows(db_engine: AsyncEngine, account_id: UUID) -> list[dict[str, Any]]:
    async with db_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("SELECT * FROM order_events WHERE account_id = :id ORDER BY id"),
                    {"id": account_id},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_single_event_inserts_audit_row_and_upserts_order(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_SINGLE")
    client_order_id = uuid4()
    order_id = await _seed_order(db_engine, account_id=account_id, client_order_id=client_order_id)
    redis = _RecordingRedis()
    consumer = OrderEventConsumer(_Registry(), session_factory, redis)  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_SINGLE",
            client_order_id=client_order_id,
            status="submitted",
        )
    )

    order = await _order_row(db_engine, order_id)
    events = await _event_rows(db_engine, account_id)
    assert order["status"] == "submitted"
    assert events[0]["order_id"] == order_id
    assert [channel for channel, _ in redis.messages] == [
        "orders:events:fleet",
        f"orders:events:account:{account_id}",
    ]


@pytest.mark.asyncio
async def test_partial_fill_updates_filled_qty_and_avg_fill_price_and_notional_filled(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_PARTIAL")
    client_order_id = uuid4()
    order_id = await _seed_order(db_engine, account_id=account_id, client_order_id=client_order_id)
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_PARTIAL",
            client_order_id=client_order_id,
            status="partial",
            filled_qty="3.5",
            avg_fill_price="101.25",
        )
    )

    order = await _order_row(db_engine, order_id)
    assert order["filled_qty"] == Decimal("3.50000000")
    assert order["avg_fill_price"] == Decimal("101.25000000")
    assert order["notional_filled"] == Decimal("354.37500000")


@pytest.mark.asyncio
async def test_terminal_status_sticky(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_TERMINAL")
    client_order_id = uuid4()
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        status="filled",
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_TERMINAL",
            client_order_id=client_order_id,
            status="submitted",
        )
    )

    assert (await _order_row(db_engine, order_id))["status"] == "filled"


@pytest.mark.asyncio
async def test_out_of_order_events_dont_revert_state(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    now = datetime.now(UTC)
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_OLD")
    client_order_id = uuid4()
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        status="partial",
        filled_qty=Decimal("5"),
        avg_fill_price=Decimal("100"),
        notional_filled=Decimal("500"),
        last_event_at=now,
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_OLD",
            client_order_id=client_order_id,
            status="submitted",
            filled_qty="1",
            avg_fill_price="10",
            broker_event_at=now - timedelta(minutes=1),
        )
    )

    order = await _order_row(db_engine, order_id)
    assert order["status"] == "partial"
    assert order["filled_qty"] == Decimal("5.00000000")
    assert order["notional_filled"] == Decimal("500.00000000")


@pytest.mark.asyncio
async def test_malformed_event_savepoint_rolls_back_only_that_event(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_BAD")
    client_order_id = uuid4()
    order_id = await _seed_order(db_engine, account_id=account_id, client_order_id=client_order_id)
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]
    counter = metrics.broker_order_events_dropped_total.labels(
        label="isa-paper",
        reason="ValueError",
    )
    before = counter._value.get()

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_BAD",
            client_order_id=client_order_id,
            filled_qty="not-a-number",
        )
    )
    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_BAD",
            client_order_id=client_order_id,
            status="submitted",
        )
    )

    assert counter._value.get() == before + 1
    assert (await _order_row(db_engine, order_id))["status"] == "submitted"
    assert len(await _event_rows(db_engine, account_id)) == 1


@pytest.mark.asyncio
async def test_tws_placed_event_writes_audit_only(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_TWS")
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number="UTEST_E1_TWS",
            client_order_id=None,
            status="submitted",
        )
    )

    events = await _event_rows(db_engine, account_id)
    async with db_engine.connect() as conn:
        order_count = (
            await conn.execute(
                text("SELECT count(*) FROM orders WHERE account_id = :id"), {"id": account_id}
            )
        ).scalar_one()
    assert events[0]["order_id"] is None
    assert order_count == 0


async def _blocking_stream() -> AsyncIterator[OrderEventMessage]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")
    yield  # pragma: no cover


async def _failing_stream() -> AsyncIterator[OrderEventMessage]:
    raise RuntimeError("stream died")
    yield  # pragma: no cover


@pytest.mark.asyncio
async def test_account_added_spawns_new_child_stream(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_ADD")
    registry = _Registry({"isa-paper": _StreamClient("isa-paper", _blocking_stream)})
    consumer = OrderEventConsumer(registry, session_factory, _RecordingRedis())  # type: ignore[arg-type]
    await consumer.start()
    try:
        await registry.account_changed.put(
            AccountChangedEvent("add", "isa-paper", "UTEST_E1_ADD", account_id)
        )
        await asyncio.sleep(0.1)
        assert ("isa-paper", "UTEST_E1_ADD") in consumer._children
    finally:
        await consumer.stop()


@pytest.mark.asyncio
async def test_account_removed_cancels_child_stream(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    account_id = await _seed_account(db_engine, account_number="UTEST_E1_REMOVE")
    registry = _Registry({"isa-paper": _StreamClient("isa-paper", _blocking_stream)})
    consumer = OrderEventConsumer(registry, session_factory, _RecordingRedis())  # type: ignore[arg-type]
    await consumer.start()
    try:
        await registry.account_changed.put(
            AccountChangedEvent("add", "isa-paper", "UTEST_E1_REMOVE", account_id)
        )
        await asyncio.sleep(0.1)
        # Soft-delete the broker_accounts row before the `remove` event —
        # mirrors spec §5 ("on remove (soft-delete) cancels the child").
        # Without this, the supervisor's next iteration re-enumerates the
        # un-deleted row and re-spawns the child.
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE broker_accounts SET deleted_at = now() "
                    "WHERE account_number = 'UTEST_E1_REMOVE'"
                )
            )
        await registry.account_changed.put(
            AccountChangedEvent("remove", "isa-paper", "UTEST_E1_REMOVE")
        )
        await asyncio.sleep(0.1)
        assert ("isa-paper", "UTEST_E1_REMOVE") not in consumer._children
    finally:
        await consumer.stop()


@pytest.mark.asyncio
async def test_one_stream_death_doesnt_affect_siblings(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
) -> None:
    await _seed_account(db_engine, account_number="UTEST_E1_DEAD_A", label="dead")
    await _seed_account(db_engine, account_number="UTEST_E1_DEAD_B", label="alive")
    registry = _Registry(
        {
            "dead": _StreamClient("dead", _failing_stream),
            "alive": _StreamClient("alive", _blocking_stream),
        }
    )
    consumer = OrderEventConsumer(registry, session_factory, _RecordingRedis())  # type: ignore[arg-type]
    await consumer.start()
    try:
        await asyncio.sleep(0.2)
        sibling = consumer._children[("alive", "UTEST_E1_DEAD_B")]
        assert not sibling.done()
    finally:
        await consumer.stop()


def test_raw_payload_account_field_redacted_in_logs() -> None:
    payload = {
        "raw_payload": {
            "account": "DU123",
            "account_number": "DU456",
            "nested": {"acctNumber": "DU789"},
        }
    }
    with capture_logs(processors=[_redact_secrets]) as logs:
        import structlog

        structlog.get_logger("test").info("broker_payload", **payload)

    assert logs[0]["raw_payload"] == {
        "account": "<redacted>",
        "account_number": "<redacted>",
        "nested": {"acctNumber": "<redacted>"},
    }
