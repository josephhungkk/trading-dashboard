from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_config, get_db, require_admin_jwt
from app.main import app


@dataclass
class _Config:
    kill_switch: bool = False
    max_notional_per_order: str = "10000"
    daily_notional_cap: str = "50000"
    trade_enabled: bool = True
    simulator_only: bool = False

    async def get_bool(self, namespace: str, key: str, *, default: bool) -> bool:
        # Per-gateway settings: namespace="broker", key="<label>.<setting>".
        # Global kill switch: namespace="broker", key="kill_switch_enabled".
        if namespace != "broker":
            return default
        if key == "kill_switch":
            return self.kill_switch
        if key == "isa-paper.trade_enabled":
            return self.trade_enabled
        if key == "isa-paper.simulator_only":
            return self.simulator_only
        return default

    async def get(self, namespace: str, key: str, *, default: str) -> str:
        if namespace != "broker":
            return default
        if key == "isa-paper.max_notional_per_order":
            return self.max_notional_per_order
        if key == "isa-paper.daily_notional_cap":
            return self.daily_notional_cap
        return default


@pytest.fixture
async def orders_client() -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    config = _Config()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    from app.api import orders as orders_api

    async with factory() as session:
        await session.begin()

        async def override_db() -> AsyncIterator[AsyncSession]:
            yield session

        async def override_admin() -> AdminIdentity:
            return AdminIdentity(email="test@example.com", kind="user", claims={})

        async def override_config() -> _Config:
            return config

        async def override_redis() -> fakeredis.aioredis.FakeRedis:
            return redis

        app.dependency_overrides[require_admin_jwt] = override_admin
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_config] = override_config
        app.dependency_overrides[orders_api.get_orders_redis] = override_redis

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            try:
                yield {"client": client, "session": session, "config": config, "redis": redis}
            finally:
                app.dependency_overrides.clear()
                await session.rollback()

    await engine.dispose()


async def _seed_account(session: AsyncSession) -> UUID:
    account_number = f"UTEST_D3_{uuid4()}"
    result = await session.execute(
        text(
            """
            INSERT INTO broker_accounts
            (broker_id, account_number, mode, gateway_label, currency_base, last_seen_via)
            VALUES ('ibkr', :account_number, 'paper', 'isa-paper', 'USD', 'isa-paper')
            RETURNING id;
            """
        ),
        {"account_number": account_number},
    )
    return UUID(str(result.scalar_one()))


async def _seed_order(
    session: AsyncSession,
    *,
    account_id: UUID,
    status: str = "pending_submit",
    symbol: str = "AAPL",
    created_at: datetime | None = None,
    notional: Decimal = Decimal("100"),
) -> UUID:
    order_id = uuid4()
    now = created_at or datetime.now(UTC)
    await session.execute(
        text(
            """
            INSERT INTO orders (
                id, account_id, client_order_id, conid, symbol, side, order_type, tif,
                qty, status, filled_qty, avg_fill_price, notional, notional_filled,
                created_at, updated_at
            )
            VALUES (
                :id, :account_id, :client_order_id, '265598', :symbol, 'BUY', 'MARKET',
                'DAY', 1, CAST(:status AS order_status_enum), 0, NULL, :notional, 0,
                :created_at, :created_at
            );
            """
        ),
        {
            "id": order_id,
            "account_id": account_id,
            "client_order_id": uuid4(),
            "symbol": symbol,
            "status": status,
            "notional": notional,
            "created_at": now,
        },
    )
    return order_id


async def _seed_event(
    session: AsyncSession,
    *,
    order_id: UUID,
    account_id: UUID,
    status: str,
    broker_event_at: datetime,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO order_events (
                order_id, account_id, broker_order_id, status, filled_qty, avg_fill_price,
                broker_event_at, raw_payload
            )
            VALUES (
                :order_id, :account_id, 'BRK-1', CAST(:status AS order_status_enum),
                1, 100, :broker_event_at, CAST(:raw_payload AS jsonb)
            );
            """
        ),
        {
            "order_id": order_id,
            "account_id": account_id,
            "status": status,
            "broker_event_at": broker_event_at,
            "raw_payload": '{"client_order_id": "client-1"}',
        },
    )


@pytest.mark.asyncio
async def test_get_orders_default_filter_active(orders_client: dict[str, Any]) -> None:
    session: AsyncSession = orders_client["session"]
    account_id = await _seed_account(session)
    active_id = await _seed_order(session, account_id=account_id, status="submitted")
    await _seed_order(session, account_id=account_id, status="filled")
    await _seed_order(session, account_id=account_id, status="cancelled")

    response = await orders_client["client"].get("/api/orders")

    assert response.status_code == 200
    ids = [order["id"] for order in response.json()["orders"]]
    assert ids == [str(active_id)]


@pytest.mark.asyncio
async def test_get_orders_status_filter(orders_client: dict[str, Any]) -> None:
    session: AsyncSession = orders_client["session"]
    account_id = await _seed_account(session)
    filled_id = await _seed_order(session, account_id=account_id, status="filled")
    await _seed_order(session, account_id=account_id, status="submitted")

    response = await orders_client["client"].get("/api/orders?status=filled")

    assert response.status_code == 200
    assert [order["id"] for order in response.json()["orders"]] == [str(filled_id)]


@pytest.mark.asyncio
async def test_get_orders_includes_broker_maintenance_envelope(
    orders_client: dict[str, Any],
) -> None:
    response = await orders_client["client"].get("/api/orders")

    assert response.status_code == 200
    assert "broker_maintenance" in response.json()


@pytest.mark.asyncio
async def test_get_orders_includes_kill_switch_active(orders_client: dict[str, Any]) -> None:
    config: _Config = orders_client["config"]
    config.kill_switch = True

    response = await orders_client["client"].get("/api/orders")

    assert response.status_code == 200
    assert response.json()["kill_switch_active"] is True


@pytest.mark.asyncio
async def test_get_order_by_id_includes_events(orders_client: dict[str, Any]) -> None:
    session: AsyncSession = orders_client["session"]
    account_id = await _seed_account(session)
    order_id = await _seed_order(session, account_id=account_id)
    older = datetime(2026, 4, 27, 9, tzinfo=UTC)
    newer = older + timedelta(minutes=5)
    await _seed_event(
        session, order_id=order_id, account_id=account_id, status="submitted", broker_event_at=older
    )
    await _seed_event(
        session, order_id=order_id, account_id=account_id, status="partial", broker_event_at=newer
    )

    response = await orders_client["client"].get(f"/api/orders/{order_id}")

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["status"] for event in events] == ["partial", "submitted"]


@pytest.mark.asyncio
async def test_get_order_by_id_404_when_missing(orders_client: dict[str, Any]) -> None:
    response = await orders_client["client"].get(f"/api/orders/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


@pytest.mark.asyncio
async def test_get_orders_policy_returns_caps_and_today_notional(
    orders_client: dict[str, Any],
) -> None:
    session: AsyncSession = orders_client["session"]
    config: _Config = orders_client["config"]
    config.max_notional_per_order = "2500"
    config.daily_notional_cap = "9000"
    account_id = await _seed_account(session)
    await _seed_order(
        session, account_id=account_id, status="submitted", notional=Decimal("125.50")
    )
    await _seed_order(session, account_id=account_id, status="partial", notional=Decimal("74.50"))
    await _seed_order(session, account_id=account_id, status="filled", notional=Decimal("999"))

    response = await orders_client["client"].get(f"/api/orders/policy/{account_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["max_notional_per_order"] == "2500.00000000"
    assert body["daily_notional_cap"] == "9000.00000000"
    assert body["notional_filled_today"] == "200.00000000"
    assert body["trade_enabled"] is True
    assert body["simulator_only"] is False
