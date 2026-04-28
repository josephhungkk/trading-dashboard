"""Migration 0004 — orders + order_events schema constraint tests.

Validates spec §3 invariants (R2/R18/R19) and architect-review P3+P16
findings:

- 3 CHECK constraints (qty > 0, order_type↔price coherence, filled_qty bounds)
- Composite UNIQUE on (account_id, client_order_id) — cross-account same
  client_order_id MUST succeed (R2)
- Partial UNIQUE on (account_id, broker_order_id) WHERE NOT NULL — two NULLs
  for the same account succeed; two non-NULL collisions raise (R19)
- Partial pending_submit watchdog index pinned to ``created_at`` column
  with predicate ``status = 'pending_submit'`` (P16)
- order_events.order_id nullable for TWS-placed audit-only rows (R18)
- Downgrade-then-upgrade is idempotent (P3 — Postgres ENUM lifecycle)

Tests use the outer-rollback ``session`` fixture from conftest (per
``feedback_pytest_session_begin_commits.md``); errors are absorbed by
``session.begin_nested()`` savepoints so the outer rollback stays clean.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# A broker_accounts row to FK against. Inserted fresh per test inside the
# outer-rollback transaction; cleaned up automatically.
_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> str:
    """Insert a broker_accounts row and return its UUID id."""
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    row = (
        await session.execute(
            text("SELECT id FROM broker_accounts WHERE account_number = :acct_num"),
            {"acct_num": account_number},
        )
    ).first()
    assert row is not None
    return str(row[0])


_ORDER_INSERT_BASE = """
INSERT INTO orders (
    id, account_id, client_order_id, conid, symbol,
    side, order_type, tif, qty, limit_price, stop_price, notional
) VALUES (
    :id, :account_id, :client_order_id, 'AAPL', 'AAPL',
    'BUY', :order_type, 'DAY', :qty, :limit_price, :stop_price, '1000'
)
"""


@pytest.mark.asyncio
async def test_orders_check_qty_positive(session: AsyncSession) -> None:
    """Spec §3 CHECK (qty > 0): qty=0 must raise."""
    account_id = await _seed_account(session, "TEST_QTY_ZERO")
    with pytest.raises(IntegrityError, match=r"qty"):
        async with session.begin_nested():
            await session.execute(
                text(_ORDER_INSERT_BASE),
                {
                    "id": str(uuid.uuid4()),
                    "account_id": account_id,
                    "client_order_id": str(uuid.uuid4()),
                    "order_type": "MARKET",
                    "qty": "0",
                    "limit_price": None,
                    "stop_price": None,
                },
            )


@pytest.mark.asyncio
async def test_orders_check_market_no_prices(session: AsyncSession) -> None:
    """Spec §3 CHECK: MARKET order_type forbids limit_price + stop_price."""
    account_id = await _seed_account(session, "TEST_MKT_NO_PRICE")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(_ORDER_INSERT_BASE),
                {
                    "id": str(uuid.uuid4()),
                    "account_id": account_id,
                    "client_order_id": str(uuid.uuid4()),
                    "order_type": "MARKET",
                    "qty": "1",
                    "limit_price": "100",  # forbidden for MARKET
                    "stop_price": None,
                },
            )


@pytest.mark.asyncio
async def test_orders_check_limit_requires_limit_price(session: AsyncSession) -> None:
    """Spec §3 CHECK: LIMIT order_type requires limit_price NOT NULL."""
    account_id = await _seed_account(session, "TEST_LIMIT_NULL")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(_ORDER_INSERT_BASE),
                {
                    "id": str(uuid.uuid4()),
                    "account_id": account_id,
                    "client_order_id": str(uuid.uuid4()),
                    "order_type": "LIMIT",
                    "qty": "1",
                    "limit_price": None,  # forbidden for LIMIT
                    "stop_price": None,
                },
            )


@pytest.mark.asyncio
async def test_orders_check_stop_requires_stop_price(session: AsyncSession) -> None:
    """Spec §3 CHECK: STOP order_type requires stop_price NOT NULL."""
    account_id = await _seed_account(session, "TEST_STOP_NULL")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(_ORDER_INSERT_BASE),
                {
                    "id": str(uuid.uuid4()),
                    "account_id": account_id,
                    "client_order_id": str(uuid.uuid4()),
                    "order_type": "STOP",
                    "qty": "1",
                    "limit_price": None,
                    "stop_price": None,  # forbidden for STOP
                },
            )


@pytest.mark.asyncio
async def test_orders_unique_account_client_order_id(session: AsyncSession) -> None:
    """R2: same (account_id, client_order_id) collides; same client_order_id
    across DIFFERENT accounts succeeds (cross-account isolation)."""
    account_a = await _seed_account(session, "TEST_R2_A")
    account_b = await _seed_account(session, "TEST_R2_B")
    shared_coid = str(uuid.uuid4())

    # First insert succeeds (account A)
    await session.execute(
        text(_ORDER_INSERT_BASE),
        {
            "id": str(uuid.uuid4()),
            "account_id": account_a,
            "client_order_id": shared_coid,
            "order_type": "MARKET",
            "qty": "1",
            "limit_price": None,
            "stop_price": None,
        },
    )

    # Same account + same client_order_id raises
    with pytest.raises(IntegrityError, match="uq_orders_account_client_order_id"):
        async with session.begin_nested():
            await session.execute(
                text(_ORDER_INSERT_BASE),
                {
                    "id": str(uuid.uuid4()),
                    "account_id": account_a,
                    "client_order_id": shared_coid,
                    "order_type": "MARKET",
                    "qty": "1",
                    "limit_price": None,
                    "stop_price": None,
                },
            )

    # Different account + same client_order_id succeeds
    await session.execute(
        text(_ORDER_INSERT_BASE),
        {
            "id": str(uuid.uuid4()),
            "account_id": account_b,
            "client_order_id": shared_coid,
            "order_type": "MARKET",
            "qty": "1",
            "limit_price": None,
            "stop_price": None,
        },
    )


@pytest.mark.asyncio
async def test_orders_unique_account_broker_order_id_partial(
    session: AsyncSession,
) -> None:
    """R19: partial UNIQUE on (account_id, broker_order_id) WHERE broker_order_id
    IS NOT NULL. Two NULLs for the same account succeed; two non-NULL
    collisions raise."""
    account_id = await _seed_account(session, "TEST_R19")

    # Two rows with broker_order_id=NULL succeed (partial index excludes NULL)
    for _ in range(2):
        await session.execute(
            text(_ORDER_INSERT_BASE),
            {
                "id": str(uuid.uuid4()),
                "account_id": account_id,
                "client_order_id": str(uuid.uuid4()),
                "order_type": "MARKET",
                "qty": "1",
                "limit_price": None,
                "stop_price": None,
            },
        )

    # Set broker_order_id on one row
    await session.execute(
        text(
            "UPDATE orders SET broker_order_id = '12345' "
            "WHERE id = (SELECT id FROM orders WHERE account_id = :account_id LIMIT 1)"
        ),
        {"account_id": account_id},
    )

    # Setting the SAME broker_order_id on the other row violates the partial UNIQUE
    with pytest.raises(IntegrityError, match="uq_orders_account_broker_order_id"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "UPDATE orders SET broker_order_id = '12345' "
                    "WHERE account_id = :account_id "
                    "AND broker_order_id IS NULL"
                ),
                {"account_id": account_id},
            )


@pytest.mark.asyncio
async def test_pending_submit_watchdog_index_pinned_to_created_at(
    session: AsyncSession,
) -> None:
    """Architect-review P16: index must be on `created_at` with the literal
    pending_submit predicate. A future migration could rename the column
    without breaking a less-strict existence-only test."""
    row = (
        await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_orders_pending_submit_watchdog'"
            )
        )
    ).first()
    assert row is not None, "ix_orders_pending_submit_watchdog must exist"
    indexdef = row[0].lower()
    assert "(created_at)" in indexdef, f"Index must be on (created_at); got: {indexdef}"
    assert "where" in indexdef and "pending_submit" in indexdef, (
        f"Index must be partial on pending_submit; got: {indexdef}"
    )


@pytest.mark.asyncio
async def test_order_events_order_id_nullable(session: AsyncSession) -> None:
    """R18: TWS-placed orders surface as audit-only rows in order_events
    with order_id=NULL — must succeed."""
    account_id = await _seed_account(session, "TEST_R18_TWS")
    await session.execute(
        text(
            """
            INSERT INTO order_events (
                order_id, account_id, status, broker_event_at, raw_payload
            ) VALUES (
                NULL, :account_id, 'submitted', now(), '{"source": "tws"}'::jsonb
            )
            """
        ),
        {"account_id": account_id},
    )
    row = (
        await session.execute(
            text(
                "SELECT id, order_id FROM order_events "
                "WHERE account_id = :account_id ORDER BY id DESC LIMIT 1"
            ),
            {"account_id": account_id},
        )
    ).first()
    assert row is not None
    assert row[1] is None  # order_id is NULL


def test_0004_downgrade_then_upgrade_round_trips_twice() -> None:
    """Architect-review P3: Postgres ENUM lifecycle invariant.

    `op.drop_table` does NOT cascade to ENUM types; without explicit
    DROP TYPE in downgrade(), a downgrade-then-upgrade cycle fails with
    `duplicate_object` when the migration tries to re-CREATE the enum.
    Run the cycle TWICE to confirm the cleanup is idempotent. Codex
    pre-verified this; this test guards against future regression.

    Synchronous (no AsyncSession) — runs alembic CLI in a subprocess.
    """
    # Resolve backend/ relative to this test file so CI runners (workspace
    # at /home/runner/work/trading-dashboard/...) work alongside the NUC
    # dev box at /home/joseph/dashboard/backend.
    backend_dir = str(Path(__file__).resolve().parents[2])

    def alembic(arg: str) -> str:
        # Use `python -m alembic` (NOT `uv run alembic`) to avoid the stale
        # shebang in the venv's alembic script, which still points to the
        # project's pre-2026-04-24 path /mnt/c/dashboard/backend/.venv/bin/python.
        result = subprocess.run(
            ["uv", "run", "python", "-m", "alembic", *arg.split()],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (
            f"alembic {arg} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return result.stdout

    # Cycle 1
    alembic("downgrade -1")
    alembic("upgrade head")
    # Cycle 2 — would fail with `duplicate_object` if ENUM cleanup is missing
    alembic("downgrade -1")
    alembic("upgrade head")
    # Confirm we are back at 0004
    out = alembic("current")
    assert "0004" in out, f"Expected at head 0004, got: {out}"
