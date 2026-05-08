"""Phase 7b.1 retro — CRIT-2 regression: SourceRouter must be called from
QuoteEngine.subscribe so that SidecarStream.add() is actually invoked.

Before the fix, ``self._router`` was stored but never called in
``subscribe()``. ``registry.get_route()`` always returned ``None`` →
``_group_by_source()`` produced an empty dict → no ``SidecarStream`` ever
received subscribe frames → no quotes flowed.

These tests assert the full automatic chain:

    subscribe(ws_id, symbols)
        → _assign_routes(added)
            → router.route(instrument)   # must be called automatically
            → registry.set_route(...)    # must persist the route
        → _group_by_source(added)        # must see the routes now
        → stream.add(canonicals)         # must be called for each symbol

Unlike ``test_quote_engine_e2e.py`` which pre-seeds routes via
``registry.set_route()`` before calling subscribe, these tests verify that
the route assignment happens automatically inside subscribe().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.instruments import AssetClass, Instrument
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceHealthState, SourceRouter

# Tests in this module are pure in-memory unit tests — no DB required.
pytestmark = pytest.mark.no_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instrument(canonical_id: str, asset_class: AssetClass = AssetClass.STOCK) -> Instrument:
    """Build a MagicMock that satisfies the Instrument interface used by SourceRouter.route().

    SQLAlchemy ORM instances cannot be constructed via ``__new__`` without a
    live engine; using MagicMock avoids that requirement while preserving the
    attribute contract (asset_class, primary_exchange, canonical_id) that
    SourceRouter reads.

    canonical_id MUST use colon-separated format (e.g. "stock:AAPL:US") so
    that ``canonical_id_components()`` can parse the country component.
    Exchange must be a known code (NASDAQ → US) so the fallback path also works.
    """
    inst = MagicMock(spec=Instrument)
    inst.id = 1
    inst.canonical_id = canonical_id
    inst.asset_class = asset_class
    inst.primary_exchange = "NASDAQ"
    inst.currency = "USD"
    inst.display_name = canonical_id
    inst.meta = {}
    return inst  # type: ignore[return-value]


def _make_stream_mock() -> MagicMock:
    """Build a MagicMock SidecarStream whose add/remove/close are awaitable."""
    stream = MagicMock()
    stream.add = AsyncMock()
    stream.remove = AsyncMock()
    stream.run = AsyncMock()
    stream.stop = MagicMock()
    stream.close_channel = AsyncMock()
    stream.request_reconnect = MagicMock()
    return stream


def _make_engine(
    source: str = "schwab",
    stream: MagicMock | None = None,
    priority: dict[str, list[str]] | None = None,
) -> tuple[QuoteEngine, MagicMock]:
    """Assemble a QuoteEngine with one mocked SidecarStream and a healthy source.

    Returns ``(engine, stream_mock)`` — callers patch ``_lookup_instrument``
    and supply a fake ``db_factory`` to exercise the routing path.
    """
    if stream is None:
        stream = _make_stream_mock()

    health = SourceHealthMap()
    health.set_state(source, SourceHealthState.HEALTHY)

    effective_priority = priority or {"stock.US": [source]}
    router = SourceRouter(
        config={
            "quote_source_priority": effective_priority,
            "ibkr_gateway_quote_assignment": {},
            "ibkr_gateway_quote_fallback": [],
        },
        health=health,
    )
    registry = SubscriptionRegistry(
        cap_per_ws=100,
        cap_global=1000,
        sub_rate_limit_per_minute=600,
    )
    redis_mock = AsyncMock()
    redis_mock.publish = AsyncMock(return_value=0)

    engine = QuoteEngine(
        registry=registry,
        router=router,
        redis=redis_mock,
        streams={source: stream},
        db_factory=None,  # unit-test mode; tests inject a fake db_factory
    )
    return engine, stream


def _fake_db_factory(session_mock: AsyncMock) -> MagicMock:
    """Return a callable that yields ``session_mock`` as an async context manager."""
    factory = MagicMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = session_mock
    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_subscribe_calls_stream_add_for_all_5_symbols() -> None:
    """Core CRIT-2 regression: after subscribe(), SidecarStream.add() must
    be called and collectively cover all 5 symbols (batched or individual)."""
    canonicals = [f"stock:SYM{i}:US" for i in range(5)]
    instruments = {c: _make_instrument(c) for c in canonicals}

    engine, stream = _make_engine(source="schwab")
    session_mock = AsyncMock()

    async def _fake_lookup(session: object, canonical_id: str) -> Instrument | None:
        return instruments.get(canonical_id)

    engine._db_factory = _fake_db_factory(session_mock)

    ws_id = uuid4()
    with patch.object(engine, "_lookup_instrument", side_effect=_fake_lookup):
        diff = await engine.subscribe(ws_id, canonicals)

    # All 5 must be accepted.
    assert set(diff.added) == set(canonicals), (
        f"Expected all 5 accepted, got added={diff.added} rejected={diff.rejected}"
    )

    # stream.add() must have been called with all 5 symbols (may be batched).
    assert stream.add.called, (
        "CRIT-2 regression: SidecarStream.add() was never called — "
        "router.route() or registry.set_route() is missing from subscribe()"
    )
    all_forwarded: set[str] = set()
    for call in stream.add.call_args_list:
        forwarded = call.args[0] if call.args else call.kwargs.get("canonical_ids", [])
        all_forwarded.update(forwarded)

    assert all_forwarded == set(canonicals), (
        f"SidecarStream.add() received {all_forwarded!r}, expected {set(canonicals)!r}"
    )


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_subscribe_sets_route_in_registry() -> None:
    """_assign_routes() must call registry.set_route() so that
    registry.get_route() returns the correct source after subscribe()."""
    canonical = "stock:AAPL:US"
    instrument = _make_instrument(canonical)
    engine, _ = _make_engine(source="schwab")
    session_mock = AsyncMock()

    async def _lookup(session: object, cid: str) -> Instrument | None:
        return instrument if cid == canonical else None

    engine._db_factory = _fake_db_factory(session_mock)

    ws_id = uuid4()
    with patch.object(engine, "_lookup_instrument", side_effect=_lookup):
        await engine.subscribe(ws_id, [canonical])

    route = engine._registry.get_route(canonical)
    assert route == "schwab", (
        f"Expected route='schwab', got {route!r} — set_route() was not called by _assign_routes()"
    )


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_subscribe_no_stream_add_when_no_db_factory() -> None:
    """When db_factory is None (bare unit-test mode), _assign_routes
    short-circuits and stream.add() is NOT called (no route assigned).

    This documents the expected contract — callers must provide db_factory
    for routing to function.
    """
    canonicals = ["stock:SYM0:US", "stock:SYM1:US"]
    engine, stream = _make_engine(source="schwab")
    # db_factory is already None from _make_engine

    ws_id = uuid4()
    await engine.subscribe(ws_id, canonicals)

    # No DB → no routes assigned → no stream.add() calls.
    stream.add.assert_not_called()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_subscribe_unknown_instrument_skips_stream_add() -> None:
    """If _lookup_instrument returns None (symbol not seeded in DB), the
    symbol is accepted by the registry but NOT forwarded to any stream."""
    canonical = "stock:UNKNOWN:US"
    engine, stream = _make_engine(source="schwab")
    session_mock = AsyncMock()

    async def _not_found(session: object, cid: str) -> None:
        return None

    engine._db_factory = _fake_db_factory(session_mock)

    ws_id = uuid4()
    with patch.object(engine, "_lookup_instrument", side_effect=_not_found):
        diff = await engine.subscribe(ws_id, [canonical])

    # Registry accepts it (format is valid from registry's POV).
    assert canonical in diff.added
    # Stream must NOT receive it — no route was assigned.
    stream.add.assert_not_called()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_second_subscribe_does_not_double_call_stream_add() -> None:
    """Subscribing the same symbol from a second WS connection must NOT
    call stream.add() again — the global refcount 1→2 transition means
    the sidecar already holds the subscription.
    """
    canonical = "stock:MSFT:US"
    instrument = _make_instrument(canonical)
    engine, stream = _make_engine(source="schwab")

    lookup_call_count = 0

    async def _lookup(session: object, cid: str) -> Instrument | None:
        nonlocal lookup_call_count
        lookup_call_count += 1
        return instrument if cid == canonical else None

    def _fresh_factory() -> MagicMock:
        s = AsyncMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=False)
        f = MagicMock(return_value=s)
        return f

    ws1 = uuid4()
    ws2 = uuid4()
    with patch.object(engine, "_lookup_instrument", side_effect=_lookup):
        engine._db_factory = _fresh_factory()
        await engine.subscribe(ws1, [canonical])
        engine._db_factory = _fresh_factory()
        # Second WS subscribes same symbol — global refcount goes 1→2.
        await engine.subscribe(ws2, [canonical])

    # stream.add() called exactly once (only the first 0→1 global transition).
    assert stream.add.call_count == 1, (
        f"stream.add() called {stream.add.call_count} times; expected exactly 1"
    )
