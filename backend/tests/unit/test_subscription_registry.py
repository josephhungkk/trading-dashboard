"""SubscriptionRegistry — refcount + cap + rate-limit (HIGH-6)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.quotes.registry import SubscriptionRegistry


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
    assert diff.rejected_reason == "cap_per_ws"


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
    assert diff.rejected_reason == "cap_global"


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
    assert diff.rejected_reason == "rate_limit"


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
