from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class LegSpec(BaseModel):
    instrument_id: int
    side: str
    qty: Decimal
    position_effect: str
    ratio: int = 1
    limit_price: Decimal | None = None
    symbol: str
    exchange: str
    currency: str
    expiry: str
    strike: Decimal
    put_call: str


class ComboSpec(BaseModel):
    strategy_type: str
    underlying_symbol: str
    underlying_canonical_id: str
    legs: list[LegSpec]
    tif: str
    account_id: str


class ComboEnvelope(BaseModel):
    net_debit_credit: Decimal
    kind: str
    max_loss: Decimal | None
    max_profit: Decimal | None
    break_even: list[Decimal]


class LegContext(BaseModel):
    leg_idx: int
    instrument_id: int
    side: str
    qty: Decimal
    position_effect: str
    multiplier: Decimal = Decimal("100")


class ComboContext(BaseModel):
    account_id: str
    mode: str
    legs: list[LegContext]
    envelope: ComboEnvelope
