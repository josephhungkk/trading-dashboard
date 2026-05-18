"""Phase 15a FOREX RFQ integration tests."""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("TEST_DISABLE_STMT_CACHE", "1")
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-chars-ok")
os.environ.setdefault("APP_CORS_ORIGINS", '["http://localhost:5173"]')
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://trader:ci@localhost:5432/dashboard",
)
os.environ.setdefault("REDIS_PASSWORD", "ci")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.forex import _RATE_BUCKETS
from app.core.db import SessionLocal
from app.main import app
from app.services.forex.rfq_service import sweep_expired_quotes


@pytest.fixture(autouse=True)
def _reset_forex_rate_limiter() -> None:
    _RATE_BUCKETS.clear()


@pytest.mark.asyncio
async def test_forex_api_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/forex/pairs")
        assert r.status_code in (401, 403), r.text

        r = await client.post(
            "/api/forex/quote",
            json={
                "pair": "EURUSD",
                "notional": "10000",
                "notional_currency": "base",
                "account_id": str(uuid.uuid4()),
            },
        )
        assert r.status_code in (401, 403), r.text


@pytest.mark.asyncio
async def test_sweep_expires_pending_quotes() -> None:
    row_id = str(uuid.uuid4())
    broker_quote_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())

    async with SessionLocal() as db:
        # Ensure a broker_accounts row exists (FK requirement)
        await db.execute(
            text(
                """
                INSERT INTO broker_accounts
                    (id, broker_id, account_number, alias, mode,
                     gateway_label, last_seen_via, currency_base)
                VALUES (:id, 'ibkr', :acct, 'test-sweep', 'paper', 'ibkr-ci', 'ibkr-ci', 'USD')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": account_id, "acct": f"TEST-{account_id[:8]}"},
        )
        # Ensure an instrument row exists (FK requirement)
        instr_result = await db.execute(
            text(
                """
                INSERT INTO instruments
                    (canonical_id, asset_class, primary_exchange, currency, display_name)
                VALUES ('forex:EURUSD:sweep_test', 'FOREX', 'IDEALPRO', 'USD', 'EUR/USD')
                ON CONFLICT (canonical_id) DO UPDATE SET updated_at = now()
                RETURNING id
                """
            )
        )
        instrument_id = instr_result.scalar_one()
        await db.execute(
            text(
                """
                INSERT INTO forex_rfq_quotes (
                    id, account_id, instrument_id, bid, ask, ttl_seconds,
                    broker_quote_id, notional, notional_currency, status, expires_at
                ) VALUES (
                    :id, :account_id, :instrument_id, '1.0800', '1.0802', 30,
                    :broker_quote_id, '10000', 'base', 'pending',
                    now() - interval '10 seconds'
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": row_id,
                "account_id": account_id,
                "instrument_id": instrument_id,
                "broker_quote_id": broker_quote_id,
            },
        )
        await db.commit()

        await sweep_expired_quotes(db)

        result = await db.execute(
            text("SELECT status FROM forex_rfq_quotes WHERE id = :id"),
            {"id": row_id},
        )
        row = result.fetchone()

    assert row is not None, "quote row not found after insert"
    assert row[0] == "expired", f"expected 'expired', got '{row[0]}'"


@pytest.mark.asyncio
async def test_get_forex_pairs_with_auth(test_client_admin: AsyncClient) -> None:
    r = await test_client_admin.get("/api/forex/pairs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict)
    assert "pairs" in body
    assert isinstance(body["pairs"], list)


@pytest.mark.asyncio
async def test_accept_with_expired_nonce(test_client_admin: AsyncClient) -> None:
    nonexistent_id = str(uuid.uuid4())
    r = await test_client_admin.post(
        f"/api/forex/quote/{nonexistent_id}/accept",
        json={
            "account_id": str(uuid.uuid4()),
            "side": "BUY",
            "qty": "10000",
        },
        headers={"X-CSRF-Token": nonexistent_id},
    )
    assert r.status_code == 422, r.text
    assert "nonce" in r.text.lower()
