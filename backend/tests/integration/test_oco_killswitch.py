"""Phase 8b T-O.13 -- assert OCO endpoint respects broker.oco.enabled kill switch."""

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
# Shared fixtures + helpers
# ---------------------------------------------------------------------------

ACCOUNT_ID = str(uuid.uuid4())

LEG_A = {
    "account_id": ACCOUNT_ID,
    "conid": "265598",
    "side": "BUY",
    "order_type": "LIMIT",
    "tif": "DAY",
    "qty": "1.00000000",
    "limit_price": "100.00000000",
}

LEG_B = {
    "account_id": ACCOUNT_ID,
    "conid": "8314",
    "side": "SELL",
    "order_type": "LIMIT",
    "tif": "DAY",
    "qty": "1.00000000",
    "limit_price": "110.00000000",
}

OCO_NONCE = "t-o-13-nonce-001"


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
# T-O.13 — kill-switch gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oco_disabled_returns_503(client: AsyncClient) -> None:
    """When broker.oco.enabled is 'false', POST /api/orders/oco returns 503 oco_disabled."""
    with patch(
        "app.services.config.ConfigService.get",
        new=AsyncMock(return_value="false"),
    ):
        response = await client.post(
            "/api/orders/oco",
            json={"order_a": LEG_A, "order_b": LEG_B, "nonce": OCO_NONCE},
        )
    assert response.status_code == 503, response.text
    detail = response.json().get("detail", response.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "oco_disabled", f"unexpected detail: {detail}"


@pytest.mark.asyncio
async def test_oco_missing_config_returns_503(client: AsyncClient) -> None:
    """When broker.oco.enabled is absent (returns None), endpoint returns 503 oco_disabled."""
    with patch(
        "app.services.config.ConfigService.get",
        new=AsyncMock(return_value=None),
    ):
        response = await client.post(
            "/api/orders/oco",
            json={"order_a": LEG_A, "order_b": LEG_B, "nonce": OCO_NONCE},
        )
    assert response.status_code == 503, response.text
    detail = response.json().get("detail", response.json())
    error_code = detail.get("error") if isinstance(detail, dict) else None
    assert error_code == "oco_disabled", f"unexpected detail: {detail}"


@pytest.mark.asyncio
async def test_oco_enabled_passes_killswitch(client: AsyncClient) -> None:
    """When broker.oco.enabled='true', kill switch lets the request through to validation.

    Uses legs from different brokers so downstream 422 (not 503) confirms the kill-switch opened.
    """
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
        return _Account(gateway_label="schwab-paper", mode="paper", currency_base="USD")

    with (
        patch("app.services.config.ConfigService.get", new=AsyncMock(side_effect=_fake_cfg_get)),
        patch(
            "app.services.orders_service._resolve_account",
            new=AsyncMock(side_effect=_fake_resolve),
        ),
    ):
        orig_redis = getattr(app.state, "redis", None)
        mock_redis = AsyncMock()
        mock_redis.execute_command = AsyncMock(return_value="nonce-payload")
        app.state.redis = mock_redis

        try:
            response = await client.post(
                "/api/orders/oco",
                json={"order_a": LEG_A, "order_b": leg_b_diff_broker, "nonce": OCO_NONCE},
            )
        finally:
            if orig_redis is None:
                del app.state.redis
            else:
                app.state.redis = orig_redis

    # Kill switch opened — downstream validation (different brokers) may reject with 422
    # but must NOT return 503 oco_disabled
    assert response.status_code != 503, (
        f"kill switch should be open when enabled=true, got 503: {response.text}"
    )
    if response.status_code == 422:
        detail = response.json().get("detail", response.json())
        error_code = detail.get("error") if isinstance(detail, dict) else None
        assert error_code != "oco_disabled", "503 error code must not appear in 422 response"
