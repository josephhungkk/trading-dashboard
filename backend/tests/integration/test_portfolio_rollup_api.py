"""Phase 10b.2 §5.2 — portfolio rollup REST endpoint integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import bindparam, text

from app.core.db import SessionLocal
from app.main import app
from app.services.portfolio_rate_limiter import (
    _reset_portfolio_limiter_for_tests,
    get_portfolio_limiter,
)


@pytest.fixture(autouse=True)
def _reset_limiter() -> Generator[None]:
    """PortfolioRateLimiter is a module-level singleton; reset its
    sliding-window state between tests so 429 doesn't leak across runs.
    Reset BEFORE and AFTER each test to avoid ordering-dependent flakes.
    """
    _reset_portfolio_limiter_for_tests()
    get_portfolio_limiter()
    yield
    _reset_portfolio_limiter_for_tests()


@pytest_asyncio.fixture(autouse=True)
async def _restore_orphaned_soft_deletes() -> AsyncIterator[None]:
    """Belt-and-braces safety net for `_soft_delete_others` orphans.

    Integration tests call `_soft_delete_others([keep_id])` to isolate
    themselves from other rows, then `_restore_others(mutated)` in the
    `finally:` block. If a test crashes BEFORE the assignment to
    `mutated`, `_restore_others` never fires and real broker accounts
    stay soft-deleted forever (we hit this on 2026-05-12 — 26 real
    accounts got stranded after a session of failed integration runs).

    This fixture snapshots the pre-test deleted set, runs the test, and
    on teardown restores anything that was newly soft-deleted by the
    test BUT NOT pre-existing. Cleanup runs even if the test body raised.
    """
    async with SessionLocal() as s:
        result = await s.execute(
            text("SELECT id FROM broker_accounts WHERE deleted_at IS NOT NULL")
        )
        pre_deleted = {row[0] for row in result.fetchall()}
    yield
    async with SessionLocal() as s:
        async with s.begin():
            if pre_deleted:
                await s.execute(
                    text(
                        "UPDATE broker_accounts SET deleted_at = NULL "
                        "WHERE deleted_at IS NOT NULL "
                        "  AND id NOT IN :pre"
                    ).bindparams(bindparam("pre", expanding=True)),
                    {"pre": [str(i) for i in pre_deleted]},
                )
            else:
                await s.execute(
                    text(
                        "UPDATE broker_accounts SET deleted_at = NULL WHERE deleted_at IS NOT NULL"
                    )
                )


@pytest_asyncio.fixture
async def app_redis():
    """Resolve the same fakeredis instance the API endpoint sees via
    get_redis() → app.state.redis. The bare `redis` fixture creates a
    SEPARATE FakeRedis, so writes there are invisible to the endpoint.
    """
    return app.state.redis


async def _seed_test_account(broker: str, native: str, nlv: str = "10000") -> UUID:
    aid = uuid4()
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    """
                    INSERT INTO broker_accounts
                      (id, broker_id, account_number, mode, gateway_label,
                       currency_base, last_seen_via, last_nlv,
                       last_nlv_currency, last_nlv_at)
                    VALUES
                      (:id, CAST(:broker AS broker_id_enum), :acct, 'paper',
                       :gateway, :base, :gateway,
                       CAST(:nlv AS NUMERIC(20,8)), :native, now())
                    """
                ),
                {
                    "id": str(aid),
                    "broker": broker,
                    "acct": f"TEST-API-{aid.hex[:8]}",
                    "gateway": f"{broker}-api-test",
                    "base": native,
                    "nlv": nlv,
                    "native": native,
                },
            )
    return aid


async def _cleanup_account(aid: UUID) -> None:
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("DELETE FROM broker_accounts WHERE id = :id"),
                {"id": str(aid)},
            )


async def _soft_delete_others(keep_ids: list[UUID]) -> list[UUID]:
    """Same as the unit-test helper; isolates the test from other rows."""
    async with SessionLocal() as s:
        async with s.begin():
            result = await s.execute(
                text(
                    "UPDATE broker_accounts SET deleted_at = now() "
                    "WHERE id NOT IN :keep AND deleted_at IS NULL "
                    "RETURNING id"
                ).bindparams(bindparam("keep", expanding=True)),
                {"keep": [str(i) for i in keep_ids]},
            )
            return [row[0] for row in result.fetchall()]


async def _restore_others(mutated_ids: list[UUID]) -> None:
    if not mutated_ids:
        return
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("UPDATE broker_accounts SET deleted_at = NULL WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": [str(i) for i in mutated_ids]},
            )


@pytest.mark.asyncio
async def test_get_rollup_returns_shape_with_auth(
    test_client_admin: AsyncClient, app_redis
) -> None:
    """GET /api/portfolio/rollup with valid auth + USD account → 200 + shape."""
    await app_redis.flushdb()
    aid = await _seed_test_account("ibkr", "USD")
    mutated = await _soft_delete_others([aid])
    await app_redis.set("fx:mid:USD:GBP", "0.7912")
    try:
        resp = await test_client_admin.get("/api/portfolio/rollup?base=GBP")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "total_nlv_base" in body
        assert "accounts" in body
        assert "exposure_by_asset_class" in body
        assert body["base_currency"] == "GBP"
    finally:
        await _restore_others(mutated)
        await _cleanup_account(aid)


@pytest.mark.asyncio
async def test_get_rollup_curve_all_three_windows(
    test_client_admin: AsyncClient, app_redis
) -> None:
    """GET /rollup/curve with each valid window → 200."""
    await app_redis.flushdb()
    aid = await _seed_test_account("ibkr", "USD")
    mutated = await _soft_delete_others([aid])
    await app_redis.set("fx:mid:USD:GBP", "0.7912")
    try:
        for window in ("intraday", "30d", "1y"):
            resp = await test_client_admin.get(
                f"/api/portfolio/rollup/curve?base=GBP&window={window}"
            )
            assert resp.status_code == 200, f"{window}: {resp.text}"
            assert resp.json()["window"] == window
    finally:
        await _restore_others(mutated)
        await _cleanup_account(aid)


@pytest.mark.asyncio
async def test_get_rollup_drill_returns_shape(test_client_admin: AsyncClient, app_redis) -> None:
    """GET /rollup/drill?asset_class=STOCK → 200 + RollupDrill shape."""
    await app_redis.flushdb()
    aid = await _seed_test_account("ibkr", "USD")
    mutated = await _soft_delete_others([aid])
    await app_redis.set("fx:mid:USD:GBP", "0.7912")
    try:
        resp = await test_client_admin.get("/api/portfolio/rollup/drill?asset_class=STOCK&base=GBP")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["asset_class"] == "STOCK"
        assert body["base_currency"] == "GBP"
        assert "instruments" in body
    finally:
        await _restore_others(mutated)
        await _cleanup_account(aid)


@pytest.mark.asyncio
async def test_burst_returns_429_after_quota(test_client_admin: AsyncClient, app_redis) -> None:
    """11th request in <1s for same subject → 429 rate_limited."""
    await app_redis.flushdb()
    aid = await _seed_test_account("ibkr", "GBP")
    mutated = await _soft_delete_others([aid])
    try:
        # Default cap is 10/s; 11th must 429
        codes: list[int] = []
        for _ in range(11):
            r = await test_client_admin.get("/api/portfolio/rollup?base=GBP")
            codes.append(r.status_code)
        assert codes[:10] == [200] * 10, codes
        assert codes[10] == 429, codes
    finally:
        await _restore_others(mutated)
        await _cleanup_account(aid)


@pytest.mark.asyncio
async def test_503_when_all_fx_unavailable(test_client_admin: AsyncClient, app_redis) -> None:
    """All non-init accounts in foreign currency + no fx rates → 503
    fx_rate_unavailable. Validates the API translates the service's
    PreviewUnavailable correctly."""
    await app_redis.flushdb()
    aid_usd = await _seed_test_account("ibkr", "USD")
    aid_hkd = await _seed_test_account("futu", "HKD")
    mutated = await _soft_delete_others([aid_usd, aid_hkd])
    try:
        resp = await test_client_admin.get("/api/portfolio/rollup?base=GBP")
        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["detail"]["error"] == "fx_rate_unavailable"
    finally:
        await _restore_others(mutated)
        await _cleanup_account(aid_usd)
        await _cleanup_account(aid_hkd)
