"""Phase 8a B5 - order event duplicate status no-op."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.brokers.base import OrderEventMessage
from app.services.order_event_consumer import OrderEventConsumer


class _AsyncContext:
    def __init__(self, value: Any = None) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: object) -> None:
        return None


class _Session:
    def __init__(self, current_status: str | None) -> None:
        self.execute = AsyncMock(return_value=_scalar_result(current_status))

    def begin(self) -> _AsyncContext:
        return _AsyncContext()

    def begin_nested(self) -> _AsyncContext:
        return _AsyncContext()


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self) -> _AsyncContext:
        return _AsyncContext(self.session)


class _Redis:
    async def publish(self, channel: str, message: str | bytes) -> int:
        return 1


def _scalar_result(value: str | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _event(
    *,
    account_id: UUID,
    status: str,
    exec_id: str = "",
) -> OrderEventMessage:
    return OrderEventMessage(
        broker_order_id="BO-1",
        client_order_id=str(uuid4()),
        status=status,
        filled_qty="0",
        avg_fill_price="",
        broker_event_at=datetime.now(UTC),
        raw_payload=json.dumps(
            {
                "account_id": str(account_id),
                "gateway_label": "isa-paper",
                "account_number": "U123",
            }
        ),
        exec_id=exec_id,
        kind="exec_details" if exec_id else "",
    )


def _consumer_with_session(session: _Session) -> OrderEventConsumer:
    return OrderEventConsumer(MagicMock(), _SessionFactory(session), _Redis())  # type: ignore[arg-type]


def _patch_write_path(consumer: OrderEventConsumer) -> None:
    consumer._matching_order_id = AsyncMock(return_value=uuid4())  # type: ignore[method-assign]
    consumer._insert_order_event = AsyncMock(return_value=123)  # type: ignore[method-assign]
    consumer._update_order = AsyncMock()  # type: ignore[method-assign]
    consumer._record_fill = AsyncMock()  # type: ignore[method-assign]
    consumer._drain_pending_fills = AsyncMock()  # type: ignore[method-assign]
    consumer._publish_payload = AsyncMock(return_value={"id": 123})  # type: ignore[method-assign]
    consumer._publish = AsyncMock()  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_duplicate_submitted_no_new_exec_is_noop() -> None:
    account_id = uuid4()
    session = _Session(current_status="submitted")
    consumer = _consumer_with_session(session)
    _patch_write_path(consumer)

    await consumer._process_event(_event(account_id=account_id, status="submitted"))

    assert session.execute.await_count == 1
    consumer._insert_order_event.assert_not_awaited()  # type: ignore[attr-defined]
    consumer._record_fill.assert_not_awaited()  # type: ignore[attr-defined]
    consumer._publish.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_duplicate_status_with_new_exec_id_records() -> None:
    account_id = uuid4()
    session = _Session(current_status="submitted")
    consumer = _consumer_with_session(session)
    _patch_write_path(consumer)

    await consumer._process_event(_event(account_id=account_id, status="submitted", exec_id="EX-1"))

    assert session.execute.await_count == 1
    consumer._insert_order_event.assert_awaited_once()  # type: ignore[attr-defined]
    consumer._record_fill.assert_awaited_once()  # type: ignore[attr-defined]
    consumer._publish.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_status_change_records() -> None:
    account_id = uuid4()
    session = _Session(current_status="submitted")
    consumer = _consumer_with_session(session)
    _patch_write_path(consumer)

    await consumer._process_event(_event(account_id=account_id, status="filled"))

    assert session.execute.await_count == 1
    consumer._insert_order_event.assert_awaited_once()  # type: ignore[attr-defined]
    consumer._update_order.assert_awaited_once()  # type: ignore[attr-defined]
    consumer._publish.assert_awaited_once()  # type: ignore[attr-defined]
