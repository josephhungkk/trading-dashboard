"""Phase 21c — Advisor perf-attribution service.

Polls ``bot_advisor_decisions`` rows with ``FOR UPDATE SKIP LOCKED``, resolves
instrument via ``InstrumentResolver.find_by_canonical_id`` (Redis-cached), then
simulates next-bar-open entry / window-close exit PnL for each enabled window
(15m / 1h / 4h / EOD).  Per-decision fail-OPEN: exceptions are logged and that
row is skipped for the current poll cycle (status unchanged).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.advisor.attribution_types import AttributionSummary
from app.services.advisor.metrics import (
    advisor_attribution_bars_unavailable_total,
    advisor_attribution_decisions_processed_total,
    advisor_attribution_poll_latency_seconds,
    advisor_attribution_skipped_total,
    advisor_attribution_unresolvable_total,
)
from app.services.market_calendar import session_close_for_decision
from app.services.quotes.instrument_resolver import InstrumentResolver

_log = structlog.get_logger(__name__)

_VALID_WINDOWS = frozenset({"15m", "1h", "4h", "eod"})
_WINDOW_DELTAS: dict[str, timedelta | None] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "eod": None,
}
_POLL_BATCH_SIZE = 500
_MAX_LOOKBACK_DAYS_DEFAULT = 7
_MIN_EOD_BUFFER_MINUTES_DEFAULT = 30
_BAR_MISS_AGE = timedelta(hours=24)


class AttributionService:
    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        redis: Any,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis

    async def poll(self, db: AsyncSession) -> None:
        """APScheduler entrypoint — claim pending/partial rows and compute outcomes."""
        enabled = await self._read_config(db, "advisor_attribution", "enabled", default="true")
        if enabled.lower() != "true":
            return

        windows_cfg = await self._read_config(
            db, "advisor_attribution", "windows", default='["15m","1h","4h","eod"]'
        )
        enabled_windows: list[str] = json.loads(windows_cfg)
        max_lookback_days = int(
            await self._read_config(
                db,
                "advisor_attribution",
                "max_lookback_days",
                default=str(_MAX_LOOKBACK_DAYS_DEFAULT),
            )
        )
        min_eod_buffer = int(
            await self._read_config(
                db,
                "advisor_attribution",
                "min_eod_buffer_minutes",
                default=str(_MIN_EOD_BUFFER_MINUTES_DEFAULT),
            )
        )

        t0 = time.monotonic()
        rows = (
            (
                await db.execute(
                    text(
                        f"""
                    SELECT id, verdict, canonical_id, created_at,
                           attribution_status, attribution_windows,
                           intent,
                           outcome_15m_correct, outcome_15m_pnl,
                           outcome_1h_correct,  outcome_1h_pnl,
                           outcome_4h_correct,  outcome_4h_pnl,
                           outcome_eod_correct, outcome_eod_pnl
                    FROM bot_advisor_decisions
                    WHERE attribution_status IN ('pending','partial')
                      AND verdict IN ('approve','veto')
                      AND created_at >= now() - interval '{max_lookback_days} days'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT {_POLL_BATCH_SIZE}
                    """
                    )
                )
            )
            .mappings()
            .fetchall()
        )

        resolver = InstrumentResolver(session=db)
        updates: list[dict[str, Any]] = []

        for row in rows:
            try:
                upd = await self._process_decision(
                    row=dict(row),
                    enabled_windows=enabled_windows,
                    max_lookback_days=max_lookback_days,
                    min_eod_buffer=min_eod_buffer,
                    resolver=resolver,
                    db=db,
                )
                updates.append(upd)
            except Exception:
                _log.exception("attribution_poll_decision_error", decision_id=row["id"])

        for upd in updates:
            await db.execute(
                text(
                    """
                    UPDATE bot_advisor_decisions SET
                        attribution_status      = :status,
                        attribution_windows     = CAST(:windows AS TEXT[]),
                        outcome_15m_correct     = :c15m,
                        outcome_15m_pnl         = :p15m,
                        outcome_1h_correct      = :c1h,
                        outcome_1h_pnl          = :p1h,
                        outcome_4h_correct      = :c4h,
                        outcome_4h_pnl          = :p4h,
                        outcome_eod_correct     = :ceod,
                        outcome_eod_pnl         = :peod,
                        attribution_computed_at = now()
                    WHERE id = :id
                    """
                ),
                upd,
            )

        await db.commit()
        advisor_attribution_poll_latency_seconds.observe(time.monotonic() - t0)

    async def _process_decision(
        self,
        row: dict[str, Any],
        enabled_windows: list[str],
        max_lookback_days: int,
        min_eod_buffer: int,
        resolver: InstrumentResolver,
        db: AsyncSession,
    ) -> dict[str, Any]:
        decision_id: int = row["id"]
        verdict: str = row["verdict"]
        canonical_id: str = row["canonical_id"]
        created_at: datetime = row["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        intent: dict[str, Any] = (
            row["intent"] if isinstance(row["intent"], dict) else json.loads(row["intent"])
        )

        # CLOSE-position decisions carry no predictive signal — skip
        if intent.get("position_effect", "OPEN").upper() == "CLOSE":
            advisor_attribution_skipped_total.labels(reason="close_position").inc()
            return self._no_change_update(decision_id, row)

        instr = await resolver.find_by_canonical_id(canonical_id, redis=self._redis)
        if instr is None:
            advisor_attribution_unresolvable_total.labels(reason="no_instrument").inc()
            return {
                "id": decision_id,
                "status": "unresolvable",
                "windows": None,
                **self._null_outcomes(),
            }

        # Snapshot windows on first compute so later config changes don't alter history
        existing_windows: list[str] | None = row["attribution_windows"]
        snapshotted_windows = (
            [w for w in enabled_windows if w in _VALID_WINDOWS]
            if existing_windows is None
            else list(existing_windows)
        )

        side_sign = 1 if intent["side"].lower() == "buy" else -1
        qty = Decimal(str(intent.get("qty", "1")))
        multiplier = instr.multiplier
        now_utc = datetime.now(UTC)

        outcomes: dict[str, tuple[bool | None, Decimal | None]] = {}
        any_bars_computed = False
        any_bars_missing_old = False

        for window in snapshotted_windows:
            # Carry over already-computed windows
            col_c = f"outcome_{window.replace('h', 'h')}_correct"
            col_p = f"outcome_{window.replace('h', 'h')}_pnl"
            existing_c = row.get(col_c)
            if existing_c is not None:
                outcomes[window] = (existing_c, row.get(col_p))
                any_bars_computed = True
                continue

            # Compute window endpoint
            if window == "eod":
                try:
                    session_close = session_close_for_decision(instr.primary_exchange, created_at)
                except ValueError:
                    advisor_attribution_unresolvable_total.labels(reason="unknown_exchange").inc()
                    return {
                        "id": decision_id,
                        "status": "unresolvable",
                        "windows": snapshotted_windows,
                        **self._null_outcomes(),
                    }
                gap_minutes = (session_close - created_at).total_seconds() / 60
                if gap_minutes < min_eod_buffer:
                    advisor_attribution_skipped_total.labels(reason="eod_buffer").inc()
                    outcomes[window] = (None, None)
                    continue
                window_end = session_close
            else:
                delta = _WINDOW_DELTAS[window]
                assert delta is not None
                window_end = created_at + delta

            if now_utc < window_end:
                outcomes[window] = (None, None)
                continue

            # Fetch first open + last close in window
            bars = (
                await db.execute(
                    text(
                        "SELECT bucket_start, open, close FROM bars_1m"
                        " WHERE instrument_id = :iid"
                        "   AND bucket_start >= :t0 AND bucket_start <= :t1"
                        " ORDER BY bucket_start ASC"
                    ),
                    {"iid": instr.id, "t0": created_at, "t1": window_end + timedelta(minutes=5)},
                )
            ).fetchall()

            if not bars:
                if now_utc > window_end + _BAR_MISS_AGE:
                    any_bars_missing_old = True
                outcomes[window] = (None, None)
                continue

            entry = Decimal(str(bars[0][1]))
            exit_ = Decimal(str(bars[-1][2]))
            pnl = (exit_ - entry) * qty * multiplier * side_sign
            correct = (pnl < Decimal("0")) if verdict == "veto" else (pnl > Decimal("0"))
            outcomes[window] = (correct, pnl)
            any_bars_computed = True
            advisor_attribution_decisions_processed_total.labels(verdict=verdict).inc()

        non_null = [(c, p) for c, p in outcomes.values() if c is not None]
        all_done = len(non_null) == len(snapshotted_windows)
        expired = created_at < now_utc - timedelta(days=max_lookback_days)

        if all_done or (expired and non_null):
            new_status = "complete"
        elif any_bars_missing_old and not any_bars_computed:
            advisor_attribution_bars_unavailable_total.inc()
            new_status = "bars_unavailable"
        elif non_null:
            new_status = "partial"
        else:
            new_status = row["attribution_status"]

        return {
            "id": decision_id,
            "status": new_status,
            "windows": snapshotted_windows,
            "c15m": outcomes.get("15m", (None, None))[0],
            "p15m": outcomes.get("15m", (None, None))[1],
            "c1h": outcomes.get("1h", (None, None))[0],
            "p1h": outcomes.get("1h", (None, None))[1],
            "c4h": outcomes.get("4h", (None, None))[0],
            "p4h": outcomes.get("4h", (None, None))[1],
            "ceod": outcomes.get("eod", (None, None))[0],
            "peod": outcomes.get("eod", (None, None))[1],
        }

    def _no_change_update(self, decision_id: int, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": decision_id,
            "status": row["attribution_status"],
            "windows": row["attribution_windows"],
            "c15m": row.get("outcome_15m_correct"),
            "p15m": row.get("outcome_15m_pnl"),
            "c1h": row.get("outcome_1h_correct"),
            "p1h": row.get("outcome_1h_pnl"),
            "c4h": row.get("outcome_4h_correct"),
            "p4h": row.get("outcome_4h_pnl"),
            "ceod": row.get("outcome_eod_correct"),
            "peod": row.get("outcome_eod_pnl"),
        }

    def _null_outcomes(self) -> dict[str, None]:
        return {
            "c15m": None,
            "p15m": None,
            "c1h": None,
            "p1h": None,
            "c4h": None,
            "p4h": None,
            "ceod": None,
            "peod": None,
        }

    async def get_summary(self, bot_id: UUID, window: str, db: AsyncSession) -> AttributionSummary:
        """Return rolling accuracy stats for bot_id at given window.

        MED-3: window is allowlisted before any SQL — no f-string interpolation.
        """
        if window not in _VALID_WINDOWS:
            raise ValueError(f"invalid_window: {window!r}. Must be one of {sorted(_VALID_WINDOWS)}")

        match window:
            case "15m":
                correct_col, pnl_col = "outcome_15m_correct", "outcome_15m_pnl"
            case "1h":
                correct_col, pnl_col = "outcome_1h_correct", "outcome_1h_pnl"
            case "4h":
                correct_col, pnl_col = "outcome_4h_correct", "outcome_4h_pnl"
            case "eod":
                correct_col, pnl_col = "outcome_eod_correct", "outcome_eod_pnl"
            case _:
                raise ValueError(f"invalid_window: {window!r}")

        rows = (
            (
                await db.execute(
                    text(
                        f"""
                    SELECT verdict, attribution_status,
                           {correct_col} AS is_correct,
                           {pnl_col}     AS pnl
                    FROM bot_advisor_decisions
                    WHERE bot_id = :bid
                    """
                    ),
                    {"bid": bot_id},
                )
            )
            .mappings()
            .fetchall()
        )

        veto_correct = veto_total = 0
        approve_correct = approve_total = 0
        avoided_losses: list[Decimal] = []
        missed_gains: list[Decimal] = []
        complete = partial = pending = bars_unavailable = unresolvable = skipped = 0

        for r in rows:
            match r["attribution_status"]:
                case "complete":
                    complete += 1
                case "partial":
                    partial += 1
                case "bars_unavailable":
                    bars_unavailable += 1
                case "unresolvable":
                    unresolvable += 1
                case "pending":
                    pending += 1

            is_correct: bool | None = r["is_correct"]
            pnl: Decimal | None = r["pnl"]

            if r["attribution_status"] == "complete" and is_correct is not None:
                if r["verdict"] == "veto":
                    veto_total += 1
                    if is_correct:
                        veto_correct += 1
                        if pnl is not None:
                            avoided_losses.append(abs(pnl))
                    else:
                        if pnl is not None:
                            missed_gains.append(abs(pnl))
                elif r["verdict"] == "approve":
                    approve_total += 1
                    if is_correct:
                        approve_correct += 1

        return AttributionSummary(
            bot_id=bot_id,
            window=window,
            veto_accuracy=veto_correct / veto_total if veto_total else None,
            approve_accuracy=approve_correct / approve_total if approve_total else None,
            avg_avoided_loss_quote=(
                sum(avoided_losses, Decimal("0")) / len(avoided_losses) if avoided_losses else None
            ),
            avg_missed_gain_quote=(
                sum(missed_gains, Decimal("0")) / len(missed_gains) if missed_gains else None
            ),
            complete_count=complete,
            partial_count=partial,
            pending_count=pending,
            bars_unavailable_count=bars_unavailable,
            unresolvable_count=unresolvable,
            skipped_count=skipped,
            generated_at=datetime.now(UTC),
        )

    async def recompute(self, bot_id: UUID, since: datetime, db: AsyncSession) -> int:
        """Reset attribution for decisions on bot_id created since `since`.

        MED-8: since must be >= now()-6 months (bars_1m retention limit).
        Returns number of rows reset.
        """
        six_months_ago = datetime.now(UTC) - timedelta(days=182)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if since < six_months_ago:
            raise ValueError(
                "since_too_old: bars_1m retention is 6 months; "
                "resetting older decisions produces bars_unavailable rows"
            )

        result = await db.execute(
            text(
                """
                UPDATE bot_advisor_decisions SET
                    attribution_status      = 'pending',
                    attribution_windows     = NULL,
                    outcome_15m_correct     = NULL,
                    outcome_15m_pnl         = NULL,
                    outcome_1h_correct      = NULL,
                    outcome_1h_pnl          = NULL,
                    outcome_4h_correct      = NULL,
                    outcome_4h_pnl          = NULL,
                    outcome_eod_correct     = NULL,
                    outcome_eod_pnl         = NULL,
                    attribution_computed_at = NULL
                WHERE bot_id = :bid
                  AND created_at >= :since
                RETURNING id
                """
            ),
            {"bid": bot_id, "since": since},
        )
        count = len(result.fetchall())
        await db.commit()
        return count

    async def _read_config(self, db: AsyncSession, namespace: str, key: str, default: str) -> str:
        row = (
            await db.execute(
                text("SELECT value_json FROM app_config WHERE namespace = :ns AND key = :k"),
                {"ns": namespace, "k": key},
            )
        ).scalar_one_or_none()
        if row is None:
            return default
        parsed = json.loads(row)
        return parsed if isinstance(parsed, str) else default
