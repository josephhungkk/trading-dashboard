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
    assert rows == [
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
    n = (await session.execute(text("SELECT COUNT(*) FROM broker_order_capability"))).scalar_one()
    assert n == 4 * 10 * 5, f"expected 200 rows, got {n}"


@pytest.mark.asyncio
async def test_capability_supported_initial_state(session: AsyncSession) -> None:
    expected = {"schwab": 0, "ibkr": 16, "futu": 4, "alpaca": 0}
    for broker_id, exp in expected.items():
        n = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM broker_order_capability "
                    "WHERE broker_id=:b AND is_supported=TRUE"
                ),
                {"b": broker_id},
            )
        ).scalar_one()
        assert n == exp, f"{broker_id}: expected {exp} supported, got {n}"


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
