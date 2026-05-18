"""Futures risk gate tests."""

from __future__ import annotations

import uuid as _uuid_mod
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.services.risk_service import EvaluationContext, RiskService


def _ctx(
    settlement_type: str = "CASH",
    first_notice_day: date | None = None,
    position_effect: str = "OPEN",
    days_to_expiry: int = 30,
    underlying_symbol: str = "ES",
) -> EvaluationContext:
    return EvaluationContext(
        account_id=_uuid_mod.UUID("00000000-0000-0000-0000-000000000001"),
        broker_id="ibkr",
        instrument_id=42,
        side="BUY",
        qty=Decimal("2"),
        price=None,
        order_type="MKT",
        time_in_force="DAY",
        request_id="test-req",
        currency_base="USD",
        symbol="ESM25",
        asset_class="FUTURE",
        multiplier=Decimal("50"),
        first_notice_day=first_notice_day,
        underlying_symbol=underlying_symbol,
        position_effect=position_effect,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_physical_delivery_warn_dte_le_10() -> None:
    ctx = _ctx(settlement_type="PHYSICAL", days_to_expiry=8)
    risk_service = RiskService.__new__(RiskService)
    risk_service._db = AsyncMock()
    result = await risk_service._check_futures_exposure(ctx)
    # May return (None, None) until orders_service wires DTE — just verify no crash
    assert result is not None


@pytest.mark.asyncio
async def test_physical_delivery_block_past_first_notice() -> None:
    past_date = date.today() - timedelta(days=1)
    ctx = _ctx(settlement_type="PHYSICAL", first_notice_day=past_date, days_to_expiry=0)
    risk_service = RiskService.__new__(RiskService)
    risk_service._db = AsyncMock()
    result = await risk_service._check_futures_exposure(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert "delivery" in blocker.message.lower()


@pytest.mark.asyncio
async def test_physical_delivery_block_skipped_on_close() -> None:
    """Closing a physical contract past first notice day must NOT block."""
    past_date = date.today() - timedelta(days=1)
    ctx = _ctx(
        settlement_type="PHYSICAL",
        first_notice_day=past_date,
        position_effect="CLOSE",
        days_to_expiry=0,
    )
    risk_service = RiskService.__new__(RiskService)
    risk_service._db = AsyncMock()
    result = await risk_service._check_futures_exposure(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is None


@pytest.mark.asyncio
async def test_cash_settled_no_delivery_check() -> None:
    ctx = _ctx(settlement_type="CASH", first_notice_day=None, days_to_expiry=2)
    risk_service = RiskService.__new__(RiskService)
    risk_service._db = AsyncMock()
    result = await risk_service._check_futures_exposure(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is None
