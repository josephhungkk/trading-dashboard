"""Phase 11a-C: REST endpoints for the AI router."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.api.ws_auth import require_jwt
from app.services.ai.capabilities import LOCAL_PROVIDERS, AICapability, resolve_models
from app.services.ai.exceptions import (
    AIProxyUnavailableError,
    AIToolCallingNotSupportedError,
    LocalModelsUnavailableError,
)
from app.services.ai.types import CompletionRequest, CompletionResult
from app.services.common.rate_limiter import RateLimitExceededError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

_RATE_LIMIT_RETRY_AFTER_S = 60  # must match AIRouterRateLimiter per_subject_window_s


class JobSubmitResponse(BaseModel):
    job_id: UUID


class JobStatusResponse(BaseModel):
    id: UUID
    status: str
    capability: str
    response: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime
    warming_started_at: datetime | None = None
    inferring_started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested: bool


async def _guarded_ai_call[T](
    request: Request,
    body: CompletionRequest,
    jwt_subject: str,
    *,
    log_tag: str,
    call: Callable[[], Awaitable[T]],
) -> T | JSONResponse:
    """Shared pre-flight guard + exception mapping for /complete and /jobs."""
    try:
        if body.tools is not None:
            raise AIToolCallingNotSupportedError("tool calling is not supported in v0.11.0")

        if body.capability == AICapability.LOCAL_ONLY:
            capability_map = await request.app.state.capability_svc.get_map()
            if not resolve_models(
                AICapability.LOCAL_ONLY,
                capability_map=capability_map,
                available_providers=LOCAL_PROVIDERS,
            ):
                raise LocalModelsUnavailableError("no local models are available")

        async with request.app.state.ai_rate_limiter.check_and_acquire(
            jwt_subject,
            body.capability.value,
        ):
            return await call()
    except RateLimitExceededError:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "rate_limited"},
            headers={"Retry-After": str(_RATE_LIMIT_RETRY_AFTER_S)},
        )
    except AIToolCallingNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="tool_calling_not_yet_supported",
        ) from exc
    except LocalModelsUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local_models_unavailable",
        ) from exc
    except AIProxyUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ai_proxy_unavailable",
        ) from exc
    except Exception as exc:
        log.exception(log_tag, error_class=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ai_internal_error",
        ) from exc


@router.post("/complete", response_model=CompletionResult)
async def post_complete(
    body: CompletionRequest,
    request: Request,
    jwt_subject: str = Depends(require_jwt),
) -> CompletionResult | JSONResponse:
    return await _guarded_ai_call(
        request,
        body,
        jwt_subject,
        log_tag="ai_complete_unhandled",
        call=lambda: request.app.state.ai_router.complete(body, jwt_subject=jwt_subject),
    )


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobSubmitResponse,
)
async def post_jobs(
    body: CompletionRequest,
    request: Request,
    jwt_subject: str = Depends(require_jwt),
) -> JobSubmitResponse | JSONResponse:
    async def _submit() -> JobSubmitResponse:
        job_id = await request.app.state.ai_router.submit_job(
            body,
            jwt_subject=jwt_subject,
        )
        return JobSubmitResponse(job_id=job_id)

    return await _guarded_ai_call(
        request,
        body,
        jwt_subject,
        log_tag="ai_jobs_unhandled",
        call=_submit,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: UUID,
    request: Request,
    jwt_subject: str = Depends(require_jwt),
) -> JobStatusResponse:
    job = await request.app.state.ai_router.get_job(job_id)
    if job is None or job.jwt_subject != jwt_subject:
        # 404 (not 403) on both miss + ownership - existence-oracle defence.
        raise HTTPException(status_code=404, detail="job_not_found")
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        capability=job.capability,
        response=job.response_jsonb,
        error=job.error,
        started_at=job.started_at,
        warming_started_at=job.warming_started_at,
        inferring_started_at=job.inferring_started_at,
        completed_at=job.completed_at,
        cancel_requested=job.cancel_requested,
    )


@router.delete("/jobs/{job_id}", status_code=204, response_class=Response)
async def delete_job(
    job_id: UUID,
    request: Request,
    jwt_subject: str = Depends(require_jwt),
) -> None:
    job = await request.app.state.ai_router.get_job(job_id)
    if job is None or job.jwt_subject != jwt_subject:
        raise HTTPException(status_code=404, detail="job_not_found")
    await request.app.state.ai_router.cancel_job(job_id)
