from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.combos import ComboOrder, OrderLeg
from app.models.orders import Order

log = structlog.get_logger(__name__)


async def handle_fill(
    db: AsyncSession,
    order_id: UUID,
    filled_qty: Decimal,
    avg_fill_price: Decimal,
) -> None:
    result = await db.execute(select(Order.combo_id).where(Order.id == order_id))
    combo_id = result.scalar_one_or_none()
    if combo_id is None:
        return

    async with db.begin_nested():
        combo_result = await db.execute(
            select(ComboOrder).where(ComboOrder.id == combo_id).with_for_update()
        )
        combo = combo_result.scalar_one_or_none()
        if combo is None:
            log.warning("combo_fill_orphaned_order", order_id=str(order_id), combo_id=str(combo_id))
            return

        await db.execute(
            update(OrderLeg)
            .where(OrderLeg.order_id == order_id)
            .values(filled_qty=filled_qty, avg_fill_price=avg_fill_price, status="filled")
        )

        new_status = await _recompute_combo_status(db, combo_id)
        combo.status = new_status

        if new_status == "legged_out":
            log.warning(
                "combo_legged_out",
                combo_id=str(combo.id),
                strategy_type=combo.strategy_type,
            )


async def _recompute_combo_status(db: AsyncSession, combo_id: UUID) -> str:
    result = await db.execute(
        select(OrderLeg.status, OrderLeg.filled_qty).where(OrderLeg.combo_id == combo_id)
    )
    rows = result.all()
    statuses = [r.status for r in rows]
    filled_qtys = [r.filled_qty for r in rows]

    if all(s == "filled" for s in statuses):
        return "filled"
    if all(s in ("cancelled", "rejected") for s in statuses) and all(q == 0 for q in filled_qtys):
        return "cancelled"
    terminal = {"filled", "cancelled", "rejected"}
    all_terminal = all(s in terminal for s in statuses)
    if (
        all_terminal
        and any(q > 0 for q in filled_qtys)
        and any(s in ("cancelled", "rejected") for s in statuses)
    ):
        return "legged_out"
    if any(q > 0 for q in filled_qtys):
        return "partially_filled"
    return "working"
