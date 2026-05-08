"""Phase 9.5 retro — verify Alembic 0028 / 0028a / 0028b migrations.

Covers CRIT-db-1 (enum widening + CHECK constraint) and MED-db-1 (FK ON DELETE
RESTRICT) and HIGH-db-1 (partial unique index on oco_links active rows).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# 0028 — order_type_enum / order_tif_enum widening + CHECK constraint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_0028_order_type_enum_values(db_session: AsyncSession) -> None:
    """All new order_type_enum values added by 0028 must exist in PG catalog."""
    expected = {
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
        # pre-existing
        "MARKET",
        "LIMIT",
        "STOP",
    }
    result = await db_session.execute(
        text(
            "SELECT enumlabel FROM pg_enum "
            "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
            "WHERE pg_type.typname = 'order_type_enum'"
        )
    )
    actual = {row[0] for row in result.fetchall()}
    missing = expected - actual
    assert not missing, f"missing order_type_enum values: {missing}"


@pytest.mark.asyncio
async def test_0028_order_tif_enum_values(db_session: AsyncSession) -> None:
    """All new order_tif_enum values added by 0028 must exist in PG catalog."""
    expected = {"IOC", "FOK", "GTD", "DAY", "GTC"}
    result = await db_session.execute(
        text(
            "SELECT enumlabel FROM pg_enum "
            "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
            "WHERE pg_type.typname = 'order_tif_enum'"
        )
    )
    actual = {row[0] for row in result.fetchall()}
    missing = expected - actual
    assert not missing, f"missing order_tif_enum values: {missing}"


@pytest.mark.asyncio
async def test_0028_orders_check_is_not_null(db_session: AsyncSession) -> None:
    """After 0028 the orders_order_type_check constraint must be NOT NULL, not
    the old 0004 whitelist.  We verify that the constraint definition no longer
    contains the narrow IN ('MARKET','LIMIT','STOP') whitelist.
    """
    result = await db_session.execute(
        text(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
              FROM pg_constraint
             WHERE conrelid = 'orders'::regclass
               AND conname = 'orders_order_type_check'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "orders_order_type_check constraint not found"
    constraint_def = row[1]
    # Must NOT be the narrow whitelist from 0004
    assert "IN " not in constraint_def, f"stale narrow CHECK still present: {constraint_def}"
    # Must reference NOT NULL / IS NOT NULL semantics
    assert "NOT NULL" in constraint_def.upper() or "is not null" in constraint_def.lower(), (
        f"CHECK does not enforce NOT NULL: {constraint_def}"
    )


# ---------------------------------------------------------------------------
# 0028a — partial unique index on oco_links active rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_0028a_unique_indexes_exist(db_session: AsyncSession) -> None:
    """Both partial unique indexes created by 0028a must exist in pg_indexes."""
    expected = {"uq_oco_links_order_id_a_active", "uq_oco_links_order_id_b_active"}
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'oco_links' "
            "  AND indexname LIKE 'uq_oco_links_order_id_%_active'"
        )
    )
    actual = {row[0] for row in result.fetchall()}
    missing = expected - actual
    assert not missing, f"partial unique indexes missing: {missing}"


@pytest.mark.asyncio
async def test_0028a_duplicate_active_order_id_a_rejected(db_session: AsyncSession) -> None:
    """Two non-terminal oco_links rows with same (broker_id, order_id_a) must be
    rejected by uq_oco_links_order_id_a_active.
    """
    acct = (
        await db_session.execute(text("SELECT id FROM broker_accounts LIMIT 1"))
    ).scalar_one_or_none()
    if acct is None:
        pytest.skip("no broker_accounts to FK against")

    from uuid import uuid4

    shared_order_a = f"OCA-{uuid4().hex[:8]}"

    ob1 = f"OCB-{uuid4().hex[:8]}"
    await db_session.execute(
        text(
            "INSERT INTO oco_links (id, broker_id, account_id, order_id_a, order_id_b, status) "
            "VALUES (:id, 'ibkr', :acct, :oa, :ob, 'ACTIVE')"
        ),
        {"id": str(uuid4()), "acct": str(acct), "oa": shared_order_a, "ob": ob1},
    )
    await db_session.flush()

    ob2 = f"OCB-{uuid4().hex[:8]}"
    with pytest.raises(IntegrityError, match="uq_oco_links_order_id_a_active"):
        await db_session.execute(
            text(
                "INSERT INTO oco_links (id, broker_id, account_id, order_id_a, order_id_b, status) "
                "VALUES (:id, 'ibkr', :acct, :oa, :ob, 'PENDING')"
            ),
            {"id": str(uuid4()), "acct": str(acct), "oa": shared_order_a, "ob": ob2},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_0028a_terminal_order_id_a_allows_reuse(db_session: AsyncSession) -> None:
    """After an OCO link reaches a terminal status the same order_id_a may appear
    in a new active link — the partial index excludes terminal rows.
    """
    acct = (
        await db_session.execute(text("SELECT id FROM broker_accounts LIMIT 1"))
    ).scalar_one_or_none()
    if acct is None:
        pytest.skip("no broker_accounts to FK against")

    from uuid import uuid4

    shared_order_a = f"OCA-{uuid4().hex[:8]}"

    ob3 = f"OCB-{uuid4().hex[:8]}"
    await db_session.execute(
        text(
            "INSERT INTO oco_links (id, broker_id, account_id, order_id_a, order_id_b, status) "
            "VALUES (:id, 'ibkr', :acct, :oa, :ob, 'COMPLETED')"
        ),
        {"id": str(uuid4()), "acct": str(acct), "oa": shared_order_a, "ob": ob3},
    )
    # Should NOT raise — terminal row is excluded from the partial index
    ob4 = f"OCB-{uuid4().hex[:8]}"
    await db_session.execute(
        text(
            "INSERT INTO oco_links (id, broker_id, account_id, order_id_a, order_id_b, status) "
            "VALUES (:id, 'ibkr', :acct, :oa, :ob, 'ACTIVE')"
        ),
        {"id": str(uuid4()), "acct": str(acct), "oa": shared_order_a, "ob": ob4},
    )
    await db_session.flush()


# ---------------------------------------------------------------------------
# 0028b — oco_links FK ON DELETE RESTRICT explicit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_0028b_fk_delete_rule_is_restrict(db_session: AsyncSession) -> None:
    """oco_links_account_id_fkey must have confdeltype = 'r' (RESTRICT)
    after 0028b; the original implicit NO ACTION has confdeltype = 'a'.
    """
    result = await db_session.execute(
        text(
            """
            SELECT confdeltype
              FROM pg_constraint
             WHERE conname = 'oco_links_account_id_fkey'
               AND conrelid = 'oco_links'::regclass
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "oco_links_account_id_fkey not found"
    # 'r' = RESTRICT, 'a' = NO ACTION (default), 'c' = CASCADE
    # asyncpg returns char-type columns as bytes; accept either form.
    actual = row[0].decode() if isinstance(row[0], bytes) else row[0]
    assert actual == "r", f"expected confdeltype='r' (RESTRICT), got '{row[0]!r}'"
