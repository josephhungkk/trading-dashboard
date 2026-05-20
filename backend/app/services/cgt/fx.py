from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
CRYPTO_TOKENS: frozenset[str] = frozenset({"BTC", "ETH", "SOL", "USDC", "USDT"})
_LONDON = ZoneInfo("Europe/London")


class FxRateNotFoundError(Exception):
    def __init__(self, currency: str, month: object) -> None:
        super().__init__(f"No HMRC FX rate for {currency} month={month}")
        self.currency = currency
        self.month = month


async def to_gbp(
    native_amount: Decimal,
    currency: str,
    executed_at: datetime,
    session: AsyncSession,
) -> tuple[Decimal, Decimal, str]:
    """Convert native_amount to GBP. Returns (gbp_amount, fx_rate, fx_source).
    Convention: fx_rate = foreign_units_per_pound. gbp = native / fx_rate."""
    if currency == "GBP":
        return native_amount, Decimal("1"), "none"
    if currency == "GBX":
        return native_amount / Decimal("100"), Decimal("100"), "gbx_to_gbp"
    if currency in CRYPTO_TOKENS:
        crypto_rate = await _get_crypto_spot(currency, executed_at, session)
        return native_amount / crypto_rate, crypto_rate, "crypto_spot_at_exec"
    london_dt = executed_at.astimezone(_LONDON)
    month = london_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()
    result = await session.execute(
        text("SELECT rate_gbp FROM hmrc_fx_rates WHERE currency=:c AND period_month=:m"),
        {"c": currency, "m": month},
    )
    raw = result.scalar_one_or_none()
    if raw is not None:
        rate = Decimal(str(raw))
        return native_amount / rate, rate, "hmrc_monthly"
    prev_month = (month - timedelta(days=1)).replace(day=1)
    result = await session.execute(
        text("SELECT rate_gbp FROM hmrc_fx_rates WHERE currency=:c AND period_month=:m"),
        {"c": currency, "m": prev_month},
    )
    raw = result.scalar_one_or_none()
    if raw is not None:
        rate = Decimal(str(raw))
        log.warning("cgt.fx.prev_month_fallback", currency=currency, month=str(month))
        return native_amount / rate, rate, "hmrc_monthly_prev_pending"
    raise FxRateNotFoundError(currency, month)


async def _get_crypto_spot(currency: str, executed_at: datetime, session: AsyncSession) -> Decimal:
    log.warning("cgt.fx.crypto_spot_placeholder", currency=currency)
    return Decimal("1")
