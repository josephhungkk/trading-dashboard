from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_auth import require_jwt
from app.core.deps import get_redis
from app.main import app


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest_asyncio.fixture
async def _bots_auth_override() -> AsyncIterator[None]:
    app.dependency_overrides[require_jwt] = lambda: "bots-advisor-test@example.com"
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)


@pytest_asyncio.fixture
async def bots_client(_bots_auth_override: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _create_bot(client: AsyncClient, auth_headers: dict[str, str]) -> str:
    resp = await client.post(
        "/api/bots",
        json={
            "name": f"AdvisorBot-{uuid4()}",
            "strategy_file": "advisor.py",
            "params_json": {},
            "bar_timeframe": "1m",
            "mode": "paper",
            "account_ids": [],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _seed_account_id(db: AsyncSession) -> UUID:
    row = await db.execute(text("SELECT id FROM broker_accounts ORDER BY created_at LIMIT 1"))
    account_id = row.scalar_one()
    return account_id


async def _insert_decision(
    db: AsyncSession,
    *,
    bot_id: str,
    account_id: UUID,
    created_at: datetime,
    verdict: str = "approve",
    canonical_id: str = "equity_us:AAPL:NASDAQ",
) -> int:
    row = await db.execute(
        text(
            """
            INSERT INTO bot_advisor_decisions (
                bot_id, account_id, canonical_id, intent, context_summary,
                prompt_version, verdict, reasoning, confidence, advice_tags,
                provider, model, fallback_chain, latency_ms, effective_mode,
                created_at
            )
            VALUES (
                :bot_id, :account_id, :canonical_id, CAST(:intent AS jsonb),
                CAST(:context_summary AS jsonb), 1, :verdict, :reasoning,
                :confidence, :advice_tags, :provider, :model, :fallback_chain,
                :latency_ms, :effective_mode, :created_at
            )
            RETURNING id
            """
        ),
        {
            "bot_id": bot_id,
            "account_id": account_id,
            "canonical_id": canonical_id,
            "intent": json.dumps({"canonical_id": canonical_id}),
            "context_summary": json.dumps({"bar_count": 0}),
            "verdict": verdict,
            "reasoning": "looks fine",
            "confidence": 0.75,
            "advice_tags": ["test"],
            "provider": "test-provider",
            "model": "test-model",
            "fallback_chain": ["test-model"],
            "latency_ms": 12,
            "effective_mode": "OBSERVE",
            "created_at": created_at,
        },
    )
    decision_id = row.scalar_one()
    await db.commit()
    return decision_id


def _valid_config(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "OBSERVE",
        "capability": "REASONING",
        "local_only": False,
        "timeout_ms": 3000,
        "daily_budget_usd": "5.00",
        "max_qps": 2.0,
        "auto_pause_threshold": 0,
        "auto_pause_window_seconds": 300,
        "min_veto_confidence": 0.0,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_get_advisor_config_returns_default(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)

    resp = await bots_client.get(f"/api/bots/{bot_id}/advisor-config", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["bot_id"] == bot_id
    assert data["config"]["mode"] == "OFF"
    assert data["account_overrides"] == {}


@pytest.mark.asyncio
async def test_put_advisor_config_updates_db(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)

    resp = await bots_client.put(
        f"/api/bots/{bot_id}/advisor-config",
        json=_valid_config(mode="VETO", min_veto_confidence=0.6),
        headers={**auth_headers, "x-csrf-nonce": "nonce"},
    )

    assert resp.status_code == 200
    assert resp.json()["config"]["mode"] == "VETO"
    row = await db_session.execute(
        text("SELECT advisor_config FROM bots WHERE id = :id"), {"id": bot_id}
    )
    stored = row.scalar_one()
    assert stored["mode"] == "VETO"
    assert stored["min_veto_confidence"] == 0.6


@pytest.mark.asyncio
async def test_put_advisor_config_requires_csrf(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)

    resp = await bots_client.put(
        f"/api/bots/{bot_id}/advisor-config",
        json=_valid_config(),
        headers=auth_headers,
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_advisor_config_invalid_mode_returns_422(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)

    resp = await bots_client.put(
        f"/api/bots/{bot_id}/advisor-config",
        json=_valid_config(mode="INVALID"),
        headers={**auth_headers, "x-csrf-nonce": "nonce"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_advisor_decisions_empty(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)

    resp = await bots_client.get(f"/api/bots/{bot_id}/advisor-decisions", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_before": None}


@pytest.mark.asyncio
async def test_get_advisor_decisions_pagination(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)
    account_id = await _seed_account_id(db_session)
    base = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    await _insert_decision(db_session, bot_id=bot_id, account_id=account_id, created_at=base)
    await _insert_decision(
        db_session, bot_id=bot_id, account_id=account_id, created_at=base + timedelta(seconds=1)
    )
    await _insert_decision(
        db_session, bot_id=bot_id, account_id=account_id, created_at=base + timedelta(seconds=2)
    )

    resp = await bots_client.get(
        f"/api/bots/{bot_id}/advisor-decisions",
        params={"limit": 2},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["next_before"] == data["items"][-1]["created_at"]
    assert data["items"][0]["created_at"] > data["items"][1]["created_at"]


@pytest.mark.asyncio
async def test_get_advisor_decision_by_id(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)
    account_id = await _seed_account_id(db_session)
    decision_id = await _insert_decision(
        db_session,
        bot_id=bot_id,
        account_id=account_id,
        created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        verdict="veto",
    )

    resp = await bots_client.get(
        f"/api/bots/{bot_id}/advisor-decisions/{decision_id}",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == decision_id
    assert data["bot_id"] == bot_id
    assert data["verdict"] == "veto"
    assert data["intent"]["canonical_id"] == "equity_us:AAPL:NASDAQ"


@pytest.mark.asyncio
async def test_get_advisor_decision_wrong_bot_returns_404(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)
    other_bot_id = await _create_bot(bots_client, auth_headers)
    account_id = await _seed_account_id(db_session)
    decision_id = await _insert_decision(
        db_session,
        bot_id=bot_id,
        account_id=account_id,
        created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )

    resp = await bots_client.get(
        f"/api/bots/{other_bot_id}/advisor-decisions/{decision_id}",
        headers=auth_headers,
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_advisor_config_publishes_to_redis(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)
    redis = AsyncMock()
    app.dependency_overrides[get_redis] = lambda: redis
    try:
        resp = await bots_client.put(
            f"/api/bots/{bot_id}/advisor-config",
            json=_valid_config(mode="VETO"),
            headers={**auth_headers, "x-csrf-nonce": "nonce"},
        )
    finally:
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 200
    redis.publish.assert_awaited_once()
    channel, payload = redis.publish.await_args.args
    assert channel == f"bot:advisor:config_changed:{bot_id}"
    assert json.loads(payload)["mode"] == "VETO"


@pytest.mark.asyncio
async def test_get_advisor_config_non_existent_bot_returns_404(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.get(f"/api/bots/{uuid4()}/advisor-config", headers=auth_headers)

    assert resp.status_code == 404
