"""Phase 7a B3 -- M6: 429 -> Retry-After honored + 3x retry with jitter."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.client import SchwabClient


@pytest.mark.asyncio
async def test_429_retry_with_retry_after(monkeypatch):
    """First call returns 429 with Retry-After: 1; second call succeeds."""
    sleep_calls: list[float] = []

    async def fake_sleep(s):
        sleep_calls.append(s)

    monkeypatch.setattr("sidecar_schwab.client.asyncio.sleep", fake_sleep)

    schwabdev_client = AsyncMock()
    schwabdev_client.account_details.side_effect = [
        _make_429("1"),
        _make_200({"securitiesAccount": {"accountNumber": "X"}}),
    ]
    token_cache_mock = AsyncMock()
    token_cache_mock.get_access_token = AsyncMock(return_value="A")
    token_cache_mock._refresh_token = "R"
    client = SchwabClient(
        schwabdev_client=schwabdev_client,
        token_cache=token_cache_mock,
    )
    result = await client.get_account_details("HASH")
    assert result["securitiesAccount"]["accountNumber"] == "X"
    assert sleep_calls == [pytest.approx(1.0, abs=0.2)]


@pytest.mark.asyncio
async def test_429_three_retries_then_raise(monkeypatch):
    """After 3 retries, raise SchwabRateLimitedError."""
    monkeypatch.setattr("sidecar_schwab.client.asyncio.sleep", AsyncMock())
    schwabdev_client = AsyncMock()
    schwabdev_client.account_details.return_value = _make_429("1")
    token_cache_mock = AsyncMock()
    token_cache_mock.get_access_token = AsyncMock(return_value="A")
    token_cache_mock._refresh_token = "R"
    client = SchwabClient(
        schwabdev_client=schwabdev_client,
        token_cache=token_cache_mock,
    )
    from sidecar_schwab.client import SchwabRateLimitedError

    with pytest.raises(SchwabRateLimitedError):
        await client.get_account_details("HASH")


def _make_429(retry_after: str):
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {"Retry-After": retry_after}
    return resp


def _make_200(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp
