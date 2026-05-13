"""Phase 11b chunk B3: opt-in tick subscription for rules with `tick_subscribed=true`.

Subscribes to the **internal** Redis pubsub bus `quote.<source>.<canonical_id>`
that Phase 7b.1's QuoteEngine publishes to (engine.py:328). Falls back to
bars_1m on bus disconnect after 3 retries (handled by the producer caller,
not by this subscriber).

Symbol → canonical_id resolution uses Phase 10b.1's chokepoint
`InstrumentResolver.find_by_alias`. Subscriptions register through
`services/quotes/subscription_manager.register_internal_subscriber(name='alerts')`
so Phase 7b.1's global subscription cap (5000) accounting holds — wired up
by `start()` at lifespan boundary in chunk B5+.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol


class _ResolverLike(Protocol):
    async def find_by_alias(self, alias: str) -> object | None: ...


class _PubSubLike(Protocol):
    async def psubscribe(self, pattern: str) -> None: ...
    async def punsubscribe(self, pattern: str) -> None: ...


class TicksSubscriber:
    """Maintains `symbol -> 'quote.*.<canonical_id>'` pattern subscriptions.

    Resolution failures (`find_by_alias` returns `None` or the resolved
    instrument has no `canonical_id`) are SILENT NO-OPS so callers don't have
    to special-case unresolvable user-typed symbols; the per-rule fail-isolation
    in the evaluator catches the downstream "no quotes ever arrive" case.
    """

    def __init__(
        self,
        *,
        pubsub: _PubSubLike,
        resolver: _ResolverLike,
        on_quote: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        self._pubsub = pubsub
        self._resolver = resolver
        self._on_quote = on_quote
        self._symbol_to_pattern: dict[str, str] = {}
        self._listener_task: asyncio.Task[None] | None = None
        self._stopping = False

    async def add_symbol(self, symbol: str) -> bool:
        """Resolve `symbol` → canonical and psubscribe. Returns True on success,
        False if the symbol is unresolvable. Idempotent — duplicate calls
        return True without re-subscribing."""
        if symbol in self._symbol_to_pattern:
            return True
        instrument = await self._resolver.find_by_alias(symbol)
        if instrument is None:
            return False
        canonical = getattr(instrument, "canonical_id", None)
        if not canonical:
            return False
        pattern = f"quote.*.{canonical}"
        self._symbol_to_pattern[symbol] = pattern
        await self._pubsub.psubscribe(pattern)
        return True

    async def remove_symbol(self, symbol: str) -> None:
        """Drop a subscription. No-op if `symbol` was never added."""
        pattern = self._symbol_to_pattern.pop(symbol, None)
        if pattern is not None:
            await self._pubsub.punsubscribe(pattern)

    @property
    def subscribed_symbols(self) -> set[str]:
        return set(self._symbol_to_pattern.keys())
