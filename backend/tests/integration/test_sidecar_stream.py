"""SidecarStream — Subscribe vs Resync on reconnect (HIGH-1).

Spins up an in-process gRPC server with a fake ``StreamQuotes`` handler,
points a :class:`SidecarStream` at it, and asserts the first-frame kind
toggles correctly between ``subscribe`` (cold start / sidecar restart) and
``resync`` (warm reconnect against an unchanged sidecar process).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import grpc  # type: ignore[import-untyped]
import pytest
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from app._generated.broker.v1 import broker_pb2 as pb
from app._generated.broker.v1 import broker_pb2_grpc as pb_grpc
from app.core.metrics import (
    QUOTE_SIDECAR_FIRST_FRAME_TOTAL,
    QUOTE_SIDECAR_RECONNECT_TOTAL,
)
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap
from app.services.quotes.upstream.sidecar_stream import SidecarStream


def _symbol_ref_builder(canonical_id: str) -> pb.SymbolRef:
    """Minimal SymbolRef stub for the test — only canonical_id matters."""
    return pb.SymbolRef(canonical_id=canonical_id, raw_symbol=canonical_id)


class FakeBrokerServicer(pb_grpc.BrokerServicer):  # type: ignore[misc]
    """Captures inbound StreamQuotesRequest frames and exposes them for
    test assertions; emits no QuoteMessage frames by default."""

    def __init__(self, started_at_seconds: int) -> None:
        self.received: list[tuple[str, list[str]]] = []
        self.started_at_seconds = started_at_seconds
        self._stop = asyncio.Event()
        self._first_frame_seen = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def Health(  # noqa: N802 — gRPC servicer method names are PascalCase
        self, request: pb.HealthRequest, context
    ) -> pb.HealthResponse:
        ts = Timestamp(seconds=self.started_at_seconds)
        return pb.HealthResponse(
            label="test",
            gateway_connected=True,
            gateway_version="0",
            sidecar_version="test",
            started_at=ts,
            broker_id="ibkr",
        )

    async def StreamQuotes(  # noqa: N802 — gRPC servicer method names are PascalCase
        self, request_iterator: AsyncIterator[pb.StreamQuotesRequest], context
    ) -> AsyncIterator[pb.QuoteMessage]:
        async for req in request_iterator:
            kind = req.WhichOneof("op")
            if kind == "subscribe":
                self.received.append(("subscribe", [s.canonical_id for s in req.subscribe.symbols]))
                self._first_frame_seen.set()
            elif kind == "resync":
                self.received.append(("resync", [s.canonical_id for s in req.resync.expected]))
                self._first_frame_seen.set()
            elif kind == "unsubscribe":
                self.received.append(
                    (
                        "unsubscribe",
                        [s.canonical_id for s in req.unsubscribe.symbols],
                    )
                )
            elif kind == "heartbeat":
                self.received.append(("heartbeat", []))
            if self._stop.is_set():
                break
        # Generator with no yields — server-side stream returns empty.
        if False:
            yield pb.QuoteMessage()


@asynccontextmanager
async def fake_server(
    started_at_seconds: int = 1234567890,
) -> AsyncIterator[tuple[grpc.aio.Server, int, FakeBrokerServicer]]:
    server = grpc.aio.server()
    fake = FakeBrokerServicer(started_at_seconds=started_at_seconds)
    pb_grpc.add_BrokerServicer_to_server(fake, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield (server, port, fake)
    finally:
        fake.stop()
        await server.stop(grace=0.1)


async def _on_quote_noop(_: pb.QuoteMessage) -> None:
    return None


async def _await_first_frame(fake: FakeBrokerServicer, timeout_seconds: float = 2.0) -> None:
    await asyncio.wait_for(fake._first_frame_seen.wait(), timeout=timeout_seconds)


async def _await_kind_after(fake: FakeBrokerServicer, kind: str, baseline: int) -> None:
    while True:
        for k, _ in fake.received[baseline:]:
            if k == kind:
                return
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_initial_connection_sends_subscribe() -> None:
    """Cold start (last_known_started_at is None) → first frame is subscribe."""
    async with fake_server() as (_, port, fake):
        registry = SubscriptionRegistry(cap_per_ws=10, cap_global=10, sub_rate_limit_per_minute=100)
        await registry.add(uuid4(), ["stock:AAPL:US"])
        registry.set_route("stock:AAPL:US", "schwab")

        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stream = SidecarStream(
            source="schwab",
            channel=channel,
            registry=registry,
            on_quote=_on_quote_noop,
            health=SourceHealthMap(),
            symbol_ref_builder=_symbol_ref_builder,
        )

        before = QUOTE_SIDECAR_FIRST_FRAME_TOTAL.labels(
            source="schwab", kind="subscribe"
        )._value.get()

        run_task = asyncio.create_task(stream.run())
        try:
            await _await_first_frame(fake)
        finally:
            stream.stop()
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            await channel.close()

        assert fake.received
        kind, syms = fake.received[0]
        assert kind == "subscribe"
        assert "stock:AAPL:US" in syms

        after = QUOTE_SIDECAR_FIRST_FRAME_TOTAL.labels(
            source="schwab", kind="subscribe"
        )._value.get()
        assert after - before == 1


@pytest.mark.asyncio
async def test_reconnect_against_unchanged_sidecar_sends_resync() -> None:
    """Warm reconnect (HealthResponse.started_at unchanged) → first frame
    is resync, not duplicate subscribe (HIGH-1)."""
    async with fake_server(started_at_seconds=42) as (_, port, fake):
        registry = SubscriptionRegistry(cap_per_ws=10, cap_global=10, sub_rate_limit_per_minute=100)
        await registry.add(uuid4(), ["stock:AAPL:US"])
        registry.set_route("stock:AAPL:US", "schwab")

        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stream = SidecarStream(
            source="schwab",
            channel=channel,
            registry=registry,
            on_quote=_on_quote_noop,
            health=SourceHealthMap(),
            symbol_ref_builder=_symbol_ref_builder,
        )

        run_task = asyncio.create_task(stream.run())
        await _await_first_frame(fake)

        fake._first_frame_seen.clear()
        first_round_count = len(fake.received)
        stream.request_reconnect()
        await asyncio.wait_for(
            _await_kind_after(fake, "resync", first_round_count),
            timeout=2.0,
        )

        stream.stop()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        await channel.close()

        post_reconnect_kinds = [k for (k, _) in fake.received[first_round_count:]]
        assert "resync" in post_reconnect_kinds
        assert "subscribe" not in post_reconnect_kinds


@pytest.mark.asyncio
async def test_reconnect_against_restarted_sidecar_sends_subscribe() -> None:
    """Cold-start signal: HealthResponse.started_at differs from last-known
    → first frame is subscribe (sidecar lost broker-side refcount)."""
    async with fake_server(started_at_seconds=100) as (_, port, fake):
        registry = SubscriptionRegistry(cap_per_ws=10, cap_global=10, sub_rate_limit_per_minute=100)
        await registry.add(uuid4(), ["stock:AAPL:US"])
        registry.set_route("stock:AAPL:US", "schwab")

        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stream = SidecarStream(
            source="schwab",
            channel=channel,
            registry=registry,
            on_quote=_on_quote_noop,
            health=SourceHealthMap(),
            symbol_ref_builder=_symbol_ref_builder,
        )

        run_task = asyncio.create_task(stream.run())
        await _await_first_frame(fake)
        first_round_count = len(fake.received)

        # Simulate sidecar restart by changing started_at + reconnecting.
        fake.started_at_seconds = 200
        fake._first_frame_seen.clear()
        stream.request_reconnect()

        await asyncio.wait_for(
            _await_kind_after(fake, "subscribe", first_round_count),
            timeout=2.0,
        )

        stream.stop()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        await channel.close()

        post_kinds = [k for (k, _) in fake.received[first_round_count:]]
        assert "subscribe" in post_kinds


@pytest.mark.asyncio
async def test_token_rotation_increments_reconnect_metric() -> None:
    async with fake_server() as (_, port, fake):
        registry = SubscriptionRegistry(cap_per_ws=10, cap_global=10, sub_rate_limit_per_minute=100)
        await registry.add(uuid4(), ["stock:AAPL:US"])
        registry.set_route("stock:AAPL:US", "schwab")

        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stream = SidecarStream(
            source="schwab",
            channel=channel,
            registry=registry,
            on_quote=_on_quote_noop,
            health=SourceHealthMap(),
            symbol_ref_builder=_symbol_ref_builder,
        )

        before = QUOTE_SIDECAR_RECONNECT_TOTAL.labels(
            source="schwab", reason="token_rotation"
        )._value.get()

        run_task = asyncio.create_task(stream.run())
        await _await_first_frame(fake)

        stream.request_reconnect()
        await asyncio.sleep(0.3)  # let the loop observe the rotation event

        stream.stop()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        await channel.close()

        after = QUOTE_SIDECAR_RECONNECT_TOTAL.labels(
            source="schwab", reason="token_rotation"
        )._value.get()
        assert after - before >= 1
