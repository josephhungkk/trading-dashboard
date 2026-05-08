"""Integration test: soft-deleted account resurrected by discoverer clears stale positions.

Covers the ``resurrected_ids`` block in ``BrokerDiscoverer._discover_once``
(brokers.py ~1093-1130): when a soft-deleted ``broker_accounts`` row is
rediscovered by the sidecar, ``deleted_at`` must flip to NULL and any
``positions`` rows for that account must be purged.
"""

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

# ---------------------------------------------------------------------------
# DB reachability guard (mirrors test_brokers_discoverer_multi_broker.py)
# ---------------------------------------------------------------------------


def _postgres_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=1):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Fake registry / client
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal fake that satisfies BrokerDiscoverer's duck-typed client contract."""

    def __init__(self, label: str, accounts: list[base.Account]) -> None:
        self.label = label
        self._accounts = accounts

    async def list_managed_accounts(self) -> list[base.Account]:
        return self._accounts


class _FakeRegistry:
    def __init__(self, clients: list[_FakeClient]) -> None:
        self._clients = clients

    async def healthy_clients(self) -> list[_FakeClient]:
        return self._clients


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACCOUNT_NUMBER = "UTEST_RESURRECT_ACCT"


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
async def cleanup_resurrect_rows(db_engine: AsyncEngine) -> AsyncIterator[None]:
    if not _postgres_reachable():
        pytest.skip("database unavailable")

    async def _cleanup() -> None:
        async with asyncio.timeout(5):
            async with db_engine.begin() as conn:
                # positions FK → broker_accounts; delete positions first.
                await conn.execute(
                    text(
                        "DELETE FROM positions WHERE account_id IN ("
                        "  SELECT id FROM broker_accounts"
                        "  WHERE account_number = :acct"
                        ")"
                    ),
                    {"acct": _ACCOUNT_NUMBER},
                )
                await conn.execute(
                    text("DELETE FROM broker_accounts WHERE account_number = :acct"),
                    {"acct": _ACCOUNT_NUMBER},
                )

    try:
        await _cleanup()
    except Exception as exc:
        pytest.skip(f"database unavailable: {exc}")

    yield

    try:
        await _cleanup()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resurrected_account_clears_stale_positions(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_resurrect_rows: None,
) -> None:
    """Rediscovering a soft-deleted account must un-delete it and wipe positions.

    Exercises the ``resurrected_ids`` block in BrokerDiscoverer._discover_once
    (Phase 5b.1 A3 R1): deleted_at flips to NULL AND stale positions are DELETEd
    before fresh positions can be loaded from the sidecar.
    """
    # 1. Seed a soft-deleted broker_accounts row.
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO broker_accounts (
                    broker_id, account_number, mode, gateway_label,
                    currency_base, last_seen_via, last_seen_at, deleted_at
                )
                VALUES (
                    CAST('ibkr' AS broker_id_enum),
                    :account_number,
                    CAST('paper' AS trading_mode_enum),
                    'isa-paper',
                    'USD',
                    'isa-paper',
                    now() - INTERVAL '2 hours',
                    now() - INTERVAL '1 hour'
                )
                RETURNING id
                """
            ),
            {"account_number": _ACCOUNT_NUMBER},
        )
        account_id = result.scalar_one()

    # 2. Seed a stale positions row for that account.
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO positions (
                    account_id, conid, qty, avg_cost, currency,
                    multiplier, asset_class, symbol, primary_exchange
                )
                VALUES (
                    :account_id, 'STALE_CONID', 10, 50.0, 'USD',
                    1, 'STOCK', 'STALE', 'NASDAQ'
                )
                """
            ),
            {"account_id": account_id},
        )

    # Sanity: positions row must exist before the tick.
    async with db_engine.connect() as conn:
        pre_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM positions WHERE account_id = :id"),
                {"id": account_id},
            )
        ).scalar_one()
    assert pre_count == 1, "setup: stale positions row must be present before tick"

    # 3. Build a fake registry that re-reports the same account (triggers resurrection).
    fake_account = base.Account(
        account_number=_ACCOUNT_NUMBER,
        mode="paper",
        gateway_label="isa-paper",
        currency_base="USD",
    )
    registry = _FakeRegistry([_FakeClient("isa-paper", [fake_account])])

    # 4. Run one discoverer tick (same pattern as test_brokers_discoverer_multi_broker).
    await BrokerDiscoverer(registry, session_factory)._discover_once()  # type: ignore[arg-type]

    # 5. Assert deleted_at is now NULL (account resurrected).
    async with db_engine.connect() as conn:
        deleted_at = (
            await conn.execute(
                text("SELECT deleted_at FROM broker_accounts WHERE id = :id"),
                {"id": account_id},
            )
        ).scalar_one()
    assert deleted_at is None, "resurrected account must have deleted_at = NULL"

    # 6. Assert positions for that account were cleared.
    async with db_engine.connect() as conn:
        post_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM positions WHERE account_id = :id"),
                {"id": account_id},
            )
        ).scalar_one()
    assert post_count == 0, "stale positions must be cleared on resurrection"
