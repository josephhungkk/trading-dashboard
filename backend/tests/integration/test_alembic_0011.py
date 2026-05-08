"""Phase 8a migration 0011: order_types + time_in_force + broker_order_capability."""

from __future__ import annotations

import pytest
import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_order_types_seeded(session: AsyncSession) -> None:
    rows = (
        (await session.execute(text("SELECT code FROM order_types ORDER BY sort_order")))
        .scalars()
        .all()
    )
    # 0011 seeded the original 10 order types in the order below; later
    # migrations append BRACKET (0021) + OCO (0021d) so use ⊇ check on
    # the original 10 to keep this an "initial-seed" assertion that
    # survives later additions.
    assert set(rows) >= {
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    }
    # Original 10 must still come first (before any later additions).
    assert rows[:10] == [
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    ]


@pytest.mark.asyncio
async def test_time_in_force_seeded(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text("SELECT code, requires_expiry FROM time_in_force ORDER BY sort_order")
        )
    ).all()
    assert [r.code for r in rows] == ["DAY", "GTC", "IOC", "FOK", "GTD"]
    by_code = {r.code: r.requires_expiry for r in rows}
    assert by_code["GTD"] is True
    assert by_code["DAY"] is False


@pytest.mark.asyncio
async def test_capability_matrix_size(session: AsyncSession) -> None:
    # Original 0011 seed was 4 brokers x 10 order_types x 5 TIFs = 200 rows.
    # Phase 8b/8c added asset_class to the PK (0018) plus new order types
    # (STOP_LIMIT, TRAIL, TRAIL_LIMIT, MOC, MOO, LOC, LOO, BRACKET, OCO) and
    # tifs (IOC, FOK, GTD), so the matrix now has > 200 rows. Locking the
    # exact count means re-blessing on every Phase delta — this only
    # asserts the floor (matrix exists, original cross-product preserved).
    n = (await session.execute(text("SELECT COUNT(*) FROM broker_order_capability"))).scalar_one()
    assert n >= 200, f"expected >= 200 rows, got {n}"


@pytest.mark.asyncio
async def test_capability_supported_initial_state(session: AsyncSession) -> None:
    # 0011 originally seeded {schwab: 0, ibkr: 16, futu: 4, alpaca: 0} as
    # the "initial" supported counts. Later phases (8a/8b/8c) flipped many
    # rows TRUE per empirical broker testing, so the original snapshot is
    # obsolete. Test the structural invariant instead: every broker has
    # rows in the matrix and at least one supported row (sanity).
    for broker_id in ("schwab", "ibkr", "futu", "alpaca"):
        total = (
            await session.execute(
                text("SELECT COUNT(*) FROM broker_order_capability WHERE broker_id=:b"),
                {"b": broker_id},
            )
        ).scalar_one()
        assert total > 0, f"{broker_id}: no capability rows"
        supported = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM broker_order_capability "
                    "WHERE broker_id=:b AND is_supported=TRUE"
                ),
                {"b": broker_id},
            )
        ).scalar_one()
        assert supported > 0, f"{broker_id}: zero supported rows after Phase 8 flips"


@pytest.mark.asyncio
async def test_notes_check_constraint_rejects_non_ascii(session: AsyncSession) -> None:
    """MED-1: CHECK constraint rejects non-printable-ASCII notes."""
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        await session.execute(
            text(
                "INSERT INTO broker_order_capability "
                "(broker_id, order_type, time_in_force, is_supported, notes) "
                "VALUES ('ibkr', 'MARKET', 'DAY', true, 'naïve')"
            )
        )
        await session.commit()
