from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

_DEFAULTS: dict[str, dict[str, Any]] = {
    "ibkr": {"per_share": Decimal("0.005"), "min_per_order": Decimal("1.00"), "tier": "fixed"},
    "futu": {"per_trade_hkd": Decimal("30.0")},
    "schwab": {"us_equity": Decimal("0.0")},
    "alpaca": {"us_equity": Decimal("0.0")},
}


class CommissionSchedule:
    def __init__(self, commission_cfg: dict[str, Any]) -> None:
        self._active_broker_id: str = commission_cfg.get("active_broker_id", "ibkr")
        self._schedules: dict[str, dict[str, Decimal]] = {}
        raw = commission_cfg.get("schedules", {})
        for broker, sched in raw.items():
            parsed: dict[str, Decimal] = {}
            for k, v in sched.items():
                try:
                    parsed[k] = Decimal(str(v))
                except ValueError, TypeError, InvalidOperation:
                    pass  # skip non-numeric fields like "tier"
            self._schedules[broker] = parsed
        for broker, defaults in _DEFAULTS.items():
            if broker not in self._schedules:
                self._schedules[broker] = {
                    k: Decimal(str(v)) for k, v in defaults.items() if k != "tier"
                }

    def compute(self, broker_id: str, *, qty: Decimal) -> Decimal:
        sched = self._schedules.get(broker_id)
        if sched is None:
            raise ValueError(f"unknown_broker: {broker_id!r}")
        if "per_share" in sched:
            commission = qty * sched["per_share"]
            min_order = sched.get("min_per_order", Decimal("0"))
            return max(commission, min_order)
        if "per_trade_hkd" in sched:
            return sched["per_trade_hkd"]
        if "us_equity" in sched:
            return sched["us_equity"]
        return Decimal("0")
