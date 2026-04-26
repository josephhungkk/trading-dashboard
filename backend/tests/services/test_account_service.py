"""Tests for AccountService (Phase 4 Task 34).

Real Postgres (autouse migration fixture) for the broker_accounts table;
mock BrokerRegistry + sidecar clients for the gRPC fan-out side. Includes
the H11 avg_cost_unit invariant assertion (UK-pence trap, see memory
ibkr_uk_pence_units.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.brokers import base
from app.core.config import settings
from app.services.brokers import (
    AccountNotFound,
    AccountService,
    BrokerRegistry,
)

# ---------- mocks ------------------------------------------------------------


def _summary(value: str = "100000.00", currency: str = "USD") -> base.Summary:
    money = base.Money(value=value, currency=currency)
    return base.Summary(
        net_liquidation=money,
        total_cash=money,
        realized_pnl=money,
        unrealized_pnl=money,
        buying_power=money,
        updated_at=None,
    )


def _position(qty: str, avg_cost: str, currency: str = "USD") -> base.Position:
    money_zero = base.Money(value="0", currency=currency)
    return base.Position(
        contract=base.Contract(
            symbol="TEST",
            exchange="SMART",
            currency=currency,
            asset_class="STOCK",
            conid="999",
            local_symbol="TEST",
        ),
        quantity=qty,
        avg_cost=base.Money(value=avg_cost, currency=currency),
        market_price=money_zero,
        market_value=money_zero,
        unrealized_pnl=money_zero,
        realized_pnl_today=money_zero,
        daily_pnl=money_zero,
    )


class _MockSidecarClient:
    def __init__(self, label: str) -> None:
        self.label = label
        self.summary_response: base.Summary = _summary()
        self.positions_response: list[base.Position] = []
        self.orders_response: list[base.Order] = []

    async def get_account_summary(self, account_number: str) -> base.Summary:
        return self.summary_response

    async def get_positions(self, account_number: str) -> list[base.Position]:
        return self.positions_response

    async def get_orders(self, account_number: str) -> list[base.Order]:
        return self.orders_response

    async def health(self) -> base.HealthResponse:
        return base.HealthResponse(
            label=self.label,
            gateway_connected=True,
            gateway_version="999",
            last_tick_at=None,
            sidecar_version="0.4.0-test",
        )

    async def close(self) -> None:
        return None


def _as_any(value: object) -> Any:
    return value


# ---------- fixtures ---------------------------------------------------------


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[Any]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def cleanup_test_rows(db_engine: AsyncEngine) -> AsyncIterator[None]:
    cleanup = text("DELETE FROM broker_accounts WHERE account_number LIKE 'UTEST_ACCSVC_%'")
    async with db_engine.begin() as conn:
        await conn.execute(cleanup)
    yield
    async with db_engine.begin() as conn:
        await conn.execute(cleanup)


async def _seed_account(
    db_engine: AsyncEngine,
    *,
    account_number: str,
    gateway_label: str,
    mode: str = "paper",
    deleted: bool = False,
) -> UUID:
    insert = text(
        """
        INSERT INTO broker_accounts
        (broker_id, account_number, mode, gateway_label, currency_base,
         last_seen_via, deleted_at)
        VALUES (CAST(:b AS broker_id_enum), :a,
                CAST(:m AS trading_mode_enum), :g, :c, :v,
                CASE WHEN :d THEN now() ELSE NULL END)
        RETURNING id
        """
    )
    async with db_engine.begin() as conn:
        result = await conn.execute(
            insert,
            {
                "b": "ibkr",
                "a": account_number,
                "m": mode,
                "g": gateway_label,
                "c": "USD",
                "v": gateway_label,
                "d": deleted,
            },
        )
        return UUID(str(result.scalar_one()))


def _mock_registry(clients: dict[str, _MockSidecarClient], degraded: list[str]) -> BrokerRegistry:
    """Build a real BrokerRegistry whose health cache + degraded_labels()
    we control. healthy_clients() doesn't matter for AccountService since
    it only calls get_client(label) which is direct lookup."""
    import time as _time

    reg = BrokerRegistry(clients={label: _as_any(c) for label, c in clients.items()})
    now = _time.monotonic()
    for label in clients:
        ok = label not in degraded
        reg._health_cache[label] = (ok, now, None)
    return reg


# ---------- list_accounts ---------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_excludes_soft_deleted_includes_degraded(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    await _seed_account(db_engine, account_number="UTEST_ACCSVC_A", gateway_label="isa-paper")
    await _seed_account(db_engine, account_number="UTEST_ACCSVC_B", gateway_label="normal-paper")
    await _seed_account(
        db_engine,
        account_number="UTEST_ACCSVC_DEL",
        gateway_label="isa-paper",
        deleted=True,
    )

    clients = {
        "isa-paper": _MockSidecarClient("isa-paper"),
        "normal-paper": _MockSidecarClient("normal-paper"),
    }
    registry = _mock_registry(clients, degraded=["normal-paper"])
    service = AccountService(registry, session_factory)
    response = await service.list_accounts()

    response_ids = {str(a.id) for a in response.accounts}
    async with db_engine.connect() as conn:
        active_ids = {
            str(r.id)
            for r in (
                await conn.execute(
                    text(
                        "SELECT id FROM broker_accounts "
                        "WHERE account_number IN ('UTEST_ACCSVC_A','UTEST_ACCSVC_B') "
                        "AND deleted_at IS NULL"
                    )
                )
            ).all()
        }
        deleted_ids = {
            str(r.id)
            for r in (
                await conn.execute(
                    text("SELECT id FROM broker_accounts WHERE account_number = 'UTEST_ACCSVC_DEL'")
                )
            ).all()
        }
    assert active_ids.issubset(response_ids)
    assert deleted_ids.isdisjoint(response_ids)
    assert "normal-paper" in response.degraded_sidecars


# ---------- get_summary / get_orders / update_alias --------------------------


@pytest.mark.asyncio
async def test_get_summary_resolves_uuid_and_calls_sidecar(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    account_id = await _seed_account(
        db_engine, account_number="UTEST_ACCSVC_SUM", gateway_label="isa-paper"
    )
    client = _MockSidecarClient("isa-paper")
    client.summary_response = _summary(value="42.00", currency="GBP")
    registry = _mock_registry({"isa-paper": client}, degraded=[])

    service = AccountService(registry, session_factory)
    summary = await service.get_summary(account_id)
    assert summary.net_liquidation.value == "42.00"
    assert summary.net_liquidation.currency == "GBP"


@pytest.mark.asyncio
async def test_get_summary_raises_for_unknown_uuid(
    session_factory: async_sessionmaker[Any],
) -> None:
    registry = _mock_registry({"isa-paper": _MockSidecarClient("isa-paper")}, degraded=[])
    service = AccountService(registry, session_factory)
    with pytest.raises(AccountNotFound):
        await service.get_summary(uuid4())


@pytest.mark.asyncio
async def test_get_summary_raises_for_soft_deleted_account(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    account_id = await _seed_account(
        db_engine,
        account_number="UTEST_ACCSVC_DELSUM",
        gateway_label="isa-paper",
        deleted=True,
    )
    registry = _mock_registry({"isa-paper": _MockSidecarClient("isa-paper")}, degraded=[])
    service = AccountService(registry, session_factory)
    with pytest.raises(AccountNotFound):
        await service.get_summary(account_id)


@pytest.mark.asyncio
async def test_update_alias_persists(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    account_id = await _seed_account(
        db_engine, account_number="UTEST_ACCSVC_ALIAS", gateway_label="isa-paper"
    )
    registry = _mock_registry({"isa-paper": _MockSidecarClient("isa-paper")}, degraded=[])
    service = AccountService(registry, session_factory)

    response = await service.update_alias(account_id, base.AccountAliasUpdate(alias="My ISA"))
    assert response.alias == "My ISA"
    assert response.id == account_id

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT alias FROM broker_accounts WHERE id = :id"),
                {"id": account_id},
            )
        ).first()
    assert row is not None
    assert row.alias == "My ISA"


@pytest.mark.asyncio
async def test_update_alias_raises_for_soft_deleted(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    account_id = await _seed_account(
        db_engine,
        account_number="UTEST_ACCSVC_DELALIAS",
        gateway_label="isa-paper",
        deleted=True,
    )
    registry = _mock_registry({"isa-paper": _MockSidecarClient("isa-paper")}, degraded=[])
    service = AccountService(registry, session_factory)
    with pytest.raises(AccountNotFound):
        await service.update_alias(account_id, base.AccountAliasUpdate(alias="x"))


# ---------- H11 avg_cost_unit invariant -------------------------------------


@pytest.mark.asyncio
async def test_get_positions_warns_on_avg_cost_unit_mismatch(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Sum(qty x avg_cost) >> NLV (1.5x cutoff). Must emit
    avg_cost_unit_suspected_wrong WARN and increment the metric, but
    still return the positions."""
    import structlog

    account_id = await _seed_account(
        db_engine, account_number="UTEST_ACCSVC_INV1", gateway_label="isa-live"
    )
    client = _MockSidecarClient("isa-live")
    # 100 shares x 10000 = 1,000,000 cost. NLV = 100 (USD). Ratio = 10000:1.
    client.positions_response = [_position(qty="100", avg_cost="10000", currency="USD")]
    client.summary_response = _summary(value="100", currency="USD")
    registry = _mock_registry({"isa-live": client}, degraded=[])
    service = AccountService(registry, session_factory)

    captured: list[dict[str, Any]] = []

    def _capture(_logger: object, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append(event_dict.copy())
        return event_dict

    saved_processors = structlog.get_config().get("processors", [])
    structlog.configure(
        processors=[_capture, structlog.processors.JSONRenderer()],
        cache_logger_on_first_use=False,
    )
    try:
        positions = await service.get_positions(account_id)
    finally:
        structlog.configure(processors=saved_processors)

    assert len(positions) == 1
    assert any(e.get("event") == "avg_cost_unit_suspected_wrong" for e in captured), (
        f"WARN event missing; captured: {[e.get('event') for e in captured]}"
    )


@pytest.mark.asyncio
async def test_get_positions_no_warning_when_within_threshold(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Sum(qty x avg_cost) ~ NLV -> no warning, no metric bump."""
    account_id = await _seed_account(
        db_engine, account_number="UTEST_ACCSVC_INV2", gateway_label="isa-paper"
    )
    client = _MockSidecarClient("isa-paper")
    # 100 shares x 100 = 10,000 cost. NLV = 12,000. Ratio ~ 0.83.
    client.positions_response = [_position(qty="100", avg_cost="100")]
    client.summary_response = _summary(value="12000.00")
    registry = _mock_registry({"isa-paper": client}, degraded=[])
    service = AccountService(registry, session_factory)
    positions = await service.get_positions(account_id)
    assert len(positions) == 1
