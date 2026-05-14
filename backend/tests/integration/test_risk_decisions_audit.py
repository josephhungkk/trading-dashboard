"""Phase 10a D7 — risk_decisions audit + pg_notify integration test.

Verifies the audit-row pipeline used by the gate insertions in
preview_order / place_order / modify_order (D3, D4, D5):

1. `_audit_risk_decision` and `_audit_risk_decision_modify` write a row
   to `risk_decisions` with the correct attempt_kind, verdict, blockers
   JSONB, and request_id.
2. The migration-0036 AFTER INSERT pg_notify trigger emits a minimal
   payload on `risk_decision` channel ({id, verdict, account_id}).

The D4/D5 implementation deferred audit-on-ALLOW/WARN to Phase 10a.5;
this test covers the BLOCK path (the only path that writes today).

State isolation: tests INSERT then DELETE by request_id (unique per test
run via uuid4()) to keep the shared NUC DB clean. The audit helpers
themselves call db.commit(), so the outer-transaction fixture would not
cleanly contain them.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.schemas.risk import GateBlockerEntry, GateVerdict, GateWarningEntry


def _block_verdict(
    blockers: list[GateBlockerEntry],
    warnings: list[GateWarningEntry] | None = None,
) -> GateVerdict:
    return GateVerdict(
        final_verdict="block",
        blockers=blockers,
        warnings=warnings or [],
        latency_ms=42,
    )


async def _existing_account_id() -> uuid.UUID:
    """Return a broker_accounts row id, seeding TEST001 if none exists."""
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                """
                INSERT INTO broker_accounts (
                    broker_id, account_number, alias, mode, gateway_label,
                    currency_base, last_seen_via
                ) VALUES (
                    'ibkr'::broker_id_enum, 'TEST001', 'test-acct-1',
                    'paper'::trading_mode_enum, 'isa-paper', 'GBP', 'isa-paper'
                ) ON CONFLICT (broker_id, account_number) DO UPDATE SET alias = EXCLUDED.alias
                RETURNING id
                """
            )
        )
        row = result.first()
        await s.commit()
    assert row is not None
    return row[0]


# Fixed UUID so the seeded orders row is idempotent and deletable by teardown.
_AUDIT_SEED_CLIENT_ORDER_ID = uuid.UUID("00000000-0000-0000-0000-555455455354")


async def _existing_order_id() -> uuid.UUID:
    """Seed a deterministic orders row for FK use; cleaned up by _cleanup_audit_seed."""
    account_id = await _existing_account_id()
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                """
                INSERT INTO orders (
                  id, account_id, client_order_id, conid, symbol, side,
                  order_type, tif, qty, filled_qty, status, notional
                )
                SELECT
                  gen_random_uuid(), :account_id, :client_order_id,
                  '265598', 'AAPL', 'BUY', 'MARKET', 'DAY', 1, 0, 'pending_submit', 0
                WHERE NOT EXISTS (
                  SELECT 1 FROM orders WHERE client_order_id = :client_order_id
                )
                RETURNING id
                """
            ),
            {"account_id": account_id, "client_order_id": _AUDIT_SEED_CLIENT_ORDER_ID},
        )
        new_row = result.first()
        if new_row is not None:
            await s.commit()
            return new_row[0]
        existing = await s.execute(
            text("SELECT id FROM orders WHERE client_order_id = :cid"),
            {"cid": _AUDIT_SEED_CLIENT_ORDER_ID},
        )
        return existing.scalar_one()


@pytest.fixture(autouse=True, scope="module")
async def _cleanup_audit_seed():
    """Delete the deterministic seed order after all tests in this module run."""
    yield
    async with SessionLocal() as s:
        await s.execute(
            text("DELETE FROM orders WHERE client_order_id = :cid"),
            {"cid": _AUDIT_SEED_CLIENT_ORDER_ID},
        )
        await s.commit()


async def _delete_decisions_by_request_id(request_id: str) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("DELETE FROM risk_decisions WHERE request_id = :rid"),
            {"rid": request_id},
        )
        await s.commit()


def _raw_dsn() -> str:
    """asyncpg-native DSN (sans the +asyncpg driver suffix SQLAlchemy uses)."""
    return settings.database_url.replace("+asyncpg", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempt_kind",
    ["place_order", "modify_order"],
)
async def test_audit_helper_writes_block_row(attempt_kind: str) -> None:
    """Both audit-writing attempt_kinds round-trip through risk_decisions on BLOCK.

    Note: preview_order is intentionally absent — D3 design deferred
    audit-on-preview; the alembic 0036 CHECK constraint on
    risk_decisions.attempt_kind enforces ('place_order', 'modify_order')
    only. preview_order surfaces blockers in the response shape but does
    not persist a row.
    """
    from app.services.orders_service import (
        _audit_risk_decision,
        _audit_risk_decision_modify,
    )

    account_id = await _existing_account_id()
    request_id = f"d7-test-{uuid.uuid4()}"
    verdict = _block_verdict(
        [
            GateBlockerEntry(
                check="kill_switch_account",
                message="account kill switch is enabled",
                code="account_kill_switch_enabled",
            )
        ]
    )

    async with SessionLocal() as db:
        try:
            if attempt_kind == "modify_order":
                order_id = await _existing_order_id()
                await _audit_risk_decision_modify(
                    db=db,
                    account_id=account_id,
                    side="BUY",
                    qty=Decimal("1"),
                    limit_price="100.00",
                    order_type="LIMIT",
                    tif="DAY",
                    verdict=verdict,
                    request_id=request_id,
                    order_id=order_id,
                )
            else:
                # _audit_risk_decision takes a PlaceOrderRequest-shaped object;
                # build a stub that exposes the attributes the helper reads
                # (account_id is passed separately).
                from types import SimpleNamespace

                stub_request = SimpleNamespace(
                    side="BUY",
                    limit_price="100.00",
                    order_type="LIMIT",
                    tif="DAY",
                )
                await _audit_risk_decision(
                    db=db,
                    account_id=account_id,
                    request=stub_request,  # type: ignore[arg-type]
                    qty=Decimal("1"),
                    verdict=verdict,
                    request_id=request_id,
                    attempt_kind=attempt_kind,
                    order_id=None,
                )

            # Re-read via a fresh session so we see only what was committed.
            async with SessionLocal() as reader:
                result = await reader.execute(
                    text(
                        """
                        SELECT account_id, side, qty, price, order_type,
                               time_in_force, verdict::text AS verdict,
                               blockers, warnings, attempt_kind::text AS attempt_kind,
                               request_id, order_id, latency_ms
                          FROM risk_decisions
                         WHERE request_id = :rid
                        """
                    ),
                    {"rid": request_id},
                )
                row = result.mappings().one()

            assert row["account_id"] == account_id
            # D7: audit helpers lowercase side to satisfy the
            # risk_decisions_side_check CHECK constraint.
            assert row["side"] == "buy"
            assert row["qty"] == Decimal("1")
            assert row["price"] == Decimal("100.00")
            assert row["order_type"] == "LIMIT"
            assert row["time_in_force"] == "DAY"
            assert row["verdict"] == "block"
            assert row["attempt_kind"] == attempt_kind
            assert row["request_id"] == request_id
            assert row["latency_ms"] == 42
            # JSONB columns: blockers list with one entry, warnings empty list.
            blockers = row["blockers"]
            if isinstance(blockers, str):
                blockers = json.loads(blockers)
            assert len(blockers) == 1
            assert blockers[0]["code"] == "account_kill_switch_enabled"
            warnings = row["warnings"]
            if isinstance(warnings, str):
                warnings = json.loads(warnings)
            assert warnings == []
        finally:
            await _delete_decisions_by_request_id(request_id)


@pytest.mark.asyncio
async def test_audit_insert_fires_pg_notify_trigger() -> None:
    """M4: INSERT into risk_decisions emits NOTIFY risk_decision, '{id, verdict, account_id}'."""
    from app.services.orders_service import _audit_risk_decision_modify

    account_id = await _existing_account_id()
    order_id = await _existing_order_id()
    request_id = f"d7-notify-{uuid.uuid4()}"
    verdict = _block_verdict(
        [
            GateBlockerEntry(
                check="kill_switch_account",
                message="test",
                code="account_kill_switch_enabled",
            )
        ]
    )

    received: list[dict[str, Any]] = []
    notify_event = asyncio.Event()

    def _on_notify(_conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            received.append(json.loads(payload))
        except json.JSONDecodeError:
            received.append({"raw": payload})
        notify_event.set()

    listener_conn = await asyncpg.connect(_raw_dsn())
    try:
        await listener_conn.add_listener("risk_decision", _on_notify)
        async with SessionLocal() as db:
            try:
                await _audit_risk_decision_modify(
                    db=db,
                    account_id=account_id,
                    side="SELL",
                    qty=Decimal("2"),
                    limit_price=None,
                    order_type="MARKET",
                    tif="DAY",
                    verdict=verdict,
                    request_id=request_id,
                    order_id=order_id,
                )
                try:
                    await asyncio.wait_for(notify_event.wait(), timeout=3.0)
                except TimeoutError:
                    pytest.fail("pg_notify on risk_decision did not arrive within 3s")
            finally:
                await _delete_decisions_by_request_id(request_id)

        assert received, "No NOTIFY captured"
        payload = received[0]
        assert set(payload.keys()) == {"id", "verdict", "account_id"}
        assert payload["verdict"] == "block"
        assert payload["account_id"] == str(account_id)
        assert isinstance(payload["id"], int)
    finally:
        await listener_conn.remove_listener("risk_decision", _on_notify)
        await listener_conn.close()


# ─── Phase 10a.5 A5.2: dedupe-helper widening tests ─────────────────────


def _allow_verdict() -> GateVerdict:
    return GateVerdict(final_verdict="allow", blockers=[], warnings=[], latency_ms=12)


def _warn_verdict() -> GateVerdict:
    return GateVerdict(
        final_verdict="warn",
        blockers=[],
        warnings=[
            GateWarningEntry(
                check="max_daily_loss",
                message="80% of cap",
                value=800.0,
                threshold=1000.0,
            )
        ],
        latency_ms=15,
    )


async def _count_decisions_by_request_id(request_id: str) -> int:
    async with SessionLocal() as s:
        result = await s.execute(
            text("SELECT COUNT(*) FROM risk_decisions WHERE request_id = :rid"),
            {"rid": request_id},
        )
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_dedupe_helper_emits_allow_row_first_call() -> None:
    """A5.1: first ALLOW invocation passes the dedupe -> row written."""
    from types import SimpleNamespace

    import fakeredis.aioredis

    from app.services.orders_service import _audit_risk_decision_with_dedupe

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    account_id = await _existing_account_id()
    request_id = f"a5-allow-1-{uuid.uuid4()}"
    stub_request = SimpleNamespace(
        side="BUY",
        limit_price="100.00",
        order_type="LIMIT",
        tif="DAY",
        conid="TEST-CONID-A5-1",
        account_id=account_id,
    )

    async with SessionLocal() as db:
        try:
            await _audit_risk_decision_with_dedupe(
                db=db,
                redis=redis,
                account_id=account_id,
                request=stub_request,  # type: ignore[arg-type]
                qty=Decimal("1"),
                verdict=_allow_verdict(),
                request_id=request_id,
                attempt_kind="place_order",
                order_id=None,
            )
            assert await _count_decisions_by_request_id(request_id) == 1
        finally:
            await _delete_decisions_by_request_id(request_id)


@pytest.mark.asyncio
async def test_dedupe_helper_skips_duplicate_allow_within_30s() -> None:
    """A5.1 HIGH-4: second identical ALLOW within 30s is suppressed."""
    from types import SimpleNamespace

    import fakeredis.aioredis

    from app.services.orders_service import _audit_risk_decision_with_dedupe

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    account_id = await _existing_account_id()
    request_id_a = f"a5-allow-dup-a-{uuid.uuid4()}"
    request_id_b = f"a5-allow-dup-b-{uuid.uuid4()}"
    stub_request = SimpleNamespace(
        side="BUY",
        limit_price="100.00",
        order_type="LIMIT",
        tif="DAY",
        conid="TEST-CONID-A5-DUP",
        account_id=account_id,
    )

    async with SessionLocal() as db:
        try:
            await _audit_risk_decision_with_dedupe(
                db=db,
                redis=redis,
                account_id=account_id,
                request=stub_request,  # type: ignore[arg-type]
                qty=Decimal("1"),
                verdict=_allow_verdict(),
                request_id=request_id_a,
                attempt_kind="place_order",
                order_id=None,
            )
            await _audit_risk_decision_with_dedupe(
                db=db,
                redis=redis,
                account_id=account_id,
                request=stub_request,  # type: ignore[arg-type]
                qty=Decimal("1"),
                verdict=_allow_verdict(),
                request_id=request_id_b,
                attempt_kind="place_order",
                order_id=None,
            )
            assert await _count_decisions_by_request_id(request_id_a) == 1
            assert await _count_decisions_by_request_id(request_id_b) == 0
        finally:
            await _delete_decisions_by_request_id(request_id_a)
            await _delete_decisions_by_request_id(request_id_b)


@pytest.mark.asyncio
async def test_dedupe_helper_warn_bypasses_dedupe() -> None:
    """A5.1: WARN verdicts always emit (operator visibility > volume control)."""
    from types import SimpleNamespace

    import fakeredis.aioredis

    from app.services.orders_service import _audit_risk_decision_with_dedupe

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    account_id = await _existing_account_id()
    request_id_a = f"a5-warn-a-{uuid.uuid4()}"
    request_id_b = f"a5-warn-b-{uuid.uuid4()}"
    stub_request = SimpleNamespace(
        side="BUY",
        limit_price="100.00",
        order_type="LIMIT",
        tif="DAY",
        conid="TEST-CONID-A5-WARN",
        account_id=account_id,
    )

    async with SessionLocal() as db:
        try:
            for rid in (request_id_a, request_id_b):
                await _audit_risk_decision_with_dedupe(
                    db=db,
                    redis=redis,
                    account_id=account_id,
                    request=stub_request,  # type: ignore[arg-type]
                    qty=Decimal("1"),
                    verdict=_warn_verdict(),
                    request_id=rid,
                    attempt_kind="place_order",
                    order_id=None,
                )
            assert await _count_decisions_by_request_id(request_id_a) == 1
            assert await _count_decisions_by_request_id(request_id_b) == 1
        finally:
            await _delete_decisions_by_request_id(request_id_a)
            await _delete_decisions_by_request_id(request_id_b)


@pytest.mark.asyncio
async def test_dedupe_helper_redis_failure_fails_open() -> None:
    """A5.1: redis SETNX raising does NOT suppress emission (fail-open)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.orders_service import _audit_risk_decision_with_dedupe

    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    account_id = await _existing_account_id()
    request_id = f"a5-redis-fail-{uuid.uuid4()}"
    stub_request = SimpleNamespace(
        side="BUY",
        limit_price="100.00",
        order_type="LIMIT",
        tif="DAY",
        conid="TEST-CONID-A5-FAIL",
        account_id=account_id,
    )

    async with SessionLocal() as db:
        try:
            await _audit_risk_decision_with_dedupe(
                db=db,
                redis=redis,
                account_id=account_id,
                request=stub_request,  # type: ignore[arg-type]
                qty=Decimal("1"),
                verdict=_allow_verdict(),
                request_id=request_id,
                attempt_kind="place_order",
                order_id=None,
            )
            assert await _count_decisions_by_request_id(request_id) == 1
        finally:
            await _delete_decisions_by_request_id(request_id)
