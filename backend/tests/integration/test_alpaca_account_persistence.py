"""Phase 9.5 retro — CRIT-db-1: Alpaca account INSERT succeeds end-to-end.

Prior to migration 0029, broker_id_enum lacked 'alpaca', so every Alpaca
account INSERT via the discoverer's CAST(:broker_id AS broker_id_enum) silently
failed. This test asserts the INSERT succeeds and the row is queryable.

Marks: asyncio, integration, no_db (raw SQL to match discoverer query shape).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.db import engine

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]


@pytest.mark.asyncio
async def test_alpaca_account_insert_and_query() -> None:
    """Full discoverer-shape INSERT + SELECT round-trip for an Alpaca account."""
    account_number = f"ALPACA_E2E_{uuid.uuid4().hex[:8]}"

    async with engine.begin() as conn:
        # Discoverer uses CAST(:broker_id AS broker_id_enum) — replicate exactly.
        insert_result = await conn.execute(
            text(
                """
                INSERT INTO broker_accounts
                    (broker_id, account_number, mode, gateway_label,
                     currency_base, last_seen_via)
                VALUES
                    (CAST(:broker_id AS broker_id_enum), :account_number,
                     'paper', 'alpaca-paper', 'USD', 'alpaca-paper')
                RETURNING id, broker_id
                """
            ),
            {"broker_id": "alpaca", "account_number": account_number},
        )
        row = insert_result.fetchone()
        assert row is not None, "INSERT returned no row"
        account_id, returned_broker_id = row
        assert str(returned_broker_id) == "alpaca", (
            f"broker_id round-trip failed: got {returned_broker_id!r}"
        )

        # SELECT back using the same CAST pattern the discoverer uses.
        select_result = await conn.execute(
            text(
                """
                SELECT id, broker_id, account_number, mode
                  FROM broker_accounts
                 WHERE broker_id = CAST(:broker_id AS broker_id_enum)
                   AND account_number = :account_number
                   AND deleted_at IS NULL
                """
            ),
            {"broker_id": "alpaca", "account_number": account_number},
        )
        selected = select_result.fetchone()
        assert selected is not None, (
            "SELECT after INSERT returned no row — enum cast may have failed"
        )
        assert str(selected[1]) == "alpaca"
        assert selected[2] == account_number

        # Cleanup.
        await conn.execute(
            text("DELETE FROM broker_accounts WHERE id = :id"),
            {"id": account_id},
        )


@pytest.mark.asyncio
async def test_alpaca_account_upsert_on_conflict() -> None:
    """ON CONFLICT (broker_id, account_number) upsert pattern must work for alpaca."""
    account_number = f"ALPACA_UC_{uuid.uuid4().hex[:8]}"

    async with engine.begin() as conn:
        try:
            # First insert.
            await conn.execute(
                text(
                    """
                    INSERT INTO broker_accounts
                        (broker_id, account_number, mode, gateway_label,
                         currency_base, last_seen_via)
                    VALUES
                        (CAST('alpaca' AS broker_id_enum), :account_number,
                         'paper', 'alpaca-paper', 'USD', 'alpaca-paper')
                    ON CONFLICT (broker_id, account_number)
                    DO UPDATE SET last_seen_via = EXCLUDED.last_seen_via
                    """
                ),
                {"account_number": account_number},
            )
            # Upsert (should update, not error).
            await conn.execute(
                text(
                    """
                    INSERT INTO broker_accounts
                        (broker_id, account_number, mode, gateway_label,
                         currency_base, last_seen_via)
                    VALUES
                        (CAST('alpaca' AS broker_id_enum), :account_number,
                         'paper', 'alpaca-paper', 'USD', 'alpaca-paper')
                    ON CONFLICT (broker_id, account_number)
                    DO UPDATE SET last_seen_via = EXCLUDED.last_seen_via
                    """
                ),
                {"account_number": account_number},
            )
        finally:
            await conn.execute(
                text("DELETE FROM broker_accounts WHERE account_number = :account_number"),
                {"account_number": account_number},
            )
