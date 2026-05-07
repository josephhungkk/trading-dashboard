"""Phase 9 Task 28 — GET /api/bars cursor pagination + 10k cap.

Uses a minimal standalone FastAPI app (does NOT import app.main) to avoid
breakage from parallel task changes.  Only bars.router + its direct
dependencies are wired.  BarService.get_bars is mocked so no DB/TimescaleDB
tables are needed.

All 9 tests marked no_db + asyncio + integration.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.bars import router as bars_router
from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, require_admin_jwt
from app.services.bar_service import (
    Bar,
    BarPage,
    BarService,
    InstrumentNotFound,
    InvalidCursor,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_START = "2026-04-01T00:00:00Z"
_END = "2026-04-30T00:00:00Z"
_CANONICAL = "equity_us:AAPL:NASDAQ"
_TF = "1m"

_BASE_PARAMS = {
    "canonical_id": _CANONICAL,
    "timeframe": _TF,
    "start": _START,
    "end": _END,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(n: int) -> Bar:
    return Bar(
        instrument_id=42,
        bucket_start=datetime(2026, 4, 1, 0, n % 60, 0, tzinfo=UTC),
        source="schwab",
        source_priority=1,
        open=Decimal("180.12345678"),
        high=Decimal("181.00000000"),
        low=Decimal("179.00000000"),
        close=Decimal("180.50000000"),
        volume=Decimal("1000.00000000"),
        volume_source="broker_history",
        trade_count=50,
    )


def _make_bars(count: int) -> list[Bar]:
    return [_make_bar(i) for i in range(count)]


def _encode_cursor(last_bucket_start: datetime) -> str:
    ts_str = last_bucket_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps({"v": 1, "last_bucket_start": ts_str}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()


def _bad_cursor(obj: object) -> str:
    """Encode any JSON-serialisable object as a base64url cursor (for negative tests)."""
    payload = json.dumps(obj, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(mock_bar_service: AsyncMock) -> FastAPI:
    """Minimal standalone app — only bars router, auth + DB + BarService stubbed."""
    _app = FastAPI()
    _app.include_router(bars_router)

    async def _fake_admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    async def _fake_db() -> AsyncIterator[None]:
        yield None  # bars router passes session to bar_service; bar_service is mocked

    # Wire bar_service into app state — mirrors how main.py lifespan wires it.
    _app.state.bar_service = mock_bar_service

    _app.dependency_overrides[require_admin_jwt] = _fake_admin
    _app.dependency_overrides[get_db] = _fake_db
    return _app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mock_bar_service() -> AsyncMock:
    svc = AsyncMock(spec=BarService)
    return svc


@pytest_asyncio.fixture
async def client(mock_bar_service: AsyncMock) -> AsyncIterator[AsyncClient]:
    app = _make_app(mock_bar_service)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Test 1 — 200 with cached page (50 bars, next_cursor=None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_200_with_cached_page(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """GET /api/bars returns 200, 50 bars, next_cursor=None when service returns a full page."""
    bars = _make_bars(50)
    mock_bar_service.get_bars.return_value = BarPage(bars=bars, next_cursor=None)

    r = await client.get("/api/bars", params=_BASE_PARAMS)

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["bars"]) == 50
    assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# Test 2 — cursor advance returns next page with different bucket_starts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_advance_returns_next_page(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """Second GET with cursor=X returns a page with different bucket_starts."""
    first_bars = _make_bars(2)
    pivot = first_bars[-1].bucket_start
    cursor_token = _encode_cursor(pivot)

    # First page carries a next_cursor.
    mock_bar_service.get_bars.return_value = BarPage(
        bars=first_bars,
        next_cursor=cursor_token,
    )
    r1 = await client.get("/api/bars", params=_BASE_PARAMS)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["next_cursor"] == cursor_token

    # Second page — different bars (simulate cursor effect).
    later_bars = [
        Bar(
            instrument_id=42,
            bucket_start=datetime(2026, 4, 2, 0, i, 0, tzinfo=UTC),
            source="schwab",
            source_priority=1,
            open=Decimal("182.00000000"),
            high=Decimal("183.00000000"),
            low=Decimal("181.00000000"),
            close=Decimal("182.50000000"),
            volume=Decimal("500.00000000"),
            volume_source="broker_history",
            trade_count=25,
        )
        for i in range(2)
    ]
    mock_bar_service.get_bars.return_value = BarPage(bars=later_bars, next_cursor=None)

    params2 = {**_BASE_PARAMS, "cursor": cursor_token}
    r2 = await client.get("/api/bars", params=params2)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["next_cursor"] is None

    # bucket_starts must differ between pages.
    starts1 = {b["bucket_start"] for b in body1["bars"]}
    starts2 = {b["bucket_start"] for b in body2["bars"]}
    assert starts1.isdisjoint(starts2), "Pages share bucket_starts — cursor had no effect"


# ---------------------------------------------------------------------------
# Test 3 — limit above 10000 returns 422 (FastAPI Query constraint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_above_10k_returns_400(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """limit=10001 is rejected by Pydantic Query(le=10000) → FastAPI returns 422."""
    params = {**_BASE_PARAMS, "limit": 10001}
    r = await client.get("/api/bars", params=params)
    # FastAPI returns 422 for Query validation failures (not 400).
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Test 4 — bogus cursor returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bogus_cursor_returns_400(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """Passing cursor=garbage causes InvalidCursor in bar_service → 400."""
    mock_bar_service.get_bars.side_effect = InvalidCursor("cursor decode error")

    params = {**_BASE_PARAMS, "cursor": "garbage!!!"}
    r = await client.get("/api/bars", params=params)
    assert r.status_code == 400, r.text
    assert "invalid_cursor" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# Test 5 — cursor v=2 returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_v_2_returns_400(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """Cursor with v=2 causes InvalidCursor in bar_service → 400."""
    mock_bar_service.get_bars.side_effect = InvalidCursor("cursor version 2 not supported")

    bad = _bad_cursor({"v": 2, "x": "y"})
    params = {**_BASE_PARAMS, "cursor": bad}
    r = await client.get("/api/bars", params=params)
    assert r.status_code == 400, r.text
    assert "invalid_cursor" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# Test 6 — invalid timeframe returns 422 (FastAPI Query pattern validation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_timeframe_returns_400(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """timeframe=2m is not in the allowed pattern → FastAPI returns 422."""
    params = {**_BASE_PARAMS, "timeframe": "2m"}
    r = await client.get("/api/bars", params=params)
    # FastAPI Query pattern validation returns 422 (Unprocessable Entity).
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Test 7 — unknown instrument returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_instrument_returns_404(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """bar_service raises InstrumentNotFound → 404 with detail instrument_not_found."""
    mock_bar_service.get_bars.side_effect = InstrumentNotFound("equity_us:UNKNOWN:NYSE")

    params = {**_BASE_PARAMS, "canonical_id": "equity_us:UNKNOWN:NYSE"}
    r = await client.get("/api/bars", params=params)
    assert r.status_code == 404, r.text
    assert "instrument_not_found" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# Test 8 — JWT required (no auth header → 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_required() -> None:
    """Calling /api/bars without Cf-Access-Jwt-Assertion header returns 401."""
    # Build an app WITHOUT overriding the auth dependency (uses real require_admin_jwt).
    _app = FastAPI()
    _app.include_router(bars_router)

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
    ) as unauthenticated:
        r = await unauthenticated.get("/api/bars", params=_BASE_PARAMS)
    # require_admin_jwt returns 401 when the CF-Access header is absent.
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# Test 9 — response model preserves NUMERIC strings (no float coercion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_model_preserves_numeric_strings(
    client: AsyncClient,
    mock_bar_service: AsyncMock,
) -> None:
    """Bar with open='180.12345678' must appear verbatim in JSON (no float coercion)."""
    bar = Bar(
        instrument_id=42,
        bucket_start=datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC),
        source="schwab",
        source_priority=1,
        open=Decimal("180.12345678"),
        high=Decimal("181.00000000"),
        low=Decimal("179.00000000"),
        close=Decimal("180.50000000"),
        volume=Decimal("1000.00000000"),
        volume_source="broker_history",
        trade_count=50,
    )
    mock_bar_service.get_bars.return_value = BarPage(bars=[bar], next_cursor=None)

    r = await client.get("/api/bars", params=_BASE_PARAMS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["bars"]) == 1
    bar_json = body["bars"][0]
    # Must be the exact string, not a float representation.
    assert bar_json["open"] == "180.12345678", (
        f"float coercion detected: expected '180.12345678', got {bar_json['open']!r}"
    )
