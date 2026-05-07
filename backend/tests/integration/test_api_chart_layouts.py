"""Phase 9 Task 29 — /api/chart/layouts CRUD + read-translator + If-Match tests.

Uses a minimal standalone FastAPI app (does NOT import app.main) to avoid
breakage from parallel task changes to orders.py. Only chart_layouts.router
+ its direct dependencies are wired.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.chart_layouts import router as chart_layouts_router
from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_config, get_db, require_admin_jwt
from app.services.chart_layout_translator import InvalidLayoutSchema, translate_chart_layout

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_test_instrument_id(session: AsyncSession) -> int:
    """Return a valid instrument_id for testing (reuse existing row)."""
    row = (await session.execute(text("SELECT id FROM instruments LIMIT 1"))).one_or_none()
    if row is None:
        pytest.skip("no instruments in DB — run seed first")
    return int(row.id)


async def _cleanup(session: AsyncSession, instrument_id: int) -> None:
    """Remove any chart_layout row left by previous test."""
    await session.execute(
        text("DELETE FROM chart_layouts WHERE instrument_id = :iid"),
        {"iid": instrument_id},
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(db: AsyncSession, mock_cfg: AsyncMock) -> FastAPI:
    """Minimal app — only chart_layouts router, with auth + DB + config stubbed."""
    _app = FastAPI()
    _app.include_router(chart_layouts_router)

    async def _fake_admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    async def _fake_db() -> AsyncIterator[AsyncSession]:
        yield db

    _app.dependency_overrides[require_admin_jwt] = _fake_admin
    _app.dependency_overrides[get_db] = _fake_db
    _app.dependency_overrides[get_config] = lambda: mock_cfg
    return _app


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Per-test session using NullPool so connections never cross event-loop boundaries."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def instrument_id(db: AsyncSession) -> int:
    return await _get_test_instrument_id(db)


@pytest_asyncio.fixture
async def mock_cfg() -> AsyncMock:
    cfg = AsyncMock()
    cfg.get_int = AsyncMock(return_value=1)
    return cfg


@pytest_asyncio.fixture
async def client(
    instrument_id: int, db: AsyncSession, mock_cfg: AsyncMock
) -> AsyncIterator[AsyncClient]:
    """Async HTTP client backed by a minimal standalone app."""
    app = _make_app(db, mock_cfg)
    await _cleanup(db, instrument_id)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    await _cleanup(db, instrument_id)


# ---------------------------------------------------------------------------
# Test 1: GET 404 for unknown instrument
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_404_unknown_instrument(
    client: AsyncClient,
) -> None:
    """GET layout for instrument with no layout returns 404."""
    r = await client.get("/api/chart/layouts/999999999")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Test 2: PUT then GET round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_round_trip(client: AsyncClient, instrument_id: int) -> None:
    """PUT a layout, then GET it back; verify ETag header matches updated_at."""
    payload: dict[str, Any] = {"indicators": ["RSI"], "zoom": 1.5}

    # First PUT — row absent, so no etag check applies
    r = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": payload, "schema_version": 1},
        headers={"If-Match": '"initial"'},
    )
    assert r.status_code == 200, r.text
    put_body = r.json()
    put_etag = r.headers.get("ETag")
    assert put_etag is not None, "ETag header missing from PUT response"

    # GET should return same payload with matching ETag
    r2 = await client.get(f"/api/chart/layouts/{instrument_id}")
    assert r2.status_code == 200, r2.text
    get_body = r2.json()
    get_etag = r2.headers.get("ETag")

    assert get_body["payload"] == payload
    assert get_etag == put_etag, f"ETag mismatch: PUT={put_etag!r} GET={get_etag!r}"
    assert get_body["updated_at"] == put_body["updated_at"]


# ---------------------------------------------------------------------------
# Test 3: GET translates older schema in-memory (DB row stays at old version)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_translates_older_schema_in_memory(
    db: AsyncSession,
    instrument_id: int,
    client: AsyncClient,
) -> None:
    """Seed a row at schema_version=1; mock translator to return modified payload.

    DB row must remain at version 1 (translation is read-only).
    """
    original_payload: dict[str, Any] = {"old_key": "old_value"}

    # Seed row directly at schema_version=1
    await db.execute(
        text(
            "INSERT INTO chart_layouts (instrument_id, payload, schema_version) "
            "VALUES (:iid, CAST(:p AS JSONB), 1)"
        ),
        {"iid": instrument_id, "p": json.dumps(original_payload)},
    )
    await db.commit()

    translated_payload: dict[str, Any] = {"new_key": "new_value"}

    with patch(
        "app.api.chart_layouts.translate_chart_layout",
        return_value=translated_payload,
    ) as mock_translate:
        r = await client.get(f"/api/chart/layouts/{instrument_id}")

    assert r.status_code == 200, r.text
    body = r.json()
    # Response carries translated payload
    assert body["payload"] == translated_payload
    mock_translate.assert_called_once()

    # DB row must still be at old schema_version (not mutated)
    row = (
        await db.execute(
            text("SELECT schema_version, payload FROM chart_layouts WHERE instrument_id = :iid"),
            {"iid": instrument_id},
        )
    ).one()
    assert row.schema_version == 1, "DB row schema_version was mutated by GET"
    assert row.payload == original_payload, "DB row payload was mutated by GET"


# ---------------------------------------------------------------------------
# Test 4: PUT without If-Match returns 428
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_requires_if_match_header(client: AsyncClient, instrument_id: int) -> None:
    """PUT without If-Match header returns 428 Precondition Required."""
    r = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"k": "v"}, "schema_version": 1},
    )
    assert r.status_code == 428, r.text


# ---------------------------------------------------------------------------
# Test 5: PUT with stale If-Match returns 412
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_with_stale_if_match_returns_412(
    client: AsyncClient, instrument_id: int, db: AsyncSession
) -> None:
    """PUT with stale etag returns 412 Precondition Failed."""
    # Seed a row first
    await db.execute(
        text(
            "INSERT INTO chart_layouts (instrument_id, payload, schema_version) "
            "VALUES (:iid, CAST(:p AS JSONB), 1)"
        ),
        {"iid": instrument_id, "p": json.dumps({"v": 1})},
    )
    await db.commit()

    r = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"v": 2}, "schema_version": 1},
        headers={"If-Match": '"1970-01-01T00:00:00+00:00"'},
    )
    assert r.status_code == 412, r.text
    assert "etag_mismatch" in r.text


# ---------------------------------------------------------------------------
# Test 6: PUT with matching If-Match succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_with_matching_if_match_succeeds(client: AsyncClient, instrument_id: int) -> None:
    """Full optimistic-concurrency cycle: first write, then update with valid ETag."""
    # First write — row absent, no etag check applies
    r1 = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"version": 1}, "schema_version": 1},
        headers={"If-Match": '"initial"'},
    )
    assert r1.status_code == 200, r1.text
    etag1 = r1.headers["ETag"]

    # Second write — pass the etag from the first write
    r2 = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"version": 2}, "schema_version": 1},
        headers={"If-Match": etag1},
    )
    assert r2.status_code == 200, r2.text
    etag2 = r2.headers["ETag"]
    assert etag2 != etag1, "ETag should change after update"

    # Verify updated payload via GET
    r3 = await client.get(f"/api/chart/layouts/{instrument_id}")
    assert r3.status_code == 200, r3.text
    assert r3.json()["payload"] == {"version": 2}


# ---------------------------------------------------------------------------
# Test 7: PUT with payload > 64 KB returns 413
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_64kb_cap(client: AsyncClient, instrument_id: int) -> None:
    """PUT with payload > 64 KB returns 413 Payload Too Large."""
    big_value = "x" * 70_000
    r = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"k": big_value}, "schema_version": 1},
        headers={"If-Match": '"anything"'},
    )
    assert r.status_code == 413, r.text
    assert "64 KB" in r.text


# ---------------------------------------------------------------------------
# Test 8: DELETE returns 204; subsequent GET returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_204(client: AsyncClient, instrument_id: int) -> None:
    """DELETE existing layout returns 204; subsequent GET returns 404."""
    # Create a layout first
    r1 = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"to": "delete"}, "schema_version": 1},
        headers={"If-Match": '"initial"'},
    )
    assert r1.status_code == 200, r1.text

    # Delete it
    r2 = await client.delete(f"/api/chart/layouts/{instrument_id}")
    assert r2.status_code == 204, r2.text

    # Subsequent GET must be 404
    r3 = await client.get(f"/api/chart/layouts/{instrument_id}")
    assert r3.status_code == 404, r3.text


# ---------------------------------------------------------------------------
# Test 9: Translator idempotent (pure unit test — no DB/HTTP)
# ---------------------------------------------------------------------------


def test_translator_idempotent_same_version() -> None:
    """translate_chart_layout({}, 1, 1) returns the input unchanged."""
    payload: dict[str, Any] = {"a": 1, "b": "hello"}
    result = translate_chart_layout(payload, from_version=1, to_version=1)
    assert result == payload


# ---------------------------------------------------------------------------
# Test 10: Translator downgrade raises InvalidLayoutSchema
# ---------------------------------------------------------------------------


def test_translator_downgrade_raises() -> None:
    """translate_chart_layout({}, 2, 1) raises InvalidLayoutSchema."""
    with pytest.raises(InvalidLayoutSchema, match="cannot downgrade 2 -> 1"):
        translate_chart_layout({}, from_version=2, to_version=1)


# ---------------------------------------------------------------------------
# Test 11: HIGH-11 — concurrent PUTs with same etag: exactly one 200, one 412
# (DB-dependent — requires live Postgres)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_puts_with_same_etag_one_wins(
    db: AsyncSession, instrument_id: int, mock_cfg: AsyncMock
) -> None:
    """Two concurrent PUTs with the same valid etag: exactly one returns 200, the other 412.

    Uses two separate DB sessions to simulate concurrent requests.
    """
    from app.api.chart_layouts import _etag as _chart_etag

    # First, create the initial row so both PUTs have a valid etag to compete with.
    await _cleanup(db, instrument_id)
    await db.execute(
        text(
            "INSERT INTO chart_layouts (instrument_id, payload, schema_version) "
            "VALUES (:iid, CAST(:p AS JSONB), 1)"
        ),
        {"iid": instrument_id, "p": json.dumps({"v": 0})},
    )
    await db.commit()

    # Fetch the etag from a fresh query
    row = (
        await db.execute(
            text("SELECT updated_at FROM chart_layouts WHERE instrument_id = :iid"),
            {"iid": instrument_id},
        )
    ).one()
    shared_etag = _chart_etag(row.updated_at)

    engine2 = create_async_engine(settings.database_url, poolclass=NullPool)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)

    # Issue two concurrent PUTs via two separate HTTP clients (each with own DB session)
    async def _do_put(session: AsyncSession) -> int:
        _app = _make_app(session, mock_cfg)
        async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
            r = await c.put(
                f"/api/chart/layouts/{instrument_id}",
                json={"payload": {"v": 1}, "schema_version": 1},
                headers={"If-Match": shared_etag},
            )
            return r.status_code

    async with factory2() as s1, factory2() as s2:
        results = await asyncio.gather(
            _do_put(s1),
            _do_put(s2),
            return_exceptions=True,
        )

    await engine2.dispose()
    await _cleanup(db, instrument_id)

    # Filter out exceptions (shouldn't happen, but be safe)
    status_codes = [r for r in results if isinstance(r, int)]
    assert sorted(status_codes) == [200, 412], (
        f"Expected exactly one 200 and one 412; got: {status_codes}"
    )


# ---------------------------------------------------------------------------
# Test 12: HIGH-13/MED-24 — translator NotImplementedError returns generic 500
# Uses mocked DB session — no live Postgres required.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_db
async def test_get_translator_not_implemented_returns_generic_500_mocked() -> None:
    """GET with a future schema_version triggers NotImplementedError; response detail is generic.

    Uses a fully mocked DB session + config — no live Postgres required.
    """
    from datetime import UTC, datetime
    from typing import NamedTuple
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.chart_layouts import router as chart_layouts_router
    from app.core.cf_access import AdminIdentity
    from app.core.deps import get_config, get_db, require_admin_jwt

    class _FakeRow(NamedTuple):
        payload: dict[str, Any]
        schema_version: int
        updated_at: datetime

    fake_row = _FakeRow(
        payload={"seed": True},
        schema_version=1,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    # Mock DB session that returns the fake row
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = fake_row
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.return_value = mock_result

    # Mock cfg returning schema_version=2 (future version — triggers NotImplementedError)
    mock_cfg = AsyncMock()
    mock_cfg.get_int = AsyncMock(return_value=2)

    _app = FastAPI()
    _app.include_router(chart_layouts_router)

    async def _fake_admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    async def _fake_db():  # type: ignore[return]
        yield mock_session

    _app.dependency_overrides[require_admin_jwt] = _fake_admin
    _app.dependency_overrides[get_db] = _fake_db
    _app.dependency_overrides[get_config] = lambda: mock_cfg

    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
        r = await c.get("/api/chart/layouts/42")

    assert r.status_code == 500, r.text
    detail = r.json().get("detail", "")
    assert "not yet implemented" not in detail, f"Internal exception leaked: {detail!r}"
    assert "schema_translation_failed" in detail, f"Generic message missing: {detail!r}"


# ---------------------------------------------------------------------------
# Test 13: MED-25 — ETag comparison uses full quoted form
# (DB-dependent — requires live Postgres)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_etag_uses_quoted_form(client: AsyncClient, instrument_id: int) -> None:
    """PUT with ETag in correct quoted form succeeds; bare (unquoted) fails with 412."""
    # First write — creates the row
    r1 = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"v": 1}, "schema_version": 1},
        headers={"If-Match": '"initial"'},
    )
    assert r1.status_code == 200, r1.text
    quoted_etag = r1.headers["ETag"]  # e.g. '"2026-05-07T..."'
    assert quoted_etag.startswith('"') and quoted_etag.endswith('"'), (
        f"ETag not properly quoted: {quoted_etag!r}"
    )

    # Try with bare (unquoted) etag — must fail
    bare_etag = quoted_etag.strip('"')
    r2 = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"v": 2}, "schema_version": 1},
        headers={"If-Match": bare_etag},
    )
    assert r2.status_code == 412, f"Expected 412 for bare ETag, got {r2.status_code}: {r2.text}"

    # Try with the correct quoted etag — must succeed
    r3 = await client.put(
        f"/api/chart/layouts/{instrument_id}",
        json={"payload": {"v": 2}, "schema_version": 1},
        headers={"If-Match": quoted_etag},
    )
    assert r3.status_code == 200, f"Expected 200 for quoted ETag, got {r3.status_code}: {r3.text}"
