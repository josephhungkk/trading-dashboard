from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cf_access import AdminIdentity
from app.core.db import engine
from app.core.deps import get_db, require_admin_jwt
from app.main import app


@pytest.fixture
async def admin_instruments_client() -> AsyncIterator[AsyncClient]:
    async with engine.connect() as conn:
        tx = await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async def override_db() -> AsyncIterator[AsyncSession]:
            async with session_factory() as session:
                yield session

        async def override_admin() -> AdminIdentity:
            return AdminIdentity(email="admin@test.local", kind="user", claims={})

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_admin_jwt] = override_admin
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(require_admin_jwt, None)
            await tx.rollback()


def _payload() -> dict[str, object]:
    return {
        "canonical_id": "stock:AAPL:US",
        "asset_class": "STOCK",
        "primary_exchange": "NASDAQ",
        "currency": "USD",
        "aliases": [{"source": "schwab", "raw_symbol": "AAPL"}],
    }


@pytest.mark.asyncio
async def test_create_instrument_201(admin_instruments_client: AsyncClient) -> None:
    response = await admin_instruments_client.post("/api/admin/instruments", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["canonical_id"] == "stock:AAPL:US"
    assert body["aliases_created"] == ["schwab:AAPL"]
    assert isinstance(body["instrument_id"], int)


@pytest.mark.asyncio
async def test_create_instrument_invalid_canonical_id_400(
    admin_instruments_client: AsyncClient,
) -> None:
    payload = _payload()
    payload["canonical_id"] = "bad:AAPL:US"

    response = await admin_instruments_client.post("/api/admin/instruments", json=payload)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_instrument_empty_aliases_400(
    admin_instruments_client: AsyncClient,
) -> None:
    payload = _payload()
    payload["aliases"] = []

    response = await admin_instruments_client.post("/api/admin/instruments", json=payload)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_instrument_requires_jwt() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/admin/instruments", json=_payload())

    assert response.status_code == 401
