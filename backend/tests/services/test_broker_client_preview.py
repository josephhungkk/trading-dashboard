"""Phase 10a C5 (M6): BrokerSidecarClient.preview_order content-hash idempotency.

Verifies:
- Decimal-string fields serialize verbatim into the proto request.
- idempotency_key is a deterministic blake2b content-hash of the canonical
  payload (NOT random/UUID per M6) - same inputs always yield the same key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_client() -> object:
    """Construct a BrokerSidecarClient stub-injected, bypassing __init__'s mTLS."""
    from app.services.brokers import BrokerSidecarClient

    c = BrokerSidecarClient.__new__(BrokerSidecarClient)
    c.label = "test"
    c.target = "test:0"
    c.deadline_seconds = 5.0
    c.channel = MagicMock()
    c.stub = MagicMock()
    c._call = AsyncMock()  # type: ignore[attr-defined]
    return c


async def test_preview_order_decimal_fields_pass_through_as_strings() -> None:
    """qty/limit_price/stop_price serialize verbatim (no float coercion)."""
    from app._generated.broker.v1 import broker_pb2

    c = _make_client()
    c._call = AsyncMock(return_value=broker_pb2.PreviewOrderResponse(accepted=True))  # type: ignore[attr-defined]
    await c.preview_order(  # type: ignore[attr-defined]
        account_id="acct-uuid-1",
        side="buy",
        symbol="AAPL",
        asset_class="STOCK",
        order_type="LMT",
        time_in_force="DAY",
        qty="100.123456",
        limit_price="150.87654321",
        stop_price=None,
    )
    request = c._call.await_args.kwargs["request"]  # type: ignore[attr-defined]
    assert request.qty == "100.123456"
    assert request.limit_price == "150.87654321"
    assert request.HasField("stop_price") is False


async def test_preview_order_idempotency_key_is_content_hash_not_random() -> None:
    """Same inputs -> same idempotency_key (blake2b content-hash, not UUID)."""
    from app._generated.broker.v1 import broker_pb2

    c = _make_client()
    c._call = AsyncMock(return_value=broker_pb2.PreviewOrderResponse(accepted=True))  # type: ignore[attr-defined]
    kwargs = {
        "account_id": "acct-uuid-1",
        "side": "buy",
        "symbol": "AAPL",
        "asset_class": "STOCK",
        "order_type": "LMT",
        "time_in_force": "DAY",
        "qty": "100",
        "limit_price": "150",
    }
    await c.preview_order(**kwargs)  # type: ignore[arg-type]
    key1 = c._call.await_args.kwargs["request"].idempotency_key  # type: ignore[attr-defined]
    await c.preview_order(**kwargs)  # type: ignore[arg-type]
    key2 = c._call.await_args.kwargs["request"].idempotency_key  # type: ignore[attr-defined]
    assert key1 == key2
    assert key1.startswith("preview:")
    assert len(key1) == len("preview:") + 32


async def test_preview_order_different_inputs_yield_different_keys() -> None:
    """Differing qty produces different content-hash."""
    from app._generated.broker.v1 import broker_pb2

    c = _make_client()
    c._call = AsyncMock(return_value=broker_pb2.PreviewOrderResponse(accepted=True))  # type: ignore[attr-defined]
    await c.preview_order(  # type: ignore[attr-defined]
        account_id="a",
        side="buy",
        symbol="AAPL",
        asset_class="STOCK",
        order_type="LMT",
        time_in_force="DAY",
        qty="100",
        limit_price="150",
    )
    key1 = c._call.await_args.kwargs["request"].idempotency_key  # type: ignore[attr-defined]
    await c.preview_order(  # type: ignore[attr-defined]
        account_id="a",
        side="buy",
        symbol="AAPL",
        asset_class="STOCK",
        order_type="LMT",
        time_in_force="DAY",
        qty="200",
        limit_price="150",
    )
    key2 = c._call.await_args.kwargs["request"].idempotency_key  # type: ignore[attr-defined]
    assert key1 != key2
