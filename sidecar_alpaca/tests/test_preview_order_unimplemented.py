"""Phase 10a C4: sidecar_alpaca PreviewOrder UNIMPLEMENTED stub.

alpaca-py has no pre-trade margin preview. Spec §5: gate's _check_margin
catches this UNIMPLEMENTED and falls back to cached BP per the H4 WARN
fail policy (spec §4 row 4).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2  # noqa: E402

pytestmark = [pytest.mark.asyncio]


async def test_preview_order_returns_unimplemented() -> None:
    """PreviewOrder always aborts UNIMPLEMENTED — gate handles the fallback."""
    h = AlpacaServicer.__new__(AlpacaServicer)
    request = broker_pb2.PreviewOrderRequest(
        account_hash="any",
        side="buy",
        symbol="AAPL",
        asset_class="STOCK",
        order_type="MKT",
        time_in_force="DAY",
        qty="10",
        idempotency_key="preview:test",
    )
    context = AsyncMock()
    context.abort = AsyncMock(side_effect=grpc.RpcError("aborted"))
    with pytest.raises(grpc.RpcError):
        await h.PreviewOrder(request, context=context)
    args, _ = context.abort.call_args
    assert args[0] == grpc.StatusCode.UNIMPLEMENTED
    assert "alpaca-py" in args[1] or "preview" in args[1].lower()
