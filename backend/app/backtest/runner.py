from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.bar_feed import BarFeed
from app.backtest.commission import CommissionSchedule
from app.backtest.context import BacktestContext
from app.backtest.fill_simulator import FillSimulator
from app.backtest.metrics import ClosedTrade, MetricsComputer
from app.backtest.progress import ProgressPublisher
from app.bot.base import FillEvent
from app.bot.sandbox import DenylistFinder, extract_params_schema

logger = structlog.get_logger(__name__)
_STRATEGIES_DIR = Path("/strategies")


class BacktestRunner:
    def __init__(self, *, db: AsyncSession, redis: Any, semaphore: asyncio.Semaphore) -> None:
        self._db = db
        self._redis = redis
        self._semaphore = semaphore

    def run(self, backtest_id: str) -> None:
        asyncio.run(self._replay(backtest_id))

    async def _replay(self, backtest_id: str) -> None:
        async with self._semaphore:
            progress = ProgressPublisher(redis=self._redis, backtest_id=backtest_id)
            try:
                await self._run_inner(backtest_id, progress)
            except Exception as exc:
                logger.exception("backtest_runner.failed", backtest_id=backtest_id)
                await self._set_failed(backtest_id, str(exc))
                await progress.publish_failed(str(exc))

    async def _run_inner(self, backtest_id: str, progress: ProgressPublisher) -> None:
        row = await self._load_and_start(backtest_id)

        strategy_path = _STRATEGIES_DIR / row["strategy_file"]
        schema = extract_params_schema(str(strategy_path))
        schema_hash = (
            hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
            if schema
            else ""
        )
        if row.get("params_schema_hash") and row["params_schema_hash"] != schema_hash:
            raise ValueError("params_schema_drift")

        instrument_id = await self._resolve_instrument(row["canonical_id"])
        feed = BarFeed(db=self._db, redis=self._redis)
        bars = await feed.load(
            canonical_id=row["canonical_id"],
            timeframe=row["timeframe"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            bars_source=row["bars_source"],
            instrument_id=instrument_id,
            upload_id=row.get("upload_id"),
        )

        commission = CommissionSchedule(row["commission_cfg"])
        sim = FillSimulator(
            slippage_bps=Decimal(str(row["slippage_bps"])) if row.get("slippage_bps") else None,
            slippage_atr_pct=(
                Decimal(str(row["slippage_atr_pct"])) if row.get("slippage_atr_pct") else None
            ),
            commission=commission,
        )

        finder = DenylistFinder(bot_id=backtest_id)
        sys.meta_path.insert(0, finder)
        try:
            spec = importlib.util.spec_from_file_location("_strategy", strategy_path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        finally:
            sys.meta_path.remove(finder)

        strategy_cls = next(
            (
                v
                for v in vars(mod).values()
                if isinstance(v, type) and hasattr(v, "on_bar") and v.__name__ != "BaseStrategy"
            ),
            None,
        )
        if strategy_cls is None:
            raise ValueError("no_strategy_class_found")

        ctx = BacktestContext(simulator=sim)
        strategy = strategy_cls()
        strategy.params = row["params_snapshot"]
        strategy.ctx = ctx

        result = strategy.on_start()
        if inspect.isawaitable(result):
            await result

        fills: list[FillEvent] = []
        total = len(bars)
        cadence = max(1, total // 200)

        for i, bar in enumerate(bars):
            if await self._redis.exists(f"backtest:cancel:{backtest_id}"):
                raise ValueError("cancelled by user")

            sim.process_pending_orders(bar, on_fill=fills.append)

            bar_result = strategy.on_bar(bar)
            if inspect.isawaitable(bar_result):
                await bar_result

            if i % cadence == 0:
                await progress.publish(i, total, len(fills), bar.ts.isoformat())

        result = strategy.on_stop()
        if inspect.isawaitable(result):
            await result

        forced_fill_ids: set = set()
        if bars:
            pre_count = len(fills)
            sim.force_close_open_positions(bars[-1], on_fill=fills.append)
            forced_fill_ids = {f.order_id for f in fills[pre_count:]}

        broker_id = row["commission_cfg"].get("active_broker_id", "ibkr")
        closed_trades = self._pair_fills(fills, forced_fill_ids, commission, broker_id)
        bar_ts = [b.ts for b in bars]
        mc = MetricsComputer(exchange="NYSE")
        report = mc.compute(closed_trades, bar_ts)

        await self._set_done(backtest_id, report)
        await progress.publish_done(report)

    def _pair_fills(
        self,
        fills: list[FillEvent],
        forced_ids: set,
        commission: CommissionSchedule,
        broker_id: str,
    ) -> list[ClosedTrade]:
        open_longs: list[FillEvent] = []
        open_shorts: list[FillEvent] = []
        closed: list[ClosedTrade] = []
        for fill in fills:
            if fill.side == "BUY":
                if open_shorts:
                    entry = open_shorts.pop(0)
                    entry_comm = commission.compute(broker_id, qty=entry.qty)
                    exit_comm = commission.compute(broker_id, qty=fill.qty)
                    gross = (entry.price - fill.price) * fill.qty
                    total_comm = entry_comm + exit_comm
                    closed.append(
                        ClosedTrade(
                            canonical_id=fill.canonical_id,
                            side="SELL",
                            qty=fill.qty,
                            entry_price=entry.price,
                            exit_price=fill.price,
                            entry_slippage=Decimal("0"),
                            exit_slippage=Decimal("0"),
                            commission=total_comm,
                            pnl=gross - total_comm,
                            forced_close=fill.order_id in forced_ids,
                            opened_at=entry.filled_at,
                            closed_at=fill.filled_at,
                        )
                    )
                else:
                    open_longs.append(fill)
            elif fill.side == "SELL":
                if open_longs:
                    entry = open_longs.pop(0)
                    entry_comm = commission.compute(broker_id, qty=entry.qty)
                    exit_comm = commission.compute(broker_id, qty=fill.qty)
                    gross = (fill.price - entry.price) * fill.qty
                    total_comm = entry_comm + exit_comm
                    closed.append(
                        ClosedTrade(
                            canonical_id=fill.canonical_id,
                            side="BUY",
                            qty=fill.qty,
                            entry_price=entry.price,
                            exit_price=fill.price,
                            entry_slippage=Decimal("0"),
                            exit_slippage=Decimal("0"),
                            commission=total_comm,
                            pnl=gross - total_comm,
                            forced_close=fill.order_id in forced_ids,
                            opened_at=entry.filled_at,
                            closed_at=fill.filled_at,
                        )
                    )
                else:
                    open_shorts.append(fill)
        return closed

    async def _load_and_start(self, backtest_id: str) -> dict:
        result = await self._db.execute(
            text("SELECT * FROM backtests WHERE id = :id"), {"id": backtest_id}
        )
        row = dict(result.mappings().one())
        # Atomic CAS: only start if still queued — prevents double-start races
        update = await self._db.execute(
            text(
                "UPDATE backtests SET status='running', started_at=now()"
                " WHERE id=:id AND status='queued' RETURNING id"
            ),
            {"id": backtest_id},
        )
        if update.one_or_none() is None:
            raise ValueError(f"backtest_not_queued: {backtest_id}")
        await self._db.commit()
        return row

    async def _resolve_instrument(self, canonical_id: str) -> int:
        result = await self._db.execute(
            text("SELECT id FROM instruments WHERE canonical_id = :cid LIMIT 1"),
            {"cid": canonical_id},
        )
        row = result.one_or_none()
        if row is None:
            raise ValueError(f"instrument_not_found: {canonical_id}")
        return int(row[0])

    async def _set_done(self, backtest_id: str, report: dict) -> None:
        await self._db.execute(
            text(
                """UPDATE backtests SET status='done', report=:r, progress_pct=100,
                    completed_at=now() WHERE id=:id"""
            ),
            {"r": json.dumps(report, default=str), "id": backtest_id},
        )
        await self._db.commit()

    async def _set_failed(self, backtest_id: str, error_msg: str) -> None:
        await self._db.execute(
            text("UPDATE backtests SET status='failed', error_msg=:e WHERE id=:id"),
            {"e": error_msg, "id": backtest_id},
        )
        await self._db.commit()
