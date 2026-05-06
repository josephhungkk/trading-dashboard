"""Phase 8a — SimRegistry synthetic event emitter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.simulator import SimRegistry


def _build_fan_out() -> MagicMock:
    fan = MagicMock()
    fan.publish = AsyncMock()
    return fan


@pytest.mark.asyncio
async def test_register_returns_sim_id_and_emits_submitted():
    fan = _build_fan_out()
    sim = SimRegistry(fan_out=fan)
    sim_id = sim.register(
        account_number="ACCT-1",
        client_order_id="SIM-test-1",
        request=MagicMock(),
    )
    assert sim_id.startswith("SIM-")
    await asyncio.sleep(0.1)  # > _SYNTHETIC_DELAY_S (0.05)
    fan.publish.assert_awaited_once()
    ev = fan.publish.call_args.args[0]
    assert ev.broker_order_id == sim_id
    assert ev.status == "submitted"


@pytest.mark.asyncio
async def test_cancel_emits_cancelled():
    fan = _build_fan_out()
    sim = SimRegistry(fan_out=fan)
    bid = sim.register(
        account_number="ACCT-1",
        client_order_id="SIM-test-1",
        request=MagicMock(),
    )
    await asyncio.sleep(0.1)
    sim.cancel(broker_order_id=bid)
    await asyncio.sleep(0.1)
    assert fan.publish.await_count == 2
    statuses = [call.args[0].status for call in fan.publish.call_args_list]
    assert statuses == ["submitted", "cancelled"]


@pytest.mark.asyncio
async def test_cancel_unknown_broker_order_id_is_noop():
    fan = _build_fan_out()
    sim = SimRegistry(fan_out=fan)
    sim.cancel(broker_order_id="SIM-bogus")
    await asyncio.sleep(0.1)
    fan.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_modify_emits_modified_then_submitted_for_replacement():
    fan = _build_fan_out()
    sim = SimRegistry(fan_out=fan)
    bid = sim.register(
        account_number="ACCT-1",
        client_order_id="SIM-test-1",
        request=MagicMock(),
    )
    await asyncio.sleep(0.1)
    new_bid = sim.modify(broker_order_id=bid, request=MagicMock())
    await asyncio.sleep(0.1)
    assert new_bid.startswith("SIM-")
    assert new_bid != bid
    statuses = [call.args[0].status for call in fan.publish.call_args_list]
    assert statuses == ["submitted", "modified", "submitted"]


@pytest.mark.asyncio
async def test_modify_unknown_broker_order_id_returns_empty():
    fan = _build_fan_out()
    sim = SimRegistry(fan_out=fan)
    new_bid = sim.modify(broker_order_id="SIM-bogus", request=MagicMock())
    assert new_bid == ""


def test_gc_drops_entries_older_than_ttl():
    fan = _build_fan_out()
    now = [1000.0]
    sim = SimRegistry(fan_out=fan, clock=lambda: now[0])
    sim.register(
        account_number="ACCT-1",
        client_order_id="SIM-old",
        request=MagicMock(),
    )
    now[0] += 3601.0  # > _SIM_TTL_SECONDS (3600)
    sim.gc()
    assert "SIM-old" not in sim._entries
    assert sim._by_bid == {}
