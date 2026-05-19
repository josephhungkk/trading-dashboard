from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest_asyncio.fixture
async def db(db_session):
    yield db_session


@pytest_asyncio.fixture(autouse=True)
async def _earnings_auth_override() -> AsyncIterator[None]:
    from app.api.ws_auth import require_jwt
    from app.main import app

    app.dependency_overrides[require_jwt] = lambda: "earnings-test@example.com"
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)


@pytest.mark.asyncio
async def test_0060_migration_tables_exist(db):
    from sqlalchemy import text

    result = await db.execute(
        text(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('earnings_events', 'earnings_hooks', 'hook_audit')
            """
        )
    )
    names = {r[0] for r in result}
    assert "earnings_events" in names
    assert "earnings_hooks" in names
    assert "hook_audit" in names


def test_earnings_event_model():
    from app.services.earnings.schemas import EarningsEvent

    ev = EarningsEvent(
        id=uuid.uuid4(),
        instrument_id=1,
        canonical_id="AAPL.XNAS",
        announced_date=date(2024, 5, 1),
        source="nasdaq_api",
        source_priority=2,
        confirmed=True,
        captured_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert ev.source == "nasdaq_api"


def test_earnings_hook_model():
    from app.services.earnings.schemas import EarningsHook

    hook = EarningsHook(
        id=uuid.uuid4(),
        instrument_id=1,
        account_id=uuid.uuid4(),
        jwt_subject="user:abc",
        hook_type="auto_flat",
        minutes_before=30,
        enabled=True,
        created_at=datetime.now(UTC),
    )
    assert hook.hook_type == "auto_flat"


def test_earnings_hook_minutes_before_minimum():
    from app.services.earnings.schemas import EarningsHook

    with pytest.raises(ValueError):
        EarningsHook(
            id=uuid.uuid4(),
            instrument_id=1,
            account_id=uuid.uuid4(),
            jwt_subject="user:abc",
            hook_type="auto_flat",
            minutes_before=5,
            enabled=True,
            created_at=datetime.now(UTC),
        )


def test_nasdaq_poller_parses_response():
    from app.services.earnings.nasdaq_calendar import NasdaqCalendarPoller

    sample = {
        "data": {
            "rows": [
                {
                    "symbol": "AAPL",
                    "earningsDate": "2024-04-25",
                    "time": "AMC",
                    "epsForecast": "1.52",
                }
            ]
        }
    }
    poller = NasdaqCalendarPoller()
    rows = poller._parse_response(sample)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["source"] == "nasdaq_api"
    assert rows[0]["source_priority"] == 2
    assert rows[0]["time_of_day"] == "after_close"


def test_finnhub_poller_parses_response():
    from app.services.earnings.finnhub_calendar import FinnhubCalendarPoller

    sample = {
        "earningsCalendar": [
            {
                "symbol": "GOOGL",
                "date": "2024-04-24",
                "hour": "bmo",
                "epsEstimate": 1.85,
            }
        ]
    }
    poller = FinnhubCalendarPoller()
    rows = poller._parse_response(sample)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "GOOGL"
    assert rows[0]["source"] == "finnhub_api"
    assert rows[0]["source_priority"] == 1
    assert rows[0]["time_of_day"] == "before_open"


def test_place_order_internal_signature():
    import inspect

    from app.services.orders_service import place_order_internal

    sig = inspect.signature(place_order_internal)
    assert "issuer" in sig.parameters
    assert "jwt_subject" in sig.parameters
    assert "bypass_pdt_when_closing" in sig.parameters


@pytest.mark.asyncio
async def test_hook_executor_dedup_redis_nx(redis):
    from app.services.earnings.hook_executor import HookExecutor

    executor = HookExecutor.__new__(HookExecutor)
    hook_id = uuid.uuid4()
    event_id = uuid.uuid4()
    claimed = await executor._claim_redis(redis, hook_id, event_id)
    assert claimed is True
    claimed2 = await executor._claim_redis(redis, hook_id, event_id)
    assert claimed2 is False


def test_hook_executor_options_side_resolution():
    from app.services.earnings.hook_executor import HookExecutor

    executor = HookExecutor.__new__(HookExecutor)
    assert executor._resolve_flat_side("OPTION", 5) == "sell_to_close"
    assert executor._resolve_flat_side("OPTION", -3) == "buy_to_close"
    assert executor._resolve_flat_side("STOCK", 100) == "sell"


@pytest.mark.asyncio
async def test_get_earnings_returns_list(client, auth_headers):
    resp = await client.get("/api/earnings", headers=auth_headers)
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_earnings_hook_minutes_before_minimum_enforced(client, auth_headers):
    resp = await client.post(
        "/api/earnings/hooks",
        json={
            "instrument_id": 1,
            "account_id": str(uuid.uuid4()),
            "hook_type": "auto_flat",
            "minutes_before": 5,
        },
        headers=auth_headers,
    )
    assert resp.status_code in (403, 422)
