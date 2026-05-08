"""Integration tests for POST /api/orders/oco endpoint (T-O.6).

Drives the OCO placement endpoint through FastAPI ASGITransport.
All sidecar calls are intercepted via dependency overrides or mock injection;
no real broker sidecars or database connections are needed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ACCOUNT_ID = str(uuid.uuid4())
CONID_A = "265598"
CONID_B = "8314"

LEG_A = {
    "account_id": ACCOUNT_ID,
    "conid": CONID_A,
    "side": "BUY",
    "order_type": "LIMIT",
    "tif": "DAY",
    "qty": "1.00000000",
    "limit_price": "100.00000000",
}

LEG_B = {
    "account_id": ACCOUNT_ID,
    "conid": CONID_B,
    "side": "SELL",
    "order_type": "LIMIT",
    "tif": "DAY",
    "qty": "1.00000000",
    "limit_price": "110.00000000",
}

OCO_NONCE = "test-oco-nonce-001"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """AsyncClient with admin JWT bypassed."""

    async def _admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    app.dependency_overrides[require_admin_jwt] = _admin
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# test_oco_killswitch_disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oco_killswitch_disabled(client: AsyncClient) -> None:
    """When broker.oco.enabled is absent (or 'false'), endpoint returns 503."""
    # Ensure the config key is absent / false by patching ConfigService.get
    with patch(
        "app.services.config.ConfigService.get",
        new=AsyncMock(return_value="false"),
    ):
        r = await client.post(
            "/api/orders/oco",
            json={
                "order_a": LEG_A,
                "order_b": LEG_B,
                "nonce": OCO_NONCE,
            },
        )
    assert r.status_code == 503, r.text
    detail = r.json().get("detail", r.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "oco_disabled", f"unexpected detail: {detail}"


# ---------------------------------------------------------------------------
# test_oco_legs_different_brokers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oco_legs_different_brokers(client: AsyncClient) -> None:
    """Resolved broker prefix mismatch → 422 oco_legs_different_brokers."""
    from app.services.orders_service import _Account

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    with (
        patch("app.services.config.ConfigService.get", new=AsyncMock(side_effect=_fake_cfg_get)),
        patch(
            "app.api.orders.resolve_account",
            new=AsyncMock(
                side_effect=[
                    _Account(gateway_label="ibkr-paper", mode="paper", currency_base="USD"),
                    _Account(gateway_label="futu-paper", mode="paper", currency_base="HKD"),
                ]
            ),
        ),
        patch(
            "app.api.orders.RedisLike.execute_command",
            new=AsyncMock(return_value="nonce-payload"),
        ),
    ):
        # Patch the redis object on the endpoint via app.state
        orig_redis = getattr(app.state, "redis", None)
        mock_redis = AsyncMock()
        mock_redis.execute_command = AsyncMock(return_value="nonce-payload")
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock(return_value=True)
        app.state.redis = mock_redis

        try:
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": LEG_B,
                    "nonce": OCO_NONCE,
                },
            )
        finally:
            if orig_redis is None:
                del app.state.redis
            else:
                app.state.redis = orig_redis

    assert r.status_code == 422, r.text
    detail = r.json().get("detail", r.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "oco_legs_different_brokers", f"unexpected detail: {detail}"


# ---------------------------------------------------------------------------
# test_oco_legs_different_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oco_legs_different_accounts(client: AsyncClient) -> None:
    """Legs with different account_id → 422 oco_legs_different_accounts."""
    account_b_id = str(uuid.uuid4())
    leg_b_diff_acct = {**LEG_B, "account_id": account_b_id}

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value="nonce-payload")
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)

    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with patch(
            "app.services.config.ConfigService.get", new=AsyncMock(side_effect=_fake_cfg_get)
        ):
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": leg_b_diff_acct,
                    "nonce": OCO_NONCE,
                },
            )
    finally:
        if orig_redis is None:
            del app.state.redis
        else:
            app.state.redis = orig_redis

    assert r.status_code == 422, r.text
    detail = r.json().get("detail", r.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "oco_legs_different_accounts", f"unexpected detail: {detail}"


# ---------------------------------------------------------------------------
# test_oco_happy_path_with_killswitch_on
# ---------------------------------------------------------------------------


def _make_oco_nonce_payload() -> str:
    """Return a JSON nonce payload whose hash matches LEG_A + LEG_B as Pydantic normalizes them."""
    import json as _json

    from app.api.orders import _oco_payload_hash
    from app.schemas.orders import OcoOrderRequest

    req = OcoOrderRequest(order_a=LEG_A, order_b=LEG_B, nonce="x")
    leg_a_dict = req.order_a.model_dump(mode="json")
    leg_b_dict = req.order_b.model_dump(mode="json")
    payload_hash = _oco_payload_hash(leg_a_dict, leg_b_dict)
    return _json.dumps({"payload_hash": payload_hash})


@pytest.mark.asyncio
async def test_oco_happy_path_with_killswitch_on(client: AsyncClient) -> None:
    """Both legs succeed → 200 with oco_link_id; oco_links INSERT is called."""
    from app.brokers.base import PlaceOrderResult
    from app.services.orders_service import _Account

    fake_account = _Account(gateway_label="ibkr-paper", mode="paper", currency_base="USD")

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    async def _fake_capability_is_supported(*args: Any, **kwargs: Any) -> bool:
        return True

    mock_sidecar = AsyncMock()
    mock_sidecar.place_order = AsyncMock(
        side_effect=[
            PlaceOrderResult(broker_order_id="SIM-A-001", status="PENDING"),
            PlaceOrderResult(broker_order_id="SIM-B-002", status="PENDING"),
        ]
    )

    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=_make_oco_nonce_payload())
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)

    mock_db_execute = AsyncMock()
    mock_db_commit = AsyncMock()

    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with (
            patch(
                "app.services.config.ConfigService.get",
                new=AsyncMock(side_effect=_fake_cfg_get),
            ),
            patch(
                "app.api.orders.resolve_account",
                new=AsyncMock(return_value=fake_account),
            ),
            patch(
                "app.services.order_capability_service.OrderCapabilityService.is_supported",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.brokers.BrokerRegistry.get_client",
                new=AsyncMock(return_value=mock_sidecar),
            ),
            patch(
                "app.api.orders.as_order_sidecar_client",
                return_value=mock_sidecar,
            ),
            patch("sqlalchemy.ext.asyncio.AsyncSession.execute", new=mock_db_execute),
            patch("sqlalchemy.ext.asyncio.AsyncSession.commit", new=mock_db_commit),
        ):
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": LEG_B,
                    "nonce": OCO_NONCE,
                },
            )
    finally:
        if orig_redis is None:
            del app.state.redis
        else:
            app.state.redis = orig_redis

    assert r.status_code == 200, r.text
    data = r.json()
    assert "oco_link_id" in data, f"missing oco_link_id: {data}"
    assert data["order_id_a"] == "SIM-A-001"
    assert data["order_id_b"] == "SIM-B-002"
    # Confirm INSERT was issued
    mock_db_execute.assert_called()


# ---------------------------------------------------------------------------
# test_oco_atomicity_rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oco_atomicity_rollback(client: AsyncClient) -> None:
    """Leg B fails → cancel_order called for leg A; error propagated to caller."""
    from app.brokers.base import PlaceOrderResult
    from app.services.orders_service import _Account

    fake_account = _Account(gateway_label="ibkr-paper", mode="paper", currency_base="USD")

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    cancel_calls: list[tuple[str, str]] = []

    async def _fake_cancel(account_number: str, broker_order_id: str) -> bool:
        cancel_calls.append((account_number, broker_order_id))
        return True

    async def _fake_place(
        account_number: str,
        client_order_id: str,
        conid: str,
        *args: Any,
        **kwargs: Any,
    ) -> PlaceOrderResult:
        if conid == CONID_A:
            return PlaceOrderResult(broker_order_id="SIM-A-001", status="PENDING")
        raise RuntimeError("sidecar_timeout: leg B placement failed")

    mock_sidecar = AsyncMock()
    mock_sidecar.place_order = AsyncMock(side_effect=_fake_place)
    mock_sidecar.cancel_order = AsyncMock(side_effect=_fake_cancel)

    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=_make_oco_nonce_payload())
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)

    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with (
            patch(
                "app.services.config.ConfigService.get",
                new=AsyncMock(side_effect=_fake_cfg_get),
            ),
            patch(
                "app.api.orders.resolve_account",
                new=AsyncMock(return_value=fake_account),
            ),
            patch(
                "app.services.order_capability_service.OrderCapabilityService.is_supported",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.brokers.BrokerRegistry.get_client",
                new=AsyncMock(return_value=mock_sidecar),
            ),
            patch(
                "app.api.orders.as_order_sidecar_client",
                return_value=mock_sidecar,
            ),
        ):
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": LEG_B,
                    "nonce": OCO_NONCE,
                },
            )
    finally:
        if orig_redis is None:
            del app.state.redis
        else:
            app.state.redis = orig_redis

    # Leg B failure → 503 propagated
    assert r.status_code == 503, r.text
    detail = r.json().get("detail", r.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "oco_leg_b_failed", f"unexpected detail: {detail}"

    # Leg A cancel must have been attempted
    assert len(cancel_calls) == 1, f"expected exactly 1 cancel call, got {cancel_calls}"
    assert cancel_calls[0][1] == "SIM-A-001", f"cancel for wrong order id: {cancel_calls[0]}"


# ---------------------------------------------------------------------------
# HIGH-sec-1: nonce payload-hash validation
# ---------------------------------------------------------------------------


def _make_redis_with_nonce(payload: str | None) -> AsyncMock:
    """Return a mock Redis whose GETDEL returns *payload*."""
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=payload)
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    return mock_redis


@pytest.mark.asyncio
async def test_oco_hash_mismatch_returns_401(client: AsyncClient) -> None:
    """Stored payload_hash doesn't match submitted legs → 401 payload_hash_mismatch
    (HIGH-sec-1: tamper detection).
    """
    import json

    from app.services.orders_service import _Account

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    same_account = _Account(gateway_label="ibkr-paper", mode="paper", currency_base="USD")

    # Store a hash that will NOT match the submitted legs
    wrong_hash_payload = json.dumps({"payload_hash": "deadbeef" * 8})
    mock_redis = _make_redis_with_nonce(wrong_hash_payload)

    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with (
            patch(
                "app.services.config.ConfigService.get",
                new=AsyncMock(side_effect=_fake_cfg_get),
            ),
            patch(
                "app.api.orders.resolve_account",
                new=AsyncMock(return_value=same_account),
            ),
        ):
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": LEG_B,
                    "nonce": OCO_NONCE,
                },
            )
    finally:
        if orig_redis is None:
            del app.state.redis
        else:
            app.state.redis = orig_redis

    assert r.status_code == 401, r.text
    detail = r.json().get("detail", r.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "payload_hash_mismatch", f"unexpected detail: {detail}"


@pytest.mark.asyncio
async def test_oco_unknown_nonce_returns_401(client: AsyncClient) -> None:
    """GETDEL returns None (nonce absent or already consumed) → 401 unknown_nonce
    (HIGH-sec-1: single-use nonce enforcement / reuse prevention).
    """
    from app.services.orders_service import _Account

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    same_account = _Account(gateway_label="ibkr-paper", mode="paper", currency_base="USD")

    # GETDEL returns None → nonce not present in Redis
    mock_redis = _make_redis_with_nonce(None)

    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with (
            patch(
                "app.services.config.ConfigService.get",
                new=AsyncMock(side_effect=_fake_cfg_get),
            ),
            patch(
                "app.api.orders.resolve_account",
                new=AsyncMock(return_value=same_account),
            ),
        ):
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": LEG_B,
                    "nonce": "expired-or-already-consumed-nonce",
                },
            )
    finally:
        if orig_redis is None:
            del app.state.redis
        else:
            app.state.redis = orig_redis

    assert r.status_code == 401, r.text
    detail = r.json().get("detail", r.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "unknown_nonce", f"unexpected detail: {detail}"


# ---------------------------------------------------------------------------
# HIGH-sec-2: rate limit on OCO nonce minting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oco_nonce_mint_rate_limit_returns_429(client: AsyncClient) -> None:
    """When the rate-limit bucket is exhausted, POST /api/orders/nonce/oco
    must return 429 (HIGH-sec-2).
    """
    from fastapi import HTTPException as _HTTPException

    mock_redis = AsyncMock()
    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with patch(
            "app.api.orders._check_modify_nonce_rate_limit",
            new=AsyncMock(
                side_effect=_HTTPException(
                    status_code=429,
                    detail={"error": "rate_limit_exceeded"},
                )
            ),
        ):
            r = await client.post(
                "/api/orders/nonce/oco",
                json={
                    "leg_a": LEG_A,
                    "leg_b": LEG_B,
                },
            )
    finally:
        if orig_redis is None:
            del app.state.redis
        else:
            app.state.redis = orig_redis

    assert r.status_code == 429, r.text
