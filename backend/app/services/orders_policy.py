"""Trade policy helpers backed by ConfigService.

These helpers centralize canary-safe trading defaults for mutating order
endpoints. Config values live in ``app_config`` under ``broker.<label>`` for
per-gateway policy and ``broker`` for global controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.config import ConfigService


@dataclass(frozen=True)
class AccountTradePolicy:
    max_notional_per_order: Decimal
    daily_notional_cap: Decimal
    trade_enabled: bool
    simulator_only: bool


async def get_account_policy(
    cfg: ConfigService,
    *,
    gateway_label: str,
    mode: str,
) -> AccountTradePolicy:
    """Return per-gateway trade policy with defensive rollout defaults."""
    namespace = f"broker.{gateway_label}"
    max_notional_raw: object = await cfg.get(namespace, "max_notional_per_order", default="10000")
    daily_cap_raw: object = await cfg.get(namespace, "daily_notional_cap", default="50000")
    trade_enabled = await cfg.get_bool(namespace, "trade_enabled", default=False)
    simulator_only = await cfg.get_bool(
        namespace,
        "simulator_only",
        default=mode == "live",
    )

    return AccountTradePolicy(
        max_notional_per_order=Decimal(str(max_notional_raw)),
        daily_notional_cap=Decimal(str(daily_cap_raw)),
        trade_enabled=trade_enabled is True,
        simulator_only=simulator_only is True,
    )


async def is_kill_switch_active(cfg: ConfigService) -> bool:
    """Return whether the global broker kill switch is enabled."""
    return (await cfg.get_bool("broker", "kill_switch_enabled", default=False)) is True
