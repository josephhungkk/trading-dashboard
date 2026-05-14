"""Phase 11a-A.5 (HIGH-5): Redis-backed LiteLLM master-key validation.

LiteLLM loads this module from /app/services/ai/litellm_auth_callback.py
inside the proxy container (resolved via dirname(config.yaml) = /app per
LiteLLM's get_instance_fn; PYTHONPATH is NOT consulted when --config is
passed). On every protected route, LiteLLM calls
user_api_key_auth(request, api_key) where api_key is already extracted
from the Authorization: Bearer header.

Zero-restart rotation: PUT /api/admin/secrets/ai/litellm_master_key
writes the new key to Redis key ``ai:litellm_master_key``. The next
LiteLLM request sees the new value — no docker compose restart.

Fail-CLOSED on every error path (Redis down, key unset, mismatch).
AI is not on the critical user-facing path; a 401 is the correct
default. Cost-ledger writes are fail-OPEN per Phase 10a pattern but
auth is the opposite.

NOTE on logging (silent-failure H1): the LiteLLM container does NOT have
structlog wired in. Diagnostic messages go to stderr via print() so they
land in `docker compose logs litellm`. The host BE writes its own
structlog entries via Task 12 lifespan.
"""

from __future__ import annotations

import hmac
import os
import sys
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request
    from litellm.proxy._types import UserAPIKeyAuth  # type: ignore[import-not-found]
    from redis.asyncio import Redis


REDIS_MASTER_KEY = "ai:litellm_master_key"


def _install_litellm_type_fallback() -> None:
    """Install minimal LiteLLM auth types when tests run without litellm."""
    try:
        import litellm.proxy._types  # type: ignore[import-not-found]  # noqa: F401 — availability probe; real import is lazy
    except ModuleNotFoundError as exc:
        if exc.name != "litellm":
            raise
    else:
        return

    litellm_module = sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    proxy_module = sys.modules.setdefault("litellm.proxy", types.ModuleType("litellm.proxy"))
    types_module = types.ModuleType("litellm.proxy._types")

    class _ProxyExceptionShim(Exception):  # noqa: N818 — mirrors LiteLLM naming
        def __init__(
            self,
            *,
            message: str,
            type: str = "invalid_request_error",  # noqa: A002 — matches LiteLLM API
            param: str | None = None,
            code: int = 401,
        ) -> None:
            super().__init__(message)
            self.message = message
            self.type = type
            self.param = param
            self.code = code

    class _UserAPIKeyAuthShim:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key

    types_module.ProxyException = _ProxyExceptionShim  # type: ignore[attr-defined]
    types_module.UserAPIKeyAuth = _UserAPIKeyAuthShim  # type: ignore[attr-defined]
    proxy_module._types = types_module  # type: ignore[attr-defined]
    litellm_module.proxy = proxy_module  # type: ignore[attr-defined]
    sys.modules["litellm.proxy._types"] = types_module


_install_litellm_type_fallback()


async def _get_redis_client() -> Redis:
    """Resolve a Redis client from REDIS_URL env (set in docker-compose).

    The LiteLLM container imports this module on startup, so we cannot
    rely on FastAPI app.state.redis — there is no FastAPI here.
    """
    from redis.asyncio import Redis

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL not set in LiteLLM container env")
    return Redis.from_url(redis_url, decode_responses=False)


async def user_api_key_auth(
    request: Request, api_key: str, *, _redis: Redis | None = None
) -> UserAPIKeyAuth:
    """Validate the incoming master key against the Redis-stored value.

    Args:
        request: FastAPI Request (provided by LiteLLM; we don't inspect it).
        api_key: extracted from Authorization: Bearer by LiteLLM.
        _redis: test-injection for unit tests; production goes via env.

    Returns:
        UserAPIKeyAuth(api_key=api_key) on success.

    Raises:
        ProxyException with code=401 on any failure.
    """
    from litellm.proxy._types import ProxyException, UserAPIKeyAuth

    redis_client = _redis if _redis is not None else await _get_redis_client()
    try:
        stored = await redis_client.get(REDIS_MASTER_KEY)
    except Exception as exc:
        # silent-failure H1: surface the failure to docker logs so an
        # operator seeing 401 storms can find the root cause. No
        # structlog in the LiteLLM container — print to stderr.
        print(
            f"litellm_auth_redis_error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise ProxyException(
            message="auth backend unavailable",
            type="invalid_request_error",
            param="api_key",
            code=401,
        ) from None
    if stored is None:
        raise ProxyException(
            message="master key not configured",
            type="invalid_request_error",
            param="api_key",
            code=401,
        )

    # silent-failure L1: decode inside the same fail-CLOSED envelope so a
    # corrupted/non-UTF8 Redis value produces a clean 401 not a 500.
    try:
        stored_str = stored.decode("utf-8") if isinstance(stored, bytes) else str(stored)
    except UnicodeDecodeError:
        print(
            "litellm_auth_redis_decode_error: stored value not valid utf-8",
            file=sys.stderr,
            flush=True,
        )
        raise ProxyException(
            message="auth backend unavailable",
            type="invalid_request_error",
            param="api_key",
            code=401,
        ) from None

    if not hmac.compare_digest(api_key, stored_str):
        raise ProxyException(
            message="invalid master key",
            type="invalid_request_error",
            param="api_key",
            code=401,
        )

    return UserAPIKeyAuth(api_key=api_key)
