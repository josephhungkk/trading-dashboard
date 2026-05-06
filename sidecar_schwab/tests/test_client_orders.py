from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.client import (
    SchwabClient,
    SchwabHTTPError,
    _extract_broker_order_id,
)


def _make_client():
    sd = MagicMock()
    sd.place_order = AsyncMock()
    sd.cancel_order = AsyncMock()
    sd.replace_order = AsyncMock()
    sd.account_orders = AsyncMock()
    sd.order_details = AsyncMock()
    sd.instruments = AsyncMock()
    sd.tokens = MagicMock(access_token="a", refresh_token="r")
    sd._session = MagicMock(headers={})
    tokens = MagicMock()
    tokens.get_access_token = AsyncMock(return_value="access")
    tokens._refresh_token = "refresh"
    return SchwabClient(schwabdev_client=sd, token_cache=tokens), sd


@pytest.mark.asyncio
async def test_place_order_returns_broker_order_id():
    client, sd = _make_client()
    resp = MagicMock()
    resp.status_code = 201
    resp.headers = {
        "Location": "https://api.schwab.com/trader/v1/accounts/HASH/orders/12345"
    }
    sd.place_order.return_value = resp

    result = await client.place_order(account_hash="HASH", payload={"x": "y"})

    assert result == {"broker_order_id": "12345"}


@pytest.mark.asyncio
async def test_place_order_raises_schwab_http_error_on_4xx():
    client, sd = _make_client()
    resp = MagicMock()
    resp.status_code = 400
    resp.headers = {}
    sd.place_order.return_value = resp

    with pytest.raises(SchwabHTTPError) as excinfo:
        await client.place_order(account_hash="HASH", payload={"x": "y"})

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_get_orders_since_returns_list():
    client, sd = _make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{"orderId": 1}, {"orderId": 2}]
    sd.account_orders.return_value = resp

    result = await client.get_orders_since("HASH", "2026-05-06T00:00:00Z")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_search_instruments_returns_list():
    client, sd = _make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"instruments": [{"symbol": "AAPL"}]}
    sd.instruments.return_value = resp

    result = await client.search_instruments("AAPL")

    assert result == [{"symbol": "AAPL"}]


@pytest.mark.asyncio
async def test_search_instruments_empty_response_returns_empty_list():
    client, sd = _make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"instruments": None}
    sd.instruments.return_value = resp

    result = await client.search_instruments("AAPL")

    assert result == []


@pytest.mark.asyncio
async def test_replace_order_returns_new_broker_order_id():
    client, sd = _make_client()
    resp = MagicMock()
    resp.status_code = 201
    resp.headers = {
        "Location": "https://api.schwab.com/trader/v1/accounts/HASH/orders/99999"
    }
    sd.replace_order.return_value = resp

    result = await client.replace_order(
        account_hash="HASH",
        order_id="12345",
        payload={"x": "y"},
    )

    assert result == {"broker_order_id": "99999"}


def test_extract_broker_order_id_raises_on_missing_location():
    with pytest.raises(ValueError):
        _extract_broker_order_id({})


@pytest.mark.asyncio
async def test_ensure_fresh_token_syncs_schwabdev_authorization_header():
    client, sd = _make_client()
    client._tokens.get_access_token = AsyncMock(return_value="abc")

    await client.ensure_fresh_token()

    assert sd._session.headers["Authorization"] == "Bearer abc"
