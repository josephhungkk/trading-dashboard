from __future__ import annotations

import json
from datetime import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

logger = structlog.get_logger(__name__)

_CAPS_TTL = 60  # seconds

_BROKER_TZ: dict[str, str] = {
    "ibkr": "US/Eastern",
    "schwab": "US/Eastern",
    "alpaca": "US/Eastern",
    "futu": "Asia/Hong_Kong",
}


class BotRiskCapError(Exception):
    pass


class BotRiskCapService:
    """Pre-filter before RiskService.evaluate(). Five checks."""

    def __init__(self, bot_id: UUID, redis: Any) -> None:
        self._bot_id = str(bot_id)
        self._redis = redis

    async def _get_caps(self) -> dict[str, Any] | None:
        key = f"bot:risk_caps:{self._bot_id}"
        try:
            raw = await self._redis.get(key)
            if raw is not None:
                return json.loads(raw)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("bot_risk_caps_redis_error", bot_id=self._bot_id)
        return None

    def _tz_date_key(self, account_id: UUID, broker_id: str) -> str:
        import zoneinfo

        tz_name = _BROKER_TZ.get(broker_id, "UTC")
        tz = zoneinfo.ZoneInfo(tz_name)
        today = dt.now(tz=tz).date().isoformat()
        return f"bot:daily_loss:{self._bot_id}:{account_id}:{today}"

    async def check(
        self,
        *,
        account_id: UUID,
        broker_id: str,
        asset_class: str,
        qty: Decimal,
        price: Decimal,
        side: str,
        instrument_id: int,
        db: AsyncSession,
    ) -> None:
        """Raise BotRiskCapError if any fail-CLOSED cap is breached."""
        try:
            caps = await self._get_caps()
        except Exception:
            logger.warning("bot_risk_caps_unavailable", bot_id=self._bot_id)
            return

        if caps is None:
            return

        notional = qty * price

        # 1. max_order_size — fail-CLOSED
        max_order_size = caps.get("max_order_size")
        if max_order_size is not None:
            if notional > Decimal(str(max_order_size)):
                metrics.bot_context_errors_total.labels(
                    bot_id=self._bot_id, error_type="max_order_size"
                ).inc()
                raise BotRiskCapError(f"max_order_size: notional {notional} > cap {max_order_size}")

        # 2. max_open_orders — fail-OPEN
        max_open_orders = caps.get("max_open_orders")
        if max_open_orders is not None:
            try:
                row = await db.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM bot_orders bo
                        JOIN orders o ON o.id = bo.order_id
                        WHERE bo.bot_id = :bot_id
                          AND o.status IN ('working','submitted')
                        """
                    ),
                    {"bot_id": self._bot_id},
                )
                open_count = row.scalar_one()
                if open_count >= max_open_orders:
                    raise BotRiskCapError(f"max_open_orders: {open_count} >= {max_open_orders}")
            except BotRiskCapError:
                raise
            except Exception:
                logger.warning("bot_risk_caps_open_orders_error", bot_id=self._bot_id)

        # 3. max_daily_loss — fail-CLOSED
        max_daily_loss = caps.get("max_daily_loss")
        if max_daily_loss is not None:
            try:
                daily_key = self._tz_date_key(account_id, broker_id)
                daily_raw = await self._redis.get(daily_key)
                daily_loss = Decimal(str(daily_raw)) if daily_raw is not None else Decimal(0)
                if daily_loss <= -Decimal(str(max_daily_loss)):
                    metrics.bot_daily_loss_cap_hits_total.labels(bot_id=self._bot_id).inc()
                    raise BotRiskCapError(
                        f"max_daily_loss: daily_loss={daily_loss} <= -{max_daily_loss}"
                    )
            except BotRiskCapError:
                raise
            except Exception as exc:
                logger.warning("bot_risk_caps_daily_loss_redis_error", bot_id=self._bot_id)
                raise BotRiskCapError("max_daily_loss: redis unavailable (fail-CLOSED)") from exc

        # 4. allowed_asset_classes — fail-CLOSED
        allowed = caps.get("allowed_asset_classes")
        if allowed is not None and asset_class not in allowed:
            raise BotRiskCapError(f"asset_class: {asset_class!r} not in allowed {allowed}")

        # 5. max_position_size — fail-CLOSED
        max_position_size = caps.get("max_position_size")
        if max_position_size is not None:
            try:
                row = await db.execute(
                    text(
                        """
                        SELECT COALESCE(SUM(p.market_value_native), 0)
                        FROM positions p
                        WHERE p.account_id = :aid AND p.instrument_id = :iid
                        """
                    ),
                    {"aid": account_id, "iid": instrument_id},
                )
                existing = Decimal(str(row.scalar_one()))
                projected = existing + notional
                if projected > Decimal(str(max_position_size)):
                    metrics.bot_context_errors_total.labels(
                        bot_id=self._bot_id, error_type="max_position_size"
                    ).inc()
                    raise BotRiskCapError(
                        f"max_position_size: projected {projected} > cap {max_position_size}"
                    )
            except BotRiskCapError:
                raise
            except Exception as exc:
                logger.warning("bot_risk_caps_position_size_error", bot_id=self._bot_id)
                raise BotRiskCapError("max_position_size: db unavailable (fail-CLOSED)") from exc
