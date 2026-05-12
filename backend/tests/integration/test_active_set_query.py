"""Phase 9 Task 9 — verify BarService.active_set query semantics.

Rewritten 2026-05-12 (Phase 11a-CI-debt-2) against the real schema:
positions has (account_id, conid) PK; watchlist_entries.currency is NOT NULL
character(3); chart_layouts requires payload jsonb. The active_set query
UNIONs instrument_ids from all three tables, dedupes, orders by
recency_score DESC, caps at 1000.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bar_service import BarService

pytestmark = [pytest.mark.integration]


async def _seed_test_account_and_instruments(db: AsyncSession) -> tuple[str, list[int]]:
    """Return (account_id, list of instrument_ids inserted)."""
    # Read existing seeded account
    account_id = (
        await db.execute(
            text(
                "SELECT id FROM broker_accounts WHERE account_number = 'TEST001' "
                "AND broker_id = 'ibkr' LIMIT 1"
            )
        )
    ).scalar_one()

    # Insert 5 unique test instruments (canonical_id namespaced so we don't
    # collide with the conftest seed).
    instrument_ids: list[int] = []
    for _ in range(5):
        canonical = f"test_active_set:I{uuid4().hex[:8]}:TEST"
        row_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO instruments (canonical_id, asset_class, primary_exchange,
                                              currency, display_name)
                    VALUES (:c, 'STOCK'::instrument_asset_class, 'TEST', 'USD', 'Test inst')
                    RETURNING id
                    """
                ),
                {"c": canonical},
            )
        ).scalar_one()
        instrument_ids.append(int(row_id))
    await db.commit()
    return str(account_id), instrument_ids


async def _cleanup(db: AsyncSession, instrument_ids: list[int], account_id: str) -> None:
    if not instrument_ids:
        return
    await db.execute(
        text("DELETE FROM positions WHERE account_id = :a AND instrument_id = ANY(:ids)"),
        {"a": account_id, "ids": instrument_ids},
    )
    await db.execute(
        text("DELETE FROM watchlist_entries WHERE instrument_id = ANY(:ids)"),
        {"ids": instrument_ids},
    )
    await db.execute(
        text("DELETE FROM chart_layouts WHERE instrument_id = ANY(:ids)"),
        {"ids": instrument_ids},
    )
    await db.execute(
        text("DELETE FROM instruments WHERE id = ANY(:ids)"),
        {"ids": instrument_ids},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_active_set_unions_positions_watchlist_chart_layouts(
    db_session: AsyncSession,
) -> None:
    """A row in any of positions, watchlist_entries, or chart_layouts should
    appear in the active set."""
    account_id, instrument_ids = await _seed_test_account_and_instruments(db_session)
    try:
        # Distribute the 5 instruments across the 3 source tables.
        # positions: instrument_ids[0:2]
        for iid in instrument_ids[0:2]:
            await db_session.execute(
                text(
                    """
                    INSERT INTO positions (
                      account_id, conid, qty, avg_cost, currency, asset_class,
                      instrument_id
                    )
                    VALUES (:a, :c, 1, 100, 'USD', 'STOCK', :iid)
                    ON CONFLICT (account_id, conid) DO NOTHING
                    """
                ),
                {"a": account_id, "c": f"CONID_{iid}", "iid": iid},
            )
        # watchlist_entries: instrument_ids[2:4]
        for iid in instrument_ids[2:4]:
            await db_session.execute(
                text(
                    """
                    INSERT INTO watchlist_entries (
                      broker_id, symbol, exchange, currency, instrument_id
                    )
                    VALUES ('ibkr', :sym, 'TEST', 'USD', :iid)
                    """
                ),
                {"sym": f"SYM_{iid}", "iid": iid},
            )
        # chart_layouts: instrument_ids[4]
        await db_session.execute(
            text(
                """
                INSERT INTO chart_layouts (instrument_id, payload, schema_version)
                VALUES (:iid, '{}'::jsonb, 1)
                """
            ),
            {"iid": instrument_ids[4]},
        )
        # Seed the bar_active_set_recency_days config (default 30) so the
        # chart_layouts source isn't filtered out.
        await db_session.execute(
            text(
                """
                INSERT INTO app_config (namespace, key, value_type, value)
                VALUES ('charts', 'bar_active_set_recency_days', 'int', '30')
                ON CONFLICT (namespace, key) DO NOTHING
                """
            ),
        )
        await db_session.commit()

        svc = BarService(registry=None)  # type: ignore[arg-type] — query doesn't touch registry
        rows = await svc.active_set(db_session)
        ids = {r.instrument_id for r in rows}
        assert all(iid in ids for iid in instrument_ids), (
            f"active_set missed instruments: expected {instrument_ids}, got {sorted(ids)}"
        )
    finally:
        await _cleanup(db_session, instrument_ids, account_id)


@pytest.mark.asyncio
async def test_active_set_caps_at_1000(db_session: AsyncSession) -> None:
    """Seed 1001 watchlist_entries (cheapest table; no PK collisions) and
    verify the query caps at 1000 rows."""
    account_id = (
        await db_session.execute(
            text(
                "SELECT id FROM broker_accounts WHERE account_number = 'TEST001' "
                "AND broker_id = 'ibkr' LIMIT 1"
            )
        )
    ).scalar_one()
    # Bulk-insert 1001 instruments + watchlist rows.
    instrument_ids: list[int] = []
    for i in range(1001):
        canonical = f"test_active_set_cap:{uuid4().hex[:12]}"
        row_id = (
            await db_session.execute(
                text(
                    """
                    INSERT INTO instruments (canonical_id, asset_class, primary_exchange,
                                              currency, display_name)
                    VALUES (:c, 'STOCK'::instrument_asset_class, 'TEST', 'USD', 'Test cap')
                    RETURNING id
                    """
                ),
                {"c": canonical},
            )
        ).scalar_one()
        instrument_ids.append(int(row_id))
        await db_session.execute(
            text(
                """
                INSERT INTO watchlist_entries (
                  broker_id, symbol, exchange, currency, instrument_id
                )
                VALUES ('ibkr', :sym, 'TEST', 'USD', :iid)
                """
            ),
            {"sym": f"SYM_{i}", "iid": int(row_id)},
        )
    await db_session.commit()

    try:
        svc = BarService(registry=None)  # type: ignore[arg-type]
        rows = await svc.active_set(db_session)
        # The query LIMIT 1000 caps the output; assert exactly that.
        assert len(rows) == 1000, f"expected 1000 rows after cap, got {len(rows)}"
    finally:
        await _cleanup(db_session, instrument_ids, str(account_id))


@pytest.mark.asyncio
async def test_active_set_dedupes_by_instrument_id(db_session: AsyncSession) -> None:
    """An instrument appearing in positions AND watchlist must show once."""
    account_id, instrument_ids = await _seed_test_account_and_instruments(db_session)
    try:
        iid = instrument_ids[0]
        # Insert into BOTH positions and watchlist_entries
        await db_session.execute(
            text(
                """
                INSERT INTO positions (
                  account_id, conid, qty, avg_cost, currency, asset_class, instrument_id
                )
                VALUES (:a, :c, :q, 100, 'USD', 'STOCK', :iid)
                ON CONFLICT (account_id, conid) DO NOTHING
                """
            ),
            {"a": account_id, "c": f"CONID_{iid}", "q": Decimal("1"), "iid": iid},
        )
        await db_session.execute(
            text(
                """
                INSERT INTO watchlist_entries (
                  broker_id, symbol, exchange, currency, instrument_id
                )
                VALUES ('ibkr', :sym, 'TEST', 'USD', :iid)
                """
            ),
            {"sym": f"SYM_{iid}", "iid": iid},
        )
        await db_session.commit()

        svc = BarService(registry=None)  # type: ignore[arg-type]
        rows = await svc.active_set(db_session)
        matches = [r for r in rows if r.instrument_id == iid]
        assert len(matches) == 1, (
            f"expected exactly 1 row for instrument {iid} (dedup), got {len(matches)}"
        )
    finally:
        await _cleanup(db_session, instrument_ids, account_id)
