"""5c D5: consumer fills + status-rank + cascade unit tests.

Covers behaviors landed in D1 (status-rank predicate), D2 (pending_fills
buffer + drain), D3 (commission_buffer / commission_report — partially —
the buffer push paths are exercised inline within fills tests), and D4
(cascade latency histogram).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.brokers.base import OrderEventMessage
from app.core import metrics
from app.core.config import settings
from app.core.ids import uuid7
from app.services.order_event_consumer import OrderEventConsumer

_ACCOUNT_PREFIX = "UTEST_FILLS_"


class _RecordingRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | bytes]] = []

    async def publish(self, channel: str, message: str | bytes) -> int:
        self.messages.append((channel, message))
        return 1

    async def execute_command(self, *args: object) -> object:
        """Stub for Redis commands used by _commission_buffer_pop / _commission_buffer_set.

        HGETALL returns an empty list (no buffered commission); HSET/EXPIRE/DEL
        return stub values so the consumer doesn't raise AttributeError and
        silently swallow the fill event.
        """
        cmd = str(args[0]).upper() if args else ""
        if cmd == "HGETALL":
            return []  # empty dict → _commission_buffer_pop returns None
        return 1


class _Registry:
    def __init__(self) -> None:
        pass


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
                "DELETE FROM fills WHERE order_id IN ("
                " SELECT o.id FROM orders o JOIN broker_accounts a ON a.id = o.account_id "
                f" WHERE a.account_number LIKE '{_ACCOUNT_PREFIX}%')"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM pending_fills WHERE account_id IN ("
                f" SELECT id FROM broker_accounts WHERE account_number LIKE '{_ACCOUNT_PREFIX}%')"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM order_events WHERE account_id IN ("
                f" SELECT id FROM broker_accounts WHERE account_number LIKE '{_ACCOUNT_PREFIX}%')"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM orders WHERE account_id IN ("
                f" SELECT id FROM broker_accounts WHERE account_number LIKE '{_ACCOUNT_PREFIX}%')"
            )
        )
        await conn.execute(
            text(f"DELETE FROM broker_accounts WHERE account_number LIKE '{_ACCOUNT_PREFIX}%'")
        )


async def _seed_account(
    db_engine: AsyncEngine, *, account_number: str, label: str = "isa-paper"
) -> UUID:
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, mode, gateway_label, currency_base, last_seen_via) "
                "VALUES ('ibkr', :acct, 'paper', :label, 'USD', :label) RETURNING id"
            ),
            {"acct": account_number, "label": label},
        )
        return UUID(str(result.scalar_one()))


async def _seed_order(
    db_engine: AsyncEngine,
    *,
    account_id: UUID,
    client_order_id: UUID,
    broker_order_id: str | None = None,
    status: str = "pending_submit",
    parent_order_id: UUID | None = None,
    last_event_at: datetime | None = None,
    cancel_requested_at: datetime | None = None,
) -> UUID:
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO orders ("
                "  id, account_id, client_order_id, broker_order_id, conid, symbol, side, "
                "  order_type, tif, qty, status, filled_qty, notional, notional_filled, "
                "  parent_order_id, last_event_at, cancel_requested_at"
                ") VALUES ("
                "  :id, :acct, :coid, :bo, '265598', 'AAPL', 'BUY', 'MARKET', 'DAY', "
                "  10, CAST(:status AS order_status_enum), 0, 1000, 0, "
                "  :pid, :last, :crat"
                ") RETURNING id"
            ),
            {
                "id": uuid7(),
                "acct": account_id,
                "coid": client_order_id,
                "bo": broker_order_id,
                "status": status,
                "pid": parent_order_id,
                "last": last_event_at,
                "crat": cancel_requested_at,
            },
        )
        return UUID(str(result.scalar_one()))


def _event(
    *,
    account_id: UUID,
    account_number: str,
    label: str = "isa-paper",
    client_order_id: UUID | str | None,
    broker_order_id: str = "BO-FILLS-1",
    status: str = "submitted",
    filled_qty: str = "0",
    avg_fill_price: str = "",
    broker_event_at: datetime | None = None,
    exec_id: str = "",
    kind: str = "",
    raw_payload_extra: dict[str, object] | None = None,
) -> OrderEventMessage:
    payload: dict[str, object] = {
        "account_id": str(account_id),
        "gateway_label": label,
        "account_number": account_number,
    }
    if raw_payload_extra is not None:
        payload.update(raw_payload_extra)
    return OrderEventMessage(
        broker_order_id=broker_order_id,
        client_order_id="" if client_order_id is None else str(client_order_id),
        status=status,
        filled_qty=filled_qty,
        avg_fill_price=avg_fill_price,
        broker_event_at=broker_event_at or datetime.now(UTC),
        raw_payload=json.dumps(payload),
        exec_id=exec_id,
        kind=kind,
    )


async def _scalar(db_engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    async with db_engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar_one()


def _cascade_count() -> int:
    samples = list(metrics.broker_bracket_cancel_cascade_seconds.collect()[0].samples)
    return int(next(s.value for s in samples if s.name.endswith("_count")))


@pytest.mark.asyncio
async def test_exec_details_writes_fills_row(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[Any]
) -> None:
    account_id = await _seed_account(db_engine, account_number=f"{_ACCOUNT_PREFIX}EXDET")
    client_order_id = uuid4()
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        broker_order_id="BO-EXDET",
        status="submitted",
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number=f"{_ACCOUNT_PREFIX}EXDET",
            client_order_id=client_order_id,
            broker_order_id="BO-EXDET",
            status="filled",
            filled_qty="10",
            avg_fill_price="100",
            exec_id="EX-EXDET-1",
            kind="exec_details",
            raw_payload_extra={"currency": "USD"},
        )
    )

    count = await _scalar(
        db_engine, "SELECT count(*) FROM fills WHERE order_id = :o", {"o": order_id}
    )
    assert count == 1
    body = await _scalar(
        db_engine,
        "SELECT json_build_object('exec_id', exec_id, 'qty', qty::text, "
        "'price', price::text, 'currency', currency)::text FROM fills WHERE order_id = :o",
        {"o": order_id},
    )
    parsed = json.loads(body)
    assert parsed["exec_id"] == "EX-EXDET-1"
    assert Decimal(parsed["qty"]) == Decimal("10")
    assert Decimal(parsed["price"]) == Decimal("100")
    assert parsed["currency"] == "USD"


@pytest.mark.asyncio
async def test_duplicate_exec_id_on_conflict_do_nothing(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[Any]
) -> None:
    account_id = await _seed_account(db_engine, account_number=f"{_ACCOUNT_PREFIX}DUP")
    client_order_id = uuid4()
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        broker_order_id="BO-DUP",
        status="submitted",
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]
    event = _event(
        account_id=account_id,
        account_number=f"{_ACCOUNT_PREFIX}DUP",
        client_order_id=client_order_id,
        broker_order_id="BO-DUP",
        status="filled",
        filled_qty="5",
        avg_fill_price="200",
        exec_id="EX-DUP-1",
        kind="exec_details",
    )

    await consumer._process_event(event)
    await consumer._process_event(event)

    count = await _scalar(
        db_engine, "SELECT count(*) FROM fills WHERE order_id = :o", {"o": order_id}
    )
    assert count == 1


@pytest.mark.asyncio
async def test_fk_violation_falls_back_to_pending_fills(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[Any]
) -> None:
    account_id = await _seed_account(db_engine, account_number=f"{_ACCOUNT_PREFIX}FKFB")
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number=f"{_ACCOUNT_PREFIX}FKFB",
            client_order_id=uuid4(),
            broker_order_id="BO-NO-MATCH",
            status="filled",
            filled_qty="3",
            avg_fill_price="50",
            exec_id="EX-FKFB-1",
            kind="exec_details",
            raw_payload_extra={"currency": "USD"},
        )
    )

    pending = await _scalar(
        db_engine,
        "SELECT count(*) FROM pending_fills WHERE exec_id = :e",
        {"e": "EX-FKFB-1"},
    )
    fills = await _scalar(
        db_engine,
        "SELECT count(*) FROM fills WHERE exec_id = :e",
        {"e": "EX-FKFB-1"},
    )
    assert pending == 1
    assert fills == 0


@pytest.mark.asyncio
async def test_pending_fills_drained_after_order_arrives(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[Any]
) -> None:
    account_id = await _seed_account(db_engine, account_number=f"{_ACCOUNT_PREFIX}DRAIN")
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO pending_fills "
                "(exec_id, broker_order_id, account_id, qty, price, currency, executed_at, "
                " raw_payload) VALUES (:e, :bo, :a, :q, :p, :c, :ts, CAST(:rp AS jsonb))"
            ),
            {
                "e": "EX-DRAIN-1",
                "bo": "BO-DRAIN",
                "a": account_id,
                "q": "5",
                "p": "75",
                "c": "USD",
                "ts": datetime.now(UTC),
                "rp": "{}",
            },
        )

    client_order_id = uuid4()
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        broker_order_id="BO-DRAIN",
        # Seed as pending_submit so that the "submitted" event status differs
        # from the current DB status, bypassing the idempotency early-return
        # guard (CRIT-2 Phase 8a) that skips events where current == new_status
        # and exec_id is empty.
        status="pending_submit",
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number=f"{_ACCOUNT_PREFIX}DRAIN",
            client_order_id=client_order_id,
            broker_order_id="BO-DRAIN",
            status="submitted",
        )
    )

    fills = await _scalar(
        db_engine, "SELECT count(*) FROM fills WHERE order_id = :o", {"o": order_id}
    )
    pending = await _scalar(
        db_engine,
        "SELECT count(*) FROM pending_fills WHERE broker_order_id = :bo",
        {"bo": "BO-DRAIN"},
    )
    assert fills == 1
    assert pending == 0


@pytest.mark.asyncio
async def test_status_rank_accepts_equal_rank_modified_to_submitted(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[Any]
) -> None:
    account_id = await _seed_account(db_engine, account_number=f"{_ACCOUNT_PREFIX}RANK")
    client_order_id = uuid4()
    last_event_at = datetime.now(UTC) - timedelta(seconds=10)
    order_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=client_order_id,
        broker_order_id="BO-RANK",
        status="modified",
        last_event_at=last_event_at,
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number=f"{_ACCOUNT_PREFIX}RANK",
            client_order_id=client_order_id,
            broker_order_id="BO-RANK",
            status="submitted",
            broker_event_at=datetime.now(UTC),
        )
    )

    status = await _scalar(
        db_engine, "SELECT status::text FROM orders WHERE id = :o", {"o": order_id}
    )
    assert status == "submitted"


@pytest.mark.asyncio
async def test_cascade_latency_observed_on_child_cancel(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[Any]
) -> None:
    account_id = await _seed_account(db_engine, account_number=f"{_ACCOUNT_PREFIX}CASC")
    parent_id = await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=uuid4(),
        broker_order_id="BO-CASC-PARENT",
        status="cancelled",
        cancel_requested_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    child_client = uuid4()
    await _seed_order(
        db_engine,
        account_id=account_id,
        client_order_id=child_client,
        broker_order_id="BO-CASC-CHILD",
        status="submitted",
        parent_order_id=parent_id,
    )
    consumer = OrderEventConsumer(_Registry(), session_factory, _RecordingRedis())  # type: ignore[arg-type]
    before = _cascade_count()

    await consumer._process_event(
        _event(
            account_id=account_id,
            account_number=f"{_ACCOUNT_PREFIX}CASC",
            client_order_id=child_client,
            broker_order_id="BO-CASC-CHILD",
            status="cancelled",
            broker_event_at=datetime.now(UTC),
        )
    )

    assert _cascade_count() == before + 1
