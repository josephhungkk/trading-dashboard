from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.services.cgt.types import TaxEvent


def test_tax_event_construction():
    te = TaxEvent(
        account_id=uuid.uuid4(),
        instrument_id=1,
        cgt_track="pool",
        event_type="fill",
        side="buy",
        qty=Decimal("100"),
        price_gbp=Decimal("10.50"),
        fx_rate=Decimal("1"),
        fx_source="none",
        original_currency="GBP",
        executed_at=datetime(2025, 7, 14, 12, 0, 0, tzinfo=UTC),
    )
    assert te.commission_gbp == Decimal("0")
    assert te.is_short_open is False
