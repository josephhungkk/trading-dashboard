from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, require_admin_jwt
from app.main import app


@pytest.fixture
async def fills_client(session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    async def override_db() -> AsyncIterator[AsyncSession]:
        yield session

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_db] = override_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            yield {"client": client, "session": session}
        finally:
            app.dependency_overrides.clear()


async def _seed_account(session: AsyncSession, account_number: str) -> UUID:
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
    symbol: str = "AAPL",
) -> UUID:
    order_id = uuid4()
    created_at = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    await session.execute(
        text(
            """
            INSERT INTO orders (
                id, account_id, client_order_id, conid, symbol, side, order_type, tif,
                qty, limit_price, status, filled_qty, avg_fill_price, notional, notional_filled,
                created_at, updated_at
            )
            VALUES (
                :id, :account_id, :client_order_id, '265598', :symbol, 'BUY', 'LIMIT', 'DAY',
                50, 150.05, CAST('filled' AS order_status_enum), 50, 150.05, 7502.50, 7502.50,
                :created_at, :created_at
            );
            """
        ),
        {
            "id": order_id,
            "account_id": account_id,
            "client_order_id": uuid4(),
            "symbol": symbol,
            "created_at": created_at,
        },
    )
    return order_id


async def _seed_fill(
    session: AsyncSession,
    *,
    order_id: UUID,
    exec_id: str,
    executed_at: datetime,
    qty: str = "50",
    price: str = "150.05",
    currency: str = "USD",
) -> UUID:
    fill_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO fills (id, order_id, exec_id, qty, price, currency, executed_at)
            VALUES (:id, :order_id, :exec_id, :qty, :price, :currency, :executed_at);
            """
        ),
        {
            "id": fill_id,
            "order_id": order_id,
            "exec_id": exec_id,
            "qty": Decimal(qty),
            "price": Decimal(price),
            "currency": currency,
            "executed_at": executed_at,
        },
    )
    return fill_id


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.mark.asyncio
async def test_fills_pagination_cursor_round_trip(fills_client: dict[str, Any]) -> None:
    session: AsyncSession = fills_client["session"]
    account_id = await _seed_account(session, "TEST_FILL_001")
    order_id = await _seed_order(session, account_id=account_id)
    start = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)

    for i in range(120):
        await _seed_fill(
            session,
            order_id=order_id,
            exec_id=f"EXEC_{i + 1:03d}",
            executed_at=start + timedelta(minutes=i),
        )

    response = await fills_client["client"].get(
        "/api/fills",
        params={
            "account_id": str(account_id),
            "from": _iso(start - timedelta(minutes=1)),
            "to": _iso(start + timedelta(minutes=120)),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["fills"]) == 100
    assert body["next_cursor"] is not None
    assert body["fills"][0]["exec_id"] == "EXEC_120"
    assert body["fills"][-1]["exec_id"] == "EXEC_021"

    response_page_2 = await fills_client["client"].get(
        "/api/fills",
        params={
            "account_id": str(account_id),
            "from": _iso(start - timedelta(minutes=1)),
            "to": _iso(start + timedelta(minutes=120)),
            "cursor": body["next_cursor"],
        },
    )

    assert response_page_2.status_code == 200
    page_2 = response_page_2.json()
    assert len(page_2["fills"]) == 20
    assert page_2["next_cursor"] is None
    assert page_2["fills"][0]["exec_id"] == "EXEC_020"
    assert page_2["fills"][-1]["exec_id"] == "EXEC_001"


@pytest.mark.asyncio
async def test_fills_date_range_filter(fills_client: dict[str, Any]) -> None:
    session: AsyncSession = fills_client["session"]
    account_id = await _seed_account(session, "TEST_FILL_DATE_001")
    order_id = await _seed_order(session, account_id=account_id)
    day_1 = datetime(2026, 4, 21, 10, 0, tzinfo=UTC)
    day_2 = datetime(2026, 4, 22, 10, 0, tzinfo=UTC)
    day_3 = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)

    await _seed_fill(session, order_id=order_id, exec_id="EXEC_001", executed_at=day_1)
    await _seed_fill(session, order_id=order_id, exec_id="EXEC_002", executed_at=day_2)
    await _seed_fill(session, order_id=order_id, exec_id="EXEC_003", executed_at=day_3)

    response = await fills_client["client"].get(
        "/api/fills",
        params={
            "account_id": str(account_id),
            "from": _iso(datetime(2026, 4, 22, 0, 0, tzinfo=UTC)),
            "to": _iso(datetime(2026, 4, 22, 23, 59, 59, tzinfo=UTC)),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [fill["exec_id"] for fill in body["fills"]] == ["EXEC_002"]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_fills_account_scoped(fills_client: dict[str, Any]) -> None:
    session: AsyncSession = fills_client["session"]
    account_a = await _seed_account(session, "TEST_FILL_SCOPE_A")
    account_b = await _seed_account(session, "TEST_FILL_SCOPE_B")
    order_a = await _seed_order(session, account_id=account_a, symbol="AAPL")
    order_b = await _seed_order(session, account_id=account_b, symbol="MSFT")
    executed_at = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)

    await _seed_fill(session, order_id=order_a, exec_id="EXEC_001", executed_at=executed_at)
    await _seed_fill(session, order_id=order_b, exec_id="EXEC_002", executed_at=executed_at)

    response = await fills_client["client"].get(
        "/api/fills",
        params={
            "account_id": str(account_a),
            "from": _iso(executed_at - timedelta(hours=1)),
            "to": _iso(executed_at + timedelta(hours=1)),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [fill["exec_id"] for fill in body["fills"]] == ["EXEC_001"]
    assert {fill["order_id"] for fill in body["fills"]} == {str(order_a)}


@pytest.mark.asyncio
async def test_fills_per_execution_detail(fills_client: dict[str, Any]) -> None:
    session: AsyncSession = fills_client["session"]
    account_id = await _seed_account(session, "TEST_FILL_MULTI_001")
    order_id = await _seed_order(session, account_id=account_id)
    start = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)

    for i in range(4):
        await _seed_fill(
            session,
            order_id=order_id,
            exec_id=f"EXEC_{i + 1:03d}",
            executed_at=start + timedelta(seconds=i),
        )

    response = await fills_client["client"].get(
        "/api/fills",
        params={
            "account_id": str(account_id),
            "from": _iso(start - timedelta(minutes=1)),
            "to": _iso(start + timedelta(minutes=1)),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["fills"]) == 4
    assert [fill["exec_id"] for fill in body["fills"]] == [
        "EXEC_004",
        "EXEC_003",
        "EXEC_002",
        "EXEC_001",
    ]
    assert all(fill["order_id"] == str(order_id) for fill in body["fills"])


@pytest.mark.asyncio
async def test_fills_empty_result_returns_null_cursor(fills_client: dict[str, Any]) -> None:
    session: AsyncSession = fills_client["session"]
    account_id = await _seed_account(session, "TEST_FILL_EMPTY_001")
    order_id = await _seed_order(session, account_id=account_id)
    await _seed_fill(
        session,
        order_id=order_id,
        exec_id="EXEC_001",
        executed_at=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
    )

    response = await fills_client["client"].get(
        "/api/fills",
        params={
            "account_id": str(account_id),
            "from": _iso(datetime(2026, 4, 27, 0, 0, tzinfo=UTC)),
            "to": _iso(datetime(2026, 4, 27, 23, 59, 59, tzinfo=UTC)),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"fills": [], "next_cursor": None}
