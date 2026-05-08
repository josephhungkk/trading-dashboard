"""FutuClient bracket rollback regressions."""
from __future__ import annotations

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.futu_client import FutuClient


def _leg(account_number: str, client_order_id: str) -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number=account_number,
        client_order_id=client_order_id,
        conid="HK.00700",
        side="BUY",
        order_type="LIMIT",
        qty="100",
        limit_price="350.00",
        tif="DAY",
    )


def _request() -> broker_pb2.PlaceBracketRequest:
    return broker_pb2.PlaceBracketRequest(
        parent=_leg("12345678", "parent"),
        stop_loss=_leg("12345678", "sl"),
        take_profit=_leg("12345678", "tp"),
        has_stop_loss=True,
        has_take_profit=True,
    )


@pytest.mark.asyncio
async def test_place_bracket_cancels_parent_when_stop_loss_fails() -> None:
    client = FutuClient()
    client.gateway_connected = True
    client._trade_ctx = object()
    placed = 0
    cancelled: list[tuple[str, str]] = []

    async def place_order(
        request: broker_pb2.PlaceOrderRequest,
    ) -> tuple[str, str]:
        nonlocal placed
        placed += 1
        if placed == 1:
            return "P-001", "submitted"
        raise RuntimeError("stop rejected")

    async def cancel_order(account_number: str, broker_order_id: str) -> bool:
        cancelled.append((account_number, broker_order_id))
        return True

    client.place_order = place_order  # type: ignore[method-assign]
    client.cancel_order = cancel_order  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match=r"parent_id=P-001"):
        await client.place_bracket(_request())

    assert cancelled == [("12345678", "P-001")]


@pytest.mark.asyncio
async def test_place_bracket_cancels_parent_and_stop_loss_when_take_profit_fails() -> None:
    client = FutuClient()
    client.gateway_connected = True
    client._trade_ctx = object()
    ids = iter(["P-001", "SL-002"])
    cancelled: list[tuple[str, str]] = []

    async def place_order(
        request: broker_pb2.PlaceOrderRequest,
    ) -> tuple[str, str]:
        del request
        try:
            return next(ids), "submitted"
        except StopIteration as exc:
            raise RuntimeError("take profit rejected") from exc

    async def cancel_order(account_number: str, broker_order_id: str) -> bool:
        cancelled.append((account_number, broker_order_id))
        return True

    client.place_order = place_order  # type: ignore[method-assign]
    client.cancel_order = cancel_order  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match=r"parent_id=P-001, sl_id=SL-002"):
        await client.place_bracket(_request())

    assert cancelled == [("12345678", "P-001"), ("12345678", "SL-002")]
