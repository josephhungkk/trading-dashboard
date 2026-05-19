from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.conid_resolver import BotConidResolver
from app.services.orders_service import place_order_for_bot

logger = structlog.get_logger(__name__)


class BotOrdersFacade:
    """Thin wrapper around orders_service module-level coroutines for bot use."""

    def __init__(
        self,
        cfg: Any,
        db: AsyncSession,
        redis: Any,
        registry: Any,
        capability: Any,
        bot_id: UUID,
        conid_resolver: BotConidResolver,
    ) -> None:
        self._cfg = cfg
        self._db = db
        self._redis = redis
        self._registry = registry
        self._capability = capability
        self._bot_id = bot_id
        self._conid_resolver = conid_resolver

    async def place_order(
        self,
        *,
        account_id: UUID,
        canonical_id: str,
        side: str,
        qty: Decimal,
        order_type: str,
        broker_id: str,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        tif: str = "DAY",
        algo_strategy: str | None = None,
        conid: int | None = None,
        position_effect: str = "OPEN",
    ) -> Any:
        if conid is None:
            conid = await self._conid_resolver.resolve(
                canonical_id=canonical_id,
                broker_id=broker_id,
                account_id=account_id,
            )
        return await place_order_for_bot(
            cfg=self._cfg,
            db=self._db,
            redis=self._redis,
            registry=self._registry,
            capability=self._capability,
            bot_id=self._bot_id,
            account_id=account_id,
            conid=conid,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            tif=tif,
            algo_strategy=algo_strategy,
            position_effect=position_effect,
        )

    async def cancel_order(self, *, order_id: UUID) -> None:
        from app.services.orders_service import cancel_order as _cancel

        await _cancel(db=self._db, registry=self._registry, order_id=order_id)
