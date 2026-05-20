"""Tests for migration 0069_1 — sector columns, per_sector limit, veto window."""

import pytest
import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_instruments_sector_columns_added(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='instruments' AND column_name IN ('sector','sub_sector')"
        )
    )
    cols = {r[0] for r in result.all()}
    assert "sector" in cols
    assert "sub_sector" in cols


@pytest.mark.asyncio
async def test_instruments_sector_index_exists(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename='instruments' AND indexname='instruments_sector_idx'"
        )
    )
    assert result.scalar_one_or_none() == "instruments_sector_idx"


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_per_sector_type(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO broker_accounts"
            " (id, broker_id, account_number, alias, mode,"
            " gateway_label, currency_base, last_seen_via)"
            " VALUES (gen_random_uuid(), 'ibkr', 'TEST_0069_1',"
            " 'test0069_1', 'paper', 'ibkr', 'USD', 'ibkr')"
        )
    )
    result = await session.execute(
        text("SELECT id FROM broker_accounts WHERE account_number='TEST_0069_1'")
    )
    acct_id = result.scalar_one()
    # per_sector with a sector value must succeed
    await session.execute(
        text(
            "INSERT INTO portfolio_exposure_limits"
            " (account_id, limit_type, max_notional, currency, sector)"
            " VALUES (:aid, 'per_sector', 100000, 'USD', 'technology')"
        ),
        {"aid": acct_id},
    )
    row = await session.execute(
        text(
            "SELECT limit_type, sector FROM portfolio_exposure_limits"
            " WHERE account_id=:aid AND limit_type='per_sector'"
        ),
        {"aid": acct_id},
    )
    r = row.first()
    assert r is not None
    assert r[0] == "per_sector"
    assert r[1] == "technology"


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_bad_type_rejected(session: AsyncSession) -> None:
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO portfolio_exposure_limits"
                    " (account_id, limit_type, max_notional, currency)"
                    " VALUES ((SELECT id FROM broker_accounts LIMIT 1),"
                    "  'bad_type', 100000, 'USD')"
                )
            )


@pytest.mark.asyncio
async def test_shadow_promotion_events_promote_pending_allowed(session: AsyncSession) -> None:
    """promote_pending and vetoed are now valid status values."""
    result = await session.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints"
            " WHERE table_name='shadow_promotion_events'"
            " AND constraint_name='shadow_promotion_events_status_check_v2'"
        )
    )
    assert result.scalar_one_or_none() == "shadow_promotion_events_status_check_v2"


@pytest.mark.asyncio
async def test_shadow_promotion_events_veto_columns_added(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='shadow_promotion_events'"
            " AND column_name IN ('veto_expires_at','veto_token')"
        )
    )
    cols = {r[0] for r in result.all()}
    assert "veto_expires_at" in cols
    assert "veto_token" in cols


@pytest.mark.asyncio
async def test_uq_shadow_promotion_pending_index_exists(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename='shadow_promotion_events'"
            " AND indexname='uq_shadow_promotion_pending'"
        )
    )
    assert result.scalar_one_or_none() == "uq_shadow_promotion_pending"


@pytest.mark.asyncio
async def test_marginal_variance_enabled_seeded(session: AsyncSession) -> None:
    row = await session.execute(
        text(
            "SELECT value, value_type FROM app_config"
            " WHERE namespace='orchestrator' AND key='marginal_variance_enabled'"
        )
    )
    r = row.first()
    assert r is not None
    assert r[0] == "true"
    assert r[1] == "bool"
