from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.combos import get_combos_redis
from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app
from app.models.instruments import Instrument

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def getdel(self, key: str) -> str | None:
        return self._store.pop(key, None)


async def _account_id(db: AsyncSession) -> str:
    result = await db.execute(text("SELECT id FROM broker_accounts LIMIT 1"))
    return str(result.scalar_one())


async def _instrument_ids(db: AsyncSession) -> tuple[int, int]:
    result = await db.execute(select(Instrument.id).limit(2))
    ids = result.scalars().all()
    return int(ids[0]), int(ids[1])


def _preview_body(inst1: int, inst2: int) -> dict:
    return {
        "strategy_type": "VERTICAL",
        "underlying_symbol": "AAPL",
        "underlying_canonical_id": "AAPL",
        "tif": "DAY",
        "legs": [
            {
                "instrument_id": inst1,
                "side": "buy",
                "qty": "1",
                "position_effect": "OPEN",
                "symbol": "AAPL",
                "exchange": "SMART",
                "currency": "USD",
                "expiry": "2026-01-17",
                "strike": "250.00",
                "put_call": "C",
                "ratio": 1,
                "limit_price": "5.00",
            },
            {
                "instrument_id": inst2,
                "side": "sell",
                "qty": "1",
                "position_effect": "OPEN",
                "symbol": "AAPL",
                "exchange": "SMART",
                "currency": "USD",
                "expiry": "2026-01-17",
                "strike": "260.00",
                "put_call": "C",
                "ratio": 1,
                "limit_price": "2.00",
            },
        ],
    }


async def test_preview_returns_envelope(db_session: AsyncSession) -> None:
    fake_redis = _FakeRedis()
    account_id = await _account_id(db_session)
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@t.com", kind="google", claims={"account_id": account_id}
    )
    app.dependency_overrides[get_combos_redis] = lambda: fake_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            inst1, inst2 = await _instrument_ids(db_session)
            response = await client.post("/api/combos/preview", json=_preview_body(inst1, inst2))
            assert response.status_code == 200
            data = response.json()
            # stub _fetch_mids returns 5.00 for all legs → net = sell - buy = 0 → CREDIT
            assert data["envelope"]["kind"] in ("DEBIT", "CREDIT")
            assert "csrf_nonce" in data
            assert data["risk_blockers"] == []
    finally:
        app.dependency_overrides.pop(require_admin_jwt, None)
        app.dependency_overrides.pop(get_combos_redis, None)


async def test_preview_invalid_legs(db_session: AsyncSession) -> None:
    fake_redis = _FakeRedis()
    account_id = await _account_id(db_session)
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@t.com", kind="google", claims={"account_id": account_id}
    )
    app.dependency_overrides[get_combos_redis] = lambda: fake_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            inst1, inst2 = await _instrument_ids(db_session)
            body = _preview_body(inst1, inst2)
            body["legs"][1]["side"] = "buy"
            response = await client.post("/api/combos/preview", json=body)
            assert response.status_code == 422
            assert response.json()["detail"]["error_code"] == "combo_invalid_legs"
    finally:
        app.dependency_overrides.pop(require_admin_jwt, None)
        app.dependency_overrides.pop(get_combos_redis, None)


async def test_confirm_expired_nonce(db_session: AsyncSession) -> None:
    fake_redis = _FakeRedis()
    account_id = await _account_id(db_session)
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@t.com", kind="google", claims={"account_id": account_id}
    )
    app.dependency_overrides[get_combos_redis] = lambda: fake_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/combos/confirm/nonexistent-nonce-abc123",
                headers={"X-Csrf-Nonce": "nonexistent-nonce-abc123"},
                json={
                    "client_combo_id": "combo-test",
                    "legs": [],
                    "underlying_canonical_id": "AAPL",
                    "strategy_type": "VERTICAL",
                    "underlying_symbol": "AAPL",
                    "tif": "DAY",
                    "net_debit_credit": "3.10",
                    "net_debit_credit_kind": "DEBIT",
                },
            )
            assert response.status_code == 410
            assert response.json()["detail"]["error_code"] == "nonce_invalid"
    finally:
        app.dependency_overrides.pop(require_admin_jwt, None)
        app.dependency_overrides.pop(get_combos_redis, None)


async def test_confirm_csrf_mismatch(db_session: AsyncSession) -> None:
    fake_redis = _FakeRedis()
    account_id = await _account_id(db_session)
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@t.com", kind="google", claims={"account_id": account_id}
    )
    app.dependency_overrides[get_combos_redis] = lambda: fake_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/combos/confirm/some-nonce-xyz",
                headers={"X-Csrf-Nonce": "DIFFERENT-nonce"},
                json={
                    "client_combo_id": "combo-test",
                    "legs": [],
                    "underlying_canonical_id": "AAPL",
                    "strategy_type": "VERTICAL",
                    "underlying_symbol": "AAPL",
                    "tif": "DAY",
                    "net_debit_credit": "3.10",
                    "net_debit_credit_kind": "DEBIT",
                },
            )
            assert response.status_code == 422
            assert response.json()["detail"]["error_code"] == "csrf_required"
    finally:
        app.dependency_overrides.pop(require_admin_jwt, None)
        app.dependency_overrides.pop(get_combos_redis, None)


async def test_confirm_payload_drift(db_session: AsyncSession) -> None:
    fake_redis = _FakeRedis()
    account_id = await _account_id(db_session)
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@t.com", kind="google", claims={"account_id": account_id}
    )
    app.dependency_overrides[get_combos_redis] = lambda: fake_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            inst1, inst2 = await _instrument_ids(db_session)
            preview_resp = await client.post(
                "/api/combos/preview", json=_preview_body(inst1, inst2)
            )
            assert preview_resp.status_code == 200
            preview_data = preview_resp.json()
            nonce = preview_data["csrf_nonce"]
            confirm_resp = await client.post(
                f"/api/combos/confirm/{nonce}",
                headers={"X-Csrf-Nonce": nonce},
                json={
                    "client_combo_id": "combo-TAMPERED",
                    "legs": _preview_body(inst1, inst2)["legs"],
                    "underlying_canonical_id": "AAPL",
                    "strategy_type": "VERTICAL",
                    "underlying_symbol": "AAPL",
                    "tif": "DAY",
                    "net_debit_credit": preview_data["envelope"]["net_debit_credit"],
                    "net_debit_credit_kind": preview_data["envelope"]["kind"],
                },
            )
            assert confirm_resp.status_code == 409
            assert confirm_resp.json()["detail"]["error_code"] == "payload_drift"
    finally:
        app.dependency_overrides.pop(require_admin_jwt, None)
        app.dependency_overrides.pop(get_combos_redis, None)
