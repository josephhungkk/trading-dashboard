"""Phase 9 Task 5 — verify chart_layouts shape, UNIQUE constraint, 64KB CHECK."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration]

EXPECTED_COLUMNS = {
    "id": "bigint",
    "instrument_id": "bigint",
    "payload": "jsonb",
    "schema_version": "integer",
    "updated_at": "timestamp with time zone",
}


@pytest.mark.asyncio
async def test_0026_table_shape(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='chart_layouts'"
            )
        )
    ).all()
    cols = {r.column_name: r.data_type for r in rows}
    assert cols == EXPECTED_COLUMNS, f"chart_layouts columns mismatch: {cols}"


@pytest.mark.asyncio
async def test_0026_unique_on_instrument(db_session: AsyncSession, seed_instrument_aapl) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    await db_session.execute(
        text(
            "INSERT INTO chart_layouts (instrument_id, payload, schema_version) "
            "VALUES (:inst, '{}'::jsonb, 1)"
        ),
        {"inst": inst_id},
    )
    await db_session.flush()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO chart_layouts (instrument_id, payload, schema_version) "
                "VALUES (:inst, '{}'::jsonb, 1)"
            ),
            {"inst": inst_id},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_0026_64kb_payload_cap(db_session: AsyncSession, seed_instrument_aapl) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    big_payload = '{"k":"' + ("x" * 70_000) + '"}'
    with pytest.raises(IntegrityError, match="chart_layouts_payload_size_chk"):
        await db_session.execute(
            text(
                "INSERT INTO chart_layouts (instrument_id, payload, schema_version) "
                "VALUES (:inst, CAST(:p AS JSONB), 1)"
            ),
            {"inst": inst_id, "p": big_payload},
        )
        await db_session.flush()
