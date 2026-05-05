"""SidecarStream drift sentinel handling tests."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-chars-ok")
os.environ.setdefault("APP_CORS_ORIGINS", '["http://localhost:5173"]')
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://trader:ci@localhost:5432/dashboard")
os.environ.setdefault("REDIS_PASSWORD", "ci")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app._generated.broker.v1 import broker_pb2 as pb
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap
from app.services.quotes.upstream.sidecar_stream import SidecarStream


def _symbol_ref_builder(canonical_id: str) -> pb.SymbolRef:
    return pb.SymbolRef(canonical_id=canonical_id, raw_symbol=canonical_id)


def _registry() -> SubscriptionRegistry:
    return SubscriptionRegistry(
        cap_per_ws=100,
        cap_global=1000,
        sub_rate_limit_per_minute=1000,
    )


def _stream(
    registry: SubscriptionRegistry,
    propagated: list[pb.QuoteMessage],
) -> SidecarStream:
    async def on_quote(quote: pb.QuoteMessage) -> None:
        propagated.append(quote)

    return SidecarStream(
        source="alpaca",
        channel=object(),
        registry=registry,
        on_quote=on_quote,
        health=SourceHealthMap(),
        symbol_ref_builder=_symbol_ref_builder,
    )


@pytest.mark.asyncio
async def test_drift_sentinel_decrements_per_source_counter() -> None:
    registry = _registry()
    registry._per_source_refs["alpaca"] = 5
    propagated: list[pb.QuoteMessage] = []
    stream = _stream(registry, propagated)

    await stream._handle_inbound_quote(
        pb.QuoteMessage(
            canonical_id="stock:AAPL:US",
            source="alpaca",
            raw_payload=b'{"drift":"cap_exceeded"}',
        )
    )

    assert registry._per_source_refs["alpaca"] == 4


@pytest.mark.asyncio
async def test_drift_sentinel_does_not_propagate_as_quote_tick() -> None:
    registry = _registry()
    registry._per_source_refs["alpaca"] = 5
    propagated: list[pb.QuoteMessage] = []
    stream = _stream(registry, propagated)

    await stream._handle_inbound_quote(
        pb.QuoteMessage(
            canonical_id="stock:AAPL:US",
            source="alpaca",
            raw_payload=b'{"drift":"cap_exceeded"}',
        )
    )

    assert propagated == []


@pytest.mark.asyncio
async def test_per_source_zero_pops_entry() -> None:
    registry = _registry()
    registry._per_source_refs["alpaca"] = 1
    propagated: list[pb.QuoteMessage] = []
    stream = _stream(registry, propagated)

    await stream._handle_inbound_quote(
        pb.QuoteMessage(
            canonical_id="stock:AAPL:US",
            source="alpaca",
            raw_payload=b'{"drift":"cap_exceeded"}',
        )
    )

    assert "alpaca" not in registry._per_source_refs
