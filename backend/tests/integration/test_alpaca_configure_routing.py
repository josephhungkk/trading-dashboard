"""Per-mode Configure routing — paper sidecar must NEVER see live creds.

Phase 7c HIGH-5. Backend dispatches Configure ONLY to the gateway_label
whose Health-reported label suffix matches the implied mode. Cross-mode
attempts increment alpaca_mode_mismatch_total{label} and refuse to send.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.metrics import ALPACA_MODE_MISMATCH_TOTAL
from app.services.broker_registry_factory import BrokerConfigurer


def _fake_health(label: str) -> SimpleNamespace:
    """Build a Health-like response object — only `label` is consulted."""
    return SimpleNamespace(label=label)


def _make_configurer(
    *,
    sidecars: dict[str, AsyncMock],
    secrets: dict[str, str],
) -> BrokerConfigurer:
    """Build a BrokerConfigurer wired to mock clients + a stub config service."""

    config_service = MagicMock()
    config_service.reveal_secret = AsyncMock(side_effect=lambda ns, key: secrets.get(key))
    config_service.get = AsyncMock(return_value=None)

    registry = MagicMock()
    registry.get_client = AsyncMock(side_effect=lambda label: sidecars[label])

    return BrokerConfigurer(
        config_service=config_service,
        registry=registry,
        targets={"alpaca-live", "alpaca-paper"},
    )


@pytest.mark.asyncio
async def test_configure_live_only_to_live_sidecar() -> None:
    """Seeding alpaca.default.live.api_key fires Configure to alpaca-live ONLY."""
    live_client = AsyncMock()
    live_client.health = AsyncMock(return_value=_fake_health("alpaca-live"))
    live_client.configure = AsyncMock(return_value=SimpleNamespace(ok=True))

    paper_client = AsyncMock()
    paper_client.health = AsyncMock(return_value=_fake_health("alpaca-paper"))
    paper_client.configure = AsyncMock(return_value=SimpleNamespace(ok=True))

    configurer = _make_configurer(
        sidecars={"alpaca-live": live_client, "alpaca-paper": paper_client},
        secrets={
            "alpaca.default.live.api_key": "PKlive",
            "alpaca.default.live.api_secret": "secretlive",
        },
    )
    ok = await configurer.configure("alpaca-live")
    assert ok is True
    live_client.configure.assert_awaited_once()
    paper_client.configure.assert_not_awaited()


@pytest.mark.asyncio
async def test_configure_paper_only_to_paper_sidecar() -> None:
    live_client = AsyncMock()
    live_client.health = AsyncMock(return_value=_fake_health("alpaca-live"))
    live_client.configure = AsyncMock(return_value=SimpleNamespace(ok=True))

    paper_client = AsyncMock()
    paper_client.health = AsyncMock(return_value=_fake_health("alpaca-paper"))
    paper_client.configure = AsyncMock(return_value=SimpleNamespace(ok=True))

    configurer = _make_configurer(
        sidecars={"alpaca-live": live_client, "alpaca-paper": paper_client},
        secrets={
            "alpaca.default.paper.api_key": "PKpaper",
            "alpaca.default.paper.api_secret": "secretpaper",
        },
    )
    ok = await configurer.configure("alpaca-paper")
    assert ok is True
    paper_client.configure.assert_awaited_once()
    live_client.configure.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_mode_pollution_refused() -> None:
    """Backend MUST refuse to send live creds to a paper-mode sidecar.

    Sidecar's Health reports label="alpaca-paper" but registry asked
    for the alpaca-live gateway. Configure must NOT fire; metric must
    increment.
    """
    rogue_paper_at_live_slot = AsyncMock()
    rogue_paper_at_live_slot.health = AsyncMock(return_value=_fake_health("alpaca-paper"))
    rogue_paper_at_live_slot.configure = AsyncMock(return_value=SimpleNamespace(ok=True))

    configurer = _make_configurer(
        sidecars={"alpaca-live": rogue_paper_at_live_slot},
        secrets={
            "alpaca.default.live.api_key": "PKlive",
            "alpaca.default.live.api_secret": "secretlive",
        },
    )
    before = ALPACA_MODE_MISMATCH_TOTAL.labels(label="alpaca-live")._value.get()
    ok = await configurer.configure("alpaca-live")
    after = ALPACA_MODE_MISMATCH_TOTAL.labels(label="alpaca-live")._value.get()

    assert ok is False
    rogue_paper_at_live_slot.configure.assert_not_awaited()
    assert after - before == 1


@pytest.mark.asyncio
async def test_forward_compat_account_label_fallback() -> None:
    """No <account_label> entry → fall back to broker.alpaca.<mode>.api_key (MED-2)."""
    live_client = AsyncMock()
    live_client.health = AsyncMock(return_value=_fake_health("alpaca-live"))
    live_client.configure = AsyncMock(return_value=SimpleNamespace(ok=True))

    configurer = _make_configurer(
        sidecars={"alpaca-live": live_client},
        secrets={
            # Only the legacy unlabeled keys exist
            "alpaca.live.api_key": "PKlegacy",
            "alpaca.live.api_secret": "secretlegacy",
        },
    )
    ok = await configurer.configure("alpaca-live")
    assert ok is True
    live_client.configure.assert_awaited_once()
