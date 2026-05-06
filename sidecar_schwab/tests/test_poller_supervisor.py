"""Phase 8a D4 — PollerSupervisor + facade routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.poller_supervisor import (
    PollerSupervisor,
    _PollerFacade,
    _SimulatorFacade,
)


def _build_redis() -> MagicMock:
    redis = MagicMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hget = AsyncMock(return_value=None)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    redis.delete = AsyncMock()
    return redis


def _build_client() -> MagicMock:
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    client.get_orders_since = AsyncMock(return_value=[])
    return client


@pytest.mark.asyncio
async def test_supervisor_starts_one_poller_per_account():
    accounts = [
        {"account_number": "ACCT-1", "account_hash": "H1"},
        {"account_number": "ACCT-2", "account_hash": "H2"},
    ]
    sup = PollerSupervisor(
        client=_build_client(), redis=_build_redis(), accounts=accounts
    )
    await sup.start()
    try:
        assert len(sup._pollers) == 2
        assert len(sup._sims) == 2
        assert sup.simulator is not None
        assert sup.poller is not None
        assert sup.get_semaphore("ACCT-1") is not None
        assert sup.get_semaphore("ACCT-2") is not None
    finally:
        await sup.stop()
    assert sup._pollers == {}
    assert sup._sims == {}
    assert sup.simulator is None
    assert sup.poller is None


@pytest.mark.asyncio
async def test_supervisor_stop_calls_each_poller_stop():
    sup = PollerSupervisor(client=_build_client(), redis=_build_redis(), accounts=[])
    fake = MagicMock()
    fake.stop = AsyncMock()
    sup._pollers = {"ACCT-1": fake}
    await sup.stop()
    fake.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_simulator_facade_register_routes_by_account_number():
    sim_a = MagicMock()
    sim_a.register = MagicMock(return_value="SIM-A-1")
    sim_b = MagicMock()
    sim_b.register = MagicMock(return_value="SIM-B-1")
    facade = _SimulatorFacade({"ACCT-A": sim_a, "ACCT-B": sim_b})

    bid = facade.register(
        account_number="ACCT-B", client_order_id="SIM-x", request=MagicMock()
    )
    assert bid == "SIM-B-1"
    sim_a.register.assert_not_called()
    sim_b.register.assert_called_once()


def test_simulator_facade_register_unknown_account_raises():
    facade = _SimulatorFacade({})
    with pytest.raises(ValueError, match="no simulator"):
        facade.register(
            account_number="MISSING", client_order_id="SIM-x", request=MagicMock()
        )


def test_simulator_facade_cancel_searches_for_broker_order_id():
    sim_a = MagicMock()
    sim_a._by_bid = {}
    sim_a.cancel = MagicMock()
    sim_b = MagicMock()
    sim_b._by_bid = {"BID-1": object()}
    sim_b.cancel = MagicMock()
    facade = _SimulatorFacade({"A": sim_a, "B": sim_b})

    facade.cancel(broker_order_id="BID-1")
    sim_a.cancel.assert_not_called()
    sim_b.cancel.assert_called_once_with(broker_order_id="BID-1")


def test_simulator_facade_cancel_unknown_id_is_noop():
    facade = _SimulatorFacade({"A": MagicMock(_by_bid={})})
    facade.cancel(broker_order_id="UNKNOWN")  # no exception


def test_simulator_facade_modify_routes_to_owning_sim():
    sim = MagicMock()
    sim._by_bid = {"BID-1": object()}
    sim.modify = MagicMock(return_value="BID-2")
    facade = _SimulatorFacade({"A": sim})
    new_bid = facade.modify(broker_order_id="BID-1", request=MagicMock())
    assert new_bid == "BID-2"


def test_simulator_facade_modify_unknown_id_returns_empty():
    facade = _SimulatorFacade({"A": MagicMock(_by_bid={})})
    assert facade.modify(broker_order_id="UNKNOWN", request=MagicMock()) == ""


def test_poller_facade_activate_fast_routes_by_account():
    p1 = MagicMock()
    p1.activate_fast = MagicMock()
    p2 = MagicMock()
    p2.activate_fast = MagicMock()
    facade = _PollerFacade({"A": p1, "B": p2})

    facade.activate_fast(account_number="B")
    p1.activate_fast.assert_not_called()
    p2.activate_fast.assert_called_once()


def test_poller_facade_activate_fast_unknown_account_is_noop():
    facade = _PollerFacade({})
    facade.activate_fast(account_number="MISSING")  # no exception


def test_poller_facade_fan_out_for_returns_per_account_fan_out():
    fan_out = object()
    p = MagicMock()
    p.fan_out = MagicMock(return_value=fan_out)
    facade = _PollerFacade({"A": p})
    assert facade.fan_out_for(account_number="A") is fan_out
    assert facade.fan_out_for(account_number="MISSING") is None
