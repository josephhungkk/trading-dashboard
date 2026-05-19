from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.bot.context import BotAccountError, BotContext
from app.services.advisor.types import AdvisorVerdict, AdvisorVetoedResult

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_place_order_unknown_account_raises():
    bot_id = uuid4()
    account_id = uuid4()
    ctx = BotContext(
        bot_id=bot_id,
        run_id=uuid4(),
        accounts=[uuid4()],  # different account
        mode="paper",
        facade=MagicMock(),
        risk_cap_svc=MagicMock(),
        db=AsyncMock(),
        redis=AsyncMock(),
    )
    with pytest.raises(BotAccountError):
        await ctx.place_order(
            account_id=account_id,
            canonical_id="AAPL",
            side="BUY",
            qty=Decimal("1"),
            order_type="MKT",
        )


@pytest.mark.asyncio
async def test_place_order_records_bot_orders():
    """place_order calls db.execute to insert into bot_orders after facade.place_order."""
    bot_id = uuid4()
    account_id = uuid4()
    order_id = uuid4()

    facade = AsyncMock()
    facade.place_order = AsyncMock(return_value=MagicMock(order_id=order_id))

    risk_svc = AsyncMock()
    risk_svc.check = AsyncMock()

    # Mock DB execute to avoid FK constraints in unit test
    db = AsyncMock()
    # Return empty rows for symbol_aliases and instruments lookups
    empty_result = MagicMock()
    empty_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=empty_result)
    db.commit = AsyncMock()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="paper")
    redis.setex = AsyncMock()

    ctx = BotContext(
        bot_id=bot_id,
        run_id=uuid4(),
        accounts=[account_id],
        mode="paper",
        facade=facade,
        risk_cap_svc=risk_svc,
        db=db,
        redis=redis,
    )

    with patch.object(ctx, "_verify_account_mode", AsyncMock()):
        await ctx.place_order(
            account_id=account_id,
            canonical_id="AAPL",
            side="BUY",
            qty=Decimal("10"),
            order_type="MKT",
        )

    # Verify db.execute was called (for bot_orders insert)
    assert db.execute.called
    assert db.commit.called
    # Verify facade.place_order was called once
    facade.place_order.assert_called_once()


def _db_for_place_order() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


def _redis_for_place_order() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="paper")
    redis.setex = AsyncMock()
    redis.zadd = AsyncMock()
    redis.zremrangebyscore = AsyncMock()
    redis.zcount = AsyncMock(return_value=0)
    redis.xadd = AsyncMock()
    return redis


def _facade_for_place_order() -> MagicMock:
    facade = MagicMock()
    facade.place_order = AsyncMock(return_value=MagicMock(order_id=uuid4()))
    return facade


def _risk_for_place_order() -> MagicMock:
    risk = MagicMock()
    risk.check = AsyncMock()
    return risk


def _ctx_for_advisor_test(
    *,
    account_id,
    advisor=None,
    advisor_config=None,
    account_overrides=None,
    facade=None,
):
    return BotContext(
        bot_id=uuid4(),
        run_id=uuid4(),
        accounts=[account_id],
        mode="paper",
        facade=facade or _facade_for_place_order(),
        risk_cap_svc=_risk_for_place_order(),
        db=_db_for_place_order(),
        redis=_redis_for_place_order(),
        advisor=advisor,
        advisor_config=advisor_config,
        account_overrides=account_overrides,
    )


async def _place_test_order(ctx: BotContext, account_id):
    with patch.object(ctx, "_verify_account_mode", AsyncMock()):
        return await ctx.place_order(
            account_id=account_id,
            canonical_id="AAPL",
            side="BUY",
            qty=Decimal("10"),
            order_type="MKT",
        )


@pytest.mark.asyncio
async def test_advisor_none_skips_gate():
    account_id = uuid4()
    facade = _facade_for_place_order()
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=None, facade=facade)

    await _place_test_order(ctx, account_id)

    facade.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_advisor_approve_facade_called():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(return_value=(AdvisorVerdict(action="approve"), 1))
    facade = _facade_for_place_order()
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=advisor, facade=facade)

    await _place_test_order(ctx, account_id)

    advisor.review.assert_awaited_once()
    facade.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_advisor_fail_open_facade_called():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(return_value=(AdvisorVerdict(action="fail_open"), 2))
    facade = _facade_for_place_order()
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=advisor, facade=facade)

    await _place_test_order(ctx, account_id)

    advisor.review.assert_awaited_once()
    facade.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_advisor_veto_returns_vetoed_result():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(
        return_value=(AdvisorVerdict(action="veto", reasoning="too big"), 99)
    )
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=advisor)

    result = await _place_test_order(ctx, account_id)

    assert isinstance(result, AdvisorVetoedResult)
    assert result.decision_id == 99
    assert result.reasoning == "too big"


@pytest.mark.asyncio
async def test_advisor_veto_facade_not_called():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(return_value=(AdvisorVerdict(action="veto"), 99))
    facade = _facade_for_place_order()
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=advisor, facade=facade)

    await _place_test_order(ctx, account_id)

    facade.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_advisor_hook_called_on_veto():
    account_id = uuid4()
    advisor = MagicMock()
    verdict = AdvisorVerdict(action="veto", reasoning="too big")
    advisor.review = AsyncMock(return_value=(verdict, 99))
    strategy = MagicMock()
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=advisor)
    ctx.set_strategy_ref(strategy)

    await _place_test_order(ctx, account_id)

    strategy.on_advisor_reject.assert_called_once()
    assert strategy.on_advisor_reject.call_args.args[1] == verdict


@pytest.mark.asyncio
async def test_advisor_hook_exception_does_not_block_veto():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(return_value=(AdvisorVerdict(action="veto"), 99))
    strategy = MagicMock()
    strategy.on_advisor_reject.side_effect = RuntimeError("hook failed")
    ctx = _ctx_for_advisor_test(account_id=account_id, advisor=advisor)
    ctx.set_strategy_ref(strategy)

    result = await _place_test_order(ctx, account_id)

    assert isinstance(result, AdvisorVetoedResult)
    assert result.decision_id == 99


@pytest.mark.asyncio
async def test_per_account_override_uses_override_config():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(return_value=(AdvisorVerdict(action="approve"), 1))
    ctx = _ctx_for_advisor_test(
        account_id=account_id,
        advisor=advisor,
        advisor_config={"mode": "VETO"},
        account_overrides={str(account_id): {"mode": "OBSERVE"}},
    )

    await _place_test_order(ctx, account_id)

    effective_config = advisor.review.call_args.kwargs["effective_config"]
    assert effective_config.mode.value == "OBSERVE"


@pytest.mark.asyncio
async def test_null_override_uses_bot_default():
    account_id = uuid4()
    advisor = MagicMock()
    advisor.review = AsyncMock(return_value=(AdvisorVerdict(action="approve"), 1))
    ctx = _ctx_for_advisor_test(
        account_id=account_id,
        advisor=advisor,
        advisor_config={"mode": "VETO"},
    )

    await _place_test_order(ctx, account_id)

    effective_config = advisor.review.call_args.kwargs["effective_config"]
    assert effective_config.mode.value == "VETO"


def test_set_strategy_ref_weakref():
    account_id = uuid4()
    strategy = MagicMock()
    ctx = _ctx_for_advisor_test(account_id=account_id)

    ctx.set_strategy_ref(strategy)

    assert ctx._strategy_ref() is strategy
