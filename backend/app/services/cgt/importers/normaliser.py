"""Convert broker-specific structs to TaxEvent / IncomeEvent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.services.cgt.types import TaxEvent

_POOL_CLASSES = frozenset(
    {
        "STK",
        "ETF",
        "WAR",
        "CBBC",
        "CASH",
        "BOND",
        "FND",
        "STOCK",
        "WARRANT",
        "FOREX",
        "CRYPTO",
        "MUTUAL_FUND",
    }
)
_DERIV_CLASSES = frozenset({"FUT", "CFD", "FUTURE"})


def resolve_cgt_track(asset_class: str, meta: dict[str, Any] | None = None) -> str:
    if meta and meta.get("tax_exempt"):
        return "exempt"
    if asset_class in _DERIV_CLASSES:
        return "derivative"
    if asset_class in ("OPT", "OPTION"):
        underlying = (meta or {}).get("underlying_asset_class", "")
        return "derivative" if underlying in ("FUTURE", "FUT") else "pool"
    return "pool"


def ibkr_trade_to_tax_event(
    *,
    trade: Any,
    account_id: uuid.UUID,
    instrument_id: int,
    cgt_class_key: str | None,
    gbp_price: Decimal,
    fx_rate: Decimal,
    fx_source: str,
    commission_gbp: Decimal,
    cgt_track: str,
) -> TaxEvent:
    buy_sell = (
        trade.buySell.name.lower() if hasattr(trade.buySell, "name") else str(trade.buySell).lower()
    )
    side = "buy" if buy_sell in ("buy", "buytocover") else "sell"
    is_short_open = buy_sell in ("sell", "sellshort") and cgt_track == "pool"
    is_short_close = buy_sell == "buytocover" and cgt_track == "pool"

    return TaxEvent(
        account_id=account_id,
        instrument_id=instrument_id,
        cgt_track=cgt_track,
        event_type="fill",
        side=side,
        qty=Decimal(str(abs(trade.quantity))),
        price_gbp=gbp_price,
        fx_rate=fx_rate,
        fx_source=fx_source,
        original_currency=trade.currency,
        executed_at=_to_utc(trade.dateTime),
        commission_native=Decimal(str(abs(trade.ibCommission or 0))),
        commission_currency=trade.ibCommissionCurrency or "GBP",
        commission_gbp=commission_gbp,
        cgt_class_key=cgt_class_key,
        bb_remaining_qty=Decimal(str(abs(trade.quantity))) if side == "buy" else Decimal("0"),
        is_short_open=is_short_open,
        is_short_close=is_short_close,
        source="broker_statement",
        external_event_id=(
            f"ibkr:flex:{getattr(trade, 'tradeID', None) or getattr(trade, 'ibExecID', None)}"
        ),
    )


def _to_utc(dt: Any) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    raise TypeError(f"Expected datetime, got {type(dt)}")
