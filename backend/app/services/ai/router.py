"""Phase 11a-B7: AI completion router with LiteLLM proxy fallback."""

from __future__ import annotations

import inspect
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
import structlog

from app.core import metrics
from app.services.ai.capabilities import AICapability, ResolvedModel, resolve_models
from app.services.ai.cost_ledger import CompletionRecord
from app.services.ai.exceptions import (
    AIProxyUnavailableError,
    AIToolCallingNotSupportedError,
    LocalModelsUnavailableError,
)
from app.services.ai.jobs import JobRecord
from app.services.ai.secrets import ProviderKeyUnavailableError
from app.services.ai.types import Chunk, CompletionRequest, CompletionResult, FallbackHop

log = structlog.get_logger(__name__)

CapabilityMap = dict[str, list[dict[str, str]]]
ProviderSet = set[str] | frozenset[str]


class _RetryableHTTPError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _SemanticHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http_{status_code}")
        self.status_code = status_code


class _ProviderSecrets(Protocol):
    async def get_provider_key(self, provider: str) -> str: ...


class _RateLimiter(Protocol):
    def check_and_acquire(self, jwt_subject: str, capability: str) -> Any: ...


class _CostLedger(Protocol):
    def record(self, rec: CompletionRecord) -> None: ...


class _JobStore(Protocol):
    async def create_job(
        self,
        *,
        jwt_subject: str,
        capability: str,
        request: dict[str, Any],
    ) -> JobRecord: ...

    async def get_job(self, job_id: UUID) -> JobRecord | None: ...
    async def cancel_job(self, job_id: UUID) -> None: ...


class AICompletionClient(ABC):
    @abstractmethod
    async def complete(self, req: CompletionRequest, *, jwt_subject: str) -> CompletionResult: ...

    @abstractmethod
    def stream(self, req: CompletionRequest, *, jwt_subject: str) -> AsyncIterator[Chunk]: ...

    @abstractmethod
    def batch_complete(
        self,
        reqs: Sequence[CompletionRequest],
        *,
        jwt_subject: str,
    ) -> AsyncIterator[CompletionResult]: ...

    @abstractmethod
    async def submit_job(self, req: CompletionRequest, *, jwt_subject: str) -> UUID: ...

    @abstractmethod
    async def get_job(self, job_id: UUID) -> JobRecord | None: ...

    @abstractmethod
    async def cancel_job(self, job_id: UUID) -> None: ...


class LiteLLMClient(AICompletionClient):
    def __init__(
        self,
        *,
        secrets: _ProviderSecrets,
        rate_limiter: _RateLimiter,
        cost_ledger: _CostLedger,
        jobs: _JobStore,
        proxy_url: str,
        master_key_provider: Callable[[], str | Awaitable[str]],
        capability_map_provider: Callable[[], CapabilityMap | Awaitable[CapabilityMap]],
        available_providers_provider: Callable[[], ProviderSet | Awaitable[ProviderSet]],
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._secrets = secrets
        self._rl = rate_limiter
        self._ledger = cost_ledger
        self._jobs = jobs
        self._proxy_url = proxy_url.rstrip("/")
        self._master_key_provider = master_key_provider
        self._capability_map_provider = capability_map_provider
        self._available_providers_provider = available_providers_provider
        self._http = http_client or httpx.AsyncClient(timeout=timeout_s)
        self._timeout_s = timeout_s

    async def complete(self, req: CompletionRequest, *, jwt_subject: str) -> CompletionResult:
        if req.tools is not None:
            raise AIToolCallingNotSupportedError("tool calling is not supported in v0.11.0")

        capability_map = await _maybe_await(self._capability_map_provider())
        available_providers: set[str] | frozenset[str] = await _maybe_await(
            self._available_providers_provider()
        )
        chain = resolve_models(
            req.capability,
            capability_map=capability_map,
            available_providers=available_providers,
            force_local_only=req.force_local_only,
        )
        if not chain and (req.capability is AICapability.LOCAL_ONLY or req.force_local_only):
            raise LocalModelsUnavailableError("no local models are available")
        if not chain:
            raise AIProxyUnavailableError("no providers are available")

        fallback_chain: list[FallbackHop] = []
        async with self._rl.check_and_acquire(jwt_subject, req.capability.value):
            for index, entry in enumerate(chain):
                try:
                    api_key = await self._secrets.get_provider_key(entry.provider)
                except ProviderKeyUnavailableError as exc:
                    log.info(
                        "ai_router_provider_key_unavailable",
                        provider=entry.provider,
                        model=entry.model,
                        exc_info=exc,
                    )
                    self._record_fallback(
                        fallback_chain,
                        chain=chain,
                        index=index,
                        reason="missing_api_key",
                    )
                    continue

                try:
                    result = await self._complete_one(req, entry=entry, api_key=api_key)
                except _RetryableHTTPError as exc:
                    self._record_fallback(
                        fallback_chain,
                        chain=chain,
                        index=index,
                        reason=exc.reason,
                    )
                    continue
                except _SemanticHTTPError as exc:
                    # Code-reviewer HIGH-3: a 4xx must still leave an audit
                    # trail. Record the fallback hop, count the outcome,
                    # and mark proxy_unavailable before raising so the
                    # operator can distinguish "no providers configured"
                    # from "configured provider returned 401".
                    self._record_fallback(
                        fallback_chain,
                        chain=chain,
                        index=index,
                        reason=f"http_{exc.status_code}",
                    )
                    metrics.AI_ROUTER_COMPLETIONS_TOTAL.labels(
                        provider=entry.provider,
                        model=entry.model,
                        capability=req.capability.value,
                        outcome="semantic_error",
                    ).inc()
                    metrics.AI_ROUTER_PROXY_UNAVAILABLE_TOTAL.inc()
                    raise AIProxyUnavailableError(str(exc)) from exc

                metrics.AI_ROUTER_COMPLETIONS_TOTAL.labels(
                    provider=result.provider,
                    model=result.model,
                    capability=req.capability.value,
                    outcome="success",
                ).inc()
                metrics.AI_ROUTER_LATENCY_SECONDS.labels(
                    provider=result.provider,
                    capability=req.capability.value,
                ).observe(result.wall_time_ms / 1000)
                metrics.AI_ROUTER_TOKENS_PROMPT_TOTAL.labels(
                    provider=result.provider,
                    model=result.model,
                ).inc(result.prompt_tokens)
                metrics.AI_ROUTER_TOKENS_COMPLETION_TOTAL.labels(
                    provider=result.provider,
                    model=result.model,
                ).inc(result.completion_tokens)
                self._ledger.record(
                    CompletionRecord(
                        request_id=str(result.request_id),
                        ts=datetime.now(UTC),
                        provider=result.provider,
                        model=result.model,
                        capability=req.capability.value,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        wall_time_ms=result.wall_time_ms,
                        outcome="success",
                    )
                )
                return result.model_copy(update={"fallback_chain": fallback_chain})

        metrics.AI_ROUTER_PROXY_UNAVAILABLE_TOTAL.inc()
        raise AIProxyUnavailableError("all AI providers exhausted")

    def stream(self, req: CompletionRequest, *, jwt_subject: str) -> AsyncIterator[Chunk]:
        raise NotImplementedError("stream is wired in chunk C with httpx async stream")

    def batch_complete(
        self,
        reqs: Sequence[CompletionRequest],
        *,
        jwt_subject: str,
    ) -> AsyncIterator[CompletionResult]:
        async def _run() -> AsyncIterator[CompletionResult]:
            for req in reqs:
                yield await self.complete(req, jwt_subject=jwt_subject)

        return _run()

    async def submit_job(self, req: CompletionRequest, *, jwt_subject: str) -> UUID:
        record = await self._jobs.create_job(
            jwt_subject=jwt_subject,
            capability=req.capability.value,
            request=req.model_dump(mode="json"),
        )
        return record.id

    async def get_job(self, job_id: UUID) -> JobRecord | None:
        return await self._jobs.get_job(job_id)

    async def cancel_job(self, job_id: UUID) -> None:
        await self._jobs.cancel_job(job_id)

    async def _complete_one(
        self,
        req: CompletionRequest,
        *,
        entry: ResolvedModel,
        api_key: str,
    ) -> CompletionResult:
        master_key = await _maybe_await(self._master_key_provider())
        start = time.perf_counter()
        try:
            response = await self._http.post(
                f"{self._proxy_url}/chat/completions",
                json={
                    "model": entry.model,
                    "messages": req.messages,
                    "max_tokens": req.max_tokens,
                    "temperature": req.temperature,
                    "api_key": api_key,
                },
                headers={"Authorization": f"Bearer {master_key}"},
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise _RetryableHTTPError("timeout") from exc
        except httpx.HTTPError as exc:
            raise _RetryableHTTPError("transport_error") from exc

        if response.status_code == 429:
            raise _RetryableHTTPError("rate_limited")
        if 500 <= response.status_code <= 599:
            raise _RetryableHTTPError(f"http_{response.status_code}")
        if 400 <= response.status_code <= 499:
            raise _SemanticHTTPError(response.status_code)

        data = response.json()
        return _parse_litellm_response(
            data,
            provider=entry.provider,
            model=entry.model,
            start=start,
        )

    def _record_fallback(
        self,
        fallback_chain: list[FallbackHop],
        *,
        chain: Sequence[ResolvedModel],
        index: int,
        reason: str,
    ) -> None:
        entry = chain[index]
        next_provider = chain[index + 1].provider if index + 1 < len(chain) else "__none__"
        fallback_chain.append(
            FallbackHop(
                from_provider=entry.provider,
                from_model=entry.model,
                reason=reason,
            )
        )
        metrics.AI_ROUTER_FALLBACK_CHAIN_TOTAL.labels(
            from_provider=entry.provider,
            to_provider=next_provider,
            reason=reason,
        ).inc()


def _parse_litellm_response(
    data: dict[str, Any],
    *,
    provider: str,
    model: str,
    start: float,
) -> CompletionResult:
    choice = data["choices"][0]
    usage = data.get("usage") or {}
    request_id = _request_uuid(data.get("id"))
    return CompletionResult(
        request_id=request_id,
        text=choice["message"]["content"],
        provider=provider,
        model=model,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        wall_time_ms=int((time.perf_counter() - start) * 1000),
    )


def _request_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return uuid4()
    return uuid4()


async def _maybe_await[T](value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
