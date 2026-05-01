"""Phase 7a B2 — token cache + RequestTokenRefresh outbound."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from sidecar_schwab.auth import TokenCache, RequestTokenRefreshError


@pytest.mark.asyncio
async def test_fresh_token_returned_without_refresh():
    cache = TokenCache(refresh_client=AsyncMock())
    cache.set_tokens(
        access_token="A",
        refresh_token="R",
        access_issued_at=datetime.now(timezone.utc),
    )
    result = await cache.get_access_token()
    assert result == "A"
    assert issubclass(RequestTokenRefreshError, RuntimeError)
    cache._refresh_client.RequestTokenRefresh.assert_not_called()


@pytest.mark.asyncio
async def test_stale_token_triggers_refresh():
    """When access_token_age > 25 min, sidecar requests refresh from backend."""
    backend_mock = AsyncMock()
    backend_mock.RequestTokenRefresh.return_value = type(
        "Resp",
        (),
        {
            "access_token": "NEW_A",
            "refresh_token": "NEW_R",
            "access_issued_at": _ts_now(),
        },
    )()
    cache = TokenCache(refresh_client=backend_mock)
    cache.set_tokens(
        access_token="OLD_A",
        refresh_token="OLD_R",
        access_issued_at=datetime.now(timezone.utc) - timedelta(minutes=26),
    )
    result = await cache.get_access_token()
    assert result == "NEW_A"
    backend_mock.RequestTokenRefresh.assert_called_once()


@pytest.mark.asyncio
async def test_no_self_refresh_to_schwab_endpoint(monkeypatch):
    """B2 invariant: sidecar must NOT call schwab.com/oauth/token directly.

    Real behavior test: patch httpx.AsyncClient transport to record any
    outbound request; trigger a stale-token refresh; assert NO requests
    went to schwabapi.com (the request goes via BackendCallback gRPC).
    """
    import httpx

    recorded: list[str] = []

    class RecordingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            recorded.append(str(request.url))
            return httpx.Response(200, content=b"{}")

    real_client = httpx.AsyncClient

    def factory(*a, **kw):
        return real_client(*a, transport=RecordingTransport(), **kw)

    monkeypatch.setattr("httpx.AsyncClient", factory)

    backend_mock = AsyncMock()
    backend_mock.RequestTokenRefresh.return_value = type(
        "R",
        (),
        {
            "access_token": "NEW_A",
            "refresh_token": "NEW_R",
            "access_issued_at": _ts_now(),
        },
    )()
    cache = TokenCache(refresh_client=backend_mock)
    cache.set_tokens(
        access_token="OLD_A", refresh_token="OLD_R",
        access_issued_at=datetime.now(timezone.utc) - timedelta(minutes=26),
    )
    await cache.get_access_token()

    backend_mock.RequestTokenRefresh.assert_called_once()
    schwab_token_calls = [
        u for u in recorded if "schwabapi.com" in u and "/oauth/token" in u
    ]
    assert schwab_token_calls == [], (
        f"sidecar must not call Schwab token endpoint; saw: {schwab_token_calls}"
    )


@pytest.mark.asyncio
async def test_lock_released_before_outbound_grpc():
    """M6 — _token_lock is released before the actual RPC call."""
    backend_mock = AsyncMock()
    cache = TokenCache(refresh_client=backend_mock)
    cache.set_tokens(
        access_token="X",
        refresh_token="Y",
        access_issued_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    lock_status = []

    async def assert_lock_released(*args, **kwargs):
        lock_status.append(cache._token_lock.locked())
        return type(
            "R",
            (),
            {
                "access_token": "Z",
                "refresh_token": "Y2",
                "access_issued_at": _ts_now(),
            },
        )()

    backend_mock.RequestTokenRefresh = assert_lock_released

    await cache.get_access_token()
    assert lock_status == [False]


def _ts_now():
    from google.protobuf.timestamp_pb2 import Timestamp

    ts = Timestamp()
    ts.GetCurrentTime()
    return ts
