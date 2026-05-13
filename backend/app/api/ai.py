"""Phase 11a-C: REST endpoints for the AI router."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

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


@router.post("/complete", response_model=CompletionResult)
async def post_complete(
    body: CompletionRequest,
    request: Request,
    jwt_subject: str = Depends(require_jwt),
) -> CompletionResult | JSONResponse:
    try:
        if body.tools is not None:
            raise AIToolCallingNotSupportedError("tool calling is not supported in v0.11.0")

        # Defence layer 1: LOCAL_ONLY API-boundary check.
        if body.capability == AICapability.LOCAL_ONLY:
            capability_map = await request.app.state.capability_svc.get_map()
            if not resolve_models(
                AICapability.LOCAL_ONLY,
                capability_map=capability_map,
                available_providers=LOCAL_PROVIDERS,
            ):
                raise LocalModelsUnavailableError("no local models are available")

        # Rate-limit is an async-CM holding the per-capability semaphore for
        # the duration of router.complete(); see services/ai/rate_limiter.py.
        async with request.app.state.ai_rate_limiter.check_and_acquire(
            jwt_subject,
            body.capability.value,
        ):
            return await request.app.state.ai_router.complete(
                body,
                jwt_subject=jwt_subject,
            )
    except RateLimitExceededError:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "rate_limited"},
            headers={"Retry-After": "60"},  # matches ai_router window
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
        log.exception("ai_complete_unhandled", error_class=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ai_internal_error",
        ) from exc
