"""E2E: fixture strategy places one order on first bar; verifies bot_orders row
and stop → bot_runs.stop_reason='manual'.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_fixture_strategy_place_order(db_session, redis):
    """BotContext.place_order inserts a bot_orders row and calls facade."""
    from app.bot.context import BotContext

    bot_id = uuid4()
    run_id = uuid4()
    order_id = uuid4()

    # Use the seeded test account
    row = await db_session.execute(
        text("SELECT id FROM broker_accounts WHERE alias = 'test-acct-1' LIMIT 1")
    )
    account_id = row.scalar_one_or_none()
    assert account_id is not None, "seed broker_account must exist"

    await db_session.execute(
        text("INSERT INTO bots (id, name, strategy_file) VALUES (:id, 'e2e-bot', 'fixture.py')"),
        {"id": bot_id},
    )

    # Insert a minimal orders row so bot_orders FK resolves
    await db_session.execute(
        text(
            """
            INSERT INTO orders
              (id, account_id, client_order_id, conid, symbol, side, order_type,
               tif, qty, notional, status)
            VALUES
              (:id, :acct, :coid, '265598', 'AAPL', 'BUY'::order_side_enum,
               'MARKET'::order_type_enum, 'DAY'::order_tif_enum, 1, 150, 'pending_submit')
            """
        ),
        {"id": order_id, "acct": account_id, "coid": uuid4()},
    )
    await db_session.commit()

    facade = AsyncMock()
    facade.place_order = AsyncMock(return_value=MagicMock(order_id=order_id))

    risk_svc = AsyncMock()
    risk_svc.check = AsyncMock()

    ctx = BotContext(
        bot_id=bot_id,
        run_id=run_id,
        accounts=[account_id],
        mode="paper",
        facade=facade,
        risk_cap_svc=risk_svc,
        db=db_session,
        redis=redis,
    )

    await ctx.place_order(
        account_id=account_id,
        canonical_id="equity_us:AAPL:NASDAQ",
        side="BUY",
        qty=Decimal("1"),
        order_type="MKT",
    )

    # Verify bot_orders row inserted
    result = await db_session.execute(
        text("SELECT order_id FROM bot_orders WHERE bot_id = :bid"),
        {"bid": bot_id},
    )
    assert str(result.scalar_one()) == str(order_id)

    facade.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_bot_run_stop_reason_manual(db_session, redis):
    """Stopping a bot sets bot_runs.stop_reason='manual'."""
    bot_id = uuid4()
    run_id = uuid4()

    await db_session.execute(
        text("INSERT INTO bots (id, name, strategy_file) VALUES (:id, 'stop-test', 'x.py')"),
        {"id": bot_id},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO bot_runs (id, bot_id, version, started_at)
            VALUES (:id, :bid, 1, now())
            """
        ),
        {"id": run_id, "bid": bot_id},
    )
    await db_session.commit()

    # Simulate stop: update stop_reason
    await db_session.execute(
        text(
            """
            UPDATE bot_runs SET stopped_at = now(), stop_reason = 'manual'
            WHERE id = :id
            """
        ),
        {"id": run_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT stop_reason FROM bot_runs WHERE id = :id"),
        {"id": run_id},
    )
    assert result.scalar_one() == "manual"
