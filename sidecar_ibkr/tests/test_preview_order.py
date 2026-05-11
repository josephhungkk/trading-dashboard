"""Phase 10a C2: sidecar_ibkr PreviewOrder handler tests.

Spec: ``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md`` §5.
Plan: Task C2. M7 async-to-sync wait pattern (filledEvent + 2.5s timeout).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_ibkr import handlers
from sidecar_ibkr._generated.broker.v1 import broker_pb2

pytestmark = pytest.mark.asyncio


def _whatif_trade(*, init_margin: str = "5000", maint_margin: str = "2500") -> MagicMock:
    """Build a Trade-shaped mock with filledEvent that resolves immediately
    plus orderStatus carrying the margin/commission strings the handler
    serializes back into PreviewOrderResponse."""
    trade = MagicMock()
    trade.order = MagicMock(orderId=12345, permId=67890)
    trade.orderStatus.status = "PreSubmitted"
    trade.orderStatus.initMarginAfter = init_margin
    trade.orderStatus.maintMarginAfter = maint_margin
    trade.orderStatus.commission = "1.50"
    trade.orderStatus.equityWithLoanAfter = "100000"
    trade.orderStatus.warningText = ""
    trade.filledEvent.wait = AsyncMock(return_value=None)
    return trade


@pytest.fixture
def mock_ib() -> MagicMock:
    ib = MagicMock()
    ib.placeOrder.return_value = _whatif_trade()
    return ib


def _make_request(*, idempotency_key: str = "preview:test1") -> broker_pb2.PreviewOrderRequest:
    return broker_pb2.PreviewOrderRequest(
        account_hash="DU111-hash",
        side="buy",
        symbol="AAPL",
        asset_class="STOCK",
        order_type="LMT",
        time_in_force="DAY",
        qty="100",
        limit_price="150.00",
        idempotency_key=idempotency_key,
    )


def _make_handler(ib: MagicMock) -> handlers.BrokerHandlers:
    return handlers.BrokerHandlers(
        ib=ib,
        pnl_cache=MagicMock(),
        label="TWS_SLOT_TEST",
        version="0.1.0-test",
        last_tick_ref={},
        simulator_only=False,
        started_at=datetime.now(UTC),
    )


async def test_preview_order_returns_decimals_as_strings(mock_ib: MagicMock) -> None:
    """Happy path: WhatIf resolves, response carries Decimal-string margin fields."""
    h = _make_handler(mock_ib)
    h._resolve_contract = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    response = await h.PreviewOrder(_make_request(), context=MagicMock())
    assert response.accepted is True
    assert response.initial_margin == "5000"
    assert response.maintenance_margin == "2500"
    assert response.commission == "1.50"
    assert response.available_funds_after == "100000"
    assert response.buying_power_after == "100000"
    mock_ib.placeOrder.assert_called_once()
    _, what_if_order = mock_ib.placeOrder.call_args.args
    assert what_if_order.whatIf is True
    assert what_if_order.account == "DU111-hash"


async def test_preview_order_idempotency_lru_dedups(mock_ib: MagicMock) -> None:
    """Same idempotency_key twice -> placeOrder called once (cache hit on 2nd)."""
    h = _make_handler(mock_ib)
    h._resolve_contract = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    req = _make_request(idempotency_key="preview:dedup-key")
    r1 = await h.PreviewOrder(req, context=MagicMock())
    r2 = await h.PreviewOrder(req, context=MagicMock())
    assert r1.initial_margin == r2.initial_margin
    assert mock_ib.placeOrder.call_count == 1
    assert h._resolve_contract.await_count == 1


async def test_preview_order_tws_rejected_status_maps_to_accepted_false(
    mock_ib: MagicMock,
) -> None:
    """B9 reviewer MED fix: orderStatus.status='Rejected' -> accepted=False.

    Backend gate then BLOCKs with margin_rejected_by_broker (spec §4 H4 row 3).
    Without this mapping, a TWS-level reject would silently pass as accepted=True.
    """
    rejected_trade = _whatif_trade()
    rejected_trade.orderStatus.status = "Rejected"
    rejected_trade.orderStatus.warningText = "insufficient margin"
    mock_ib.placeOrder.return_value = rejected_trade

    h = _make_handler(mock_ib)
    h._resolve_contract = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    response = await h.PreviewOrder(_make_request(idempotency_key="r1"), context=MagicMock())
    assert response.accepted is False
    assert "insufficient margin" in response.reject_reason


async def test_preview_order_filled_event_timeout_returns_deadline_exceeded(
    mock_ib: MagicMock,
) -> None:
    """filledEvent.wait raises TimeoutError -> context.abort(DEADLINE_EXCEEDED)."""
    slow_trade = MagicMock()
    slow_trade.filledEvent.wait = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_ib.placeOrder.return_value = slow_trade

    h = _make_handler(mock_ib)
    h._resolve_contract = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    context = AsyncMock()
    context.abort = AsyncMock(side_effect=grpc.RpcError("aborted"))
    with pytest.raises(grpc.RpcError):
        await h.PreviewOrder(_make_request(), context=context)
    context.abort.assert_awaited_once()
    args, _ = context.abort.call_args
    assert args[0] == grpc.StatusCode.DEADLINE_EXCEEDED
    assert "timeout" in args[1].lower()
