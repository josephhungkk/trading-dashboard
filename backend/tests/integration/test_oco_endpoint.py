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
    """Legs referencing different broker prefixes → 422 oco_legs_different_brokers."""
    account_b_id = str(uuid.uuid4())
    leg_b_diff_broker = {**LEG_B, "account_id": account_b_id}

    async def _fake_cfg_get(ns: str, key: str, default: Any = None) -> Any:
        if ns == "broker" and key == "oco.enabled":
            return "true"
        return default

    async def _fake_resolve(db: Any, account_id: Any) -> Any:
        from app.services.orders_service import _Account

        if str(account_id) == ACCOUNT_ID:
            return _Account(gateway_label="ibkr-paper", mode="paper", currency_base="USD")
        # Second account belongs to a different broker (futu)
        return _Account(gateway_label="futu-paper", mode="paper", currency_base="HKD")

    async def _fake_redis_getdel(*args: Any, **kwargs: Any) -> str:
        return "nonce-payload"

    with (
        patch("app.services.config.ConfigService.get", new=AsyncMock(side_effect=_fake_cfg_get)),
        patch(
            "app.services.orders_service._resolve_account",
            new=AsyncMock(side_effect=_fake_resolve),
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
        app.state.redis = mock_redis

        try:
            r = await client.post(
                "/api/orders/oco",
                json={
                    "order_a": LEG_A,
                    "order_b": {**leg_b_diff_broker},
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
            PlaceOrderResult(broker_order_id="SIM-A-001"),
            PlaceOrderResult(broker_order_id="SIM-B-002"),
        ]
    )

    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value="nonce-payload")

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
                "app.services.orders_service._resolve_account",
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
                "app.api.orders._as_order_sidecar_client",
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
            return PlaceOrderResult(broker_order_id="SIM-A-001")
        raise RuntimeError("sidecar_timeout: leg B placement failed")

    mock_sidecar = AsyncMock()
    mock_sidecar.place_order = AsyncMock(side_effect=_fake_place)
    mock_sidecar.cancel_order = AsyncMock(side_effect=_fake_cancel)

    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value="nonce-payload")

    orig_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis

    try:
        with (
            patch(
                "app.services.config.ConfigService.get",
                new=AsyncMock(side_effect=_fake_cfg_get),
            ),
            patch(
                "app.services.orders_service._resolve_account",
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
                "app.api.orders._as_order_sidecar_client",
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
