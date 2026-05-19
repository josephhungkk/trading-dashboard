import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.bot.fill_router import BotFillRouter


@pytest.mark.asyncio
async def test_fill_for_bot_order_publishes_to_bot():
    """When bot_orders row exists, fill event is published to bot:fill:{bot_id}."""
    bot_id = uuid4()
    order_id = uuid4()

    db = AsyncMock()
    # Simulate bot_orders row found
    row_result = MagicMock()
    row_result.first = MagicMock(return_value=(bot_id,))
    db.execute = AsyncMock(return_value=row_result)

    redis = AsyncMock()
    published: list = []
    redis.publish = AsyncMock(side_effect=lambda ch, msg: published.append((ch, msg)))
    redis.setex = AsyncMock()

    router = BotFillRouter(db=db, redis=redis)
    event = {
        "type": "order:fill",
        "order_id": str(order_id),
        "account_id": str(uuid4()),
        "canonical_id": "AAPL",
        "side": "buy",
        "qty": "10",
        "price": "150.50",
        "filled_at": "2026-01-02T10:01:00+00:00",
    }
    await router.handle_event(json.dumps(event))

    assert any(f"bot:fill:{bot_id}" in ch for ch, _ in published)


@pytest.mark.asyncio
async def test_non_bot_order_ignored():
    """Events for orders not in bot_orders are silently ignored."""
    db = AsyncMock()
    # Simulate no bot_orders row
    row_result = MagicMock()
    row_result.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=row_result)

    redis = AsyncMock()
    redis.publish = AsyncMock()

    router = BotFillRouter(db=db, redis=redis)
    event = {
        "type": "order:fill",
        "order_id": str(uuid4()),
        "account_id": str(uuid4()),
        "canonical_id": "AAPL",
        "side": "buy",
        "qty": "1",
        "price": "100",
        "filled_at": "2026-01-02T10:00:00+00:00",
    }
    await router.handle_event(json.dumps(event))
    redis.publish.assert_not_called()


@pytest.mark.asyncio
async def test_non_fill_event_ignored():
    """Events with type != 'order:fill' are silently ignored."""
    db = AsyncMock()
    redis = AsyncMock()
    redis.publish = AsyncMock()

    router = BotFillRouter(db=db, redis=redis)
    event = {"type": "order:placed", "order_id": str(uuid4())}
    await router.handle_event(json.dumps(event))
    redis.publish.assert_not_called()
    db.execute.assert_not_called()
