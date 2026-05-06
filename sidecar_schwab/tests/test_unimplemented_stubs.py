"""Phase 7a B10 - write + streaming RPCs return UNIMPLEMENTED (Phase 8/7b deferrals)."""

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,request_proto",
    [
        ("GetContract", pb.ContractRef()),
        # PlaceOrder flipped live in Phase 8a C3 — see test_handlers_place_order.py
        # CancelOrder flipped live in Phase 8a C4 — see test_handlers_cancel_modify.py
        # ModifyOrder flipped live in Phase 8a C4 — see test_handlers_cancel_modify.py
        ("PlaceBracket", pb.PlaceBracketRequest()),
        ("SearchContracts", pb.SearchContractsRequest()),
        ("OrderEvent", pb.AccountRef()),
    ],
)
async def test_unimplemented_returns_unimplemented(method, request_proto):
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    ctx.abort = AsyncMock(side_effect=grpc.RpcError("aborted"))
    fn = getattr(servicer, method)
    try:
        await fn(request_proto, ctx)
    except grpc.RpcError:
        pass
    ctx.abort.assert_called_once()
    code = ctx.abort.call_args.args[0]
    assert code == grpc.StatusCode.UNIMPLEMENTED
