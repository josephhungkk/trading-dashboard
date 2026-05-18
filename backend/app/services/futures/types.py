"""Futures service types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class FutureContractMonth:
    conid: str
    contract_month: str  # "202506"
    expiry: date
    first_notice_day: date | None  # None for cash-settled
    tick_size: Decimal
    tick_value: Decimal
    multiplier: Decimal
    settlement_type: str  # "CASH" | "PHYSICAL"
    exchange: str
    underlying_symbol: str

    def to_cache_dict(self) -> dict[str, Any]:
        return {
            "conid": self.conid,
            "contract_month": self.contract_month,
            "expiry": self.expiry.isoformat(),
            "first_notice_day": self.first_notice_day.isoformat()
            if self.first_notice_day
            else None,
            "tick_size": str(self.tick_size),
            "tick_value": str(self.tick_value),
            "multiplier": str(self.multiplier),
            "settlement_type": self.settlement_type,
            "exchange": self.exchange,
            "underlying_symbol": self.underlying_symbol,
        }

    @classmethod
    def from_cache_dict(cls, d: dict[str, Any], root_symbol: str) -> FutureContractMonth:
        return cls(
            conid=d["conid"],
            contract_month=d["contract_month"],
            expiry=date.fromisoformat(d["expiry"]),
            first_notice_day=date.fromisoformat(d["first_notice_day"])
            if d.get("first_notice_day")
            else None,
            tick_size=Decimal(d["tick_size"]),
            tick_value=Decimal(d["tick_value"]),
            multiplier=Decimal(d["multiplier"]),
            settlement_type=d["settlement_type"],
            exchange=d["exchange"],
            underlying_symbol=d.get("underlying_symbol", root_symbol),
        )
