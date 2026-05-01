"""Phase 7a E4 — config_writer POSTs auth code; retries on 5xx."""
import pytest

from sidecar_schwab_refresher.config_writer import post_oauth_callback


CF_HEADERS = {
    "CF-Access-Client-Id": "abc.access",
    "CF-Access-Client-Secret": "shhh",
}


@pytest.mark.asyncio
async def test_post_oauth_callback_success(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://backend:8000/api/admin/brokers/schwab/oauth-callback?code=C&state=S&actor=tier2",
        json={"access_token_issued_at": "2026-04-30T12:00:00+00:00"},
    )
    result = await post_oauth_callback(
        backend_url="http://backend:8000",
        code="C",
        state="S",
        cf_headers=CF_HEADERS,
    )
    assert "access_token_issued_at" in result


@pytest.mark.asyncio
async def test_retry_on_5xx(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://backend:8000/api/admin/brokers/schwab/oauth-callback?code=C&state=S&actor=tier2",
        status_code=502,
        json={},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://backend:8000/api/admin/brokers/schwab/oauth-callback?code=C&state=S&actor=tier2",
        status_code=200,
        json={"ok": True},
    )
    import asyncio
    real_sleep = asyncio.sleep
    asyncio.sleep = lambda *_a, **_kw: real_sleep(0)  # type: ignore
    try:
        result = await post_oauth_callback(
            backend_url="http://backend:8000",
            code="C",
            state="S",
            cf_headers=CF_HEADERS,
            max_retries=2,
        )
        assert result["ok"] is True
    finally:
        asyncio.sleep = real_sleep  # type: ignore
