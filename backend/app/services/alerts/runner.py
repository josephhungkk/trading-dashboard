"""Phase 11b chunk-B-close: lifespan-side glue between bars_1m_insert NOTIFY,
the AlertsEvaluator queue, and the DeliveryDispatcher.

This module owns the impure side: SQL reads (rule load + symbol resolution +
fire writes), Redis pubsub subscription, dormancy bookkeeping. The evaluator
remains pure (queue + index + producer-side debounce).

Wired in ``app/main.py`` lifespan.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.alerts.delivery import AlertFire, DeliveryDispatcher
from app.services.alerts.evaluator import AlertsEvaluator
from app.services.alerts.predicates import evaluate, referenced_symbols

log = structlog.get_logger(__name__)

_BARS_1M_CHANNEL = "bars_1m_insert"
_DORMANCY_THRESHOLD = 10  # spec §6 fail-isolation cutoff


class AlertsBarsRedisSubscriber:
    """Subscribes to the Redis ``bars_1m_insert`` channel and feeds the
    evaluator. The bridge republishes Postgres NOTIFY payloads here, so
    this subscriber only needs to drive ``evaluator._on_bars_1m_notify``.
    """

    def __init__(
        self,
        *,
        redis: Any,
        evaluator: AlertsEvaluator,
        resolve_symbol: Callable[[int], str | None],
    ) -> None:
        self._redis = redis
        self._evaluator = evaluator
        self._resolve_symbol = resolve_symbol
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def _consume(self) -> None:
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(_BARS_1M_CHANNEL)
            while not self._stopping:
                try:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # In-memory test fakes may not implement get_message;
                    # real Redis transient errors should also not abort the
                    # subscriber. Back off briefly and retry.
                    await asyncio.sleep(0.5)
                    continue
                if msg is None:
                    # Yield even when the underlying get_message returns
                    # synchronously (test fakes, busy Redis) so cancel()
                    # can be delivered.
                    await asyncio.sleep(0)
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                if not isinstance(data, str):
                    continue
                try:
                    await self._evaluator._on_bars_1m_notify(
                        data, resolve_symbol=self._resolve_symbol
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("alerts_runner.bars_1m_dispatch_failed")
        finally:
            try:
                await pubsub.unsubscribe(_BARS_1M_CHANNEL)
                await pubsub.aclose()
            except Exception:
                pass

    def start(self) -> None:
        self._stopping = False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


async def load_active_rules(db: AsyncSession) -> list[tuple[int, dict[str, Any], list[str], str]]:
    """Return (rule_id, predicate_json, delivery_channels, jwt_subject) for every
    rule whose ``status='active'`` (i.e. confirmed and not dormant). Used by the
    evaluator to rebuild its inverted index on startup + on rule mutations.
    """
    result = await db.execute(
        text(
            "SELECT id, predicate_json, delivery_channels, jwt_subject "
            "FROM alerts WHERE status = 'active' AND deleted_at IS NULL"
        )
    )
    return [(row.id, row.predicate_json, row.delivery_channels, row.jwt_subject) for row in result]


async def resolve_symbol_for_instrument(db: AsyncSession, *, instrument_id: int) -> str | None:
    """Map an ``instruments.id`` back to a raw symbol the predicate can match.

    Predicates store user-typed symbols (e.g. ``AAPL``). The bars_1m_insert
    NOTIFY payload carries ``instrument_id``. Look up the first matching
    ``symbol_aliases.raw_symbol`` — for stocks there is one per source and
    the user's predicate symbol is normally the same as the broker raw_symbol.
    """
    row = (
        await db.execute(
            text("SELECT raw_symbol FROM symbol_aliases WHERE instrument_id = :i LIMIT 1"),
            {"i": instrument_id},
        )
    ).first()
    return row.raw_symbol if row is not None else None


async def build_evaluator_state(db: AsyncSession, *, symbol: str) -> dict[str, Any]:
    """Build the predicate evaluation ``state`` for one symbol.

    Pulls the last 50 bars_1m bars (sufficient for ma_cross / pct_change_window
    / volume_spike with windows up to 50m). The most recent ``close`` becomes
    ``state['prices'][symbol]``; the full series is ``state['bars'][symbol]``.
    """
    rows = (
        await db.execute(
            text(
                "SELECT bucket_start AS ts, close, volume "
                "FROM bars_1m JOIN symbol_aliases USING (instrument_id) "
                "WHERE raw_symbol = :s ORDER BY bucket_start DESC LIMIT 50"
            ),
            {"s": symbol},
        )
    ).all()
    bars = [
        {"ts": r.ts, "close": float(r.close), "volume": float(r.volume)} for r in reversed(rows)
    ]
    last_close = bars[-1]["close"] if bars else 0.0
    return {
        "prices": {symbol: last_close},
        "bars": {symbol: bars},
    }


async def record_fire_and_dispatch(
    db: AsyncSession,
    *,
    dispatcher: DeliveryDispatcher,
    rule_id: int,
    jwt_subject: str,
    user_label: str,
    predicate_json: dict[str, Any],
    delivery_channels: list[str],
    state: dict[str, Any],
    symbol: str,
) -> None:
    """Write alert_fires + alert_fire_context, then fan-out to channels.

    Per spec §8, delivery dispatch is fire-and-forget from the evaluator's
    POV: per-channel failures are isolated inside ``fan_out``; this function
    awaits the fan_out only to keep the worker's queue ordering deterministic.
    """
    evaluated_close = state.get("prices", {}).get(symbol)
    fired_row = (
        await db.execute(
            text(
                "INSERT INTO alert_fires (alert_id, jwt_subject, fired_at, verdict) "
                "VALUES (:r, :s, now(), 'true') RETURNING id, fired_at"
            ),
            {"r": rule_id, "s": jwt_subject},
        )
    ).first()
    fire_id = fired_row.id if fired_row is not None else 0
    await db.execute(
        text(
            "INSERT INTO alert_fire_context (alert_id, fired_at, evaluated_values) "
            "VALUES (:r, now(), CAST(:v AS jsonb))"
        ),
        {"r": rule_id, "v": json.dumps({"close": evaluated_close, "symbol": symbol})},
    )
    await db.commit()
    fire = AlertFire(
        fire_id=fire_id,
        alert_id=rule_id,
        jwt_subject=jwt_subject,
        verdict="true",
        evaluated_values={"close": evaluated_close, "symbol": symbol},
        user_label=user_label,
        fired_at_iso=fired_row.fired_at.isoformat() if fired_row is not None else "",
    )
    await dispatcher.fan_out(fire, channel_keys=delivery_channels)


async def mark_eval_error(db: AsyncSession, *, rule_id: int) -> None:
    """Increment ``consecutive_eval_errors`` and tip into ``dormant`` at the
    spec §6 threshold (10 consecutive errors)."""
    await db.execute(
        text(
            "UPDATE alerts SET consecutive_eval_errors = consecutive_eval_errors + 1, "
            "status = CASE WHEN consecutive_eval_errors + 1 >= :t THEN 'dormant' ELSE status END, "
            "dormancy_reason = CASE WHEN consecutive_eval_errors + 1 >= :t "
            "  THEN 'eval_errors' ELSE dormancy_reason END "
            "WHERE id = :r"
        ),
        {"r": rule_id, "t": _DORMANCY_THRESHOLD},
    )
    await db.commit()


def build_process_callback(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: DeliveryDispatcher,
) -> Callable[[dict[str, object]], Awaitable[None]]:
    """Return a ``process(item)`` closure suitable for
    ``AlertsEvaluator.start_worker``.

    Each invocation opens its own DB session via ``session_factory`` so the
    worker loop does not hold a long-lived transaction.
    """

    async def process(item: dict[str, object]) -> None:
        raw_rule_id = item.get("rule_id")
        raw_symbol = item.get("symbol")
        if not isinstance(raw_rule_id, int) or not isinstance(raw_symbol, str):
            return
        rule_id: int = raw_rule_id
        symbol: str = raw_symbol
        async with session_factory() as db:
            try:
                row = (
                    await db.execute(
                        text(
                            "SELECT id, jwt_subject, user_label, predicate_json, "
                            "delivery_channels, status FROM alerts "
                            "WHERE id = :r AND deleted_at IS NULL"
                        ),
                        {"r": rule_id},
                    )
                ).first()
                if row is None or row.status != "active":
                    return
                state = await build_evaluator_state(db, symbol=symbol)
                if not state["bars"].get(symbol):
                    # No bars cached yet — nothing to evaluate.
                    return
                try:
                    fired = evaluate(row.predicate_json, state)
                except Exception:
                    await mark_eval_error(db, rule_id=rule_id)
                    return
                if not fired:
                    # Reset error counter on a successful (non-firing)
                    # evaluation — spec §6 fail-isolation says dormancy is
                    # CONSECUTIVE errors only.
                    await db.execute(
                        text(
                            "UPDATE alerts SET consecutive_eval_errors = 0 "
                            "WHERE id = :r AND consecutive_eval_errors > 0"
                        ),
                        {"r": rule_id},
                    )
                    await db.commit()
                    return
                await record_fire_and_dispatch(
                    db,
                    dispatcher=dispatcher,
                    rule_id=rule_id,
                    jwt_subject=row.jwt_subject,
                    user_label=row.user_label,
                    predicate_json=row.predicate_json,
                    delivery_channels=row.delivery_channels,
                    state=state,
                    symbol=symbol,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("alerts_runner.process_failed", rule_id=rule_id)

    return process


def build_index_rebuild_callback(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    evaluator: AlertsEvaluator,
) -> Callable[[], Awaitable[None]]:
    """Return the ``_rebuild_fn`` for ``AlertsEvaluator`` — repopulates the
    inverted index from the database."""

    async def rebuild() -> None:
        async with session_factory() as db:
            rules = await load_active_rules(db)
        evaluator.index._symbol_to_rules.clear()
        evaluator.index._rule_to_symbols.clear()
        for rule_id, predicate_json, _channels, _subject in rules:
            try:
                symbols = referenced_symbols(predicate_json)
            except Exception:
                continue
            if symbols:
                evaluator.index.add(rule_id=rule_id, symbols=symbols)

    return rebuild


async def run_capability_invalidation_listener(
    redis: Any,
    *,
    on_invalidate: Callable[[], Awaitable[None]],
) -> None:
    """Listen on ``app_config:invalidate:alert_capabilities`` and call
    ``on_invalidate`` on every fire. Mirrors 11a's pubsub-listener idiom.
    """
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe("app_config:invalidate:alert_capabilities")
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue
            try:
                await on_invalidate()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("alerts_runner.capability_invalidate_failed")
    finally:
        try:
            await pubsub.unsubscribe("app_config:invalidate:alert_capabilities")
            await pubsub.aclose()
        except Exception:
            pass


__all__ = [
    "AlertsBarsRedisSubscriber",
    "build_evaluator_state",
    "build_index_rebuild_callback",
    "build_process_callback",
    "load_active_rules",
    "mark_eval_error",
    "record_fire_and_dispatch",
    "resolve_symbol_for_instrument",
    "run_capability_invalidation_listener",
]


def _make_dormancy_threshold() -> int:
    """Expose threshold for tests."""
    return _DORMANCY_THRESHOLD
