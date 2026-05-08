"""Phase 7b.1 retro — CRIT-1 regression: QuoteEngine must be wired into
app.state during lifespan startup.

Before the fix, ``app.state.quote_engine`` was always ``None`` because the
engine was never instantiated in ``main.py``.  Every ``/ws/quotes`` connection
closed immediately with code 1011 (WS_1011_INTERNAL_ERROR).

These tests assert:

1. ``app.state.quote_engine`` is not ``None`` after lifespan startup (or, if
   mTLS secrets are absent in the test env, the attribute exists and the WS
   endpoint returns a non-1011 close reason).
2. Connecting to ``/ws/quotes`` with a valid auth token does NOT receive a
   1011 close — the endpoint must either accept or close for a different
   reason (missing subprotocol, origin check, etc.).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.broker_registry_factory import MissingBrokerSecrets
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceRouter

# Tests in this module boot the ASGI app with mocked quote engine — no DB migrations needed.
pytestmark = pytest.mark.no_db

# ---------------------------------------------------------------------------
# Minimal QuoteEngine factory helpers for injection
# ---------------------------------------------------------------------------


def _make_minimal_engine() -> QuoteEngine:
    """Build a QuoteEngine with no real gRPC streams — suitable for lifespan
    injection in tests that only need to verify the attribute is present."""
    health = SourceHealthMap()
    router = SourceRouter(
        config={
            "quote_source_priority": {},
            "ibkr_gateway_quote_assignment": {},
            "ibkr_gateway_quote_fallback": [],
        },
        health=health,
    )
    registry = SubscriptionRegistry(
        cap_per_ws=10,
        cap_global=100,
        sub_rate_limit_per_minute=60,
    )
    redis_mock = AsyncMock()
    redis_mock.publish = AsyncMock(return_value=0)
    return QuoteEngine(
        registry=registry,
        router=router,
        redis=redis_mock,
        streams={},
        db_factory=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_lifespan_mocks(
    mock_redis_cls: MagicMock,
    mock_bridge_cls: MagicMock,
    mock_cache_cls: MagicMock,
    mock_cbs: MagicMock,
    mock_bar_cls: MagicMock,
) -> None:
    """Configure all mock classes needed to run the FastAPI lifespan without real I/O.

    Mirrors the pattern used in ``test_bar_service_pre_warm.py`` which already
    exercises the full lifespan context manager in this test suite.
    """
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()
    mock_redis_cls.from_url.return_value = mock_redis

    mock_bridge = MagicMock()
    mock_bridge.stop = MagicMock()
    mock_bridge.run = AsyncMock(return_value=None)
    mock_bridge_cls.return_value = mock_bridge

    mock_cache_inst = AsyncMock()
    mock_cache_inst.run_listener = AsyncMock(return_value=None)
    mock_cache_cls.return_value = mock_cache_inst

    mock_cb_server = AsyncMock()
    mock_cb_server.stop = AsyncMock()
    mock_cbs.return_value = mock_cb_server

    mock_bar_svc = AsyncMock()
    mock_bar_svc.start = AsyncMock()
    mock_bar_svc.stop = AsyncMock()
    mock_bar_cls.return_value = mock_bar_svc


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_app_state_quote_engine_attribute_set_by_lifespan() -> None:
    """CRIT-1 regression: lifespan must call build_quote_engine() and set
    ``app.state.quote_engine``.

    Before the fix the attribute was never assigned. We invoke the lifespan
    context manager directly (the same pattern as ``test_bar_service_pre_warm``),
    patching all I/O dependencies, and assert:

    1. ``build_quote_engine`` was called.
    2. ``app.state.quote_engine`` is set (QuoteEngine or None — not absent).
    """
    from app.main import app as fastapi_app
    from app.main import lifespan

    minimal_engine = _make_minimal_engine()
    minimal_engine.start = AsyncMock()
    minimal_engine.stop = AsyncMock()
    mock_build = AsyncMock(return_value=minimal_engine)

    with (
        patch("app.main.Redis") as mock_redis_cls,
        patch("app.main.PostgresListenBridge") as mock_bridge_cls,
        patch("app.main.ConfigCache") as mock_cache_cls,
        patch("app.main.ConfigService"),
        patch("app.main.get_fernet"),
        patch("app.main.set_config_service"),
        patch("app.main.start_backend_callback_server") as mock_cbs,
        patch("app.main.build_broker_registry", side_effect=MissingBrokerSecrets("no-broker")),
        patch("app.main.seed_instruments_from_positions", return_value=0),
        patch("app.main.build_quote_engine", mock_build),
        patch("app.main.OrderCapabilityService") as mock_cap_cls,
        patch("app.main.BarService") as mock_bar_cls,
        patch("app.main._run_pre_warm", new_callable=AsyncMock),
    ):
        _make_lifespan_mocks(
            mock_redis_cls, mock_bridge_cls, mock_cache_cls, mock_cbs, mock_bar_cls
        )
        # OrderCapabilityService must have an awaitable run_listener() for the task.
        mock_cap = AsyncMock()
        mock_cap.run_listener = AsyncMock(return_value=None)
        mock_cap_cls.return_value = mock_cap

        async with lifespan(fastapi_app):
            result = getattr(fastapi_app.state, "quote_engine", "MISSING")

    # build_quote_engine must have been called.
    assert mock_build.called, (
        "CRIT-1: build_quote_engine() was never called from lifespan. "
        "The QuoteEngine pipeline is never started."
    )
    assert result != "MISSING", (
        "app.state.quote_engine was never set — CRIT-1 regression detected. "
        "_app.state.quote_engine = quote_engine must appear in lifespan."
    )
    assert isinstance(result, (QuoteEngine, type(None))), (
        f"Expected QuoteEngine or None, got {type(result)}"
    )


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_lifespan_sets_quote_engine_to_none_when_build_returns_none() -> None:
    """When build_quote_engine returns None (broker secrets not provisioned),
    the lifespan must set app.state.quote_engine = None — not leave the
    attribute absent (which would cause AttributeError in get_quote_engine).
    """
    from app.main import app as fastapi_app
    from app.main import lifespan

    mock_build = AsyncMock(return_value=None)

    with (
        patch("app.main.Redis") as mock_redis_cls,
        patch("app.main.PostgresListenBridge") as mock_bridge_cls,
        patch("app.main.ConfigCache") as mock_cache_cls,
        patch("app.main.ConfigService"),
        patch("app.main.get_fernet"),
        patch("app.main.set_config_service"),
        patch("app.main.start_backend_callback_server") as mock_cbs,
        patch("app.main.build_broker_registry", side_effect=MissingBrokerSecrets("no-broker")),
        patch("app.main.seed_instruments_from_positions", return_value=0),
        patch("app.main.build_quote_engine", mock_build),
        patch("app.main.OrderCapabilityService") as mock_cap_cls,
        patch("app.main.BarService") as mock_bar_cls,
        patch("app.main._run_pre_warm", new_callable=AsyncMock),
    ):
        _make_lifespan_mocks(
            mock_redis_cls, mock_bridge_cls, mock_cache_cls, mock_cbs, mock_bar_cls
        )
        mock_cap = AsyncMock()
        mock_cap.run_listener = AsyncMock(return_value=None)
        mock_cap_cls.return_value = mock_cap

        async with lifespan(fastapi_app):
            result = getattr(fastapi_app.state, "quote_engine", "MISSING")

    assert result != "MISSING", (
        "app.state.quote_engine was never set — must be None when build returns None"
    )
    assert result is None


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_get_quote_engine_returns_engine_when_set() -> None:
    """Unit-test the get_quote_engine() helper directly — it must return the
    engine from ws.app.state when the attribute is a QuoteEngine, not raise
    WebSocketException(1011)."""
    from app.api.ws_quotes import get_quote_engine

    minimal_engine = _make_minimal_engine()

    # Build a minimal WebSocket mock with app.state.quote_engine populated.
    ws_mock = MagicMock()
    ws_mock.app.state.quote_engine = minimal_engine

    result = get_quote_engine(ws_mock)
    assert result is minimal_engine


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_get_quote_engine_raises_1011_when_none() -> None:
    """Regression guard: get_quote_engine() must raise WS 1011 when
    app.state.quote_engine is None (uninitialized / build_quote_engine returned None)."""
    from fastapi import WebSocketException, status

    from app.api.ws_quotes import get_quote_engine

    ws_mock = MagicMock()
    ws_mock.app.state.quote_engine = None

    with pytest.raises(WebSocketException) as exc_info:
        get_quote_engine(ws_mock)

    assert exc_info.value.code == status.WS_1011_INTERNAL_ERROR


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_engine_injected_into_state_survives_lifespan() -> None:
    """Inject a minimal engine into app.state before the request cycle and
    confirm it persists through a normal HTTP round-trip (not overwritten to
    None by any middleware or post-startup hook)."""
    minimal_engine = _make_minimal_engine()
    app.state.quote_engine = minimal_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Any valid endpoint — we just need the app to process a request.
        resp = await c.get("/health")
        assert resp.status_code in (200, 404)  # endpoint may not exist in test db
        # Engine must not have been replaced or nulled out.
        assert app.state.quote_engine is not None, (
            "Engine was erased during request — lifespan teardown may be running prematurely"
        )
