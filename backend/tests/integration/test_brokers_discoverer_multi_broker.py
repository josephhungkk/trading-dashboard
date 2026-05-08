"""Multi-broker account discoverer regressions."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.brokers import base
from app.core.config import settings
from app.services.brokers import BrokerDiscoverer


def _postgres_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=1):
            return True
    except OSError:
        return False


class _FutuClient:
    label = "futu"

    async def list_managed_accounts(self) -> list[base.Account]:
        return [
            base.Account(
                account_number="UTEST_DISCOVER_MULTI_FUTU",
                mode="LIVE",
                gateway_label="futu",
                currency_base="HKD",
            )
        ]


class _Registry:
    async def healthy_clients(self) -> list[_FutuClient]:
        return [_FutuClient()]


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        settings.database_url,
        connect_args={"timeout": 2},
        pool_pre_ping=True,
        pool_timeout=2,
    )
    try:
        yield engine
    finally:
        try:
            async with asyncio.timeout(2):
                await engine.dispose()
        except Exception:
            pass


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[Any]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def cleanup_test_rows(db_engine: AsyncEngine) -> AsyncIterator[None]:
    if not _postgres_reachable():
        pytest.skip("database unavailable")

    cleanup = text(
        "DELETE FROM broker_accounts "
        "WHERE account_number IN ("
        "'UTEST_DISCOVER_MULTI_FUTU', 'UTEST_DISCOVER_MULTI_IBKR')"
    )
    try:
        async with asyncio.timeout(5):
            async with db_engine.begin() as conn:
                await conn.execute(cleanup)
    except Exception as exc:
        pytest.skip(f"database unavailable: {exc}")
    yield
    try:
        async with asyncio.timeout(5):
            async with db_engine.begin() as conn:
                await conn.execute(cleanup)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_rows_seen_keys_preserve_futu_broker_id(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    seed = text(
        """
        INSERT INTO broker_accounts (
            broker_id, account_number, mode, gateway_label, currency_base,
            last_seen_via, last_seen_at, deleted_at
        )
        VALUES (
            CAST(:broker_id AS broker_id_enum), :account_number,
            CAST(:mode AS trading_mode_enum), :gateway_label, :currency_base,
            'futu', now() - INTERVAL '31 minutes', NULL
        )
        """
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            seed,
            {
                "broker_id": "futu",
                "account_number": "UTEST_DISCOVER_MULTI_FUTU",
                "mode": "live",
                "gateway_label": "futu",
                "currency_base": "HKD",
            },
        )
        await conn.execute(
            seed,
            {
                "broker_id": "ibkr",
                "account_number": "UTEST_DISCOVER_MULTI_IBKR",
                "mode": "live",
                "gateway_label": "futu",
                "currency_base": "USD",
            },
        )

    await BrokerDiscoverer(_Registry(), session_factory)._discover_once()  # type: ignore[arg-type]

    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT broker_id::text, account_number, deleted_at
                      FROM broker_accounts
                     WHERE account_number IN (
                           'UTEST_DISCOVER_MULTI_FUTU',
                           'UTEST_DISCOVER_MULTI_IBKR'
                     )
                     ORDER BY broker_id::text
                    """
                )
            )
        ).all()

    by_broker = {row.broker_id: row for row in rows}
    assert by_broker["futu"].deleted_at is None
    assert by_broker["ibkr"].deleted_at is not None
