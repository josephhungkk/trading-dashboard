"""Phase 10a C3: sidecar_schwab PreviewOrder handler tests.

Spec: ``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md`` §5.
Plan: Task C3. M8 separate token bucket (60 req/min) so preview spam can never
starve placeOrder capacity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab import handlers
from sidecar_schwab._generated.broker.v1 import broker_pb2

pytestmark = pytest.mark.asyncio


def _make_request() -> broker_pb2.PreviewOrderRequest:
    return broker_pb2.PreviewOrderRequest(
        account_hash="HASH-EXAMPLE-1234",
        side="buy",
        symbol="AAPL",
        asset_class="STOCK",
        order_type="LMT",
        time_in_force="DAY",
        qty="100",
        limit_price="150.00",
        idempotency_key="preview:test1",
    )


def _make_servicer(client: AsyncMock) -> handlers.BrokerServicer:
    """Construct BrokerServicer with the bare minimum injected deps it needs
    for PreviewOrder. Other fields irrelevant for this test path."""
    h = handlers.BrokerServicer.__new__(handlers.BrokerServicer)
    h._client = client
    h._simulator = None
    h._abort_for_http = handlers.BrokerServicer._abort_for_http.__get__(h)  # type: ignore[attr-defined]
    return h


async def test_preview_order_calls_rest_endpoint() -> None:
    """Happy path: token bucket grants, REST call returns Schwab JSON,
    handler translates accepted/commission/available_funds_after into proto."""
    schwab_body = {
        "orderValidationResult": {"rejects": [], "alerts": []},
        "commissionAndFee": {"commission": {"value": "1.50"}},
        "projectedAvailableFund": "12345.67",
        "projectedBuyingPower": "23456.78",
    }
    client = AsyncMock()
    client.preview_token_bucket = AsyncMock(return_value=True)
    client.ensure_fresh_token = AsyncMock(return_value=None)
    client.preview_order = AsyncMock(return_value=schwab_body)

    h = _make_servicer(client)
    response = await h.PreviewOrder(_make_request(), context=AsyncMock())
    assert response.accepted is True
    assert response.commission == "1.50"
    assert response.available_funds_after == "12345.67"
    assert response.buying_power_after == "23456.78"
    client.preview_order.assert_awaited_once()


async def test_preview_order_blocks_on_rate_limit() -> None:
    """Token bucket exhausted -> RESOURCE_EXHAUSTED + metric increment.

    M8 invariant: rate-limited preview MUST NOT consume Schwab REST budget;
    the bucket check happens before ensure_fresh_token / preview_order.
    """
    client = AsyncMock()
    client.preview_token_bucket = AsyncMock(return_value=False)
    client.ensure_fresh_token = AsyncMock(return_value=None)
    client.preview_order = AsyncMock()

    h = _make_servicer(client)
    context = AsyncMock()
    context.abort = AsyncMock(side_effect=grpc.RpcError("aborted"))
    with pytest.raises(grpc.RpcError):
        await h.PreviewOrder(_make_request(), context=context)
    context.abort.assert_awaited_once()
    args, _ = context.abort.call_args
    assert args[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert "rate-limited" in args[1].lower()
    # M8 critical: REST endpoints NOT touched on rate-limit.
    client.ensure_fresh_token.assert_not_awaited()
    client.preview_order.assert_not_awaited()


async def test_preview_response_raw_payload_strips_account_fields() -> None:
    """B9 reviewer HIGH fix (security): allowlist response fields so
    Schwab's accountActivityRecord/accountId/accountNumber/hashValue
    don't bypass the AccountResponse boundary strip via
    raw_provider_payload.
    """
    schwab_body = {
        "orderValidationResult": {"rejects": [], "alerts": []},
        "commissionAndFee": {"commission": {"value": "1.50"}},
        "projectedAvailableFund": "12345.67",
        "projectedBuyingPower": "23456.78",
        # These MUST NOT appear in raw_provider_payload:
        "accountActivityRecord": {"accountNumber": "12345-leak", "accountId": "67890"},
        "hashValue": "EXAMPLE_HASH_LEAK",
    }
    client = AsyncMock()
    client.preview_token_bucket = AsyncMock(return_value=True)
    client.ensure_fresh_token = AsyncMock(return_value=None)
    client.preview_order = AsyncMock(return_value=schwab_body)

    h = _make_servicer(client)
    response = await h.PreviewOrder(_make_request(), context=AsyncMock())
    assert "12345-leak" not in response.raw_provider_payload
    assert "67890" not in response.raw_provider_payload
    assert "EXAMPLE_HASH_LEAK" not in response.raw_provider_payload
    assert "accountActivityRecord" not in response.raw_provider_payload
    assert "accountNumber" not in response.raw_provider_payload
    assert "hashValue" not in response.raw_provider_payload
    # But the safe fields are preserved:
    assert "12345.67" in response.raw_provider_payload  # projectedAvailableFund
    assert "23456.78" in response.raw_provider_payload  # projectedBuyingPower


async def test_preview_response_handles_non_list_alerts_safely() -> None:
    """B9 reviewer MED fix: unexpected non-list rejects/alerts shape doesn't crash."""
    schwab_body = {
        "orderValidationResult": {
            "rejects": "unexpected_string_shape",
            "alerts": "another_string",
        },
    }
    client = AsyncMock()
    client.preview_token_bucket = AsyncMock(return_value=True)
    client.ensure_fresh_token = AsyncMock(return_value=None)
    client.preview_order = AsyncMock(return_value=schwab_body)

    h = _make_servicer(client)
    # Must not raise AttributeError on rejects[0] or alerts iteration:
    response = await h.PreviewOrder(_make_request(), context=AsyncMock())
    assert response.accepted is True  # rejects coerced to []
    assert response.warnings == []  # alerts coerced to []


async def test_preview_token_bucket_sliding_window() -> None:
    """SchwabClient.preview_token_bucket: 60 capacity over 60s sliding window."""
    from sidecar_schwab.client import SchwabClient

    c = SchwabClient.__new__(SchwabClient)
    SchwabClient.__init__(c, schwabdev_client=MagicMock(), token_cache=MagicMock())
    # First 60 grants succeed.
    granted = 0
    for _ in range(60):
        if await c.preview_token_bucket():
            granted += 1
    assert granted == 60
    # 61st rejected.
    assert await c.preview_token_bucket() is False
