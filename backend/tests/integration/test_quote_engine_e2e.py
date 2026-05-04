"""QuoteEngine end-to-end + invariants INV-Q-1..4 (Phase 7b.1 B5)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import grpc  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from app._generated.broker.v1 import broker_pb2 as pb
from app._generated.broker.v1 import broker_pb2_grpc as pb_grpc
from app.core.metrics import (
    QUOTE_ENGINE_TICKS_TOTAL,
    QUOTE_ROUTE_CHANGES_TOTAL,
)
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceRouter
from app.services.quotes.upstream.sidecar_stream import SidecarStream


def _q(canonical: str, source: str = "schwab", payload: bytes = b"") -> pb.QuoteMessage:
    received = Timestamp()
    received.GetCurrentTime()
    return pb.QuoteMessage(
        canonical_id=canonical,
        source=source,
        last="100.50",
        bid="100.49",
        ask="100.51",
        received_at=received,
        raw_payload=payload,
    )


@pytest.fixture
def router() -> SourceRouter:
    return SourceRouter(
        config={
            "quote_source_priority": {
                "stock.US": ["schwab", "ibkr", "yfinance"],
            }
        },
        health=SourceHealthMap(),
    )


@pytest.fixture
def registry() -> SubscriptionRegistry:
    return SubscriptionRegistry(cap_per_ws=100, cap_global=1000, sub_rate_limit_per_minute=1000)


# ── INV-Q-1: single-worker has no subscriber task ─────────────────────────


@pytest.mark.asyncio
async def test_inv_q_1_single_worker_has_no_subscriber_task(
    redis: Any, registry: SubscriptionRegistry, router: SourceRouter
) -> None:
    engine = QuoteEngine(registry=registry, router=router, redis=redis)
    assert engine.is_subscriber_task_running() is False
    assert engine._subscriber_task is None  # type: ignore[attr-defined]


# ── INV-Q-2: raw_payload stripped at engine boundary ──────────────────────


@pytest.mark.asyncio
async def test_inv_q_2_raw_payload_stripped_by_default(
    redis: Any, registry: SubscriptionRegistry, router: SourceRouter
) -> None:
    """Default: OPERATOR_TRACE_QUOTES unset -> raw_payload zeroed."""
    os.environ.pop("OPERATOR_TRACE_QUOTES", None)
    engine = QuoteEngine(registry=registry, router=router, redis=redis)

    sentinel = b"INTERNAL_DEBUG_DO_NOT_LEAK"
    quote = _q("stock:AAPL:US", payload=sentinel)
    await engine._on_quote(quote)  # type: ignore[attr-defined]

    cached = engine.get_cached("stock:AAPL:US")
    assert cached is not None
    assert cached.raw_payload == b""


@pytest.mark.asyncio
async def test_inv_q_2_raw_payload_preserved_with_operator_trace(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator opt-in: OPERATOR_TRACE_QUOTES=1 -> raw_payload survives."""
    monkeypatch.setenv("OPERATOR_TRACE_QUOTES", "1")
    engine = QuoteEngine(registry=registry, router=router, redis=redis)

    sentinel = b"OPERATOR_DEBUG_BYTES"
    quote = _q("stock:AAPL:US", payload=sentinel)
    await engine._on_quote(quote)  # type: ignore[attr-defined]

    cached = engine.get_cached("stock:AAPL:US")
    assert cached is not None
    assert cached.raw_payload == sentinel


@pytest.mark.asyncio
async def test_inv_q_2_redis_publish_envelope_has_empty_raw_payload(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    """Redis bus envelope must NOT carry raw_payload bytes by default."""
    os.environ.pop("OPERATOR_TRACE_QUOTES", None)
    engine = QuoteEngine(registry=registry, router=router, redis=redis)

    pubsub = redis.pubsub()
    await pubsub.subscribe("quote.schwab.stock:AAPL:US")

    await engine._on_quote(_q("stock:AAPL:US", payload=b"LEAK"))  # type: ignore[attr-defined]

    message = None
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message:
            break
    await pubsub.unsubscribe("quote.schwab.stock:AAPL:US")
    await pubsub.aclose()

    assert message is not None
    payload = json.loads(message["data"])
    assert payload["v"] == 1
    assert "publisher_worker_id" in payload
    # raw_payload absent or empty (proto JSON omits empty bytes by default).
    assert not payload["q"].get("raw_payload")


# ── INV-Q-3: per-symbol staleness does NOT trigger reroute ────────────────


@pytest.mark.asyncio
async def test_inv_q_3_engine_does_not_call_reroute_on_quote_path(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    """The _on_quote path must not invoke router.reroute under any path —
    staleness emission lives in F2's WSConflator, not here. Verify by
    spying on the router."""
    router_spy = MagicMock(wraps=router)
    router_spy.reroute = AsyncMock(side_effect=AssertionError("reroute called"))
    engine = QuoteEngine(registry=registry, router=router_spy, redis=redis)

    await engine._on_quote(_q("stock:AAPL:US"))  # type: ignore[attr-defined]
    await engine._on_quote(_q("stock:AAPL:US"))  # type: ignore[attr-defined]

    router_spy.reroute.assert_not_called()


@pytest.mark.asyncio
async def test_inv_q_3_route_changes_metric_unchanged_by_tick_path(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    engine = QuoteEngine(registry=registry, router=router, redis=redis)

    before = QUOTE_ROUTE_CHANGES_TOTAL.labels(
        from_source="schwab", to_source="ibkr", asset_class="stock"
    )._value.get()

    for _ in range(10):
        await engine._on_quote(_q("stock:AAPL:US"))  # type: ignore[attr-defined]

    after = QUOTE_ROUTE_CHANGES_TOTAL.labels(
        from_source="schwab", to_source="ibkr", asset_class="stock"
    )._value.get()
    assert before == after


# ── INV-Q-4: token-rotation triggers reconnect within 2 s ─────────────────


class _FakeServicer(pb_grpc.BrokerServicer):  # type: ignore[misc]
    def __init__(self) -> None:
        self.first_frame_seen = asyncio.Event()
        self.frame_count = 0

    async def Health(  # noqa: N802
        self, request: pb.HealthRequest, context: Any
    ) -> pb.HealthResponse:
        ts = Timestamp(seconds=1234567890)
        return pb.HealthResponse(
            label="test",
            gateway_connected=True,
            gateway_version="0",
            sidecar_version="test",
            started_at=ts,
            broker_id="schwab",
        )

    async def StreamQuotes(  # noqa: N802
        self, request_iterator: AsyncIterator[pb.StreamQuotesRequest], context: Any
    ) -> AsyncIterator[pb.QuoteMessage]:
        async for _req in request_iterator:
            self.frame_count += 1
            self.first_frame_seen.set()
        if False:  # generator marker
            yield pb.QuoteMessage()


@pytest_asyncio.fixture
async def fake_grpc_server() -> AsyncIterator[tuple[int, _FakeServicer]]:
    server = grpc.aio.server()
    servicer = _FakeServicer()
    pb_grpc.add_BrokerServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield (port, servicer)
    finally:
        await server.stop(grace=0.1)


@pytest.mark.asyncio
async def test_inv_q_4_token_rotation_reconnect_under_2s(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
    fake_grpc_server: tuple[int, _FakeServicer],
) -> None:
    port, servicer = fake_grpc_server
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")

    def _builder(canonical: str) -> pb.SymbolRef:
        return pb.SymbolRef(canonical_id=canonical, raw_symbol=canonical)

    health = SourceHealthMap()

    async def _on_quote_noop(_q: pb.QuoteMessage) -> None:
        return None

    stream = SidecarStream(
        source="schwab",
        channel=channel,
        registry=registry,
        on_quote=_on_quote_noop,
        health=health,
        symbol_ref_builder=_builder,
    )

    engine = QuoteEngine(
        registry=registry,
        router=router,
        redis=redis,
        streams={"schwab": stream},
    )

    await engine.start()
    try:
        await asyncio.wait_for(servicer.first_frame_seen.wait(), timeout=2.0)
        first_round_count = servicer.frame_count
        servicer.first_frame_seen.clear()

        t0 = time.monotonic()
        engine.request_token_rotation("schwab")

        while True:
            if servicer.frame_count > first_round_count:
                break
            if time.monotonic() - t0 > 2.0:
                pytest.fail("reconnect did not happen within 2 s")
            await asyncio.sleep(0.02)

        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"reconnect took {elapsed:.3f}s (budget 2.0s)"
    finally:
        await engine.stop()
        await channel.close()


# ── subscribe / unsubscribe / disconnect_ws happy-path wiring ────────────


@pytest.mark.asyncio
async def test_subscribe_dispatches_to_stream_when_route_set(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    schwab_stream = MagicMock()
    schwab_stream.add = AsyncMock()
    schwab_stream.remove = AsyncMock()

    engine = QuoteEngine(
        registry=registry,
        router=router,
        redis=redis,
        streams={"schwab": schwab_stream},
    )

    ws = uuid4()
    # Pre-set route so subscribe can dispatch to the right stream.
    registry.set_route("stock:AAPL:US", "schwab")
    diff = await engine.subscribe(ws, ["stock:AAPL:US"])

    assert "stock:AAPL:US" in diff.added
    schwab_stream.add.assert_awaited()


@pytest.mark.asyncio
async def test_unsubscribe_propagates_to_stream(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    schwab_stream = MagicMock()
    schwab_stream.add = AsyncMock()
    schwab_stream.remove = AsyncMock()

    engine = QuoteEngine(
        registry=registry,
        router=router,
        redis=redis,
        streams={"schwab": schwab_stream},
    )

    ws = uuid4()
    registry.set_route("stock:AAPL:US", "schwab")
    await engine.subscribe(ws, ["stock:AAPL:US"])

    diff = await engine.unsubscribe(ws, ["stock:AAPL:US"])
    assert "stock:AAPL:US" in diff.removed
    schwab_stream.remove.assert_awaited()


@pytest.mark.asyncio
async def test_disconnect_ws_clears_conflator_and_cascades(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    schwab_stream = MagicMock()
    schwab_stream.add = AsyncMock()
    schwab_stream.remove = AsyncMock()

    engine = QuoteEngine(
        registry=registry,
        router=router,
        redis=redis,
        streams={"schwab": schwab_stream},
    )

    ws = uuid4()
    cb = AsyncMock()
    engine.register_conflator(ws, cb)
    registry.set_route("stock:AAPL:US", "schwab")
    await engine.subscribe(ws, ["stock:AAPL:US"])

    diff = await engine.disconnect_ws(ws)
    assert "stock:AAPL:US" in diff.removed
    schwab_stream.remove.assert_awaited()
    assert ws not in engine._conflators  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_on_quote_notifies_only_subscribed_conflators(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    engine = QuoteEngine(registry=registry, router=router, redis=redis)

    ws_a, ws_b = uuid4(), uuid4()
    cb_a = AsyncMock()
    cb_b = AsyncMock()
    engine.register_conflator(ws_a, cb_a)
    engine.register_conflator(ws_b, cb_b)

    registry.set_route("stock:AAPL:US", "schwab")
    registry.set_route("stock:MSFT:US", "schwab")
    await engine.subscribe(ws_a, ["stock:AAPL:US"])
    await engine.subscribe(ws_b, ["stock:MSFT:US"])

    await engine._on_quote(_q("stock:AAPL:US"))  # type: ignore[attr-defined]

    cb_a.assert_awaited_once()
    cb_b.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_ticks_total_metric_increments(
    redis: Any,
    registry: SubscriptionRegistry,
    router: SourceRouter,
) -> None:
    engine = QuoteEngine(registry=registry, router=router, redis=redis)

    before = QUOTE_ENGINE_TICKS_TOTAL.labels(source="schwab")._value.get()
    await engine._on_quote(_q("stock:AAPL:US"))  # type: ignore[attr-defined]
    await engine._on_quote(_q("stock:AAPL:US"))  # type: ignore[attr-defined]
    after = QUOTE_ENGINE_TICKS_TOTAL.labels(source="schwab")._value.get()
    assert after - before == 2
