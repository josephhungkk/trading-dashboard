"""Tests for sidecar.pnl_cache (Phase 4 Task 8)."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from sidecar.pnl_cache import PnLCache


@dataclass
class FakePnLSingle:
    """Minimal stand-in for ib_async PnLSingle.

    ib_async ships no type stubs and PnLSingle is normally returned by
    reqPnLSingleAsync as a live-updating proxy. For tests we just need an
    object whose unrealizedPnL/realizedPnL/dailyPnL attributes are readable.
    """

    # ib_async exposes PnL fields in mixedCase to mirror the IBKR Java API;
    # mirror their names here so PnLCache.snapshot's getattr-style reads work.
    unrealizedPnL: float | None = None  # noqa: N815 — match ib_async API surface
    realizedPnL: float | None = None  # noqa: N815
    dailyPnL: float | None = None  # noqa: N815


@dataclass
class FakeIB:
    """Fake ib_async.IB.

    `req_delay` simulates network latency on reqPnLSingleAsync so concurrent
    PnLCache.get() callers race for the same in-flight subscription
    (HIGH-1 TOCTOU regression guard).

    `cancel_raises` simulates a dead gateway socket where cancelPnLSingle
    raises ConnectionError mid-iteration (CR-6 regression guard).
    """

    req_delay: float = 0.0
    cancel_raises: bool = False
    req_calls: list[tuple[str, str, int]] = field(default_factory=list)
    cancel_calls: list[tuple[str, str, int]] = field(default_factory=list)

    async def reqPnLSingleAsync(  # noqa: N802 — match ib_async API
        self, account: str, model_code: str, conid: int
    ) -> FakePnLSingle:
        self.req_calls.append((account, model_code, conid))
        if self.req_delay:
            await asyncio.sleep(self.req_delay)
        return FakePnLSingle(unrealizedPnL=1.0, realizedPnL=2.0, dailyPnL=3.0)

    def cancelPnLSingle(  # noqa: N802 — match ib_async API
        self, account: str, model_code: str, conid: int
    ) -> None:
        self.cancel_calls.append((account, model_code, conid))
        if self.cancel_raises:
            raise ConnectionError("gateway socket closed")


@pytest.mark.asyncio
async def test_get_caches_first_subscribe() -> None:
    """A second .get() for the same key reuses the cached PnLSingle."""
    ib = FakeIB()
    cache = PnLCache(ib)  # type: ignore[arg-type]
    a = await cache.get("U1234567", 12345)
    b = await cache.get("U1234567", 12345)
    assert a is b
    assert len(ib.req_calls) == 1


@pytest.mark.asyncio
async def test_get_concurrent_callers_share_one_subscribe() -> None:
    """HIGH-1: two concurrent gRPC handlers must NOT each fire reqPnLSingleAsync.

    Without the in-flight Future, both callers see an empty cache and both
    issue subscribes; the first PnLSingle then leaks (auto-updating, never
    cancelled by cancel_all). The 50ms req_delay forces overlap.
    """
    ib = FakeIB(req_delay=0.05)
    cache = PnLCache(ib)  # type: ignore[arg-type]
    results = await asyncio.gather(
        cache.get("U1234567", 12345),
        cache.get("U1234567", 12345),
        cache.get("U1234567", 12345),
    )
    assert results[0] is results[1] is results[2]
    assert len(ib.req_calls) == 1, "in-flight Future must dedupe concurrent subscribes"


@pytest.mark.asyncio
async def test_get_concurrent_distinct_keys_each_subscribe() -> None:
    """Different (account, conid) keys each get their own subscribe."""
    ib = FakeIB(req_delay=0.05)
    cache = PnLCache(ib)  # type: ignore[arg-type]
    await asyncio.gather(
        cache.get("U1111111", 12345),
        cache.get("U2222222", 12345),
        cache.get("U1111111", 67890),
    )
    assert len(ib.req_calls) == 3


@pytest.mark.asyncio
async def test_get_failed_subscribe_clears_inflight() -> None:
    """After a failed subscribe the next .get() retries — inflight must clear."""

    class FailingIB(FakeIB):
        attempts: int = 0

        async def reqPnLSingleAsync(  # noqa: N802 — match ib_async API
            self, account: str, model_code: str, conid: int
        ) -> FakePnLSingle:
            self.req_calls.append((account, model_code, conid))
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient network blip")
            return FakePnLSingle(unrealizedPnL=1.0)

    ib = FailingIB()
    cache = PnLCache(ib)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="transient"):
        await cache.get("U1234567", 12345)

    pnl = await cache.get("U1234567", 12345)
    assert pnl.unrealizedPnL == 1.0
    assert len(ib.req_calls) == 2


@pytest.mark.asyncio
async def test_cancel_all_clears_cache_on_clean_cancel() -> None:
    ib = FakeIB()
    cache = PnLCache(ib)  # type: ignore[arg-type]
    await cache.get("U1234567", 11111)
    await cache.get("U1234567", 22222)
    await cache.cancel_all()
    assert ib.cancel_calls == [("U1234567", "", 11111), ("U1234567", "", 22222)]
    assert cache.snapshot("U1234567", 11111) == (None, None, None)


@pytest.mark.asyncio
async def test_cancel_all_clears_cache_when_cancel_raises() -> None:
    """CR-6: per-iteration try/except so a dead gateway doesn't strand the cache.

    Without the guard the first ConnectionError aborts cancel_all, the cache
    stays populated, and the next reconnect re-subscribes on top of orphaned
    PnLSingle proxies.
    """
    ib = FakeIB(cancel_raises=True)
    cache = PnLCache(ib)  # type: ignore[arg-type]
    await cache.get("U1234567", 11111)
    await cache.get("U1234567", 22222)
    await cache.cancel_all()
    assert len(ib.cancel_calls) == 2, "every key must be attempted"
    assert cache.snapshot("U1234567", 11111) == (None, None, None)
    assert cache.snapshot("U1234567", 22222) == (None, None, None)


@pytest.mark.asyncio
async def test_snapshot_returns_none_triple_when_unsubscribed() -> None:
    cache = PnLCache(FakeIB())  # type: ignore[arg-type]
    assert cache.snapshot("U1234567", 99999) == (None, None, None)


@pytest.mark.asyncio
async def test_snapshot_returns_decimal_when_subscribed() -> None:
    ib = FakeIB()
    cache = PnLCache(ib)  # type: ignore[arg-type]
    await cache.get("U1234567", 12345)
    assert cache.snapshot("U1234567", 12345) == (
        Decimal("1.0"),
        Decimal("2.0"),
        Decimal("3.0"),
    )


@pytest.mark.asyncio
async def test_snapshot_coerces_nan_to_none() -> None:
    """ib_async returns NaN for ~30s after subscribe; gRPC handlers must see None."""

    class NanIB(FakeIB):
        async def reqPnLSingleAsync(  # noqa: N802 — match ib_async API
            self, account: str, model_code: str, conid: int
        ) -> FakePnLSingle:
            self.req_calls.append((account, model_code, conid))
            return FakePnLSingle(
                unrealizedPnL=math.nan,
                realizedPnL=math.nan,
                dailyPnL=math.nan,
            )

    cache = PnLCache(NanIB())  # type: ignore[arg-type]
    await cache.get("U1234567", 12345)
    assert cache.snapshot("U1234567", 12345) == (None, None, None)
