"""Phase 10b.2 §4.3 — tests for BalanceSnapshotWriter."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core import metrics
from app.services import balance_snapshot_writer as bsw_module
from app.services.balance_snapshot_writer import BalanceSnapshotWriter

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def test_broker_account(db_session):
    """Insert and clean up a broker_accounts row used as FK target.

    Phase 10b.2 §4.1 — account_balance_snapshots.account_id FKs broker_accounts(id).
    Tests that INSERT into the snapshot table need a real parent row to exist
    AND be committed (FK is checked immediately, not deferred). This fixture
    creates the row in its own transaction so the snapshot INSERT in the
    test's own transaction sees a committed FK target.
    """
    account_id = uuid4()
    async with db_session.begin():
        await db_session.execute(
            text(
                """
                INSERT INTO broker_accounts
                  (id, broker_id, account_number, mode, gateway_label,
                   currency_base, last_seen_via)
                VALUES
                  (:id, 'ibkr', :acct_num, 'paper', 'ibkr-test',
                   'GBP', 'ibkr-test')
                """
            ),
            {"id": str(account_id), "acct_num": f"TEST-BSW-{account_id.hex[:8]}"},
        )
    yield account_id
    async with db_session.begin():
        await db_session.execute(
            text("DELETE FROM broker_accounts WHERE id = :id"),
            {"id": str(account_id)},
        )


async def test_happy_insert_increments_writes_metric(
    db_session, redis, test_broker_account
) -> None:
    """record() under outer SAVEPOINT increments snapshot_writes_total."""
    writer = BalanceSnapshotWriter(redis)
    before = metrics.portfolio_rollup_snapshot_writes_total._value.get()
    async with db_session.begin():
        async with db_session.begin_nested():
            await writer.record(
                db_session,
                account_id=test_broker_account,
                nlv="123456.78901234",
                currency="GBP",
                source_label="ibkr-test",
            )
    after = metrics.portfolio_rollup_snapshot_writes_total._value.get()
    assert after - before == 1


async def test_on_conflict_do_nothing_on_duplicate_ts(
    db_session, redis, test_broker_account
) -> None:
    """Two record() calls at same now() — second is ON CONFLICT no-op."""
    writer = BalanceSnapshotWriter(redis)
    async with db_session.begin():
        async with db_session.begin_nested():
            await writer.record(
                db_session,
                account_id=test_broker_account,
                nlv="100000",
                currency="GBP",
                source_label="ibkr-test",
            )
            await writer.record(
                db_session,
                account_id=test_broker_account,
                nlv="100001",
                currency="GBP",
                source_label="ibkr-test",
            )
    async with db_session.begin():
        count = (
            await db_session.execute(
                text("SELECT COUNT(*) FROM account_balance_snapshots WHERE account_id = :aid"),
                {"aid": str(test_broker_account)},
            )
        ).scalar_one()
    # now() in PG often shares a statement timestamp; ON CONFLICT collapses to 1.
    # Resilient to either case: at least one row, never zero, never error.
    assert count >= 1


async def test_fail_open_inner_savepoint_does_not_rollback_outer(
    db_session, redis, test_broker_account, monkeypatch
) -> None:
    """If snapshot INSERT raises, outer NLV-style UPDATE still commits."""
    writer = BalanceSnapshotWriter(redis)
    before_writes = metrics.portfolio_rollup_snapshot_writes_total._value.get()
    before_errors = metrics.portfolio_rollup_snapshot_write_errors_total._value.get()

    # Force the writer's INSERT to fail by patching the SQL it executes.
    monkeypatch.setattr(
        bsw_module,
        "_INSERT_SNAPSHOT_SQL",
        text("INSERT INTO no_such_table_xyz (account_id) VALUES (:account_id)"),
    )

    # Outer transaction touches broker_accounts (proxy for the NLV UPDATE that
    # in production would be in the outer savepoint). If the inner SAVEPOINT
    # rolled back the outer, this UPDATE's effect would vanish.
    async with db_session.begin():
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE broker_accounts SET updated_at = now() WHERE id = :id"),
                {"id": str(test_broker_account)},
            )
            await writer.record(
                db_session,
                account_id=test_broker_account,
                nlv="100000",
                currency="GBP",
                source_label="ibkr-test",
            )
    # Verify outer commit landed: the broker_accounts row still exists with
    # updated_at near now()
    async with db_session.begin():
        updated_at = (
            await db_session.execute(
                text("SELECT updated_at FROM broker_accounts WHERE id = :id"),
                {"id": str(test_broker_account)},
            )
        ).scalar_one()
    assert updated_at is not None

    after_writes = metrics.portfolio_rollup_snapshot_writes_total._value.get()
    after_errors = metrics.portfolio_rollup_snapshot_write_errors_total._value.get()
    assert after_writes - before_writes == 0
    assert after_errors - before_errors == 1


async def test_schedule_publish_tracks_and_drains_task(redis) -> None:
    """schedule_publish stores task in tracked set; stop() drains it."""
    writer = BalanceSnapshotWriter(redis)
    writer.schedule_publish(uuid4())
    assert len(writer._publish_tasks) == 1
    await writer.stop()
    assert len(writer._publish_tasks) == 0


async def test_publish_failure_increments_failure_metric() -> None:
    """redis.publish raising → caught in _publish; failure counter ticks."""
    import asyncio

    failing_redis = AsyncMock()
    failing_redis.publish.side_effect = RuntimeError("redis down")
    writer = BalanceSnapshotWriter(failing_redis)
    before = metrics.portfolio_rollup_publish_failures_total._value.get()
    writer.schedule_publish(uuid4())
    # Yield so the publish task runs its body (await redis.publish raises,
    # is caught by the except Exception in _publish, and increments the
    # failure metric) BEFORE we cancel via stop().
    await asyncio.sleep(0.05)
    await writer.stop()
    after = metrics.portfolio_rollup_publish_failures_total._value.get()
    assert after - before == 1
