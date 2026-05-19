from __future__ import annotations

import weakref
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.orders_facade import BotOrdersFacade
from app.bot.risk_caps import BotRiskCapService
from app.services.advisor.auto_pause import AutoPauseService
from app.services.advisor.service import AdvisorService
from app.services.advisor.types import (
    AdvisorConfig,
    AdvisorVerdict,
    AdvisorVetoedResult,
    OrderIntent,
)

logger = structlog.get_logger(__name__)

_MODE_CACHE_TTL = 60


class BotAccountError(Exception):
    pass


class BotModeMismatchError(Exception):
    pass


class BotContext:
    """Strategy-facing surface. All side-effects go through here."""

    def __init__(
        self,
        *,
        bot_id: UUID,
        run_id: UUID,
        accounts: list[UUID],
        mode: Literal["paper", "live"],
        facade: BotOrdersFacade,
        risk_cap_svc: BotRiskCapService,
        db: AsyncSession,
        redis: Any,
        advisor: AdvisorService | None = None,
        advisor_config: dict[str, Any] | None = None,
        account_overrides: dict[str, dict[str, Any]] | None = None,
        bar_aggregator: Any = None,
    ) -> None:
        self.bot_id = bot_id
        self.run_id = run_id
        self.accounts = accounts
        self.mode = mode
        self._facade = facade
        self._risk_cap_svc = risk_cap_svc
        self._db = db
        self._redis = redis
        self._advisor = advisor
        self._advisor_config = (
            AdvisorConfig.from_jsonb_dict(advisor_config) if advisor_config else AdvisorConfig()
        )
        self._account_overrides: dict[str, dict[str, Any]] = account_overrides or {}
        self._strategy_ref: weakref.ref[Any] | None = None
        self._strategy_params: dict[str, Any] = {}
        self._bar_aggregator = bar_aggregator

    def set_strategy_ref(self, strategy: Any) -> None:
        self._strategy_ref = weakref.ref(strategy)

    def set_strategy_params(self, params: dict[str, Any]) -> None:
        self._strategy_params = params

    def _resolve_effective_advisor_config(self, account_id: UUID) -> AdvisorConfig:
        override_raw: dict[str, Any] | None = self._account_overrides.get(str(account_id))
        if override_raw is not None:
            return AdvisorConfig.from_jsonb_dict(override_raw)
        return self._advisor_config

    async def subscribe(self, canonical_id: str) -> None:
        if self._bar_aggregator is not None:
            await self._bar_aggregator.add_symbol(canonical_id)

    async def _verify_account_mode(self, account_id: UUID) -> None:
        cache_key = f"bot:acct_mode:{account_id}"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            actual_mode = cached.decode() if isinstance(cached, bytes) else cached
        else:
            row = await self._db.execute(
                text("SELECT mode FROM broker_accounts WHERE id = :aid"),
                {"aid": account_id},
            )
            actual_mode = row.scalar_one_or_none() or "paper"
            await self._redis.setex(cache_key, _MODE_CACHE_TTL, actual_mode)

        if actual_mode != self.mode:
            raise BotModeMismatchError(
                f"bot mode={self.mode!r} but account {account_id} mode={actual_mode!r}"
            )

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
    ) -> Any:
        if account_id not in self.accounts:
            raise BotAccountError(f"account_id {account_id} is not in bot.accounts")
        await self._verify_account_mode(account_id)

        instr_row = await self._db.execute(
            text("SELECT id, asset_class FROM instruments WHERE canonical_id = :cid LIMIT 1"),
            {"cid": canonical_id},
        )
        instr = instr_row.first()
        instrument_id = instr[0] if instr is not None else 0
        asset_class = instr[1] if instr is not None else "STOCK"

        price = limit_price or Decimal("0")
        await self._risk_cap_svc.check(
            account_id=account_id,
            broker_id=broker_id,
            asset_class=asset_class,
            qty=qty,
            price=price,
            side=side,
            instrument_id=instrument_id,
            db=self._db,
        )

        decision_id: int | None = None
        verdict: AdvisorVerdict | None = None
        if self._advisor is not None:
            effective_config = self._resolve_effective_advisor_config(account_id)
            intent = OrderIntent(
                canonical_id=canonical_id,
                side=side,
                qty=str(qty),
                order_type=order_type,
                limit_price=str(limit_price) if limit_price is not None else None,
                stop_price=str(stop_price) if stop_price is not None else None,
                tif=tif,
                algo_strategy=algo_strategy,
                position_effect=position_effect,
                broker_id=broker_id,
                account_id=account_id,
            )
            review_result: tuple[AdvisorVerdict, int | None] = await self._advisor.review(
                bot_id=self.bot_id,
                run_id=self.run_id,
                account_id=account_id,
                intent=intent,
                strategy_params=self._strategy_params,
                effective_config=effective_config,
                db=self._db,
            )
            verdict, decision_id = review_result
            if verdict.action == "veto":
                strategy = self._strategy_ref() if self._strategy_ref is not None else None
                if strategy is not None:
                    try:
                        strategy.on_advisor_reject(intent, verdict)
                    except Exception:
                        logger.warning(
                            "on_advisor_reject_hook_error",
                            bot_id=str(self.bot_id),
                            strategy_class=type(strategy).__name__,
                            exc_info=True,
                        )
                await AutoPauseService(self._redis).record_reject(
                    bot_id=self.bot_id, config=effective_config
                )
                return AdvisorVetoedResult(
                    decision_id=decision_id,
                    reasoning=verdict.reasoning,
                    advice_tags=verdict.advice_tags,
                )

        result = await self._facade.place_order(
            account_id=account_id,
            canonical_id=canonical_id,
            side=side,
            qty=qty,
            order_type=order_type,
            broker_id=broker_id,
            limit_price=limit_price,
            stop_price=stop_price,
            tif=tif,
            algo_strategy=algo_strategy,
            conid=conid,
            position_effect=position_effect,
        )

        await self._db.execute(
            text(
                "INSERT INTO bot_orders"
                " (order_id, bot_id, account_id, placed_at, advisor_decision_id)"
                " VALUES (:oid, :bid, :aid, now(), :adv_id)"
            ),
            {
                "oid": result.order_id,
                "bid": self.bot_id,
                "aid": account_id,
                "adv_id": decision_id
                if self._advisor is not None and verdict is not None and verdict.action == "approve"
                else None,
            },
        )
        await self._db.commit()
        return result

    async def cancel_order(self, order_id: UUID) -> None:
        row = await self._db.execute(
            text("SELECT order_id FROM bot_orders WHERE order_id = :oid AND bot_id = :bid"),
            {"oid": order_id, "bid": self.bot_id},
        )
        if row.scalar_one_or_none() is None:
            raise BotAccountError(f"order {order_id} not found in bot_orders for this bot")
        await self._facade.cancel_order(order_id=order_id)

    async def get_positions(self, account_id: UUID) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            text("SELECT * FROM positions WHERE account_id = :aid"),
            {"aid": account_id},
        )
        return [dict(r._mapping) for r in rows.fetchall()]

    async def get_open_orders(self, account_id: UUID) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            text(
                "SELECT * FROM orders WHERE account_id = :aid AND status IN ('working','submitted')"
            ),
            {"aid": account_id},
        )
        return [dict(r._mapping) for r in rows.fetchall()]

    async def get_fills_today(self, account_id: UUID) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            text(
                """
                SELECT f.* FROM order_fills f
                JOIN orders o ON o.id = f.order_id
                WHERE o.account_id = :aid AND f.filled_at >= CURRENT_DATE
                """
            ),
            {"aid": account_id},
        )
        return [dict(r._mapping) for r in rows.fetchall()]
