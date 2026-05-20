import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_table(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1 FROM portfolio_exposure_limits LIMIT 0"))


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_unique_total(session: AsyncSession) -> None:
    """Partial unique index prevents two total_notional rows for same account."""
    await session.execute(
        text(
            "INSERT INTO broker_accounts"
            " (id, broker_id, account_number, alias, mode,"
            " gateway_label, currency_base, last_seen_via)"
            " VALUES (gen_random_uuid(), 'ibkr', 'TEST22A',"
            " 'test22a', 'paper', 'ibkr', 'USD', 'ibkr')"
        )
    )
    result = await session.execute(
        text("SELECT id FROM broker_accounts WHERE account_number='TEST22A'")
    )
    acct_id = result.scalar_one()
    await session.execute(
        text(
            "INSERT INTO portfolio_exposure_limits (account_id, limit_type, max_notional, currency)"
            " VALUES (:aid, 'total_notional', 100000, 'USD')"
        ),
        {"aid": acct_id},
    )
    with pytest.raises(Exception, match="uq_portfolio_exposure_total"):
        await session.execute(
            text(
                "INSERT INTO portfolio_exposure_limits"
                " (account_id, limit_type, max_notional, currency)"
                " VALUES (:aid, 'total_notional', 200000, 'USD')"
            ),
            {"aid": acct_id},
        )


@pytest.mark.asyncio
async def test_portfolio_correlation_snapshots_table(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1 FROM portfolio_correlation_snapshots LIMIT 0"))


@pytest.mark.asyncio
async def test_shadow_promotion_events_promoted_via_column(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='shadow_promotion_events' AND column_name='promoted_via'"
        )
    )
    assert result.scalar_one_or_none() == "promoted_via"


@pytest.mark.asyncio
async def test_uq_shadow_promotion_success_index(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename='shadow_promotion_events'"
            " AND indexname='uq_shadow_promotion_success'"
        )
    )
    assert result.scalar_one_or_none() == "uq_shadow_promotion_success"
