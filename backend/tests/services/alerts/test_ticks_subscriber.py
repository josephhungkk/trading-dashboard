"""Phase 11b chunk B3: ticks subscriber tests — psubscribe per resolved symbol,
silently drop unresolvable symbols, idempotent add, remove_symbol cleanly
punsubscribes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.alerts.ticks_subscriber import TicksSubscriber


class FakePubSub:
    def __init__(self) -> None:
        self.psubscribed: list[str] = []
        self.punsubscribed: list[str] = []

    async def psubscribe(self, pattern: str) -> None:
        self.psubscribed.append(pattern)

    async def punsubscribe(self, pattern: str) -> None:
        self.punsubscribed.append(pattern)


async def test_ticks_subscriber_psubscribes_resolved_symbol() -> None:
    redis_pubsub = FakePubSub()
    resolver = AsyncMock()
    resolver.find_by_alias.return_value = MagicMock(canonical_id="AAPL@nasdaq.usd")
    sub = TicksSubscriber(
        pubsub=redis_pubsub,
        resolver=resolver,
        on_quote=AsyncMock(),
    )
    ok = await sub.add_symbol("AAPL")
    assert ok is True
    assert redis_pubsub.psubscribed == ["quote.*.AAPL@nasdaq.usd"]
    assert "AAPL" in sub.subscribed_symbols


async def test_ticks_subscriber_unknown_symbol_skipped() -> None:
    redis_pubsub = FakePubSub()
    resolver = AsyncMock()
    resolver.find_by_alias.return_value = None
    sub = TicksSubscriber(
        pubsub=redis_pubsub,
        resolver=resolver,
        on_quote=AsyncMock(),
    )
    ok = await sub.add_symbol("ZZZZ")
    assert ok is False
    assert redis_pubsub.psubscribed == []


async def test_ticks_subscriber_no_canonical_id_skipped() -> None:
    """find_by_alias may return an instrument without canonical_id (rare data
    issue) — must not crash; must NOT psubscribe."""
    redis_pubsub = FakePubSub()
    resolver = AsyncMock()
    # MagicMock auto-spawns canonical_id as Mock; force None via spec.
    bogus = MagicMock(spec=[])
    resolver.find_by_alias.return_value = bogus
    sub = TicksSubscriber(
        pubsub=redis_pubsub,
        resolver=resolver,
        on_quote=AsyncMock(),
    )
    ok = await sub.add_symbol("WEIRD")
    assert ok is False
    assert redis_pubsub.psubscribed == []


async def test_ticks_subscriber_add_is_idempotent() -> None:
    redis_pubsub = FakePubSub()
    resolver = AsyncMock()
    resolver.find_by_alias.return_value = MagicMock(canonical_id="AAPL@nasdaq.usd")
    sub = TicksSubscriber(
        pubsub=redis_pubsub,
        resolver=resolver,
        on_quote=AsyncMock(),
    )
    await sub.add_symbol("AAPL")
    await sub.add_symbol("AAPL")
    await sub.add_symbol("AAPL")
    # Only one psubscribe call total.
    assert redis_pubsub.psubscribed == ["quote.*.AAPL@nasdaq.usd"]
    # Resolver is consulted only the first time (cache hit on subsequent calls).
    assert resolver.find_by_alias.call_count == 1


async def test_ticks_subscriber_remove_punsubscribes() -> None:
    redis_pubsub = FakePubSub()
    resolver = AsyncMock()
    resolver.find_by_alias.return_value = MagicMock(canonical_id="AAPL@nasdaq.usd")
    sub = TicksSubscriber(
        pubsub=redis_pubsub,
        resolver=resolver,
        on_quote=AsyncMock(),
    )
    await sub.add_symbol("AAPL")
    await sub.remove_symbol("AAPL")
    assert redis_pubsub.punsubscribed == ["quote.*.AAPL@nasdaq.usd"]
    assert "AAPL" not in sub.subscribed_symbols


async def test_ticks_subscriber_remove_unknown_is_noop() -> None:
    redis_pubsub = FakePubSub()
    resolver = AsyncMock()
    sub = TicksSubscriber(
        pubsub=redis_pubsub,
        resolver=resolver,
        on_quote=AsyncMock(),
    )
    await sub.remove_symbol("NEVER-ADDED")
    assert redis_pubsub.punsubscribed == []
