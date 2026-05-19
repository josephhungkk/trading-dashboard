from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.advisor.context_builder import ContextBuilder
from app.services.advisor.metrics import (
    advisor_audit_insert_failures_total,
    advisor_budget_exceeded_total,
    advisor_config_reloads_total,
    advisor_decisions_total,
    advisor_fail_open_total,
    advisor_in_flight_skips_total,
    advisor_latency_seconds,
    advisor_publish_failures_total,
    advisor_unexpected_errors_total,
    advisor_unknown_tags_total,
)
from app.services.advisor.prompts import ALLOWED_ADVICE_TAGS, PROMPT_VERSION, SYSTEM_PROMPT
from app.services.advisor.types import (
    AdvisorConfig,
    AdvisorMode,
    AdvisorVerdict,
    ContextSummary,
    OrderIntent,
)
from app.services.ai.types import CompletionRequest

logger = structlog.get_logger(__name__)


class AdvisorService:
    def __init__(self, ai_client, redis: Any, db_factory: async_sessionmaker) -> None:
        self._ai_client = ai_client
        self._redis = redis
        self._db_factory = db_factory
        self._in_flight: dict[str, asyncio.Lock] = {}

    async def review(
        self,
        *,
        bot_id: UUID,
        run_id: UUID | None,
        account_id: UUID,
        intent: OrderIntent,
        strategy_params: dict,
        effective_config: AdvisorConfig,
        db: AsyncSession,
    ) -> tuple[AdvisorVerdict, int | None]:
        if effective_config.mode == AdvisorMode.OFF:
            return AdvisorVerdict(action="approve", confidence=None), None

        lock = self._in_flight.setdefault(str(bot_id), asyncio.Lock())
        if lock.locked():
            advisor_in_flight_skips_total.labels(bot_id=str(bot_id)).inc()
            return await self._fail_open(
                bot_id=bot_id,
                run_id=run_id,
                account_id=account_id,
                intent=intent,
                effective_config=effective_config,
                reason="advisor_in_flight",
            )

        async with lock:
            if not await self._budget_ok_and_reserve(bot_id, effective_config):
                advisor_budget_exceeded_total.labels(bot_id=str(bot_id)).inc()
                return await self._fail_open(
                    bot_id=bot_id,
                    run_id=run_id,
                    account_id=account_id,
                    intent=intent,
                    effective_config=effective_config,
                    reason="daily_budget_exceeded",
                )

            result = None
            latency_ms = 0
            context_summary = self._empty_context_summary()
            start = time.monotonic()
            try:
                payload, context_summary = await ContextBuilder.build(intent, strategy_params, db)
                req = CompletionRequest(
                    capability=effective_config.capability,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"<<BEGIN_CONTEXT>>{payload}<<END_CONTEXT>>"},
                    ],
                    caller=f"advisor:bot:{bot_id}",
                    force_local_only=effective_config.local_only,
                )
                result = await asyncio.wait_for(
                    self._complete(req, bot_id=bot_id),
                    timeout=effective_config.timeout_ms / 1000,
                )
                latency_ms = int((time.monotonic() - start) * 1000)
                advisor_latency_seconds.labels(
                    mode=str(effective_config.mode),
                    capability=str(effective_config.capability),
                ).observe(latency_ms / 1000)

                try:
                    verdict = AdvisorVerdict.model_validate(json.loads(result.text))
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    return await self._fail_open(
                        bot_id=bot_id,
                        run_id=run_id,
                        account_id=account_id,
                        intent=intent,
                        effective_config=effective_config,
                        reason=f"schema_error:{exc.__class__.__name__}",
                    )

                verdict = await self._apply_safety_rules(verdict, effective_config)
                if verdict.action == "fail_open":
                    advisor_fail_open_total.labels(reason=verdict.reasoning or "safety_rule").inc()

                if effective_config.mode == AdvisorMode.OBSERVE and verdict.action == "veto":
                    verdict = verdict.model_copy(update={"action": "approve"})
                    advisor_fail_open_total.labels(reason="observe_mode_veto_downgrade").inc()

                decision_id = await self._persist(
                    bot_id=bot_id,
                    run_id=run_id,
                    account_id=account_id,
                    intent=intent,
                    effective_config=effective_config,
                    verdict=verdict,
                    result=result,
                    latency_ms=latency_ms,
                    context_summary=context_summary,
                )
                await self._publish(
                    bot_id=bot_id,
                    account_id=account_id,
                    intent=intent,
                    verdict=verdict,
                    latency_ms=latency_ms,
                    effective_config=effective_config,
                    decision_id=decision_id,
                )
                advisor_decisions_total.labels(
                    mode=str(effective_config.mode),
                    verdict=verdict.action,
                    capability=str(effective_config.capability),
                ).inc()
                return verdict, decision_id
            except TimeoutError:
                latency_ms = int((time.monotonic() - start) * 1000)
                return await self._fail_open(
                    bot_id=bot_id,
                    run_id=run_id,
                    account_id=account_id,
                    intent=intent,
                    effective_config=effective_config,
                    reason="timeout",
                )
            except Exception as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                advisor_unexpected_errors_total.labels(error_class=exc.__class__.__name__).inc()
                logger.exception(
                    "advisor_review_unexpected_error",
                    bot_id=str(bot_id),
                    run_id=str(run_id) if run_id else None,
                    latency_ms=latency_ms,
                )
                return await self._fail_open(
                    bot_id=bot_id,
                    run_id=run_id,
                    account_id=account_id,
                    intent=intent,
                    effective_config=effective_config,
                    reason=f"unexpected:{exc.__class__.__name__}",
                )

    async def _apply_safety_rules(
        self, verdict: AdvisorVerdict, config: AdvisorConfig
    ) -> AdvisorVerdict:
        if verdict.action == "veto" and not verdict.reasoning.strip():
            return AdvisorVerdict(
                action="fail_open",
                reasoning="veto_without_reasoning",
                confidence=None,
            )

        if (
            verdict.action == "veto"
            and config.min_veto_confidence > 0
            and verdict.confidence is not None
            and verdict.confidence < config.min_veto_confidence
        ):
            return AdvisorVerdict(
                action="fail_open",
                reasoning="low_confidence",
                confidence=verdict.confidence,
                advice_tags=verdict.advice_tags,
            )

        if _contains_prompt_echo(verdict.reasoning):
            return AdvisorVerdict(
                action="fail_open",
                reasoning="prompt_echo_detected",
                confidence=None,
            )

        cleaned_tags: list[str] = []
        for tag in verdict.advice_tags:
            if tag in ALLOWED_ADVICE_TAGS:
                cleaned_tags.append(tag)
            else:
                advisor_unknown_tags_total.labels(tag=tag).inc()
                cleaned_tags.append("other")

        if cleaned_tags != verdict.advice_tags:
            return verdict.model_copy(update={"advice_tags": cleaned_tags})
        return verdict

    async def _budget_ok_and_reserve(self, bot_id: UUID, config: AdvisorConfig) -> bool:
        key = f"advisor:spend_estimate_cents:{bot_id}:{date.today().isoformat()}"
        limit_cents = int(config.daily_budget_usd * Decimal("100"))
        try:
            counter = await self._redis.incrby(key, 100)
            await self._redis.expire(key, 172800)
        except Exception as exc:
            logger.warning("advisor_budget_reserve_failed", bot_id=str(bot_id), exc_info=exc)
            return True
        return int(counter) <= limit_cents

    async def _persist(
        self,
        *,
        bot_id,
        run_id,
        account_id,
        intent,
        effective_config,
        verdict,
        result,
        latency_ms,
        context_summary,
    ) -> int | None:
        stmt = text(
            "INSERT INTO bot_advisor_decisions "
            "(bot_id, bot_run_id, account_id, canonical_id, intent, context_summary, "
            "prompt_version, verdict, reasoning, confidence, advice_tags, provider, model, "
            "fallback_chain, latency_ms, ai_completion_ts, "
            "ai_completion_request_id, effective_mode) "
            "VALUES (:bot_id, :bot_run_id, :account_id, :canonical_id, CAST(:intent AS jsonb), "
            "CAST(:context_summary AS jsonb), :prompt_version, :verdict, :reasoning, "
            ":confidence, :advice_tags, :provider, :model, :fallback_chain, :latency_ms, "
            ":ai_completion_ts, :ai_completion_request_id, :effective_mode) RETURNING id"
        )
        params = {
            "bot_id": bot_id,
            "bot_run_id": run_id,
            "account_id": account_id,
            "canonical_id": intent.canonical_id,
            "intent": json.dumps(intent.model_dump(mode="json"), default=_json_default),
            "context_summary": json.dumps(_model_dump(context_summary), default=_json_default),
            "prompt_version": PROMPT_VERSION,
            "verdict": verdict.action,
            "reasoning": verdict.reasoning,
            "confidence": verdict.confidence,
            "advice_tags": verdict.advice_tags,
            "provider": getattr(result, "provider", None),
            "model": getattr(result, "model", None),
            "fallback_chain": _fallback_chain(result),
            "latency_ms": latency_ms,
            "ai_completion_ts": datetime.now(UTC) if result is not None else None,
            "ai_completion_request_id": getattr(result, "request_id", None),
            "effective_mode": str(effective_config.mode),
        }

        try:
            async with self._db_factory() as session:
                row = await session.execute(stmt, params)
                await session.commit()
                return int(row.scalar_one())
        except Exception as exc:
            advisor_audit_insert_failures_total.inc()
            logger.critical(
                "advisor_audit_insert_failed",
                bot_id=str(bot_id),
                run_id=str(run_id) if run_id else None,
                account_id=str(account_id),
                canonical_id=intent.canonical_id,
                exc_info=exc,
            )
            await self._xadd_audit_dlq(bot_id=bot_id, params=params)
            return None

    async def _fail_open(
        self,
        *,
        bot_id,
        run_id,
        account_id,
        intent,
        effective_config,
        reason,
    ) -> tuple[AdvisorVerdict, int | None]:
        verdict = AdvisorVerdict(
            action="fail_open", reasoning=str(reason), confidence=None, advice_tags=["other"]
        )
        decision_id = await self._persist(
            bot_id=bot_id,
            run_id=run_id,
            account_id=account_id,
            intent=intent,
            effective_config=effective_config,
            verdict=verdict,
            result=None,
            latency_ms=0,
            context_summary=self._empty_context_summary(),
        )
        await self._publish(
            bot_id=bot_id,
            account_id=account_id,
            intent=intent,
            verdict=verdict,
            latency_ms=0,
            effective_config=effective_config,
            decision_id=decision_id,
        )
        advisor_fail_open_total.labels(reason=str(reason)).inc()
        advisor_decisions_total.labels(
            mode=str(effective_config.mode),
            verdict=verdict.action,
            capability=str(effective_config.capability),
        ).inc()
        return verdict, decision_id

    async def _publish(
        self,
        *,
        bot_id,
        account_id,
        intent,
        verdict,
        latency_ms,
        effective_config,
        decision_id,
    ) -> None:
        payload = {
            "v": 1,
            "bot_id": str(bot_id),
            "account_id": str(account_id),
            "canonical_id": intent.canonical_id,
            "verdict": verdict.action,
            "reasoning": verdict.reasoning,
            "confidence": verdict.confidence,
            "advice_tags": verdict.advice_tags,
            "latency_ms": latency_ms,
            "mode": str(effective_config.mode),
            "decision_id": decision_id,
        }
        try:
            await self._redis.publish(
                f"bot:advisor:{bot_id}", json.dumps(payload, default=_json_default)
            )
        except Exception as exc:
            advisor_publish_failures_total.inc()
            logger.warning("advisor_publish_failed", bot_id=str(bot_id), exc_info=exc)

    async def update_account_gate_outcome(
        self,
        decision_id: int | None,
        outcome: str,
        gate_decision_id: int | None = None,
    ) -> None:
        if decision_id is None:
            return
        try:
            async with self._db_factory() as session:
                await session.execute(
                    text(
                        "UPDATE bot_advisor_decisions SET "
                        "account_gate_outcome = :o, account_gate_decision_id = :gid "
                        "WHERE id = :id"
                    ),
                    {"o": outcome, "gid": gate_decision_id, "id": decision_id},
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "advisor_gate_outcome_update_failed", decision_id=decision_id, exc_info=exc
            )

    def reload_config(self, bot_id: UUID, new_config: AdvisorConfig) -> None:
        advisor_config_reloads_total.labels(bot_id=str(bot_id)).inc()

    async def _complete(self, req: CompletionRequest, *, bot_id: UUID):
        try:
            sig = inspect.signature(self._ai_client.complete)
        except TypeError, ValueError:
            sig = None
        if sig is not None and "jwt_subject" in sig.parameters:
            return await self._ai_client.complete(req, jwt_subject=f"advisor:bot:{bot_id}")
        return await self._ai_client.complete(req)

    async def _xadd_audit_dlq(self, *, bot_id: UUID, params: dict[str, Any]) -> None:
        try:
            payload = json.dumps(
                {
                    "id": str(uuid4()),
                    "ts": datetime.now(UTC).isoformat(),
                    "decision": params,
                },
                default=_json_default,
            )
            await self._redis.xadd(f"advisor:audit:dlq:{bot_id}", {"data": payload})
        except Exception as exc:
            logger.critical("advisor_audit_dlq_write_failed", bot_id=str(bot_id), exc_info=exc)

    @staticmethod
    def _empty_context_summary() -> ContextSummary:
        return ContextSummary(
            bar_count=0,
            position_count=0,
            recent_fill_count=0,
            risk_decision_count=0,
            params_hash="",
            payload_token_estimate=0,
        )


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _fallback_chain(result: Any) -> list[str]:
    if result is None:
        return []
    chain = getattr(result, "fallback_chain", []) or []
    rendered: list[str] = []
    for hop in chain:
        if hasattr(hop, "model_dump"):
            rendered.append(json.dumps(hop.model_dump(mode="json"), default=_json_default))
        else:
            rendered.append(str(hop))
    return rendered


def _contains_prompt_echo(reasoning: str) -> bool:
    if len(reasoning) <= 50:
        return False
    for index in range(0, max(len(SYSTEM_PROMPT) - 50, 0)):
        if SYSTEM_PROMPT[index : index + 51] in reasoning:
            return True
    return False


def _json_default(value: Any) -> str:
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
