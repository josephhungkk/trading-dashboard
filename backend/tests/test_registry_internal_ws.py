import pytest

from app.services.quotes.registry import SubscriptionRegistry


@pytest.mark.asyncio
async def test_internal_ws_uses_override_cap():
    reg = SubscriptionRegistry(
        cap_global=100,
        cap_per_ws=3,
        sub_rate_limit_per_minute=60,
        cap_per_ws_override={"__internal:scanner": 8},
    )
    symbols = [f"SYM{i}" for i in range(8)]
    diff = await reg.add("__internal:scanner", symbols)
    assert len(diff.added) == 8
    assert len(diff.rejected) == 0


@pytest.mark.asyncio
async def test_internal_ws_no_rate_limit():
    reg = SubscriptionRegistry(
        cap_global=200,
        cap_per_ws=3,
        sub_rate_limit_per_minute=5,
        cap_per_ws_override={"__internal:scanner": 50},
    )
    symbols = [f"SYM{i}" for i in range(50)]
    diff = await reg.add("__internal:scanner", symbols)
    assert len(diff.added) == 50


@pytest.mark.asyncio
async def test_normal_ws_still_rate_limited():
    import uuid

    reg = SubscriptionRegistry(
        cap_global=100,
        cap_per_ws=100,
        sub_rate_limit_per_minute=3,
    )
    ws_id = uuid.uuid4()
    symbols = [f"SYM{i}" for i in range(10)]
    diff = await reg.add(ws_id, symbols)
    assert len(diff.rejected_rate_limit) > 0
