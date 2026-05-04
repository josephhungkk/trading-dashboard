"""SubscriptionRegistry — refcount + cap + rate-limit (HIGH-6)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.metrics import QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL
from app.services.quotes.registry import RATE_WINDOW_SECONDS, SubscriptionRegistry


@pytest.fixture
def registry() -> SubscriptionRegistry:
    return SubscriptionRegistry(
        cap_per_ws=10,
        cap_global=20,
        sub_rate_limit_per_minute=100,
    )


@pytest.mark.asyncio
async def test_first_sub_returns_diff_globally_added(registry: SubscriptionRegistry) -> None:
    ws = uuid4()
    diff = await registry.add(ws, ["stock:AAPL:US"])
    assert diff.added == {"stock:AAPL:US"}
    assert diff.rejected == set()
    assert diff.rejected_reason is None


@pytest.mark.asyncio
async def test_second_ws_same_symbol_no_global_diff(
    registry: SubscriptionRegistry,
) -> None:
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, ["stock:AAPL:US"])
    diff = await registry.add(ws2, ["stock:AAPL:US"])
    assert diff.added == set()
    assert diff.rejected == set()


@pytest.mark.asyncio
async def test_unsub_returns_diff_when_last_ref(
    registry: SubscriptionRegistry,
) -> None:
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, ["stock:AAPL:US"])
    await registry.add(ws2, ["stock:AAPL:US"])

    diff = await registry.remove(ws1, ["stock:AAPL:US"])
    assert diff.removed == set()

    diff = await registry.remove(ws2, ["stock:AAPL:US"])
    assert diff.removed == {"stock:AAPL:US"}


@pytest.mark.asyncio
async def test_remove_ws_cleans_all(registry: SubscriptionRegistry) -> None:
    ws = uuid4()
    await registry.add(ws, ["stock:AAPL:US", "idx:SPX:US"])
    diff = await registry.remove_ws(ws)
    assert diff.removed == {"stock:AAPL:US", "idx:SPX:US"}


@pytest.mark.asyncio
async def test_remove_unknown_symbol_is_noop(registry: SubscriptionRegistry) -> None:
    """Removing a symbol the WS never subscribed to is silently ignored."""
    ws = uuid4()
    diff = await registry.remove(ws, ["stock:NEVER:US"])
    assert diff.removed == set()


@pytest.mark.asyncio
async def test_per_ws_cap_partial_success(registry: SubscriptionRegistry) -> None:
    ws = uuid4()
    diff = await registry.add(ws, [f"stock:S{i}:US" for i in range(15)])
    assert len(diff.added) == 10
    assert len(diff.rejected) == 5
    assert diff.rejected_per_ws == diff.rejected
    assert diff.rejected_reason == "per_ws"


@pytest.mark.asyncio
async def test_global_cap_partial_success(registry: SubscriptionRegistry) -> None:
    """Two WSes fill the 20-symbol global cap with disjoint symbol sets;
    a third's distinct adds all reject."""
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, [f"stock:G{j}:US" for j in range(10)])
    await registry.add(ws2, [f"stock:G{j}:US" for j in range(10, 20)])
    assert registry.global_count() == 20

    ws3 = uuid4()
    diff = await registry.add(ws3, [f"stock:H{j}:US" for j in range(5)])
    assert len(diff.added) == 0
    assert len(diff.rejected) == 5
    assert diff.rejected_global == diff.rejected
    assert diff.rejected_reason == "global"


@pytest.mark.asyncio
async def test_global_cap_bypasses_already_active_symbol(
    registry: SubscriptionRegistry,
) -> None:
    """Spec §5.2.4 — a symbol whose global refcount is already >0 does NOT
    count against the global cap when a new WS subscribes."""
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, [f"stock:G{j}:US" for j in range(10)])
    await registry.add(ws2, [f"stock:G{j}:US" for j in range(10, 20)])
    assert registry.global_count() == 20  # cap fully consumed by disjoint sets

    ws3 = uuid4()
    diff = await registry.add(ws3, ["stock:G0:US"])  # already-active
    assert diff.added == set()  # not a 0→1 transition
    assert diff.rejected == set()
    assert diff.rejected_reason is None


@pytest.mark.asyncio
async def test_rate_limit_kicks_in() -> None:
    """sub_rate_limit_per_minute counts adds across calls; once breached
    further adds are rejected with reason 'rate_limit'."""
    reg = SubscriptionRegistry(cap_per_ws=1000, cap_global=10000, sub_rate_limit_per_minute=5)
    ws = uuid4()
    diff = await reg.add(ws, [f"stock:R{i}:US" for i in range(5)])
    assert len(diff.added) == 5

    diff = await reg.add(ws, ["stock:R6:US"])
    assert diff.added == set()
    assert diff.rejected == {"stock:R6:US"}
    assert diff.rejected_rate_limit == {"stock:R6:US"}
    assert diff.rejected_reason == "rate_limit"


@pytest.mark.asyncio
async def test_rate_limit_window_expires_after_60s() -> None:
    """After 60 s the sliding window evicts old timestamps and adds resume."""
    reg = SubscriptionRegistry(cap_per_ws=1000, cap_global=10000, sub_rate_limit_per_minute=3)
    ws = uuid4()

    base = 1000.0
    with patch("app.services.quotes.registry.time.monotonic", return_value=base):
        await reg.add(ws, [f"stock:W{i}:US" for i in range(3)])

    # Window fully expired — fast-forward past RATE_WINDOW_SECONDS.
    with patch(
        "app.services.quotes.registry.time.monotonic",
        return_value=base + RATE_WINDOW_SECONDS + 1,
    ):
        diff = await reg.add(ws, ["stock:LATER:US"])
        assert diff.added == {"stock:LATER:US"}
        assert diff.rejected == set()


@pytest.mark.asyncio
async def test_rate_limit_counts_rejected_attempts() -> None:
    """Flood of rejected adds (cap_per_ws hit) still trips the rate gate —
    rate window counts every attempt, not only accepts (HIGH fix)."""
    reg = SubscriptionRegistry(cap_per_ws=2, cap_global=1000, sub_rate_limit_per_minute=3)
    ws = uuid4()
    await reg.add(ws, [f"stock:F{i}:US" for i in range(5)])
    # 2 accepted (per_ws cap) + 3 rejected per_ws = 5 attempts, all counted
    # in the rate window. The 6th attempt below should hit rate_limit
    # before per_ws because the window is full.
    diff2 = await reg.add(ws, ["stock:F99:US"])
    assert diff2.rejected_reason == "rate_limit"


@pytest.mark.asyncio
async def test_get_active_returns_globally_subscribed(
    registry: SubscriptionRegistry,
) -> None:
    ws1, ws2 = uuid4(), uuid4()
    await registry.add(ws1, ["stock:AAPL:US", "stock:MSFT:US"])
    await registry.add(ws2, ["stock:MSFT:US", "stock:GOOG:US"])
    assert registry.get_active() == {"stock:AAPL:US", "stock:MSFT:US", "stock:GOOG:US"}


@pytest.mark.asyncio
async def test_get_active_for_source(registry: SubscriptionRegistry) -> None:
    ws = uuid4()
    await registry.add(ws, ["stock:AAPL:US", "stock:0700:HK"])
    registry.set_route("stock:AAPL:US", "schwab")
    registry.set_route("stock:0700:HK", "futu")

    assert registry.get_active_for("schwab") == {"stock:AAPL:US"}
    assert registry.get_active_for("futu") == {"stock:0700:HK"}
    assert registry.get_active_for("ibkr") == set()


@pytest.mark.asyncio
async def test_double_add_is_idempotent(registry: SubscriptionRegistry) -> None:
    """Adding the same symbol twice on the same WS does not double-count."""
    ws = uuid4()
    diff1 = await registry.add(ws, ["stock:AAPL:US"])
    diff2 = await registry.add(ws, ["stock:AAPL:US"])
    assert diff1.added == {"stock:AAPL:US"}
    assert diff2.added == set()
    assert diff2.rejected == set()


@pytest.mark.asyncio
async def test_remove_then_re_add_resurrects_globally(
    registry: SubscriptionRegistry,
) -> None:
    """After last unsubscribe, re-adding signals the engine again."""
    ws = uuid4()
    await registry.add(ws, ["stock:AAPL:US"])
    await registry.remove(ws, ["stock:AAPL:US"])
    diff = await registry.add(ws, ["stock:AAPL:US"])
    assert diff.added == {"stock:AAPL:US"}


@pytest.mark.asyncio
async def test_remove_ws_clears_rate_bucket(registry: SubscriptionRegistry) -> None:
    """Bulk disconnect must also drop the rate-limit bucket — otherwise a
    reconnecting WS inherits stale flood history."""
    ws = uuid4()
    await registry.add(ws, ["stock:AAPL:US"])
    await registry.remove_ws(ws)
    # Internal-state assertion: bucket gone.
    assert ws not in registry._rate_buckets  # type: ignore[attr-defined]
    assert ws not in registry._per_ws  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_empty_symbols_creates_no_phantom_entries(
    registry: SubscriptionRegistry,
) -> None:
    """add(ws, []) must not create empty per-WS or rate-bucket entries —
    leaks unbounded under runaway probe traffic."""
    ws = uuid4()
    await registry.add(ws, [])
    assert ws not in registry._per_ws  # type: ignore[attr-defined]
    assert ws not in registry._rate_buckets  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_metric_counter_increments_on_per_ws_rejection(
    registry: SubscriptionRegistry,
) -> None:
    """quote_subscription_cap_rejected_total{cap_kind=per_ws} ticks per
    rejected symbol."""
    before = QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(cap_kind="per_ws")._value.get()
    ws = uuid4()
    await registry.add(ws, [f"stock:M{i}:US" for i in range(15)])  # 5 rejections
    after = QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(cap_kind="per_ws")._value.get()
    assert after - before == 5
