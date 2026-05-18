from unittest.mock import AsyncMock

import pytest

from app.services.futures.settlement_listener import _record_settlement


@pytest.mark.asyncio
async def test_record_settlement_cash_inserts_and_notifies():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    redis = AsyncMock()
    redis.publish = AsyncMock()

    telegram = AsyncMock()
    telegram.send_message = AsyncMock()

    event = {
        "account_id": "123",
        "instrument_id": "456",
        "symbol": "ES",
        "settlement_price": "5234.25",
        "cash_delta": "100.50",
        "settlement_type": "CASH",
        "settled_at": "2023-01-01T00:00:00Z",
    }

    await _record_settlement(db=db, redis=redis, telegram=telegram, event=event)

    assert db.execute.called
    assert redis.publish.called
    assert telegram.send_message.called

    call_args = telegram.send_message.call_args
    msg = call_args.kwargs.get("text") or call_args[1].get("text")
    assert "5234.25" in msg
    assert "CASH" in msg


@pytest.mark.asyncio
async def test_record_settlement_physical_warning_message():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    redis = AsyncMock()
    redis.publish = AsyncMock()

    telegram = AsyncMock()
    telegram.send_message = AsyncMock()

    event = {
        "account_id": "123",
        "instrument_id": "456",
        "symbol": "ES",
        "settlement_price": "5234.25",
        "cash_delta": "0",
        "settlement_type": "PHYSICAL",
        "settled_at": "2023-01-01T00:00:00Z",
    }

    await _record_settlement(db=db, redis=redis, telegram=telegram, event=event)

    call_args = telegram.send_message.call_args
    msg = call_args.kwargs.get("text") or call_args[1].get("text")
    assert "physical" in msg.lower() or "delivery" in msg.lower()


@pytest.mark.asyncio
async def test_record_settlement_notification_failure_does_not_raise():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    redis = AsyncMock()
    redis.publish = AsyncMock()

    telegram = AsyncMock()
    telegram.send_message = AsyncMock(side_effect=Exception("Telegram error"))

    event = {
        "account_id": "123",
        "instrument_id": "456",
        "symbol": "ES",
        "settlement_price": "5234.25",
        "cash_delta": "100.50",
        "settlement_type": "CASH",
        "settled_at": "2023-01-01T00:00:00Z",
    }

    # Should not raise
    await _record_settlement(db=db, redis=redis, telegram=telegram, event=event)

    assert db.execute.called
