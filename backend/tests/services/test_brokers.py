"""Tests for BrokerRegistry (Phase 4 Task 32).

Uses 4 mock BrokerSidecarClients (one per gateway label) with overridable
health() return values + a monkey-patched time.monotonic() so freshness
expiry assertions are deterministic.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.brokers import base
from app.services.brokers import (
    BrokerRegistry,
    BrokerSidecarTimeout,
    BrokerSidecarUnavailable,
)


class _MockClient:
    """Duck-typed substitute for BrokerSidecarClient. Tests poke
    set_ok() / set_unavailable() / set_timeout() to control what the
    next health() call returns or raises."""

    def __init__(self, label: str) -> None:
        self.label = label
        self._response: base.HealthResponse | Exception = base.HealthResponse(
            label=label,
            gateway_connected=True,
            gateway_version="999",
            last_tick_at=None,
            sidecar_version="0.4.0-test",
        )
        self.closed = False

    def set_ok(self) -> None:
        self._response = base.HealthResponse(
            label=self.label,
            gateway_connected=True,
            gateway_version="999",
            last_tick_at=None,
            sidecar_version="0.4.0-test",
        )

    def set_unavailable(self) -> None:
        self._response = BrokerSidecarUnavailable(f"{self.label} down")

    def set_timeout(self) -> None:
        self._response = BrokerSidecarTimeout(f"{self.label} timeout")

    async def health(self) -> base.HealthResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self) -> None:
        self.closed = True


def _as_any(value: object) -> Any:
    """Cast through Any so the duck-typed _MockClient satisfies the typed
    BrokerSidecarClient parameter without inheriting from the real class."""
    return value


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, float]]:
    """Patches time.monotonic in app.services.brokers so tests can advance
    the registry's clock without sleeping."""
    state = {"now": 1_000_000.0}

    def _now() -> float:
        return state["now"]

    monkeypatch.setattr("app.services.brokers.time.monotonic", _now)
    yield state


@pytest.fixture
def registry() -> tuple[BrokerRegistry, dict[str, _MockClient]]:
    mocks = {
        label: _MockClient(label)
        for label in ("isa-live", "isa-paper", "normal-live", "normal-paper")
    }
    reg = BrokerRegistry(
        clients={label: _as_any(client) for label, client in mocks.items()},
        freshness_seconds=90.0,
        probe_interval_healthy=60.0,
        probe_interval_unhealthy=5.0,
    )
    return reg, mocks


# --- get_client --------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_client_returns_the_registered_client(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
) -> None:
    reg, mocks = registry
    client = await reg.get_client("isa-paper")
    assert client is mocks["isa-paper"]


@pytest.mark.asyncio
async def test_get_client_raises_for_unknown_label(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
) -> None:
    reg, _ = registry
    with pytest.raises(KeyError):
        await reg.get_client("does-not-exist")


# --- probe_once + healthy_clients --------------------------------------------


@pytest.mark.asyncio
async def test_unprobed_clients_are_excluded_from_healthy_set(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    assert await reg.healthy_clients() == []
    assert sorted(await reg.degraded_labels()) == [
        "isa-live",
        "isa-paper",
        "normal-live",
        "normal-paper",
    ]


@pytest.mark.asyncio
async def test_probe_once_marks_all_healthy(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    await reg.probe_once()
    healthy = await reg.healthy_clients()
    assert len(healthy) == 4
    assert await reg.degraded_labels() == []


@pytest.mark.asyncio
async def test_unhealthy_to_healthy_transition(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, mocks = registry
    mocks["isa-live"].set_unavailable()
    mocks["normal-paper"].set_timeout()

    await reg.probe_once()
    degraded = sorted(await reg.degraded_labels())
    assert degraded == ["isa-live", "normal-paper"]
    healthy_labels = {c.label for c in await reg.healthy_clients()}
    assert healthy_labels == {"isa-paper", "normal-live"}

    mocks["isa-live"].set_ok()
    mocks["normal-paper"].set_ok()
    await reg.probe_once()
    assert await reg.degraded_labels() == []
    assert len(await reg.healthy_clients()) == 4


@pytest.mark.asyncio
async def test_healthy_to_unhealthy_transition(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, mocks = registry
    await reg.probe_once()
    assert len(await reg.healthy_clients()) == 4

    mocks["isa-paper"].set_unavailable()
    await reg.probe_once()
    assert await reg.degraded_labels() == ["isa-paper"]


# --- freshness expiry --------------------------------------------------------


@pytest.mark.asyncio
async def test_freshness_expiry_drops_stale_healthy(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    await reg.probe_once()
    assert len(await reg.healthy_clients()) == 4

    # Advance clock past the 90s freshness window without re-probing.
    clock["now"] += 91.0
    assert await reg.healthy_clients() == []
    assert sorted(await reg.degraded_labels()) == [
        "isa-live",
        "isa-paper",
        "normal-live",
        "normal-paper",
    ]


@pytest.mark.asyncio
async def test_freshness_window_inclusive_at_exactly_90s(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    await reg.probe_once()

    # `now - probed_at <= freshness` -> exactly 90s is still healthy.
    clock["now"] += 90.0
    assert len(await reg.healthy_clients()) == 4

    # 0.001s past -> drops out.
    clock["now"] += 0.001
    assert await reg.healthy_clients() == []


# --- close -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_every_client(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
) -> None:
    reg, mocks = registry
    await reg.close()
    assert all(m.closed for m in mocks.values())
