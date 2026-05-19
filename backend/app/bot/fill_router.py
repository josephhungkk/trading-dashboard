from __future__ import annotations

import datetime as _dt
import json
import zoneinfo
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

logger = structlog.get_logger(__name__)

_BROKER_TZ: dict[str, str] = {
    "ibkr": "US/Eastern",
    "schwab": "US/Eastern",
    "alpaca": "US/Eastern",
    "futu": "Asia/Hong_Kong",
}


class BotFillRouter:
    """Asyncio task in backend. Routes order:fill events to bot:fill:{bot_id} pubsub."""

    def __init__(self, db: AsyncSession, redis: Any) -> None:
        self._db = db
        self._redis = redis

    async def handle_event(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except Exception:
            return

        if event.get("type") != "order:fill":
            return

        order_id_str = event.get("order_id")
        if not order_id_str:
            return

        try:
            order_id = UUID(order_id_str)
        except ValueError:
            return

        row = await self._db.execute(
            text("SELECT bot_id FROM bot_orders WHERE order_id = :oid"),
            {"oid": order_id},
        )
        result = row.first()
        if result is None:
            return

        bot_id = result[0]
        account_id = UUID(event["account_id"])
        side = event.get("side", "buy")

        fill_payload = json.dumps(event)
        await self._redis.publish(f"bot:fill:{bot_id}", fill_payload)
        metrics.bot_fill_events_total.labels(bot_id=str(bot_id), side=side).inc()

        await self._update_daily_loss(bot_id=bot_id, account_id=account_id)

    async def _update_daily_loss(self, bot_id: Any, account_id: UUID) -> None:
        try:
            pnl_row = await self._db.execute(
                text(
                    """
                    SELECT COALESCE(unrealised_pnl, 0) + COALESCE(realised_pnl, 0)
                    FROM v_account_intraday_pnl
                    WHERE account_id = :aid
                    """
                ),
                {"aid": account_id},
            )
            pnl = pnl_row.scalar_one_or_none()
            if pnl is None:
                return

            broker_row = await self._db.execute(
                text("SELECT broker_id FROM broker_accounts WHERE id = :aid"),
                {"aid": account_id},
            )
            broker_id = broker_row.scalar_one_or_none() or "ibkr"
            tz_name = _BROKER_TZ.get(broker_id, "UTC")
            tz = zoneinfo.ZoneInfo(tz_name)
            today = datetime.now(tz=tz).date().isoformat()

            key = f"bot:daily_loss:{bot_id}:{account_id}:{today}"
            now_local = _dt.datetime.now(tz=tz)
            midnight = (now_local + _dt.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            ttl = int((midnight - now_local).total_seconds())
            await self._redis.setex(key, ttl, str(pnl))
        except Exception:
            logger.exception(
                "bot_fill_router_daily_loss_error",
                bot_id=str(bot_id),
                account_id=str(account_id),
            )

    async def run(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("orders:events:fleet")
        logger.info("bot_fill_router_started")
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            await self.handle_event(data)
