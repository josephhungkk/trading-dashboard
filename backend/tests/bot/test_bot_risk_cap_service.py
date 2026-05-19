import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.bot.risk_caps import BotRiskCapError, BotRiskCapService


def make_caps(**kwargs):
    defaults = {
        "max_order_size": None,
        "max_open_orders": None,
        "max_daily_loss": None,
        "allowed_asset_classes": None,
        "max_position_size": None,
    }
    defaults.update(kwargs)
    return defaults


@pytest.fixture
def redis_mock():
    m = AsyncMock()
    m.get = AsyncMock(return_value=None)
    m.setex = AsyncMock()
    m.hget = AsyncMock(return_value=None)
    return m


@pytest.mark.asyncio
async def test_max_order_size_block(redis_mock):
    bot_id = uuid4()
    svc = BotRiskCapService(bot_id=bot_id, redis=redis_mock)
    caps = make_caps(max_order_size=Decimal("1000"))
    redis_mock.get = AsyncMock(return_value=json.dumps(caps, default=str))

    with pytest.raises(BotRiskCapError, match="max_order_size"):
        await svc.check(
            account_id=uuid4(),
            broker_id="ibkr",
            asset_class="STOCK",
            qty=Decimal("100"),
            price=Decimal("20"),  # 100*20 = 2000 > 1000
            side="BUY",
            instrument_id=1,
            db=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_max_order_size_pass(redis_mock):
    bot_id = uuid4()
    svc = BotRiskCapService(bot_id=bot_id, redis=redis_mock)
    caps = make_caps(max_order_size=Decimal("5000"))
    redis_mock.get = AsyncMock(return_value=json.dumps(caps, default=str))

    await svc.check(
        account_id=uuid4(),
        broker_id="ibkr",
        asset_class="STOCK",
        qty=Decimal("100"),
        price=Decimal("20"),
        side="BUY",
        instrument_id=1,
        db=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_daily_loss_cap_block(redis_mock):
    bot_id = uuid4()
    account_id = uuid4()

    async def redis_get(key):
        if "daily_loss" in key:
            return "-600"
        return json.dumps(make_caps(max_daily_loss=Decimal("500")), default=str)

    redis_mock.get = AsyncMock(side_effect=redis_get)
    svc = BotRiskCapService(bot_id=bot_id, redis=redis_mock)

    with pytest.raises(BotRiskCapError, match="max_daily_loss"):
        await svc.check(
            account_id=account_id,
            broker_id="ibkr",
            asset_class="STOCK",
            qty=Decimal("1"),
            price=Decimal("1"),
            side="BUY",
            instrument_id=1,
            db=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_allowed_asset_classes_block(redis_mock):
    bot_id = uuid4()
    caps = make_caps(allowed_asset_classes=["STOCK", "ETF"])
    redis_mock.get = AsyncMock(return_value=json.dumps(caps, default=str))
    svc = BotRiskCapService(bot_id=bot_id, redis=redis_mock)

    with pytest.raises(BotRiskCapError, match="asset_class"):
        await svc.check(
            account_id=uuid4(),
            broker_id="ibkr",
            asset_class="CRYPTO",
            qty=Decimal("1"),
            price=Decimal("1"),
            side="BUY",
            instrument_id=1,
            db=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_no_caps_passes_all(redis_mock):
    bot_id = uuid4()
    redis_mock.get = AsyncMock(return_value=json.dumps(make_caps(), default=str))
    svc = BotRiskCapService(bot_id=bot_id, redis=redis_mock)

    await svc.check(
        account_id=uuid4(),
        broker_id="ibkr",
        asset_class="CRYPTO",
        qty=Decimal("1000000"),
        price=Decimal("1000000"),
        side="BUY",
        instrument_id=1,
        db=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_redis_failure_fail_open(redis_mock):
    bot_id = uuid4()
    redis_mock.get = AsyncMock(side_effect=Exception("redis down"))
    svc = BotRiskCapService(bot_id=bot_id, redis=redis_mock)

    await svc.check(
        account_id=uuid4(),
        broker_id="ibkr",
        asset_class="STOCK",
        qty=Decimal("1"),
        price=Decimal("1"),
        side="BUY",
        instrument_id=1,
        db=AsyncMock(),
    )
