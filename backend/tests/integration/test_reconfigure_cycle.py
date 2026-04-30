"""E4 — H2 regression: BrokerRegistry re-Configures after sidecar restart.

The architect-review H2 finding warns that a sidecar restart (started_at
changes) leaves it permanently unconfigured unless the registry detects the
change and re-fires Configure. This test drives probe_once with two
HealthResponses differing only in started_at and asserts the configurer is
called both times.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from app._generated.broker.v1 import broker_pb2
from app.services.brokers import BrokerRegistry, BrokerSidecarClient


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Shadow the global autouse Alembic fixture; this test is in-memory only."""
    return None


def _health(started_at: datetime, broker_id: str = "futu") -> broker_pb2.HealthResponse:
    ts = Timestamp()
    ts.FromDatetime(started_at)
    return broker_pb2.HealthResponse(
        label="futu",
        gateway_connected=True,
        gateway_version="0.6.0",
        sidecar_version="0.6.0",
        started_at=ts,
        broker_id=broker_id,
    )


@pytest.mark.asyncio
async def test_reconfigure_fires_on_first_probe() -> None:
    """First probe with a fresh sidecar populates _configured."""
    t1 = datetime.now(UTC).replace(microsecond=0)
    fake_client = MagicMock(spec=BrokerSidecarClient)
    fake_client.health = AsyncMock(return_value=_health(t1))

    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)

    registry = BrokerRegistry({"futu": fake_client})
    registry._configurer = fake_configurer

    await registry.probe_once()

    fake_configurer.configure.assert_awaited_once_with("futu")
    assert registry._configured["futu"] == t1


@pytest.mark.asyncio
async def test_reconfigure_fires_again_when_started_at_changes() -> None:
    """H2 — sidecar restart bumps started_at, registry re-fires Configure."""
    t1 = datetime.now(UTC).replace(microsecond=0)
    t2 = t1 + timedelta(seconds=30)

    fake_client = MagicMock(spec=BrokerSidecarClient)
    fake_client.health = AsyncMock(return_value=_health(t1))

    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)

    registry = BrokerRegistry({"futu": fake_client})
    registry._configurer = fake_configurer

    await registry.probe_once()
    assert fake_configurer.configure.await_count == 1
    assert registry._configured["futu"] == t1

    fake_client.health = AsyncMock(return_value=_health(t2))
    await registry.probe_once()

    assert fake_configurer.configure.await_count == 2
    assert registry._configured["futu"] == t2


@pytest.mark.asyncio
async def test_reconfigure_does_not_fire_when_started_at_unchanged() -> None:
    """Steady state: started_at unchanged across probes -> no extra Configure call."""
    t1 = datetime.now(UTC).replace(microsecond=0)

    fake_client = MagicMock(spec=BrokerSidecarClient)
    fake_client.health = AsyncMock(return_value=_health(t1))

    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)

    registry = BrokerRegistry({"futu": fake_client})
    registry._configurer = fake_configurer

    await registry.probe_once()
    await registry.probe_once()
    await registry.probe_once()

    assert fake_configurer.configure.await_count == 1


@pytest.mark.asyncio
async def test_reconfigure_skipped_for_non_target_label() -> None:
    """IBKR labels (not in configurer.targets) skip the Configure path entirely."""
    t1 = datetime.now(UTC).replace(microsecond=0)

    fake_client = MagicMock(spec=BrokerSidecarClient)
    fake_client.health = AsyncMock(return_value=_health(t1, broker_id="ibkr"))

    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)

    registry = BrokerRegistry({"isa-paper": fake_client})
    registry._configurer = fake_configurer

    await registry.probe_once()

    fake_configurer.configure.assert_not_called()
    assert "isa-paper" not in registry._configured
