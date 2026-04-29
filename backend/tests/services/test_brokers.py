"""Tests for BrokerRegistry (Phase 4 Task 32) and BrokerDiscoverer (Task 33).

Tasks 32 use 4 mock BrokerSidecarClients (one per gateway label) with
overridable health() return values + a monkey-patched time.monotonic() so
freshness expiry assertions are deterministic.

Task 33 (C1 invariant tests) uses a real Postgres connection and the live
broker_accounts schema (the conftest autouse fixture runs migrations to
head) plus mock clients that return canned ListManagedAccounts results.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.brokers import base
from app.core.config import settings
from app.services.brokers import (
    BrokerDiscoverer,
    BrokerRegistry,
    BrokerSidecarClient,
    BrokerSidecarTimeout,
    BrokerSidecarUnavailable,
)


class _MockClient:
    """Duck-typed substitute for BrokerSidecarClient. Tests poke
    set_ok() / set_unavailable() / set_timeout() to control what the
    next health() call returns or raises."""

    def __init__(self, label: str) -> None:
        self.label = label
        self._response: base.HealthResponse | Exception = base.HealthResponse(
            label=label,
            gateway_connected=True,
            gateway_version="999",
            last_tick_at=None,
            sidecar_version="0.4.0-test",
        )
        self.closed = False

    def set_ok(self) -> None:
        self._response = base.HealthResponse(
            label=self.label,
            gateway_connected=True,
            gateway_version="999",
            last_tick_at=None,
            sidecar_version="0.4.0-test",
        )

    def set_unavailable(self) -> None:
        self._response = BrokerSidecarUnavailable(f"{self.label} down")

    def set_timeout(self) -> None:
        self._response = BrokerSidecarTimeout(f"{self.label} timeout")

    async def health(self) -> base.HealthResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self) -> None:
        self.closed = True


def _as_any(value: object) -> Any:
    """Cast through Any so the duck-typed _MockClient satisfies the typed
    BrokerSidecarClient parameter without inheriting from the real class."""
    return value


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, float]]:
    """Patches time.monotonic in app.services.brokers so tests can advance
    the registry's clock without sleeping."""
    state = {"now": 1_000_000.0}

    def _now() -> float:
        return state["now"]

    monkeypatch.setattr("app.services.brokers.time.monotonic", _now)
    yield state


@pytest.fixture
def registry() -> tuple[BrokerRegistry, dict[str, _MockClient]]:
    mocks = {
        label: _MockClient(label)
        for label in ("isa-live", "isa-paper", "normal-live", "normal-paper")
    }
    reg = BrokerRegistry(
        clients={label: _as_any(client) for label, client in mocks.items()},
        freshness_seconds=90.0,
        probe_interval_healthy=60.0,
        probe_interval_unhealthy=5.0,
    )
    return reg, mocks


# --- get_client --------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_client_returns_the_registered_client(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
) -> None:
    reg, mocks = registry
    client = await reg.get_client("isa-paper")
    assert client is mocks["isa-paper"]


@pytest.mark.asyncio
async def test_get_client_raises_for_unknown_label(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
) -> None:
    reg, _ = registry
    with pytest.raises(KeyError):
        await reg.get_client("does-not-exist")


# --- probe_once + healthy_clients --------------------------------------------


@pytest.mark.asyncio
async def test_unprobed_clients_are_excluded_from_healthy_set(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    assert await reg.healthy_clients() == []
    assert sorted(await reg.degraded_labels()) == [
        "isa-live",
        "isa-paper",
        "normal-live",
        "normal-paper",
    ]


@pytest.mark.asyncio
async def test_probe_once_marks_all_healthy(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    await reg.probe_once()
    healthy = await reg.healthy_clients()
    assert len(healthy) == 4
    assert await reg.degraded_labels() == []


@pytest.mark.asyncio
async def test_unhealthy_to_healthy_transition(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, mocks = registry
    mocks["isa-live"].set_unavailable()
    mocks["normal-paper"].set_timeout()

    await reg.probe_once()
    degraded = sorted(await reg.degraded_labels())
    assert degraded == ["isa-live", "normal-paper"]
    healthy_labels = {c.label for c in await reg.healthy_clients()}
    assert healthy_labels == {"isa-paper", "normal-live"}

    mocks["isa-live"].set_ok()
    mocks["normal-paper"].set_ok()
    await reg.probe_once()
    assert await reg.degraded_labels() == []
    assert len(await reg.healthy_clients()) == 4


@pytest.mark.asyncio
async def test_healthy_to_unhealthy_transition(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, mocks = registry
    await reg.probe_once()
    assert len(await reg.healthy_clients()) == 4

    mocks["isa-paper"].set_unavailable()
    await reg.probe_once()
    assert await reg.degraded_labels() == ["isa-paper"]


# --- freshness expiry --------------------------------------------------------


@pytest.mark.asyncio
async def test_freshness_expiry_drops_stale_healthy(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    await reg.probe_once()
    assert len(await reg.healthy_clients()) == 4

    # Advance clock past the 90s freshness window without re-probing.
    clock["now"] += 91.0
    assert await reg.healthy_clients() == []
    assert sorted(await reg.degraded_labels()) == [
        "isa-live",
        "isa-paper",
        "normal-live",
        "normal-paper",
    ]


@pytest.mark.asyncio
async def test_freshness_window_inclusive_at_exactly_90s(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
    clock: dict[str, float],
) -> None:
    reg, _ = registry
    await reg.probe_once()

    # `now - probed_at <= freshness` -> exactly 90s is still healthy.
    clock["now"] += 90.0
    assert len(await reg.healthy_clients()) == 4

    # 0.001s past -> drops out.
    clock["now"] += 0.001
    assert await reg.healthy_clients() == []


# --- close -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_every_client(
    registry: tuple[BrokerRegistry, dict[str, _MockClient]],
) -> None:
    reg, mocks = registry
    await reg.close()
    assert all(m.closed for m in mocks.values())


# === BrokerDiscoverer (Task 33) — C1 race-free soft-delete invariants ========


class _DiscoverableMockClient(_MockClient):
    """Adds list_managed_accounts() to the mock so BrokerDiscoverer can
    exercise the upsert/soft-delete path."""

    def __init__(self, label: str, accounts: list[base.Account] | Exception) -> None:
        super().__init__(label)
        self._accounts = accounts

    def set_accounts(self, accounts: list[base.Account] | Exception) -> None:
        self._accounts = accounts

    async def list_managed_accounts(self) -> list[base.Account]:
        if isinstance(self._accounts, Exception):
            raise self._accounts
        return list(self._accounts)


def _account(account_number: str, *, mode: str = "PAPER", currency: str = "USD") -> base.Account:
    return base.Account(
        account_number=account_number,
        mode=mode,  # type: ignore[arg-type]
        gateway_label="",
        currency_base=currency,
    )


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session_factory(
    db_engine: AsyncEngine,
) -> async_sessionmaker[Any]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def cleanup_test_rows(db_engine: AsyncEngine) -> AsyncIterator[None]:
    """Removes any UTEST_DISCOVER_* rows before + after each test so
    consecutive runs are independent and the live DB stays clean."""
    cleanup_sql = text("DELETE FROM broker_accounts WHERE account_number LIKE 'UTEST_DISCOVER_%'")
    async with db_engine.begin() as conn:
        await conn.execute(cleanup_sql)
    yield
    async with db_engine.begin() as conn:
        await conn.execute(cleanup_sql)


def _build_registry_with_known_health(
    clients: dict[str, _DiscoverableMockClient],
    *,
    healthy_labels: set[str],
) -> BrokerRegistry:
    """Build a real BrokerRegistry but pre-populate its _health_state so
    healthy_clients() returns exactly the labels in `healthy_labels`."""
    import time as _time

    reg = BrokerRegistry(
        clients={label: _as_any(c) for label, c in clients.items()},
        freshness_seconds=900.0,  # large so manual ts doesn't expire mid-test
    )
    now = _time.monotonic()
    for label in clients:
        ok = label in healthy_labels
        reg._health_cache[label] = (ok, now, None)
    return reg


@pytest.mark.asyncio
async def test_soft_delete_fires_when_sidecar_healthy_and_account_missing(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Pre-seed 2 accounts owned by isa-live (last_seen_at = 31min ago).
    Run _discover_once where isa-live is healthy but reports neither →
    both rows get deleted_at set."""
    seed_sql = text(
        "INSERT INTO broker_accounts "
        "(broker_id, account_number, mode, gateway_label, currency_base, "
        " last_seen_via, last_seen_at) "
        "VALUES (CAST(:b AS broker_id_enum), :a, "
        "        CAST(:m AS trading_mode_enum), :g, :c, :v, "
        "        now() - INTERVAL '31 minutes')"
    )
    async with db_engine.begin() as conn:
        for acct in ("UTEST_DISCOVER_A", "UTEST_DISCOVER_B"):
            await conn.execute(
                seed_sql,
                {
                    "b": "ibkr",
                    "a": acct,
                    "m": "live",
                    "g": "isa-live",
                    "c": "USD",
                    "v": "isa-live",
                },
            )

    clients = {"isa-live": _DiscoverableMockClient("isa-live", accounts=[])}
    registry = _build_registry_with_known_health(clients, healthy_labels={"isa-live"})

    discoverer = BrokerDiscoverer(registry, session_factory)
    await discoverer._discover_once()

    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT account_number, deleted_at FROM broker_accounts "
                    "WHERE account_number LIKE 'UTEST_DISCOVER_%' ORDER BY account_number"
                )
            )
        ).all()
    assert len(rows) == 2
    assert all(r.deleted_at is not None for r in rows), (
        f"both rows should have deleted_at set; got {rows}"
    )


@pytest.mark.asyncio
async def test_soft_delete_skipped_when_all_sidecars_unhealthy(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Pre-seed 4 accounts across 4 sidecars. ALL 4 sidecars unhealthy →
    healthy_labels is empty → no soft-delete fires; all 4 still active."""
    seed_sql = text(
        "INSERT INTO broker_accounts "
        "(broker_id, account_number, mode, gateway_label, currency_base, "
        " last_seen_via, last_seen_at) "
        "VALUES (CAST(:b AS broker_id_enum), :a, "
        "        CAST(:m AS trading_mode_enum), :g, :c, :v, "
        "        now() - INTERVAL '31 minutes')"
    )
    labels = ["isa-live", "isa-paper", "normal-live", "normal-paper"]
    async with db_engine.begin() as conn:
        for label in labels:
            await conn.execute(
                seed_sql,
                {
                    "b": "ibkr",
                    "a": f"UTEST_DISCOVER_{label.upper()}",
                    "m": "live" if "live" in label else "paper",
                    "g": label,
                    "c": "USD",
                    "v": label,
                },
            )

    clients = {label: _DiscoverableMockClient(label, accounts=[]) for label in labels}
    registry = _build_registry_with_known_health(clients, healthy_labels=set())

    discoverer = BrokerDiscoverer(registry, session_factory)
    await discoverer._discover_once()

    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT account_number, deleted_at FROM broker_accounts "
                    "WHERE account_number LIKE 'UTEST_DISCOVER_%' ORDER BY account_number"
                )
            )
        ).all()
    assert len(rows) == 4
    assert all(r.deleted_at is None for r in rows), (
        f"no row should be soft-deleted when all sidecars unhealthy; got {rows}"
    )


@pytest.mark.asyncio
async def test_discover_loop_survives_iteration_failure(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """One client raises on list_managed_accounts. _discover_once must
    log + skip that client without raising and still upsert the rest."""
    good = _DiscoverableMockClient(
        "isa-live", accounts=[_account("UTEST_DISCOVER_GOOD", mode="LIVE")]
    )
    bad = _DiscoverableMockClient(
        "isa-paper",
        accounts=BrokerSidecarUnavailable("isa-paper down"),
    )
    clients = {"isa-live": good, "isa-paper": bad}
    registry = _build_registry_with_known_health(clients, healthy_labels={"isa-live", "isa-paper"})

    discoverer = BrokerDiscoverer(registry, session_factory)
    # Must not raise.
    await discoverer._discover_once()

    async with db_engine.connect() as conn:
        good_row = (
            await conn.execute(
                text(
                    "SELECT account_number FROM broker_accounts "
                    "WHERE account_number = 'UTEST_DISCOVER_GOOD'"
                )
            )
        ).first()
    assert good_row is not None, "the good client's account should still upsert"


@pytest.mark.asyncio
async def test_reappearance_clears_deleted_at(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Account marked deleted_at = past. Same sidecar reports it again →
    deleted_at cleared, last_seen_at bumped."""
    seed_sql = text(
        "INSERT INTO broker_accounts "
        "(broker_id, account_number, mode, gateway_label, currency_base, "
        " last_seen_via, last_seen_at, deleted_at) "
        "VALUES (CAST(:b AS broker_id_enum), :a, "
        "        CAST(:m AS trading_mode_enum), :g, :c, :v, "
        "        now() - INTERVAL '1 hour', "
        "        now() - INTERVAL '5 minutes')"
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            seed_sql,
            {
                "b": "ibkr",
                "a": "UTEST_DISCOVER_REAPPEAR",
                "m": "live",
                "g": "isa-live",
                "c": "USD",
                "v": "isa-live",
            },
        )

    client = _DiscoverableMockClient(
        "isa-live", accounts=[_account("UTEST_DISCOVER_REAPPEAR", mode="LIVE")]
    )
    registry = _build_registry_with_known_health({"isa-live": client}, healthy_labels={"isa-live"})

    discoverer = BrokerDiscoverer(registry, session_factory)
    await discoverer._discover_once()

    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT deleted_at, last_seen_at FROM broker_accounts "
                    "WHERE account_number = 'UTEST_DISCOVER_REAPPEAR'"
                )
            )
        ).first()
    assert row is not None
    assert row.deleted_at is None, "deleted_at must clear on reappearance"
    # last_seen_at bumped to ~now (was 1h ago); just check it's recent.
    from datetime import UTC, datetime, timedelta

    assert datetime.now(UTC) - row.last_seen_at < timedelta(seconds=10)


# === Phase 5a (Task C6) — fan-out / skip-write / overlap / resurrect / overflow


def _summary(*, currency: str, value: str) -> base.Summary:
    """Construct a base.Summary populated only on net_liquidation; the
    other Money fields default to empty (the discoverer only reads
    net_liquidation in Phase 5a)."""
    nlv = base.Money(value=value, currency=currency)
    empty = base.Money(value="", currency="")
    return base.Summary(
        net_liquidation=nlv,
        total_cash=empty,
        realized_pnl=empty,
        unrealized_pnl=empty,
        buying_power=empty,
        updated_at=datetime.now(UTC),
    )


async def _select_nlv_row(db_engine: AsyncEngine, account_number: str) -> Any:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT account_number, last_nlv, last_nlv_currency, last_nlv_at, deleted_at "
                    "FROM broker_accounts WHERE account_number = :a"
                ),
                {"a": account_number},
            )
        ).first()


@pytest.mark.asyncio
async def test_fan_out_writes_nlv_for_healthy_clients(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Two healthy gateways report one account each → both get NLV written."""
    a_label, b_label = "isa-live", "isa-paper"
    a_acct, b_acct = "UTEST_DISCOVER_FAN_A", "UTEST_DISCOVER_FAN_B"
    a_client = _DiscoverableMockClient(a_label, accounts=[_account(a_acct, mode="LIVE")])
    b_client = _DiscoverableMockClient(b_label, accounts=[_account(b_acct, mode="PAPER")])
    a_client.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="USD", value="100.50")
    )
    b_client.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="GBP", value="250.25")
    )
    registry = _build_registry_with_known_health(
        {a_label: a_client, b_label: b_client},
        healthy_labels={a_label, b_label},
    )
    await BrokerDiscoverer(registry, session_factory)._discover_once()

    a_row = await _select_nlv_row(db_engine, a_acct)
    b_row = await _select_nlv_row(db_engine, b_acct)
    assert a_row is not None and a_row.last_nlv == Decimal("100.50000000")
    assert a_row.last_nlv_currency == "USD"
    assert b_row is not None and b_row.last_nlv == Decimal("250.25000000")
    assert b_row.last_nlv_currency == "GBP"


@pytest.mark.asyncio
async def test_one_timed_out_client_does_not_taint_others(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    slow_label, fast_label = "isa-live", "isa-paper"
    slow_acct = "UTEST_DISCOVER_SLOW"
    fast_acct = "UTEST_DISCOVER_FAST"
    slow = _DiscoverableMockClient(slow_label, accounts=[_account(slow_acct, mode="LIVE")])
    fast = _DiscoverableMockClient(fast_label, accounts=[_account(fast_acct, mode="PAPER")])
    slow.get_account_summary = AsyncMock(side_effect=TimeoutError())  # type: ignore[attr-defined]
    fast.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="EUR", value="42.00")
    )
    registry = _build_registry_with_known_health(
        {slow_label: slow, fast_label: fast},
        healthy_labels={slow_label, fast_label},
    )
    await BrokerDiscoverer(registry, session_factory)._discover_once()

    slow_row = await _select_nlv_row(db_engine, slow_acct)
    fast_row = await _select_nlv_row(db_engine, fast_acct)
    assert slow_row is not None and slow_row.last_nlv is None
    assert fast_row is not None and fast_row.last_nlv == Decimal("42.00000000")


@pytest.mark.asyncio
async def test_skip_write_when_currency_empty(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    label, acct = "normal-live", "UTEST_DISCOVER_EMPTY_CCY"
    client = _DiscoverableMockClient(label, accounts=[_account(acct, mode="LIVE")])
    client.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="", value="100")
    )
    registry = _build_registry_with_known_health({label: client}, healthy_labels={label})
    await BrokerDiscoverer(registry, session_factory)._discover_once()

    row = await _select_nlv_row(db_engine, acct)
    assert row is not None
    assert row.last_nlv is None
    assert row.last_nlv_currency is None
    assert row.last_nlv_at is None


@pytest.mark.asyncio
async def test_skip_write_when_value_empty(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    label, acct = "normal-live", "UTEST_DISCOVER_EMPTY_VAL"
    client = _DiscoverableMockClient(label, accounts=[_account(acct, mode="LIVE")])
    client.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="USD", value="")
    )
    registry = _build_registry_with_known_health({label: client}, healthy_labels={label})
    await BrokerDiscoverer(registry, session_factory)._discover_once()

    row = await _select_nlv_row(db_engine, acct)
    assert row is not None
    assert row.last_nlv is None


@pytest.mark.asyncio
async def test_overlap_guard_skips_concurrent_tick(
    session_factory: async_sessionmaker[Any],
) -> None:
    """Holding _tick_lock blocks discover_loop's _discover_once invocation
    and emits broker_discover_iteration_skipped_overlap."""
    registry = _build_registry_with_known_health({}, healthy_labels=set())
    discoverer = BrokerDiscoverer(registry, session_factory, interval_seconds=0.01)

    with structlog.testing.capture_logs() as captured:
        async with discoverer._tick_lock:
            task = asyncio.create_task(discoverer.discover_loop())
            await asyncio.sleep(0.05)
            discoverer._stop_event.set()
            await task

    assert any(e.get("event") == "broker_discover_iteration_skipped_overlap" for e in captured), (
        f"expected skipped-overlap log; got events: {[e.get('event') for e in captured]}"
    )


@pytest.mark.asyncio
async def test_resurrect_clears_stale_nlv(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Soft-deleted row with stale NLV reappears → CASE clears NLV first,
    then the fresh discover tick repopulates with the new value."""
    label, acct = "normal-paper", "UTEST_DISCOVER_RESURRECT"
    seed_sql = text(
        "INSERT INTO broker_accounts "
        "(broker_id, account_number, mode, gateway_label, currency_base, "
        " last_seen_via, last_seen_at, deleted_at, "
        " last_nlv, last_nlv_currency, last_nlv_at) "
        "VALUES (CAST(:b AS broker_id_enum), :a, CAST(:m AS trading_mode_enum), "
        "        :g, :c, :v, now() - INTERVAL '1 hour', now() - INTERVAL '5 minutes', "
        "        9999, 'USD', now() - INTERVAL '2 weeks')"
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            seed_sql,
            {"b": "ibkr", "a": acct, "m": "paper", "g": label, "c": "USD", "v": label},
        )

    client = _DiscoverableMockClient(label, accounts=[_account(acct, mode="PAPER")])
    client.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="USD", value="200.00")
    )
    registry = _build_registry_with_known_health({label: client}, healthy_labels={label})
    await BrokerDiscoverer(registry, session_factory)._discover_once()

    row = await _select_nlv_row(db_engine, acct)
    assert row is not None
    assert row.deleted_at is None
    assert row.last_nlv == Decimal("200.00000000")  # fresh value, not stale 9999
    assert row.last_nlv_currency == "USD"


@pytest.mark.asyncio
async def test_overflow_does_not_taint_other_accounts(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[Any],
    cleanup_test_rows: None,
) -> None:
    """Savepoint isolation: an over-NUMERIC(20,8) NLV on one account
    must not block the UPDATE for a healthy sibling account."""
    big_label, ok_label = "isa-live", "normal-live"
    big_acct, ok_acct = "UTEST_DISCOVER_OVERFLOW", "UTEST_DISCOVER_OK"
    big = _DiscoverableMockClient(big_label, accounts=[_account(big_acct, mode="LIVE")])
    ok = _DiscoverableMockClient(ok_label, accounts=[_account(ok_acct, mode="LIVE")])
    big.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="USD", value="9" * 30)
    )
    ok.get_account_summary = AsyncMock(  # type: ignore[attr-defined]
        return_value=_summary(currency="USD", value="100")
    )
    registry = _build_registry_with_known_health(
        {big_label: big, ok_label: ok},
        healthy_labels={big_label, ok_label},
    )
    await BrokerDiscoverer(registry, session_factory)._discover_once()

    big_row = await _select_nlv_row(db_engine, big_acct)
    ok_row = await _select_nlv_row(db_engine, ok_acct)
    assert big_row is not None and big_row.last_nlv is None  # savepoint rolled back
    assert ok_row is not None and ok_row.last_nlv == Decimal("100.00000000")


@pytest.mark.asyncio
async def test_label_mismatch_marks_label_degraded_and_increments_metric() -> None:
    from prometheus_client import REGISTRY as PCREG

    health = base.HealthResponse(
        label="futu",
        gateway_connected=True,
        gateway_version="0.6.0",
        last_tick_at=None,
        sidecar_version="0.6.0",
    )
    object.__setattr__(health, "broker_id", "ibkr")
    before = (
        PCREG.get_sample_value("broker_registry_label_mismatch_total", {"label": "futu"}) or 0.0
    )
    fake_client = MagicMock(spec=BrokerSidecarClient)
    fake_client.health = AsyncMock(return_value=health)
    registry = BrokerRegistry({"futu": _as_any(fake_client)})

    with structlog.testing.capture_logs():
        await registry.probe_once()

    degraded = await registry.degraded_labels()
    sample = PCREG.get_sample_value("broker_registry_label_mismatch_total", {"label": "futu"})
    assert "futu" in degraded
    assert sample == before + 1.0


@pytest.mark.asyncio
async def test_label_mismatch_logs_critical_expected_and_actual() -> None:
    health = base.HealthResponse(
        label="isa-live",
        gateway_connected=True,
        gateway_version="0.6.0",
        last_tick_at=None,
        sidecar_version="0.6.0",
    )
    object.__setattr__(health, "broker_id", "futu")
    fake_client = MagicMock(spec=BrokerSidecarClient)
    fake_client.health = AsyncMock(return_value=health)
    registry = BrokerRegistry({"isa-live": _as_any(fake_client)})

    with structlog.testing.capture_logs() as captured:
        await registry.probe_once()

    assert any(
        event.get("event") == "broker_registry_label_mismatch"
        and event.get("log_level") == "critical"
        and event.get("label") == "isa-live"
        and event.get("expected") == "ibkr"
        and event.get("actual") == "futu"
        for event in captured
    ), f"expected critical mismatch log; got {captured}"
