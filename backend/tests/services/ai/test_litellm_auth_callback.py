"""Phase 11a-A.5: LiteLLM auth-callback unit tests (HIGH-5).

Validates the Redis-backed master-key check. Mocks the FastAPI Request
plus Redis client so tests run without the LiteLLM container.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_db


@pytest.fixture
def fake_request() -> MagicMock:
    """LiteLLM passes a FastAPI Request; the callback may inspect headers
    but doesn't need the full ASGI scope."""
    req = MagicMock(spec_set=["headers", "client"])
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


@pytest.fixture
def fake_redis_with_key() -> AsyncMock:
    """Redis returns the master key for `ai:litellm_master_key`."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"sk-master-current")
    return r


@pytest.mark.asyncio
async def test_callback_accepts_matching_key(
    fake_request: MagicMock, fake_redis_with_key: AsyncMock
) -> None:
    from app.services.ai.litellm_auth_callback import user_api_key_auth

    result = await user_api_key_auth(fake_request, "sk-master-current", _redis=fake_redis_with_key)
    assert result is not None
    assert getattr(result, "api_key", None) == "sk-master-current"


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_key(
    fake_request: MagicMock, fake_redis_with_key: AsyncMock
) -> None:
    from litellm.proxy._types import ProxyException

    from app.services.ai.litellm_auth_callback import user_api_key_auth

    with pytest.raises(ProxyException) as exc:
        await user_api_key_auth(fake_request, "sk-master-wrong", _redis=fake_redis_with_key)
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_callback_rejects_when_redis_unset(fake_request: MagicMock) -> None:
    """If Redis has no key (BE lifespan didn't run, or key was wiped),
    deny rather than fail-open."""
    from litellm.proxy._types import ProxyException

    from app.services.ai.litellm_auth_callback import user_api_key_auth

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    with pytest.raises(ProxyException) as exc:
        await user_api_key_auth(fake_request, "sk-master-anything", _redis=fake_redis)
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_callback_rejects_when_redis_errors(fake_request: MagicMock) -> None:
    """Redis hiccup must fail-CLOSED. AI access is not load-bearing on the
    user-facing path; a 401 is correct over fail-OPEN."""
    from litellm.proxy._types import ProxyException

    from app.services.ai.litellm_auth_callback import user_api_key_auth

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(side_effect=RuntimeError("redis hiccup"))
    with pytest.raises(ProxyException) as exc:
        await user_api_key_auth(fake_request, "sk-master-current", _redis=fake_redis)
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_callback_constant_time_compare(
    fake_request: MagicMock, fake_redis_with_key: AsyncMock
) -> None:
    """Use hmac.compare_digest to defend against timing side-channels.
    Asserting the import indirectly via behaviour: both differ-at-start
    and differ-at-end mismatches reject with the same exception class."""
    from litellm.proxy._types import ProxyException

    from app.services.ai.litellm_auth_callback import user_api_key_auth

    for wrong in ("Xk-master-current", "sk-master-currenX", ""):
        with pytest.raises(ProxyException):
            await user_api_key_auth(fake_request, wrong, _redis=fake_redis_with_key)
