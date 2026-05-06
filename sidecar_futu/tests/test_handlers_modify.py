"""T-F.1 — ModifyOrder handler (real-mode branch via FutuClient.modify_order_live)."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _modify_req(
    account_number: str = "12345678",
    broker_order_id: str = "999111",
    qty: str = "200",
    limit_price: str = "355.00",
) -> broker_pb2.ModifyOrderRequest:
    return broker_pb2.ModifyOrderRequest(
        account_number=account_number,
        broker_order_id=broker_order_id,
        qty=qty,
        limit_price=broker_pb2.Money(value=limit_price, currency="HKD"),
    )


class _AbortCtx:
    """Context stub that raises grpc.RpcError on abort()."""

    def __init__(self) -> None:
        self.aborted: tuple[grpc.StatusCode, str] | None = None

    async def abort(self, code: grpc.StatusCode, detail: str) -> None:
        self.aborted = (code, detail)
        raise grpc.RpcError(detail)


@pytest.mark.asyncio
async def test_modify_order_uses_simulate_for_paper() -> None:
    """Paper account → modify_order_live called with SIMULATE trd_env."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    # Seed account as SIMULATE (paper)
    handlers._client._accounts_trd_env["12345678"] = "SIMULATE"

    captured: dict[str, object] = {}

    async def spy_modify(
        account_number: str,
        broker_order_id: str,
        qty: float,
        price: float,
    ) -> tuple[bool, str]:
        captured["account"] = account_number
        captured["trd_env"] = handlers._client._accounts_trd_env.get(account_number)
        return True, ""

    handlers._client.modify_order_live = spy_modify  # type: ignore[method-assign]

    response = await handlers.ModifyOrder(_modify_req(), context=MagicMock())
    assert response.status == "SUBMITTED"
    assert captured["trd_env"] == "SIMULATE"


@pytest.mark.asyncio
async def test_modify_order_uses_real_for_live() -> None:
    """Live account → modify_order_live called with REAL trd_env."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client._accounts_trd_env["12345678"] = "REAL"

    captured: dict[str, object] = {}

    async def spy_modify(
        account_number: str,
        broker_order_id: str,
        qty: float,
        price: float,
    ) -> tuple[bool, str]:
        captured["trd_env"] = handlers._client._accounts_trd_env.get(account_number)
        return True, ""

    handlers._client.modify_order_live = spy_modify  # type: ignore[method-assign]

    response = await handlers.ModifyOrder(_modify_req(), context=MagicMock())
    assert response.status == "SUBMITTED"
    assert captured["trd_env"] == "REAL"


@pytest.mark.asyncio
async def test_modify_order_failed_precondition() -> None:
    """modify_order_live returning (False, msg) → FAILED_PRECONDITION abort."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client._accounts_trd_env["12345678"] = "REAL"

    async def failing_modify(
        account_number: str,
        broker_order_id: str,
        qty: float,
        price: float,
    ) -> tuple[bool, str]:
        return False, "order_not_modifiable"

    handlers._client.modify_order_live = failing_modify  # type: ignore[method-assign]

    ctx = _AbortCtx()
    with pytest.raises(grpc.RpcError):
        await handlers.ModifyOrder(_modify_req(), context=ctx)

    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert "order_not_modifiable" in ctx.aborted[1]


@pytest.mark.asyncio
async def test_modify_order_returns_broker_order_id() -> None:
    """Successful modify returns original broker_order_id in response."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.modify_order_live = AsyncMock(return_value=(True, ""))  # type: ignore[method-assign]

    response = await handlers.ModifyOrder(
        _modify_req(broker_order_id="XYZ-42"), context=MagicMock()
    )
    assert response.broker_order_id == "XYZ-42"
    assert response.status == "SUBMITTED"
