from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.param_tuner.metrics as m
from app.services.ai.capabilities import AICapability
from app.services.ai.router import AICompletionClient
from app.services.ai.types import CompletionRequest
from app.services.param_tuner.context_builder import TunerContextBuilder
from app.services.param_tuner.types import (
    BacktestResultSnapshot,
    CandidateListResponse,
    ParamCandidate,
    SupervisorRestartError,
    TunerAlreadyActiveError,
    TunerCostCeilingError,
    TunerTrigger,
)

logger = structlog.get_logger(__name__)

MAX_CANDIDATES = 5
DEFAULT_QUEUE_DEPTH_LIMIT = 20
COST_ESTIMATE_PER_TRIGGER = 0.10


class BacktestSubmitter:
    def __init__(self, db_factory: async_sessionmaker[AsyncSession]) -> None:
        self.db_factory = db_factory

    async def submit(self, bot_id: UUID, params: dict[str, Any]) -> UUID:
        async with self.db_factory() as db:
            backtest_result = await db.execute(
                text("""
                    SELECT slippage_bps, commission_per_share, canonical_id, timeframe
                    FROM backtests
                    WHERE bot_id=:bid AND status='done'
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"bid": str(bot_id)},
            )
            backtest_row: dict[str, Any] = dict(backtest_result.mappings().first() or {})

            config_result = await db.execute(
                text("""
                    SELECT value FROM app_config
                    WHERE key='param_tuner/backtest_window_days'
                """)
            )
            config_row = config_result.mappings().first()
            window_days = int(config_row["value"]) if config_row else 90
            params_schema_hash = hashlib.sha256(
                json.dumps(params, sort_keys=True, default=str).encode()
            ).hexdigest()

            insert_result = await db.execute(
                text("""
                    INSERT INTO backtests (
                        bot_id, status, params_schema_hash, bars_source,
                        slippage_bps, commission_per_share, canonical_id,
                        timeframe, window_days
                    )
                    VALUES (
                        :bid, 'queued', :params_schema_hash, 'param_tuner',
                        :slippage_bps, :commission_per_share, :canonical_id,
                        :timeframe, :window_days
                    )
                    RETURNING id
                """),
                {
                    "bid": str(bot_id),
                    "params_schema_hash": params_schema_hash,
                    "slippage_bps": backtest_row.get("slippage_bps"),
                    "commission_per_share": backtest_row.get("commission_per_share"),
                    "canonical_id": backtest_row.get("canonical_id"),
                    "timeframe": backtest_row.get("timeframe"),
                    "window_days": window_days,
                },
            )
            backtest_id = insert_result.scalar_one()
            await db.commit()
            return UUID(str(backtest_id))

    async def queue_depth(self) -> int:
        async with self.db_factory() as db:
            result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM backtests
                    WHERE status IN ('queued','running')
                """)
            )
            return int(result.scalar_one() or 0)


class ParamTunerService:
    def __init__(
        self,
        ai_client: AICompletionClient,
        redis: Any,
        db_factory: async_sessionmaker[AsyncSession],
        backtest_submitter: BacktestSubmitter,
    ) -> None:
        self.ai_client = ai_client
        self.redis = redis
        self.db_factory = db_factory
        self.backtest_submitter = backtest_submitter

    async def trigger(
        self,
        bot_id: UUID,
        triggered_by: TunerTrigger,
        db: AsyncSession,
    ) -> UUID:
        if triggered_by == TunerTrigger.SCHEDULED:
            scheduled_result = await db.execute(
                text("""
                    SELECT value FROM app_config
                    WHERE key='param_tuner/scheduled_enabled'
                """)
            )
            scheduled_row = scheduled_result.mappings().first()
            if scheduled_row and scheduled_row["value"] == "false":
                raise TunerAlreadyActiveError("scheduled_disabled")

        bot_result = await db.execute(
            text("""
                SELECT id, strategy_params, strategy_schema, is_shadow, deleted_at
                FROM bots
                WHERE id=:bid AND deleted_at IS NULL
            """),
            {"bid": str(bot_id)},
        )
        _bot_row = bot_result.mappings().first()
        if not _bot_row:
            raise ValueError("bot_not_found")
        bot_row: dict[str, Any] = dict(_bot_row)
        if bot_row["is_shadow"]:
            raise ValueError("cannot_tune_shadow_bot")
        if bot_row["strategy_schema"] is None:
            raise ValueError("strategy_schema_missing_cannot_tune")

        active_result = await db.execute(
            text("""
                SELECT id FROM bot_param_suggestions
                WHERE bot_id=:bid AND status IN ('pending','backtesting','ranked')
                FOR UPDATE SKIP LOCKED
            """),
            {"bid": str(bot_id)},
        )
        if active_result.first():
            raise TunerAlreadyActiveError("already_active")

        utc_date = datetime.now(UTC).date().isoformat()
        key = f"param_tuner:cost_pending:{utc_date}"
        reservation_ok = True
        try:
            post_increment = await self.redis.incrbyfloat(key, COST_ESTIMATE_PER_TRIGGER)
            await self.redis.expire(key, 86400)
            committed_cost = await self._get_committed_cost(bot_id, db)
            ceiling = await self._get_cost_ceiling(db)
            if committed_cost + float(post_increment) > ceiling:
                await self.redis.incrbyfloat(key, -COST_ESTIMATE_PER_TRIGGER)
                raise TunerCostCeilingError("cost_ceiling_exceeded")
        except TunerCostCeilingError:
            raise
        except Exception:
            logger.warning("tuner_cost_reservation_failed", bot_id=str(bot_id))
            reservation_ok = False

        context_builder = TunerContextBuilder()
        fenced_payload, token_est = await context_builder.build(bot_id, bot_row, db)
        prompt_hash = hashlib.sha256(fenced_payload.encode()).hexdigest()[:16]

        cloud_result = await db.execute(
            text("""
                SELECT value FROM app_config
                WHERE key='param_tuner/allow_cloud_reasoning'
            """)
        )
        cloud_row = cloud_result.mappings().first()
        capability = (
            AICapability.REASONING
            if cloud_row and cloud_row["value"] == "true"
            else AICapability.LOCAL_ONLY
        )

        t0 = time.monotonic()
        req = CompletionRequest(
            messages=[{"role": "user", "content": fenced_payload}],
            capability=capability,
            caller=f"param_tuner:bot:{bot_id}",
            max_tokens=min(token_est + 512, 4096),
        )
        try:
            response = await self.ai_client.complete(req, jwt_subject=f"system:bot:{bot_id}")
            if reservation_ok:
                await self.redis.incrbyfloat(key, -COST_ESTIMATE_PER_TRIGGER)
        except Exception:
            if reservation_ok:
                await self.redis.incrbyfloat(key, -COST_ESTIMATE_PER_TRIGGER)
            m.param_tuner_trigger_failures_total.labels(reason="ai_error").inc()
            raise
        m.param_tuner_ai_latency_seconds.observe(time.monotonic() - t0)

        try:
            parsed = CandidateListResponse.model_validate_json(response.text)
        except ValidationError, Exception:
            candidates_raw: list[dict[str, Any]] = []
        else:
            candidates_raw = parsed.candidates[:MAX_CANDIDATES]

        valid_candidates: list[ParamCandidate] = []
        for candidate in candidates_raw:
            params = candidate.get("params", candidate) if isinstance(candidate, dict) else {}
            if self._validate_candidate(params, bot_row["strategy_schema"], {}):
                valid_candidates.append(ParamCandidate(params=params))
            else:
                m.param_tuner_invalid_candidates_total.labels(reason="schema_validation").inc()

        if not valid_candidates:
            suggestion_id = await self._persist_suggestion(
                bot_id, triggered_by, prompt_hash, [], "failed", db
            )
            await self._publish(bot_id, {"v": 1, "type": "failed", "reason": "no_valid_candidates"})
            m.param_tuner_trigger_failures_total.labels(reason="no_valid_candidates").inc()
            return suggestion_id

        queue_limit = await self._get_queue_limit(db)
        depth = await self.backtest_submitter.queue_depth()
        if depth >= queue_limit:
            suggestion_id = await self._persist_suggestion(
                bot_id, triggered_by, prompt_hash, [], "failed", db
            )
            await self._publish(bot_id, {"v": 1, "type": "failed", "reason": "queue_full"})
            m.param_tuner_trigger_failures_total.labels(reason="queue_full").inc()
            return suggestion_id

        suggestion_id = await self._persist_suggestion(
            bot_id, triggered_by, prompt_hash, valid_candidates, "backtesting", db
        )
        m.param_tuner_trigger_total.labels(triggered_by=str(triggered_by)).inc()

        for cand in valid_candidates:
            try:
                cand.backtest_job_id = await self.backtest_submitter.submit(bot_id, cand.params)
                m.param_tuner_backtest_fan_out_total.inc()
            except Exception:
                logger.warning(
                    "tuner_backtest_submit_failed",
                    bot_id=str(bot_id),
                    suggestion_id=str(suggestion_id),
                )

        await db.execute(
            text("""
                UPDATE bot_param_suggestions
                SET candidates=:c
                WHERE id=:sid
            """),
            {"c": self._dump_candidates(valid_candidates), "sid": str(suggestion_id)},
        )
        await db.commit()
        await self._publish(
            bot_id,
            {
                "v": 1,
                "type": "backtesting",
                "suggestion_id": str(suggestion_id),
                "candidate_count": len(valid_candidates),
            },
        )
        return suggestion_id

    async def poll_backtest_results(self, db: AsyncSession) -> None:
        suggestions_result = await db.execute(
            text("""
                SELECT id, bot_id, candidates
                FROM bot_param_suggestions
                WHERE status='backtesting'
            """)
        )
        for suggestion in suggestions_result.mappings():
            sid = UUID(str(suggestion["id"]))
            bot_id = UUID(str(suggestion["bot_id"]))
            candidates = self._load_candidates(suggestion["candidates"])
            changed = False

            for candidate in candidates:
                if candidate.backtest_job_id and candidate.backtest_result is None:
                    backtest_result = await db.execute(
                        text("""
                            SELECT status, kpi_sharpe, kpi_mar, kpi_max_dd,
                                   kpi_win_rate, kpi_avg_trade_pnl
                            FROM backtests
                            WHERE id=:jid
                        """),
                        {"jid": str(candidate.backtest_job_id)},
                    )
                    backtest_row = backtest_result.mappings().first()
                    if backtest_row and backtest_row["status"] in ("done", "failed"):
                        candidate.backtest_result = BacktestResultSnapshot(
                            sharpe=backtest_row["kpi_sharpe"],
                            mar=backtest_row["kpi_mar"],
                            max_dd=backtest_row["kpi_max_dd"],
                            win_rate=backtest_row["kpi_win_rate"],
                            avg_trade_pnl=Decimal(str(backtest_row["kpi_avg_trade_pnl"] or 0)),
                        )
                        changed = True

            if candidates and all(c.backtest_result is not None for c in candidates):
                baseline_result = await db.execute(
                    text("""
                        SELECT AVG(kpi_sharpe), AVG(kpi_mar), AVG(kpi_max_dd)
                        FROM bot_runs
                        WHERE bot_id=:bid AND status='stopped'
                        ORDER BY started_at DESC LIMIT 5
                    """),
                    {"bid": str(bot_id)},
                )
                baseline_row = baseline_result.first()
                baseline_sharpe = baseline_row[0] if baseline_row else None

                for candidate in candidates:
                    snap = candidate.backtest_result
                    sharpe = snap.sharpe if snap is not None else None
                    candidate.delta_vs_current = (
                        {"sharpe": str(sharpe - baseline_sharpe)}
                        if sharpe is not None and baseline_sharpe is not None
                        else {}
                    )

                candidates.sort(
                    key=lambda c: (
                        c.backtest_result is not None and c.backtest_result.sharpe is not None,
                        c.backtest_result.sharpe
                        if c.backtest_result is not None and c.backtest_result.sharpe is not None
                        else float("-inf"),
                        c.backtest_result.mar
                        if c.backtest_result is not None and c.backtest_result.mar is not None
                        else float("-inf"),
                    ),
                    reverse=True,
                )
                for index, candidate in enumerate(candidates, start=1):
                    candidate.rank = index

                await db.execute(
                    text("""
                        UPDATE bot_param_suggestions
                        SET candidates=:c, status='ranked'
                        WHERE id=:sid
                    """),
                    {"c": self._dump_candidates(candidates), "sid": str(sid)},
                )
                await db.commit()
                top_snap = candidates[0].backtest_result if candidates else None
                top_sharpe = top_snap.sharpe if top_snap is not None else None
                await self._publish(
                    bot_id,
                    {
                        "v": 1,
                        "type": "ranked",
                        "suggestion_id": str(sid),
                        "top_sharpe": top_sharpe,
                    },
                )
                m.param_tuner_ranked_total.inc()
            elif changed:
                await db.execute(
                    text("""
                        UPDATE bot_param_suggestions
                        SET candidates=:c
                        WHERE id=:sid
                    """),
                    {"c": self._dump_candidates(candidates), "sid": str(sid)},
                )
                await db.commit()

    async def approve(
        self,
        suggestion_id: UUID,
        candidate_index: int,
        approved_by: str,
        db: AsyncSession,
        supervisor: Any,
    ) -> None:
        suggestion_result = await db.execute(
            text("""
                SELECT id, bot_id, triggered_by, status, candidates
                FROM bot_param_suggestions
                WHERE id=:sid
            """),
            {"sid": str(suggestion_id)},
        )
        suggestion = suggestion_result.mappings().first()
        if not suggestion or suggestion["status"] != "ranked":
            raise ValueError("suggestion_not_ranked")

        candidates = self._load_candidates(suggestion["candidates"])
        if candidate_index < 0 or candidate_index >= len(candidates):
            raise ValueError("candidate_index_out_of_bounds")
        candidate = candidates[candidate_index]
        if candidate.backtest_result is None:
            raise ValueError("candidate_backtest_missing")

        bot_id = UUID(str(suggestion["bot_id"]))
        await db.execute(
            text("""
                UPDATE bots
                SET strategy_params=:p
                WHERE id=:bid
            """),
            {"p": json.dumps(candidate.params, default=str), "bid": str(bot_id)},
        )
        await db.execute(
            text("""
                UPDATE bot_param_suggestions
                SET status='applied'
                WHERE id=:sid
            """),
            {"sid": str(suggestion_id)},
        )
        await db.commit()

        status_result = await db.execute(
            text("SELECT status FROM bots WHERE id=:bid"),
            {"bid": str(bot_id)},
        )
        bot_status = status_result.scalar_one_or_none()
        if bot_status in ("running", "paused", "error"):
            try:
                await supervisor.restart(bot_id)
            except SupervisorRestartError:
                logger.warning("tuner_supervisor_restart_failed", bot_id=str(bot_id))

        await self._publish(
            bot_id,
            {
                "v": 1,
                "type": "applied",
                "suggestion_id": str(suggestion_id),
                "candidate_index": candidate_index,
                "approved_by": approved_by,
            },
        )
        m.param_tuner_applied_total.labels(triggered_by=suggestion["triggered_by"]).inc()

    async def reject(
        self,
        suggestion_id: UUID,
        rejected_by: str,
        db: AsyncSession,
    ) -> None:
        await db.execute(
            text("""
                UPDATE bot_param_suggestions
                SET status='rejected', rejected_by=:rb
                WHERE id=:sid
            """),
            {"rb": rejected_by, "sid": str(suggestion_id)},
        )
        await db.commit()

    def _validate_candidate(
        self, cdict: dict[str, Any], schema: dict[str, Any], bounds: dict[str, Any]
    ) -> bool:
        for param, value in cdict.items():
            spec = schema.get(param)
            bound_spec = bounds.get(param, {})
            if spec is None:
                return False
            if isinstance(spec, str):
                expected_type = spec
                schema_bounds = {}
            else:
                expected_type = spec.get("type")
                schema_bounds = spec

            if expected_type == "int" and (not isinstance(value, int) or isinstance(value, bool)):
                return False
            if expected_type == "float" and (
                not isinstance(value, int | float) or isinstance(value, bool)
            ):
                return False
            if expected_type == "bool" and not isinstance(value, bool):
                return False
            if expected_type == "str" and not isinstance(value, str):
                return False

            minimum = schema_bounds.get("min", bound_spec.get("min"))
            maximum = schema_bounds.get("max", bound_spec.get("max"))
            if isinstance(value, int | float) and not isinstance(value, bool):
                if minimum is not None and value < minimum:
                    return False
                if maximum is not None and value > maximum:
                    return False
        return True

    async def _persist_suggestion(
        self,
        bot_id: UUID,
        triggered_by: TunerTrigger,
        prompt_hash: str,
        candidates: list[ParamCandidate],
        status: str,
        db: AsyncSession,
    ) -> UUID:
        result = await db.execute(
            text("""
                INSERT INTO bot_param_suggestions (
                    bot_id, triggered_by, prompt_hash, candidates, status
                )
                VALUES (:bid, :triggered_by, :prompt_hash, :candidates, :status)
                RETURNING id
            """),
            {
                "bid": str(bot_id),
                "triggered_by": str(triggered_by),
                "prompt_hash": prompt_hash,
                "candidates": self._dump_candidates(candidates),
                "status": status,
            },
        )
        suggestion_id = result.scalar_one()
        await db.commit()
        return UUID(str(suggestion_id))

    async def _publish(self, bot_id: UUID, frame: dict[str, Any]) -> None:
        try:
            await self.redis.publish(f"bot:tuner:{bot_id}", json.dumps(frame, default=str))
        except Exception:
            logger.warning("tuner_publish_failed", bot_id=str(bot_id))

    async def _get_committed_cost(self, bot_id: UUID, db: AsyncSession) -> float:
        result = await db.execute(
            text("""
                SELECT COALESCE(SUM(cost_usd),0)
                FROM ai_completions
                WHERE caller LIKE 'param_tuner:bot:%'
                  AND created_at >= NOW() - INTERVAL '1 day'
            """)
        )
        return float(result.scalar_one() or 0)

    async def _get_cost_ceiling(self, db: AsyncSession) -> float:
        result = await db.execute(
            text("""
                SELECT value FROM app_config
                WHERE key='param_tuner/cost_ceiling_usd_daily'
            """)
        )
        value = result.scalar_one_or_none()
        return float(value) if value is not None else 10.0

    async def _get_queue_limit(self, db: AsyncSession) -> int:
        result = await db.execute(
            text("""
                SELECT value FROM app_config
                WHERE key='param_tuner/queue_depth_limit'
            """)
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else DEFAULT_QUEUE_DEPTH_LIMIT

    def _dump_candidates(self, candidates: list[ParamCandidate]) -> str:
        return json.dumps(
            [candidate.model_dump(mode="json") for candidate in candidates],
            default=str,
        )

    def _load_candidates(self, raw: Any) -> list[ParamCandidate]:
        if raw is None:
            return []
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw
        return [ParamCandidate.model_validate(item) for item in data]
