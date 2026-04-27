from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime, timedelta
from types import MethodType
from typing import cast
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.brokers.base import Contract, Money, Order, OrderEventMessage, OrderStatus
from app.core import metrics
from app.core.config import settings
from app.core.ids import uuid7
from app.services.brokers import BrokerRegistry
from app.services.order_event_consumer import OrderEventConsumer
from app.services.pending_submit_watchdog import PendingSubmitWatchdog


class _RecordingRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | bytes]] = []

    async def publish(self, channel: str, message: str | bytes) -> int:
        self.messages.append((channel, message))
        return 1


class _BrokerClient:
    def __init__(self, label: str, orders: list[Order]) -> None:
        self.label = label
        self.orders = orders
        self.seen_accounts: list[str] = []

    async def get_orders(self, account_number: str) -> list[Order]:
        self.seen_accounts.append(account_number)
        return self.orders


class _Registry:
    def __init__(self, clients: dict[str, _BrokerClient] | None = None) -> None:
        self._clients = clients or {}

    async def get_client(self, label: str) -> _BrokerClient:
        return self._clients[label]


class _RecordingConsumer:
    def __init__(self) -> None:
        self.events: list[OrderEventMessage] = []

    async def _process_event(self, event: OrderEventMessage) -> None:
        self.events.append(event)


class _CountingWatchdog(PendingSubmitWatchdog):
    def __init__(self) -> None:
        self.scan_times: list[datetime] = []
        super().__init__(
            _Registry(),
            cast(async_sessionmaker[AsyncSession], None),
            _RecordingConsumer(),
        )

    async def _scan_once(self) -> None:
        self.scan_times.append(self._utcnow())


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
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
                      WHERE account_number LIKE 'UTEST_E2_%'
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
                      WHERE account_number LIKE 'UTEST_E2_%'
                 )
                """
            )
        )
        await conn.execute(
            text("DELETE FROM broker_accounts WHERE account_number LIKE 'UTEST_E2_%'")
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
    created_at: datetime | None = None,
) -> UUID:
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO orders (
                    id, account_id, client_order_id, conid, symbol, side, order_type, tif,
                    qty, status, filled_qty, avg_fill_price, notional, notional_filled,
                    created_at, updated_at
                )
                VALUES (
                    :id, :account_id, :client_order_id, '265598', 'AAPL', 'BUY', 'MARKET',
                    'DAY', 10, CAST(:status AS order_status_enum), 0, NULL, 1000, 0,
                    COALESCE(:created_at, now()), COALESCE(:created_at, now())
                )
                RETURNING id
                """
            ),
            {
                "id": uuid7(),
                "account_id": account_id,
                "client_order_id": client_order_id,
                "status": status,
                "created_at": created_at,
            },
        )
        return UUID(str(result.scalar_one()))


async def _order_row(db_engine: AsyncEngine, order_id: UUID) -> dict[str, object]:
    async with db_engine.connect() as conn:
        row = (
            (await conn.execute(text("SELECT * FROM orders WHERE id = :id"), {"id": order_id}))
            .mappings()
            .one()
        )
    return dict(row)


async def _event_rows(db_engine: AsyncEngine, account_id: UUID) -> list[dict[str, object]]:
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


def _order(
    *,
    order_id: UUID,
    status: OrderStatus = "SUBMITTED",
    updated_at: datetime | None = None,
) -> Order:
    money = Money(value="", currency="USD")
    return Order(
        order_id=str(order_id),
        contract=Contract(
            symbol="AAPL",
            exchange="SMART",
            currency="USD",
            asset_class="STOCK",
            conid="265598",
            local_symbol="AAPL",
        ),
        side="BUY",
        order_type="MARKET",
        quantity="10",
        limit_price=money,
        stop_price=money,
        time_in_force="DAY",
        status=status,
        quantity_filled="0",
        avg_fill_price=money,
        submitted_at=updated_at,
        updated_at=updated_at,
    )


def _counter_value(name: str, *, label: str) -> float:
    return cast(float, getattr(metrics, name).labels(label=label)._value.get())


@pytest.mark.asyncio
async def test_watchdog_finds_stuck_pending_after_60s(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    account_id = await _seed_account(db_engine, account_number="UTEST_E2_FIND")
    client_order_id = uuid4()
    await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        created_at=now - timedelta(seconds=61),
    )
    client = _BrokerClient("isa-paper", [_order(order_id=client_order_id, updated_at=now)])
    consumer = _RecordingConsumer()
    watchdog = PendingSubmitWatchdog(_Registry({"isa-paper": client}), session_factory, consumer)

    await watchdog._scan_once()

    assert client.seen_accounts == ["UTEST_E2_FIND"]
    assert [event.client_order_id for event in consumer.events] == [str(client_order_id)]


@pytest.mark.asyncio
async def test_watchdog_recovers_from_broker_match(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    account_id = await _seed_account(db_engine, account_number="UTEST_E2_RECOVER")
    client_order_id = uuid4()
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        created_at=now - timedelta(seconds=61),
    )
    client = _BrokerClient("isa-paper", [_order(order_id=client_order_id, updated_at=now)])
    consumer = OrderEventConsumer(
        cast(BrokerRegistry, _Registry()),
        session_factory,
        cast(Redis, _RecordingRedis()),
    )
    before = _counter_value("broker_order_pending_submit_recovered_total", label="isa-paper")
    watchdog = PendingSubmitWatchdog(_Registry({"isa-paper": client}), session_factory, consumer)

    await watchdog._scan_once()

    assert (await _order_row(db_engine, order_id))["status"] == "submitted"
    assert _counter_value("broker_order_pending_submit_recovered_total", label="isa-paper") == (
        before + 1
    )


@pytest.mark.asyncio
async def test_watchdog_5min_no_match_escalates_to_rejected(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    account_id = await _seed_account(db_engine, account_number="UTEST_E2_ORPHAN")
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=uuid4(),
        created_at=now - timedelta(minutes=5, seconds=1),
    )
    before = _counter_value("broker_order_pending_submit_orphan_total", label="isa-paper")
    watchdog = PendingSubmitWatchdog(
        _Registry({"isa-paper": _BrokerClient("isa-paper", [])}),
        session_factory,
        _RecordingConsumer(),
    )

    await watchdog._scan_once()

    events = await _event_rows(db_engine, account_id)
    assert (await _order_row(db_engine, order_id))["status"] == "rejected"
    assert events[0]["status"] == "rejected"
    assert cast(dict[str, str], events[0]["raw_payload"])["recovery_outcome"] == (
        "broker_no_match_after_5min"
    )
    assert _counter_value("broker_order_pending_submit_orphan_total", label="isa-paper") == (
        before + 1
    )


@pytest.mark.asyncio
async def test_watchdog_runs_every_30s(monkeypatch: pytest.MonkeyPatch) -> None:
    watchdog = _CountingWatchdog()
    clock = iter(
        [
            datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
            datetime(2026, 4, 27, 10, 0, 30, tzinfo=UTC),
        ]
    )
    observed_timeouts: list[float] = []

    def fake_utcnow(self: PendingSubmitWatchdog) -> datetime:
        return next(clock)

    async def fake_wait_for(
        awaitable: Awaitable[bool],
        *,
        timeout: float,  # noqa: ASYNC109  # mocking asyncio.wait_for signature
    ) -> bool:
        _ = awaitable
        observed_timeouts.append(timeout)
        if len(observed_timeouts) == 1:
            raise TimeoutError
        return True

    monkeypatch.setattr(
        watchdog,
        "_utcnow",
        MethodType(fake_utcnow, watchdog),
    )
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    await watchdog.start()
    await asyncio.sleep(0)
    await watchdog.stop()

    assert observed_timeouts == [30.0, 30.0]
    assert len(watchdog.scan_times) == 2
    assert watchdog.scan_times[1] - watchdog.scan_times[0] == timedelta(seconds=30)


@pytest.mark.asyncio
async def test_startup_reconciliation_runs_same_pass() -> None:
    watchdog = _CountingWatchdog()

    await watchdog.reconcile_at_startup()

    assert len(watchdog.scan_times) == 1


@pytest.mark.asyncio
async def test_watchdog_uses_partial_index(db_engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    account_id = await _seed_account(db_engine, account_number="UTEST_E2_INDEX")
    await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=uuid4(),
        created_at=now - timedelta(seconds=61),
    )

    async with db_engine.begin() as conn:
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS)
                        SELECT
                            o.id,
                            o.client_order_id,
                            o.created_at,
                            ba.gateway_label,
                            ba.account_number,
                            ba.id AS account_id
                        FROM orders o
                        JOIN broker_accounts ba ON ba.id = o.account_id
                        WHERE o.status = 'pending_submit'::order_status_enum
                          AND o.created_at < :stale_threshold
                        ORDER BY o.created_at
                        """
                    ),
                    {"stale_threshold": now - timedelta(seconds=60)},
                )
            )
            .mappings()
            .all()
        )
    plan = "\n".join(str(row["QUERY PLAN"]) for row in rows)
    assert "ix_orders_pending_submit_watchdog" in plan


@pytest.mark.asyncio
async def test_watchdog_escalation_writes_audit_event_in_same_tx(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    account_id = await _seed_account(db_engine, account_number="UTEST_E2_ATOMIC")
    rollback_order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=uuid4(),
        created_at=now - timedelta(minutes=5, seconds=1),
    )
    watchdog = PendingSubmitWatchdog(
        _Registry({"isa-paper": _BrokerClient("isa-paper", [])}),
        session_factory,
        _RecordingConsumer(),
    )

    # Pass an account_id that doesn't exist in broker_accounts so the audit-row
    # INSERT raises IntegrityError on the FK constraint. Architect-review P13:
    # the orders UPDATE -> rejected MUST be paired with the order_events INSERT
    # in the SAME transaction; rolling back the audit row must also roll back
    # the UPDATE.
    with pytest.raises(IntegrityError):
        await watchdog._escalate_to_rejected(
            order_id=rollback_order_id,
            account_id=uuid4(),
            gateway_label="isa-paper",
            now=now,
        )

    rollback_order = await _order_row(db_engine, rollback_order_id)
    events = await _event_rows(db_engine, account_id)
    # UPDATE rolled back: still pending_submit.
    assert rollback_order["status"] == "pending_submit"
    # No audit row written for rollback_order (the FK violation rolled back
    # the entire savepoint).
    assert all(e["order_id"] != rollback_order_id for e in events)
