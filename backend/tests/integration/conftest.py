"""Integration test conftest — overrides _apply_migrations + shared instrument fixtures.

The dev NUC DB does not have TimescaleDB installed locally, so Alembic cannot
run migrations 0023+ (which require CREATE EXTENSION timescaledb). The needed
tables (chart_layouts, instruments, app_config) are created manually for
development. In CI the Timescale Docker image is used and full migrations run.

This conftest replaces the autouse session-scope _apply_migrations fixture
with a no-op so integration tests can run against the already-migrated dev DB,
and provides shared instrument-seeding fixtures used across alembic_002[4-7]
and bar/chart-layout integration tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_auth import require_jwt
from app.main import app
from app.services.ai.exceptions import AIToolCallingNotSupportedError
from app.services.ai.jobs import JobRecord
from app.services.ai.types import CompletionRequest, CompletionResult
from app.services.common.rate_limiter import RateLimitExceededError


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """No-op override — dev DB tables created manually; CI runs full migrations."""
    return


@pytest_asyncio.fixture
async def seed_instrument_aapl() -> Callable[[AsyncSession], Awaitable[int]]:
    """Insert a single AAPL test instrument and return its id.

    Used by tests/integration/test_alembic_0024.py, _0026.py, _0027.py, and
    test_active_set_query.py — they all need an instrument row to satisfy
    FK constraints on chart_layouts / bars / bar_backfill_jobs.
    """

    async def _seed(session: AsyncSession) -> int:
        result = await session.execute(
            text(
                """
                INSERT INTO instruments (canonical_id, asset_class, primary_exchange, currency)
                VALUES ('stock:AAPL:US', 'STOCK', 'NASDAQ', 'USD')
                ON CONFLICT (canonical_id) DO UPDATE SET updated_at = now()
                RETURNING id
                """
            )
        )
        await session.flush()
        return int(result.scalar_one())

    return _seed


@pytest_asyncio.fixture
async def bulk_seed_1500_instruments() -> Callable[[AsyncSession], Awaitable[int]]:
    """Insert 1500 synthetic test instruments — used by active-set cap tests."""

    async def _seed(session: AsyncSession) -> int:
        await session.execute(
            text(
                """
                INSERT INTO instruments (canonical_id, asset_class, primary_exchange, currency)
                SELECT
                    'stock:TEST' || i || ':US',
                    'STOCK',
                    'NASDAQ',
                    'USD'
                FROM generate_series(1, 1500) AS s(i)
                ON CONFLICT (canonical_id) DO NOTHING
                """
            )
        )
        await session.flush()
        return 1500

    return _seed


class _FakeRouter:
    def __init__(self) -> None:
        self.jobs: dict[UUID, JobRecord] = {}
        self.cancelled_job_ids: list[UUID] = []

    async def complete(
        self,
        req: CompletionRequest,
        *,
        jwt_subject: str,
    ) -> CompletionResult:
        if req.tools is not None:
            raise AIToolCallingNotSupportedError("tool calling is not supported")
        return CompletionResult(
            request_id=uuid4(),
            text=f"ok:{jwt_subject}",
            provider="test-provider",
            model="test-model",
            prompt_tokens=1,
            completion_tokens=2,
            wall_time_ms=3,
        )

    async def submit_job(
        self,
        req: CompletionRequest,
        *,
        jwt_subject: str,
    ) -> UUID:
        job_id = uuid4()
        self.jobs[job_id] = _job_record(
            job_id=job_id,
            jwt_subject=jwt_subject,
            status="pending",
            capability=req.capability.value,
        )
        return job_id

    async def get_job(self, job_id: UUID) -> JobRecord | None:
        return self.jobs.get(job_id)

    async def cancel_job(self, job_id: UUID) -> None:
        self.cancelled_job_ids.append(job_id)

    def job_record(
        self,
        *,
        job_id: UUID,
        jwt_subject: str,
        status: str = "completed",
        capability: str = "CODING",
    ) -> JobRecord:
        return _job_record(
            job_id=job_id,
            jwt_subject=jwt_subject,
            status=status,
            capability=capability,
        )


class _FakeCapabilitySvc:
    def __init__(self, capability_map: dict[str, list[dict[str, str]]]) -> None:
        self._capability_map = capability_map

    async def get_map(self) -> dict[str, list[dict[str, str]]]:
        return self._capability_map


class _FakeRateLimiter:
    @asynccontextmanager
    async def check_and_acquire(
        self,
        jwt_subject: str,
        capability: str,
    ) -> AsyncIterator[None]:
        yield


class _RateLimitedFakeRateLimiter:
    @asynccontextmanager
    async def check_and_acquire(
        self,
        jwt_subject: str,
        capability: str,
    ) -> AsyncIterator[None]:
        raise RateLimitExceededError("rate limited")
        yield


def _job_record(
    *,
    job_id: UUID,
    jwt_subject: str,
    status: str = "completed",
    capability: str = "CODING",
) -> JobRecord:
    started_at = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    return JobRecord(
        id=job_id,
        jwt_subject=jwt_subject,
        status=status,
        capability=capability,
        request_jsonb={"messages": [{"role": "user", "content": "hi"}]},
        response_jsonb={"text": "done"},
        error=None,
        started_at=started_at,
        warming_started_at=datetime(2026, 5, 13, 10, 0, 1, tzinfo=UTC),
        inferring_started_at=datetime(2026, 5, 13, 10, 0, 2, tzinfo=UTC),
        completed_at=datetime(2026, 5, 13, 10, 0, 3, tzinfo=UTC),
        cancel_requested=False,
    )


@pytest.fixture
async def authed_client() -> AsyncIterator[AsyncClient]:
    async def _jwt_subject() -> str:
        return "ci@example.com"

    original_state: dict[str, Any] = {
        "ai_router": getattr(app.state, "ai_router", None),
        "ai_rate_limiter": getattr(app.state, "ai_rate_limiter", None),
        "capability_svc": getattr(app.state, "capability_svc", None),
    }
    missing = {name for name in original_state if not hasattr(app.state, name)}

    app.state.ai_router = _FakeRouter()
    app.state.ai_rate_limiter = _FakeRateLimiter()
    app.state.capability_svc = _FakeCapabilitySvc(
        {
            "LOCAL_ONLY": [{"provider": "ollama-nuc", "model": "llama3.1"}],
        }
    )
    app.dependency_overrides[require_jwt] = _jwt_subject

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()
    for name, value in original_state.items():
        if name in missing:
            try:
                delattr(app.state, name)
            except AttributeError:
                pass
        else:
            setattr(app.state, name, value)


@pytest.fixture
async def fake_router(authed_client: AsyncClient) -> _FakeRouter:
    return app.state.ai_router


@pytest.fixture
async def authed_client_with_empty_local_capability_map(
    authed_client: AsyncClient,
) -> AsyncClient:
    app.state.capability_svc = _FakeCapabilitySvc({})
    return authed_client


@pytest.fixture
async def authed_client_rate_limited(
    authed_client: AsyncClient,
) -> AsyncClient:
    app.state.ai_rate_limiter = _RateLimitedFakeRateLimiter()
    return authed_client


@pytest.fixture
async def authed_client_with_fake_router(
    authed_client: AsyncClient,
) -> AsyncClient:
    app.state.ai_router = _FakeRouter()
    return authed_client
