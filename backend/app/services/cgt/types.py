from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class TaxEvent:
    account_id: uuid.UUID
    instrument_id: int
    cgt_track: str
    event_type: str
    side: str
    qty: Decimal
    price_gbp: Decimal
    fx_rate: Decimal
    fx_source: str
    original_currency: str
    executed_at: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    fill_id: uuid.UUID | None = None
    leg_index: int = 0
    broker_statement_id: uuid.UUID | None = None
    external_event_id: str | None = None
    source: str = "fill_live"
    is_short_open: bool = False
    is_short_close: bool = False
    commission_native: Decimal = Decimal("0")
    commission_currency: str = "GBP"
    commission_gbp: Decimal = Decimal("0")
    cgt_class_key: str | None = None
    bb_remaining_qty: Decimal = Decimal("0")
    bot_id: uuid.UUID | None = None
    transfer_group_id: uuid.UUID | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PoolState:
    account_id: uuid.UUID
    instrument_id: int
    qty: Decimal
    total_cost_gbp: Decimal

    @property
    def avg_cost_gbp(self) -> Decimal:
        if self.qty == 0:
            return Decimal("0")
        return self.total_cost_gbp / self.qty


@dataclass(frozen=True)
class Disposal:
    disposal_tax_event_id: uuid.UUID
    match_seq: int
    cgt_track: str
    tax_year: int
    disposal_date: object
    proceeds_gbp: Decimal
    allowable_cost_gbp: Decimal
    gain_gbp: Decimal
    match_type: str
    account_id: uuid.UUID
    instrument_id: int
    pool_event_id: uuid.UUID | None = None
    short_obligation_id: uuid.UUID | None = None
    derivative_id: uuid.UUID | None = None


@dataclass(frozen=True)
class IncomeEvent:
    account_id: uuid.UUID
    event_type: str
    income_subtype: str
    gross_gbp: Decimal
    withholding_tax_gbp: Decimal
    net_gbp: Decimal
    fx_rate: Decimal
    fx_source: str
    original_currency: str
    tax_year: int
    pay_date: object
    instrument_id: int | None = None
    broker_statement_id: uuid.UUID | None = None
    external_event_id: str | None = None
    tax_event_id: uuid.UUID | None = None
    ex_date: object | None = None
    notes: str | None = None
