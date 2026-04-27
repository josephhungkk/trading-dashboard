"""Tests for app.services.orders_policy."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.orders_policy import get_account_policy, is_kill_switch_active


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Pure unit tests; shadow the global DB migration fixture."""


def _mock_config() -> Any:
    cfg = AsyncMock()
    cfg.get = AsyncMock(side_effect=lambda _ns, _key, default=None: default)
    cfg.get_bool = AsyncMock(side_effect=lambda _ns, _key, default=None: default)
    return cfg


@pytest.mark.asyncio
async def test_get_max_notional_per_order_default() -> None:
    policy = await get_account_policy(_mock_config(), gateway_label="isa-live", mode="live")

    assert policy.max_notional_per_order == Decimal("10000")


@pytest.mark.asyncio
async def test_get_daily_notional_cap_default() -> None:
    policy = await get_account_policy(_mock_config(), gateway_label="isa-live", mode="live")

    assert policy.daily_notional_cap == Decimal("50000")


@pytest.mark.asyncio
async def test_get_trade_enabled_default_false() -> None:
    policy = await get_account_policy(_mock_config(), gateway_label="isa-live", mode="live")

    assert policy.trade_enabled is False


@pytest.mark.asyncio
async def test_get_simulator_only_default_true_for_live() -> None:
    policy = await get_account_policy(_mock_config(), gateway_label="isa-live", mode="live")

    assert policy.simulator_only is True


@pytest.mark.asyncio
async def test_get_simulator_only_default_false_for_paper() -> None:
    policy = await get_account_policy(_mock_config(), gateway_label="isa-paper", mode="paper")

    assert policy.simulator_only is False


@pytest.mark.asyncio
async def test_kill_switch_default_false() -> None:
    assert await is_kill_switch_active(_mock_config()) is False
