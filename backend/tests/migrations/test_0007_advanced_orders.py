"""Migration 0006 + 0007 - advanced orders schema constraint tests.

Mirrors test_0005 patterns: outer-rollback `session` fixture from
tests.fixtures.db_session + session.begin_nested() savepoints for
IntegrityError tolerance.

The 'modified' enum value ships in 0006 alone (Postgres can't ADD VALUE
and use it in the same transaction); everything else - parent_order_id +
oca_group + order_status_rank + fills + pending_fills - lives in 0007.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> str:
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    return (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :n"),
            {"n": account_number},
        )
    ).scalar_one()


async def _seed_order(session: AsyncSession, account_id: str, client_order_id: str) -> str:
    await session.execute(
        text(
            "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, side, "
            "order_type, tif, qty, limit_price, status, notional) "
            "VALUES (gen_random_uuid(), :a, :c, '265598', 'AAPL', 'BUY', 'LIMIT', "
            "'DAY', '100', '150', 'submitted', '15000')"
        ),
        {"a": account_id, "c": client_order_id},
    )
    return (
        await session.execute(
            text("SELECT id::text FROM orders WHERE client_order_id = :c"),
            {"c": client_order_id},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_modified_enum_value_present(session: AsyncSession) -> None:
    """ALTER TYPE order_status_enum ADD VALUE 'modified' applied (0006)."""
    result = await session.execute(
        text(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = 'order_status_enum' "
            "ORDER BY e.enumsortorder"
        )
    )
    labels = [r[0] for r in result.all()]
    assert "modified" in labels
    assert labels.index("modified") > labels.index("submitted"), (
        "ADD VALUE ... AFTER 'submitted' must place modified after submitted"
    )


@pytest.mark.asyncio
async def test_order_status_rank_function(session: AsyncSession) -> None:
    """order_status_rank returns the expected priority numbers (0007)."""
    result = await session.execute(
        text(
            "SELECT order_status_rank('pending_submit'::order_status_enum), "
            "       order_status_rank('submitted'::order_status_enum), "
            "       order_status_rank('modified'::order_status_enum), "
            "       order_status_rank('partial'::order_status_enum), "
            "       order_status_rank('filled'::order_status_enum), "
            "       order_status_rank('cancelled'::order_status_enum)"
        )
    )
    ranks = result.one()
    assert ranks == (0, 1, 2, 3, 4, 5)


@pytest.mark.asyncio
async def test_orders_parent_order_id_self_fk(session: AsyncSession) -> None:
    """parent_order_id self-referential FK enforces existence (0007)."""
    acct_id = await _seed_account(session, "TEST_PARENT_001")
    parent_id = await _seed_order(session, acct_id, "11111111-1111-1111-1111-111111111111")

    # Valid: child references existing parent.
    await session.execute(
        text(
            "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, side, "
            "order_type, tif, qty, stop_price, status, notional, parent_order_id) "
            "VALUES (gen_random_uuid(), :a, :c, '265598', 'AAPL', 'SELL', 'STOP', "
            "'DAY', '100', '145', 'submitted', '14500', :p)"
        ),
        {"a": acct_id, "c": "22222222-2222-2222-2222-222222222222", "p": parent_id},
    )

    # Invalid: child references non-existent parent.
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, side, "
                    "order_type, tif, qty, stop_price, status, notional, parent_order_id) "
                    "VALUES (gen_random_uuid(), :a, :c, '265598', 'AAPL', 'SELL', 'STOP', "
                    "'DAY', '100', '145', 'submitted', '14500', "
                    "'00000000-0000-0000-0000-000000000000')"
                ),
                {"a": acct_id, "c": "33333333-3333-3333-3333-333333333333"},
            )


@pytest.mark.asyncio
async def test_orders_parent_order_id_cascade_set_null(session: AsyncSession) -> None:
    """ON DELETE SET NULL: hard-deleting parent leaves child surviving with NULL FK."""
    acct_id = await _seed_account(session, "TEST_CASCADE_001")
    parent_id = await _seed_order(session, acct_id, "44444444-4444-4444-4444-444444444444")
    await session.execute(
        text(
            "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, side, "
            "order_type, tif, qty, stop_price, status, notional, parent_order_id) "
            "VALUES (gen_random_uuid(), :a, :c, '265598', 'AAPL', 'SELL', 'STOP', "
            "'DAY', '100', '145', 'submitted', '14500', :p)"
        ),
        {"a": acct_id, "c": "55555555-5555-5555-5555-555555555555", "p": parent_id},
    )

    await session.execute(text("DELETE FROM orders WHERE id::text = :p"), {"p": parent_id})

    row = (
        await session.execute(
            text("SELECT parent_order_id FROM orders WHERE client_order_id = :c"),
            {"c": "55555555-5555-5555-5555-555555555555"},
        )
    ).one()
    assert row[0] is None


@pytest.mark.asyncio
async def test_fills_exec_id_unique(session: AsyncSession) -> None:
    """fills.exec_id UNIQUE catches duplicate inserts."""
    acct_id = await _seed_account(session, "TEST_FILLS_UNQ_001")
    order_id = await _seed_order(session, acct_id, "66666666-6666-6666-6666-666666666666")

    await session.execute(
        text(
            "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
            "VALUES (:o, '0001f4a8.66c0e220.01.01', '50', '150.05', 'USD', now())"
        ),
        {"o": order_id},
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
                    "VALUES (:o, '0001f4a8.66c0e220.01.01', '25', '150.05', 'USD', now())"
                ),
                {"o": order_id},
            )


@pytest.mark.asyncio
async def test_fills_qty_positive_check(session: AsyncSession) -> None:
    """qty > 0 CHECK rejects zero/negative."""
    acct_id = await _seed_account(session, "TEST_FILLS_QTY_001")
    order_id = await _seed_order(session, acct_id, "77777777-7777-7777-7777-777777777777")

    with pytest.raises((IntegrityError, DBAPIError)):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
                    "VALUES (:o, 'EXEC_ZERO', '0', '150', 'USD', now())"
                ),
                {"o": order_id},
            )


@pytest.mark.asyncio
async def test_fills_currency_three_letter(session: AsyncSession) -> None:
    """currency CHECK rejects non-3-letter / lowercase."""
    acct_id = await _seed_account(session, "TEST_FILLS_CUR_001")
    order_id = await _seed_order(session, acct_id, "88888888-8888-8888-8888-888888888888")

    with pytest.raises((IntegrityError, DataError, DBAPIError)):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
                    "VALUES (:o, 'EXEC_BADCUR', '50', '150', 'usd', now())"
                ),
                {"o": order_id},
            )


@pytest.mark.asyncio
async def test_fills_order_id_restrict(session: AsyncSession) -> None:
    """ON DELETE RESTRICT prevents deleting an order that has fills."""
    acct_id = await _seed_account(session, "TEST_FILLS_RES_001")
    order_id = await _seed_order(session, acct_id, "99999999-9999-9999-9999-999999999999")
    await session.execute(
        text(
            "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
            "VALUES (:o, 'EXEC_RES_001', '50', '150', 'USD', now())"
        ),
        {"o": order_id},
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(text("DELETE FROM orders WHERE id::text = :o"), {"o": order_id})


@pytest.mark.asyncio
async def test_pending_fills_no_fk_on_broker_order_id(session: AsyncSession) -> None:
    """pending_fills.broker_order_id has no FK - intentional, accepts orphan rows."""
    acct_id = await _seed_account(session, "TEST_PEND_001")
    # No matching orders row - this is the orphan case CRIT-2 buffers.
    await session.execute(
        text(
            "INSERT INTO pending_fills (exec_id, broker_order_id, account_id, qty, price, "
            "currency, executed_at, raw_payload) "
            "VALUES ('EXEC_ORPHAN_001', 'NONEXISTENT_BROKER_ID', :a, '50', '150', "
            "'USD', now(), '{}')"
        ),
        {"a": acct_id},
    )
    count = (
        await session.execute(
            text("SELECT count(*) FROM pending_fills WHERE exec_id = 'EXEC_ORPHAN_001'")
        )
    ).scalar_one()
    assert count == 1
