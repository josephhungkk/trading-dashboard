"""Phase 15b crypto integration tests."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

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

from app.core.db import SessionLocal
from app.main import app
from app.services.crypto.book_manager import OrderBook

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crypto_assets_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/assets", params={"account_id": str(uuid.uuid4())})
        assert r.status_code in (401, 403), r.text


@pytest.mark.asyncio
async def test_crypto_instrument_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/instrument/BTC-USD")
        assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# CryptoService DB integration: instrument seed + resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crypto_instrument_seed_and_resolve() -> None:
    canonical_id = f"crypto:BTC.USD:ci_{uuid.uuid4().hex[:8]}"

    async with SessionLocal() as db:
        await db.execute(
            text(
                """
                INSERT INTO instruments
                    (canonical_id, asset_class, primary_exchange, currency, display_name)
                VALUES (:cid, 'CRYPTO', 'COINBASE', 'USD', 'BTC/USD')
                ON CONFLICT (canonical_id) DO UPDATE SET updated_at = now()
                """
            ),
            {"cid": canonical_id},
        )
        await db.commit()

        result = await db.execute(
            text("SELECT canonical_id, asset_class FROM instruments WHERE canonical_id = :cid"),
            {"cid": canonical_id},
        )
        row = result.fetchone()

    assert row is not None, "instrument row not found"
    assert row[0] == canonical_id
    assert row[1] == "CRYPTO"


# ---------------------------------------------------------------------------
# OrderBook in-memory unit
# ---------------------------------------------------------------------------


def test_order_book_apply_delta_add() -> None:
    book = OrderBook()
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("1.0"), seq=1)
    book.apply_delta(side="ask", price=Decimal("50100"), qty=Decimal("0.5"), seq=2)
    snap = book.snapshot()
    assert len(snap["bids"]) == 1
    assert len(snap["asks"]) == 1
    assert snap["bids"][0] == (Decimal("50000"), Decimal("1.0"))
    assert snap["asks"][0] == (Decimal("50100"), Decimal("0.5"))


def test_order_book_apply_delta_update() -> None:
    book = OrderBook()
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("1.0"), seq=1)
    # Update qty on existing level
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("2.5"), seq=2)
    snap = book.snapshot()
    assert snap["bids"][0] == (Decimal("50000"), Decimal("2.5"))


def test_order_book_apply_delta_remove() -> None:
    book = OrderBook()
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("1.0"), seq=1)
    book.apply_delta(side="bid", price=Decimal("49900"), qty=Decimal("0.5"), seq=2)
    # Remove a level by setting qty to 0
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("0"), seq=3)
    snap = book.snapshot()
    prices = [p for p, _ in snap["bids"]]
    assert Decimal("50000") not in prices
    assert Decimal("49900") in prices


def test_order_book_bids_sorted_descending() -> None:
    book = OrderBook()
    book.apply_delta(side="bid", price=Decimal("49900"), qty=Decimal("1.0"), seq=1)
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("1.0"), seq=2)
    book.apply_delta(side="bid", price=Decimal("49800"), qty=Decimal("1.0"), seq=3)
    snap = book.snapshot()
    prices = [p for p, _ in snap["bids"]]
    assert prices == sorted(prices, reverse=True)


def test_order_book_asks_sorted_ascending() -> None:
    book = OrderBook()
    book.apply_delta(side="ask", price=Decimal("50200"), qty=Decimal("1.0"), seq=1)
    book.apply_delta(side="ask", price=Decimal("50100"), qty=Decimal("1.0"), seq=2)
    book.apply_delta(side="ask", price=Decimal("50300"), qty=Decimal("1.0"), seq=3)
    snap = book.snapshot()
    prices = [p for p, _ in snap["asks"]]
    assert prices == sorted(prices)


def test_order_book_depth_truncation() -> None:
    book = OrderBook()
    for i in range(30):
        book.apply_delta(side="bid", price=Decimal(str(50000 - i * 10)), qty=Decimal("1.0"), seq=i)
    snap = book.snapshot(depth=5)
    assert len(snap["bids"]) == 5


def test_order_book_seq_tracking() -> None:
    book = OrderBook()
    book.apply_delta(side="bid", price=Decimal("50000"), qty=Decimal("1.0"), seq=42)
    assert book.last_seq == 42
