"""Phase 15a: Alembic 0051 migration tests — forex_rfq_quotes schema."""

from __future__ import annotations

import pytest
import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_forex_rfq_quotes_columns_exist(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'forex_rfq_quotes'"
        )
    )
    columns = {row[0] for row in result.fetchall()}
    expected = {
        "id",
        "request_id",
        "account_id",
        "instrument_id",
        "bid",
        "ask",
        "ttl_seconds",
        "broker_quote_id",
        "side",
        "notional",
        "notional_currency",
        "status",
        "reject_reason",
        "order_id",
        "created_at",
        "expires_at",
    }
    assert not expected - columns


@pytest.mark.asyncio
async def test_forex_asset_class_enum_exists(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT unnest(enum_range(NULL::instrument_asset_class))"))
    values = [r[0] for r in result.fetchall()]
    assert "FOREX" in values


@pytest.mark.asyncio
async def test_forex_risk_limit_kind_enum_exists(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT unnest(enum_range(NULL::risk_limit_kind))"))
    values = [r[0] for r in result.fetchall()]
    assert "forex_max_notional_per_trade" in values


@pytest.mark.asyncio
async def test_forex_status_check_constraint(session: AsyncSession) -> None:
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await session.execute(
            text(
                "INSERT INTO forex_rfq_quotes "
                "(account_id, instrument_id, bid, ask, ttl_seconds, status, expires_at) "
                "VALUES (gen_random_uuid(), 1, 1.08, 1.082, 30, 'invalid_status', now())"
            )
        )
