"""Phase 8b T-O.1 -- verify oco_links table after Alembic 0016 runs."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_oco_links_table_exists(db_session: AsyncSession) -> None:
    """oco_links table must exist after 0016 migration."""
    result = await db_session.execute(text("SELECT to_regclass('oco_links') IS NOT NULL AS exists"))
    assert result.scalar_one() is True


@pytest.mark.asyncio
async def test_oco_status_check_constraint_rejects_bad_status(
    db_session: AsyncSession,
) -> None:
    """INSERT with status='INVALID_STATUS' must raise IntegrityError."""
    acct = (
        await db_session.execute(text("SELECT id FROM broker_accounts LIMIT 1"))
    ).scalar_one_or_none()
    if acct is None:
        pytest.skip("no broker_accounts to FK against")

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO oco_links "
                "(id, broker_id, account_id, order_id_a, order_id_b, status) "
                "VALUES (:id, 'schwab', :acct, 'a1', 'b1', 'INVALID_STATUS')"
            ),
            {"id": str(uuid4()), "acct": str(acct)},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_oco_links_partial_index_exists(db_session: AsyncSession) -> None:
    """Partial index idx_oco_links_status must exist after 0016 migration."""
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='oco_links' AND indexname='idx_oco_links_status'"
        )
    )
    assert result.scalar_one() == "idx_oco_links_status"
