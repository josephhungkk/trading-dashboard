"""Phase 7a B4 — Configure with metadata map (v3); H4 access-token age check."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


def _build_request(*, access_token="A", refresh_token="R", issued_at=None,
                    app_key="K", app_secret="S") -> broker_pb2.ConfigureRequest:
    """Build a ConfigureRequest using the metadata map (Schwab pathway)."""
    if issued_at is None:
        issued_at = datetime.now(timezone.utc)
    return broker_pb2.ConfigureRequest(
        metadata={
            "app_key":          app_key,
            "app_secret":       app_secret,
            "access_token":     access_token,
            "refresh_token":    refresh_token,
            "access_issued_at": issued_at.isoformat(),
        },
    )


def _ctx_with_async_abort():
    """grpc.aio.ServicerContext.abort is an awaitable; build an AsyncMock for it."""
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    ctx.abort = AsyncMock(side_effect=grpc.RpcError("aborted"))
    return ctx


@pytest.mark.asyncio
async def test_configure_first_time_succeeds():
    servicer = BrokerServicer()
    ctx = _ctx_with_async_abort()
    resp = await servicer.Configure(_build_request(), ctx)
    assert resp.ok is True
    assert resp.detail == ""


@pytest.mark.asyncio
async def test_configure_idempotent_same_tokens():
    servicer = BrokerServicer()
    ctx = _ctx_with_async_abort()
    issued = datetime.now(timezone.utc)
    req = _build_request(issued_at=issued)
    resp1 = await servicer.Configure(req, ctx)
    resp2 = await servicer.Configure(req, ctx)
    assert resp1.ok and resp2.ok
    assert servicer._configure_count == 1


@pytest.mark.asyncio
async def test_configure_rebuilds_on_token_change():
    servicer = BrokerServicer()
    ctx = _ctx_with_async_abort()
    issued = datetime.now(timezone.utc)
    await servicer.Configure(_build_request(access_token="A1", refresh_token="R1", issued_at=issued), ctx)
    await servicer.Configure(_build_request(access_token="A2", refresh_token="R2", issued_at=issued), ctx)
    assert servicer._configure_count == 2


@pytest.mark.asyncio
async def test_configure_h4_discards_stale_access_token():
    """H4 — when access_issued_at is >25min old, sidecar does NOT use the
    supplied access_token; instead it stays unset, forcing the next outbound
    call to trigger RequestTokenRefresh via TokenCache."""
    servicer = BrokerServicer()
    ctx = _ctx_with_async_abort()
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    resp = await servicer.Configure(
        _build_request(access_token="STALE_A", issued_at=stale), ctx)
    assert resp.ok is True
    assert servicer._token_cache._access_token == ""
    assert servicer._token_cache._refresh_token == "R"


@pytest.mark.asyncio
async def test_configure_rejects_request_without_metadata():
    """Schwab Configure requires metadata map populated; otherwise reject."""
    servicer = BrokerServicer()
    ctx = _ctx_with_async_abort()
    bare = broker_pb2.ConfigureRequest()
    try:
        await servicer.Configure(bare, ctx)
    except grpc.RpcError:
        pass
    ctx.abort.assert_called_once()
    args = ctx.abort.call_args.args
    assert args[0] == grpc.StatusCode.INVALID_ARGUMENT
