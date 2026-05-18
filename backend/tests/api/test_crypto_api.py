from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_crypto_assets_requires_auth() -> None:
    from httpx import ASGITransport

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/assets", params={"account_id": "test"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_crypto_instrument_requires_auth() -> None:
    from httpx import ASGITransport

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/crypto/instrument/BTC.USD")
    assert r.status_code in (401, 403)
