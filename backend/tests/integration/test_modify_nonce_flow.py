"""Integration tests for POST /api/orders/nonce/modify + POST /api/orders/modify.

Task 30: mint endpoint + GETDEL nonce gate for the drag-handle SL/TP modify flow.

All sidecar and DB calls are mocked; no real broker connection required.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_config, get_db, require_admin_jwt
from app.core.logging import _redact_secrets
from app.main import app

# All tests in this module use fakeredis + mocked service; no DB needed.
pytestmark = [pytest.mark.no_db, pytest.mark.integration]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORDER_ID = "00000000-0000-4000-8000-000000000abc"

_MODIFY_BODY_FIELDS: dict[str, Any] = {
    "qty": "2.00000000",
    "order_type": "LIMIT",
    "tif": "DAY",
    "limit_price": "99.00000000",
}


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """In-memory fakeredis wired into app.state.redis for each test."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    original = getattr(app.state, "redis", None)
    app.state.redis = r
    yield r
    if original is None:
        try:
            del app.state.redis
        except AttributeError:
            pass
    else:
        app.state.redis = original
    await r.aclose()


@pytest.fixture
async def authed_client(fake_redis: fakeredis.aioredis.FakeRedis) -> AsyncIterator[AsyncClient]:
    """AsyncClient with admin JWT bypassed and all lifecycle deps mocked.

    Overrides:
    - require_admin_jwt  → stub AdminIdentity (no CF Access needed)
    - get_config         → AsyncMock ConfigService (no DB/Redis lifespan needed)
    - get_db             → AsyncMock AsyncSession  (no real PG needed)
    - get_broker_registry → AsyncMock BrokerRegistry (no real sidecars needed)

    app.state.redis is already wired to ``fake_redis`` by the ``fake_redis``
    fixture, so get_orders_redis() picks it up automatically.
    """
    from unittest.mock import AsyncMock, MagicMock

    async def _admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    async def _fake_db() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    def _fake_config() -> AsyncMock:
        return AsyncMock()

    def _fake_registry() -> MagicMock:
        return MagicMock()

    app.dependency_overrides[require_admin_jwt] = _admin
    app.dependency_overrides[get_config] = _fake_config
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_broker_registry] = _fake_registry
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper: mint a nonce via the endpoint
# ---------------------------------------------------------------------------


async def _mint(client: AsyncClient, order_id: str = ORDER_ID) -> str:
    r = await client.post("/api/orders/nonce/modify", json={"order_id": order_id})
    assert r.status_code == 200, f"mint failed: {r.text}"
    return r.json()["nonce"]


# ---------------------------------------------------------------------------
# Test 1: mint returns nonce (UUID4 hex) and expires_at ~30s from now
# ---------------------------------------------------------------------------


async def test_mint_returns_nonce_and_expiry(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    before = datetime.now(UTC)
    r = await authed_client.post("/api/orders/nonce/modify", json={"order_id": ORDER_ID})

    assert r.status_code == 200, r.text
    data = r.json()

    assert "nonce" in data, f"missing nonce key: {data}"
    assert "expires_at" in data, f"missing expires_at key: {data}"

    # nonce must be a 32-char hex string (UUID4 hex)
    assert re.fullmatch(r"[0-9a-f]{32}", data["nonce"]), f"bad nonce format: {data['nonce']}"

    # expires_at must be ~30 s from now (allow ±2 s for test latency)
    expires_at = datetime.fromisoformat(data["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    delta = (expires_at - before).total_seconds()
    assert 28 <= delta <= 32, f"expires_at delta out of range: {delta}s"


# ---------------------------------------------------------------------------
# Test 2: mint stores key in Redis with TTL 25-30 s
# ---------------------------------------------------------------------------


async def test_mint_stores_in_redis_with_ttl(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    r = await authed_client.post("/api/orders/nonce/modify", json={"order_id": ORDER_ID})
    assert r.status_code == 200, r.text
    nonce = r.json()["nonce"]

    key = f"nonce:modify:{ORDER_ID}:{nonce}"
    ttl = await fake_redis.ttl(key)
    assert 25 <= ttl <= 30, f"TTL out of expected range [25, 30]: {ttl}"


# ---------------------------------------------------------------------------
# Test 3: POST /modify consumes nonce via GETDEL and succeeds
# ---------------------------------------------------------------------------


async def test_modify_consumes_nonce_via_getdel(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    nonce = await _mint(authed_client)
    key = f"nonce:modify:{ORDER_ID}:{nonce}"

    # Key must exist before submission
    assert await fake_redis.exists(key) == 1

    # Mock the service to avoid real DB/broker calls
    with patch(
        "app.services.orders_service.modify_order",
        new=AsyncMock(return_value={"status": "modified", "id": ORDER_ID}),
    ):
        r = await authed_client.post(
            "/api/orders/modify",
            json={"order_id": ORDER_ID, "nonce": nonce, **_MODIFY_BODY_FIELDS},
        )

    assert r.status_code == 200, r.text

    # Key must be gone after GETDEL
    assert await fake_redis.exists(key) == 0, "nonce key still exists after GETDEL"


# ---------------------------------------------------------------------------
# Test 4: reuse of a consumed nonce returns 412
# ---------------------------------------------------------------------------


async def test_modify_reuse_nonce_returns_412(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    nonce = await _mint(authed_client)

    with patch(
        "app.services.orders_service.modify_order",
        new=AsyncMock(return_value={"status": "modified", "id": ORDER_ID}),
    ):
        r1 = await authed_client.post(
            "/api/orders/modify",
            json={"order_id": ORDER_ID, "nonce": nonce, **_MODIFY_BODY_FIELDS},
        )
    assert r1.status_code == 200, f"first modify failed: {r1.text}"

    # Second submit with same nonce must fail
    with patch(
        "app.services.orders_service.modify_order",
        new=AsyncMock(return_value={"status": "modified", "id": ORDER_ID}),
    ):
        r2 = await authed_client.post(
            "/api/orders/modify",
            json={"order_id": ORDER_ID, "nonce": nonce, **_MODIFY_BODY_FIELDS},
        )
    assert r2.status_code == 412, f"expected 412, got {r2.status_code}: {r2.text}"
    assert r2.json().get("detail") == "nonce_invalid_or_expired"


# ---------------------------------------------------------------------------
# Test 5: expired / missing nonce (key deleted to simulate expiry) returns 412
# ---------------------------------------------------------------------------


async def test_modify_expired_nonce_returns_412(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    # Mint but then manually delete the key to simulate TTL expiry
    nonce = await _mint(authed_client)
    key = f"nonce:modify:{ORDER_ID}:{nonce}"
    await fake_redis.delete(key)

    r = await authed_client.post(
        "/api/orders/modify",
        json={"order_id": ORDER_ID, "nonce": nonce, **_MODIFY_BODY_FIELDS},
    )
    assert r.status_code == 412, f"expected 412, got {r.status_code}: {r.text}"
    assert r.json().get("detail") == "nonce_invalid_or_expired"


# ---------------------------------------------------------------------------
# Test 6: missing nonce field in POST /modify body returns 422
# ---------------------------------------------------------------------------


async def test_modify_missing_nonce_field_returns_422(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    r = await authed_client.post(
        "/api/orders/modify",
        json={"order_id": ORDER_ID, **_MODIFY_BODY_FIELDS},  # no "nonce"
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# Test 7: wrong nonce string (key not in Redis) returns 412
# ---------------------------------------------------------------------------


async def test_modify_wrong_nonce_returns_412(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    # Mint to put a valid key in Redis, but submit with a different nonce string
    await _mint(authed_client)
    wrong_nonce = "deadbeef" * 4  # 32 hex chars, but wrong value

    r = await authed_client.post(
        "/api/orders/modify",
        json={"order_id": ORDER_ID, "nonce": wrong_nonce, **_MODIFY_BODY_FIELDS},
    )
    assert r.status_code == 412, f"expected 412, got {r.status_code}: {r.text}"
    assert r.json().get("detail") == "nonce_invalid_or_expired"


# ---------------------------------------------------------------------------
# Test 8: nonce value is redacted from structlog output (codex_default G)
# ---------------------------------------------------------------------------


async def test_nonce_redacted_from_logs(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Nonce value must not appear in any log record after structlog redaction."""
    nonce_value = "abcdef1234567890abcdef1234567890"

    with capture_logs(processors=[_redact_secrets]) as captured_logs:
        # Simulate what the endpoint logs — a record with an explicit nonce= kwarg
        structlog.get_logger("test").info(
            "modify_nonce_minted",
            order_id=ORDER_ID,
            nonce=nonce_value,
        )
        structlog.get_logger("test").info(
            "modify_nonce_consumed",
            order_id=ORDER_ID,
            nonce=nonce_value,
        )

    for record in captured_logs:
        # The raw nonce string must not appear anywhere in the record repr
        record_str = str(record)
        assert nonce_value not in record_str, f"nonce value leaked into log record: {record_str}"
        # If the 'nonce' key survived, it must be the redacted sentinel
        if "nonce" in record:
            assert record["nonce"] == "<redacted>", (
                f"nonce field not redacted to sentinel: {record['nonce']!r}"
            )


# ---------------------------------------------------------------------------
# Test 9: MED-26 — mint with non-UUID order_id returns 422
# ---------------------------------------------------------------------------


async def test_mint_with_non_uuid_order_id_returns_422(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """POST /api/orders/nonce/modify with order_id='abc' (not UUID4) returns 422."""
    r = await authed_client.post("/api/orders/nonce/modify", json={"order_id": "abc"})
    assert r.status_code == 422, (
        f"Expected 422 for non-UUID order_id, got {r.status_code}: {r.text}"
    )


async def test_mint_with_empty_order_id_returns_422(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """POST /api/orders/nonce/modify with order_id='' returns 422."""
    r = await authed_client.post("/api/orders/nonce/modify", json={"order_id": ""})
    assert r.status_code == 422, f"Expected 422 for empty order_id, got {r.status_code}: {r.text}"


async def test_mint_with_valid_uuid4_succeeds(
    authed_client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """POST /api/orders/nonce/modify with valid UUID4 order_id returns 200."""
    r = await authed_client.post("/api/orders/nonce/modify", json={"order_id": ORDER_ID})
    assert r.status_code == 200, (
        f"Expected 200 for valid UUID4 order_id, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# Test 10: MED-28 — token / jwt / authorization keys are redacted
# ---------------------------------------------------------------------------


async def test_token_keys_redacted_from_logs() -> None:
    """token, jwt, authorization, access_token, refresh_token keys must be redacted."""
    sensitive_value = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0In0.abc"

    with capture_logs(processors=[_redact_secrets]) as captured_logs:
        structlog.get_logger("test").info(
            "auth_event",
            token=sensitive_value,
            jwt=sensitive_value,
            authorization=f"Bearer {sensitive_value}",
            access_token=sensitive_value,
            refresh_token=sensitive_value,
        )

    assert len(captured_logs) == 1
    record = captured_logs[0]
    for key in ("token", "jwt", "authorization", "access_token", "refresh_token"):
        assert key in record, f"key {key!r} missing from log record"
        assert record[key] == "<redacted>", f"{key!r} not redacted: {record[key]!r}"
