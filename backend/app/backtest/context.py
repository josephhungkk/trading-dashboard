from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.backtest.fill_simulator import FillSimulator


class BacktestContext:
    mode: str = "backtest"

    def __init__(self, *, simulator: FillSimulator) -> None:
        self._sim = simulator

    async def subscribe(self, canonical_id: str, timeframe: str = "1m") -> None:
        pass

    async def place_order(
        self,
        *,
        account_id: UUID,
        canonical_id: str,
        side: str,
        qty: Decimal,
        order_type: str,
        broker_id: str = "ibkr",
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        tif: str = "DAY",
        algo_strategy: str | None = None,
        position_effect: str = "OPEN",
        conid: int | None = None,
    ) -> UUID:
        order_id = uuid.uuid4()
        self._sim.queue_order(
            order_id=order_id,
            canonical_id=canonical_id,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            tif=tif,
        )
        return order_id

    async def get_position(self, canonical_id: str) -> Decimal:
        return self._sim.get_position(canonical_id)

    async def cancel_order(self, order_id: UUID) -> None:
        self._sim.cancel_order(order_id)
