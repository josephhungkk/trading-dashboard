from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.backtest.commission import CommissionSchedule
from app.bot.base import BarEvent, FillEvent

_GTC_MAX_DAYS = 90
_VALID_TIF = frozenset({"DAY", "GTC", "IOC", "FOK"})


@dataclasses.dataclass
class PendingOrder:
    order_id: UUID
    canonical_id: str
    side: str
    qty: Decimal
    order_type: str
    limit_price: Decimal | None
    tif: str
    placed_at_ts: Any  # datetime of the bar when the order was queued


class FillSimulator:
    def __init__(
        self,
        *,
        slippage_bps: Decimal | None,
        slippage_atr_pct: Decimal | None,
        commission: CommissionSchedule,
        market_calendar_exchange: str = "NYSE",
        atr14: Decimal | None = None,
    ) -> None:
        if slippage_bps is None and slippage_atr_pct is None:
            raise ValueError("Exactly one of slippage_bps or slippage_atr_pct must be set.")
        if slippage_bps is not None and slippage_atr_pct is not None:
            raise ValueError("Exactly one of slippage_bps or slippage_atr_pct must be set.")
        self._slippage_bps = slippage_bps
        self._slippage_atr_pct = slippage_atr_pct
        self._atr14 = atr14
        self._commission = commission
        self._exchange = market_calendar_exchange
        self._pending: list[PendingOrder] = []
        self._positions: dict[str, Decimal] = {}

    def queue_order(
        self,
        *,
        order_id: UUID,
        canonical_id: str,
        side: str,
        qty: Decimal,
        order_type: str,
        limit_price: Decimal | None,
        tif: str,
        placed_at_ts: Any = None,
    ) -> None:
        if tif not in _VALID_TIF:
            raise NotImplementedError(f"TIF {tif!r} not supported in backtest")
        self._pending.append(
            PendingOrder(
                order_id=order_id,
                canonical_id=canonical_id,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                placed_at_ts=placed_at_ts,
            )
        )

    def cancel_order(self, order_id: UUID) -> None:
        self._pending = [o for o in self._pending if o.order_id != order_id]

    def get_position(self, canonical_id: str) -> Decimal:
        return self._positions.get(canonical_id, Decimal("0"))

    def _slippage(self, price: Decimal, side: str) -> Decimal:
        if self._slippage_bps is not None:
            slip = price * self._slippage_bps / Decimal("10000")
        elif self._slippage_atr_pct is not None and self._atr14 is not None:
            slip = self._atr14 * self._slippage_atr_pct
        else:
            slip = Decimal("0")
        return slip if side == "BUY" else -slip

    def _would_fill(self, order: PendingOrder, bar: BarEvent) -> bool:
        if order.order_type == "MKT":
            return True
        if order.order_type == "LMT" and order.limit_price is not None:
            if order.side == "BUY":
                return bar.open <= order.limit_price
            return bar.open >= order.limit_price
        return False

    def process_pending_orders(
        self, bar: BarEvent, *, on_fill: Callable[[FillEvent], None]
    ) -> None:
        remaining: list[PendingOrder] = []
        for order in self._pending:
            if order.canonical_id != bar.canonical_id:
                remaining.append(order)
                continue

            # GTC expiry
            if order.tif == "GTC" and order.placed_at_ts is not None:
                if (bar.ts - order.placed_at_ts) > timedelta(days=_GTC_MAX_DAYS):
                    continue  # expired — discard silently

            fills_this_order = self._would_fill(order, bar)

            if fills_this_order:
                fill_price = bar.open + self._slippage(bar.open, order.side)
                fill = FillEvent(
                    order_id=order.order_id,
                    account_id=None,  # type: ignore[arg-type]
                    canonical_id=order.canonical_id,
                    side=order.side,
                    qty=order.qty,
                    price=fill_price,
                    filled_at=bar.ts,
                )
                on_fill(fill)
                delta = order.qty if order.side == "BUY" else -order.qty
                self._positions[order.canonical_id] = (
                    self._positions.get(order.canonical_id, Decimal("0")) + delta
                )
            else:
                if order.tif in ("IOC", "FOK"):
                    continue  # cancel immediately
                remaining.append(order)

        self._pending = remaining

    def force_close_open_positions(
        self, final_bar: BarEvent, *, on_fill: Callable[[FillEvent], None]
    ) -> None:
        self._pending = []  # discard all unfilled orders
        for canonical_id, qty in list(self._positions.items()):
            if qty == Decimal("0"):
                continue
            side = "SELL" if qty > 0 else "BUY"
            close_qty = abs(qty)
            fill_price = final_bar.close + self._slippage(final_bar.close, side)
            fill = FillEvent(
                order_id=uuid.uuid4(),
                account_id=None,  # type: ignore[arg-type]
                canonical_id=canonical_id,
                side=side,
                qty=close_qty,
                price=fill_price,
                filled_at=final_bar.ts,
            )
            on_fill(fill)
            self._positions[canonical_id] = Decimal("0")

    def reset(self) -> None:
        self._pending = []
        self._positions = {}
