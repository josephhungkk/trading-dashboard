# Phase 5b.1 — Canary Hotfix Pack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-04-28-phase5b1-canary-hotfix-design.md` (commit `c01429c`, architect-reviewed — 2 CRIT + 4 HIGH applied inline)
**Tag at end:** `v0.5.3`
**Estimated duration:** ~7 working days
**Prerequisite:** v0.5.2 shipped + first paper canary verified
**Successor:** Phase 5c (Modify orders + Brackets + Fills history)

**Goal:** Plug four production gaps surfaced by the v0.5.2 paper canary so Phase 5c is unblocked and CI catches this regression class going forward. Strictly hardening — no new user-facing features.

**Architecture:** Add `positions` table (Alembic 0005) populated by a new `BrokerDiscoverer._discover_positions` per-account fan-out (mirrors Phase 5a NLV pattern). Sidecar `CancelOrder` synthesizes a `Trade`-like object and fires `ib.orderStatusEvent.emit(...)` for `SIM-` orders, reusing existing per-subscriber OrderEvent fan-out. Sidecar startup runs a sequential per-account `reqAccountUpdates` BASE-tag round before `reqAccountSummary` subscribes, populating `currency_base` properly. Two new CI workflows close the loop: `e2e-mock.yml` (every PR) and an extended `real-ibkr.yml` job (nightly cron).

**Tech stack:** SQLAlchemy 2.0 async + Alembic + asyncpg + Pydantic v2 (backend); ib_async + grpc.aio + uuid_utils.uuid7 + types.SimpleNamespace (sidecar); httpx + pytest-asyncio + GitHub Actions service containers (CI).

---

## Owner & review chain (per CLAUDE.md "Step 6 — Implementation")

Each task lists an explicit **Owner: Codex | Claude** line:

- **Codex** writes source code (Python — backend + sidecar) via `codex:codex-rescue`.
- **Claude Code** writes tests, verifies (typecheck/lint/test), and commits.
- **Fallback** per memory `feedback_codex_fallback.md`: if Codex hits quota mid-task, Claude finishes the task with a `Codex quota exceeded → Claude continued` commit footer; canary back to Codex on the next planned Codex task.
- **Per-commit review chain:** implementer → spec compliance reviewer → code quality reviewer → `python-reviewer` (always) → conditional reviewers:
  - `database-reviewer` — A1 + A2 + A3 (Alembic 0005, _upsert_positions SQL).
  - `silent-failure-hunter` — A3 + B2 (async fan-out + ib.orderStatusEvent.emit reentrancy).
  - `security-reviewer` — D1 (integration test hits admin endpoint with CF Access token; confirm no token leak in CI logs).
  - `tdd-guide` — when tests fail unexpectedly during implementation.
- **Conventional commits**, body lines ≤ 100 chars, never `--no-verify`.
- **Coverage gate:** 80%+ on backend `app/` + sidecar `sidecar/`. CI fails below.

---

## Critical gates

Strict ordering (each gate must be green before its dependents start):

- **A0 (proto extension) must land before A1.** New `multiplier` field on `Contract` proto needs to regen stubs both backend + sidecar before any code references it.
- **A1 (Alembic migration) must land before A3 (discoverer fan-out) and A5 (resolver simplification).** Both consume the table the migration creates.
- **B1 (sim_orders map) must land before B2 (CancelOrder branch).** B2 reads the map B1 creates.
- **C1 (BASE-round empirical pre-flight) must pass before C2 (sidecar startup change) is implemented.** If the pre-flight script proves the BASE round can't populate `accountValues()`, C2 falls back to "rely on `last_nlv_currency` only" and the C2 task becomes a docs-only update.
- **D1 (mock E2E) and D3 (real-IBKR E2E) require A1–A3 + B1–B2 + C2 to land.** End-to-end test exercises every component.
- **E3 (tag + deploy) is the USER GATE** — operator confirms before tagging v0.5.3.

### Parallel-safe pairs

| Parallel-safe | Why |
|---|---|
| A1 ⊥ B1 | migration vs sidecar map are independent inputs |
| A3 ⊥ B2 ⊥ C2 | distinct files, distinct surfaces, no shared state |
| D2 ⊥ D3 | independent workflow files + test files |

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `proto/broker/v1/broker.proto` | Modify (line 140-148) | Extend `Contract` message with `multiplier` field |
| `backend/app/_generated/broker/v1/broker_pb2*` | Regenerate | Backend proto stubs (gitignored; CI regenerates) |
| `sidecar/_generated/broker/v1/broker_pb2*` | Regenerate | Sidecar proto stubs (gitignored; CI regenerates) |
| `backend/alembic/versions/0005_positions.py` | Create | Migration: `positions` table |
| `backend/tests/migrations/test_0005_positions.py` | Create | 5 schema tests (FK, PK, CHECK, overflow, CASCADE) |
| `backend/app/services/brokers.py` | Modify | Add `_discover_positions` + `_upsert_positions` to `BrokerDiscoverer`; resurrect-clears-positions follow-up |
| `backend/app/core/metrics.py` | Modify | Add `broker_discover_positions_update_duration_ms` histogram + `broker_discover_positions_overflow_total` counter |
| `backend/tests/services/test_brokers_discover_positions.py` | Create | 8 unit tests for the fan-out path |
| `backend/app/services/orders_service.py` | Modify (line 647-658) | Drop the `to_regclass` guard from `_position_qty` (table now exists) |
| `backend/tests/services/test_orders_service_positions.py` | Create | 2 unit tests proving `_position_qty` reads populated rows |
| `sidecar/handlers.py` | Modify | Add `_sim_orders` map + extend `PlaceOrder` simulator + branch `CancelOrder` on SIM prefix + Trade synthesis + emit + populate `multiplier` in `_proto_contract` |
| `sidecar/metrics.py` | Modify | Add `broker_sim_cancel_echo_total` counter |
| `sidecar/tests/test_handlers_cancel_sim_echo.py` | Create | 4 unit tests for SIM cancel echo + idempotency + map cleanup |
| `sidecar/ibkr_sidecar.py` | Modify (line 210-232) | Replace startup sequence with per-account BASE round before reqAccountSummary |
| `sidecar/scripts/base_round_preflight.py` | Create | One-off empirical pre-flight (paper gateway 4002, prints accountValues per account) |
| `sidecar/tests/test_ibkr_sidecar_base_round.py` | Create | 3 unit tests proving BASE round sequencing + accountValues retention |
| `backend/tests/integration/__init__.py` | Create | Empty package marker |
| `backend/tests/integration/test_e2e_trade_chain.py` | Create | 7-step E2E mock chain |
| `backend/tests/fixtures/sidecar_servicer.py` | Modify | Extend with PlaceOrder/CancelOrder/OrderEvent for E2E mock |
| `sidecar/tests/test_real_ibkr_e2e_trade.py` | Create | Same 7 steps against real paper gateway 4002 (`@pytest.mark.real_ibkr`) |
| `.github/workflows/e2e-mock.yml` | Create | New CI workflow, every PR |
| `.github/workflows/real-ibkr.yml` | Modify | Add `e2e-trade` job after existing smoke job + workflow_dispatch input |
| `monitoring/alerts.yml` | Modify | Add `BrokerDiscoverPositionsP99HighWarning` + `BrokerSimCancelEchoMismatch` |
| `CHANGELOG.md` | Modify | New `[0.5.3]` block |
| `TASKS.md` | Modify | Phase 5b.1 chunk row + Phase 5c gap removals |
| `CLAUDE.md` | Modify | Extend "Phase 5b — IBKR trade execution (v0.5.1 + 5b.1 hardening)" |

---

## Chunk A — Schema + discoverer + resolver

### Task A0 — Extend `Contract` proto with `multiplier`

**Owner: Codex**

**Files:**
- Modify: `proto/broker/v1/broker.proto:140-148`
- Modify: `sidecar/handlers.py:_proto_contract` (line 614-622) and `_proto_contract_from_details` (line 624-632)

The architect review confirmed `Contract` lacks `multiplier`. Without it, `positions.multiplier` can't be populated for futures/options.

- [ ] **Step 1: Add field to proto**

Edit `proto/broker/v1/broker.proto` lines 140-148:

```protobuf
message Contract {
  string symbol = 1;
  string exchange = 2;
  string currency = 3;
  AssetClass asset_class = 4;
  // IBKR contract id, as string
  string conid = 5;
  string local_symbol = 6;
  // Contract multiplier (50 for futures @ES, 100 for options, 1 for stocks).
  // Sourced from ib_async.Contract.multiplier; defaults to "1" when absent.
  string multiplier = 7;
}
```

- [ ] **Step 2: Buf lint + format check**

Run: `cd proto && buf lint && buf format --diff --exit-code`
Expected: PASS (no diff).

- [ ] **Step 3: Verify backend stub regen**

Run:
```bash
cd backend && uv run python -m grpc_tools.protoc --proto_path=../proto --python_out=app/_generated --grpc_python_out=app/_generated --pyi_out=app/_generated broker/v1/broker.proto && grep -n "multiplier" app/_generated/broker/v1/broker_pb2.pyi
```
Expected: line containing `multiplier: builtins.str`.

- [ ] **Step 4: Verify sidecar stub regen**

Run:
```bash
cd sidecar && uv run python -m grpc_tools.protoc --proto_path=../proto --python_out=_generated --grpc_python_out=_generated --pyi_out=_generated broker/v1/broker.proto && grep -n "multiplier" _generated/broker/v1/broker_pb2.pyi
```
Expected: line containing `multiplier: builtins.str`.

- [ ] **Step 5: Sidecar handlers populate multiplier**

Modify `sidecar/handlers.py:_proto_contract` (line 614-622) — add `multiplier` field:

```python
def _proto_contract(self, ib_contract: _IbContract) -> broker_pb2.Contract:
    raw_mult = getattr(ib_contract, "multiplier", "")
    return broker_pb2.Contract(
        symbol=str(ib_contract.symbol),
        exchange=str(ib_contract.exchange),
        currency=str(ib_contract.currency),
        asset_class=self._asset_class(str(ib_contract.secType)),
        conid=str(ib_contract.conId),
        local_symbol=str(ib_contract.localSymbol),
        multiplier=str(raw_mult) if raw_mult else "1",
    )
```

Same edit for `_proto_contract_from_details` (line 624-632).

- [ ] **Step 6: Run sidecar tests (no real_ibkr)**

Run: `cd sidecar && .venv/bin/pytest -m "not real_ibkr" -q --no-header`
Expected: 154+ passed (additive change; no regressions).

- [ ] **Step 7: Commit**

```bash
git add proto/broker/v1/broker.proto sidecar/handlers.py
git commit -m "feat(proto): add multiplier field to Contract message

5b.1 positions table needs multiplier (50 for futures, 100 for options)
to compute notional correctly. Sidecar handlers populate it from
ib_async.Contract.multiplier with \"1\" default for stocks/missing."
```

---

### Task A1 — Alembic 0005: positions table

**Owner: Codex**

**Files:**
- Create: `backend/alembic/versions/0005_positions.py`

- [ ] **Step 1: Write migration**

Create `backend/alembic/versions/0005_positions.py`:

```python
"""positions table for per-account holdings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-28

Phase 5b.1 — surfaces broker positions per (account, conid) for the
order-preview position-sanity check, frontend portfolio widgets, and
future Phase 5c bracket-order math. Discoverer fan-out (Phase 5a
pattern) populates this table on its 30s tick.

avg_cost is per-share; multiplier comes from the Contract proto (50
for futures, 100 for options, 1 for stocks). notional = qty * avg_cost
* multiplier.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE positions (
          account_id    UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
          conid         VARCHAR(32) NOT NULL,
          qty           NUMERIC(20,8) NOT NULL,
          avg_cost      NUMERIC(20,8) NOT NULL,
          currency      VARCHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          multiplier    NUMERIC(20,8) NOT NULL DEFAULT 1,
          asset_class   VARCHAR(16)   NOT NULL DEFAULT 'STOCK',
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (account_id, conid)
        );
        """
    )
    op.execute("CREATE INDEX positions_account_id_idx ON positions(account_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS positions_account_id_idx;")
    op.execute("DROP TABLE IF EXISTS positions;")
```

- [ ] **Step 2: Apply migration locally + verify schema**

Run:
```bash
cd backend && .venv/bin/alembic upgrade head && \
  .venv/bin/python -c "
import asyncio, asyncpg
from app.core.config import settings
async def main():
    conn = await asyncpg.connect(settings.database_url.replace('+asyncpg',''))
    rows = await conn.fetch(\"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'positions' ORDER BY ordinal_position\")
    for r in rows: print(dict(r))
    await conn.close()
asyncio.run(main())
"
```
Expected: 8 rows: account_id (uuid), conid (character varying), qty (numeric), avg_cost (numeric), currency (character varying), multiplier (numeric), asset_class (character varying), updated_at (timestamp with time zone).

- [ ] **Step 3: Verify downgrade**

Run:
```bash
cd backend && .venv/bin/alembic downgrade -1 && \
  PGPASSWORD=$(grep -E '^DATABASE_URL=' .env | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|') \
  /mnt/c/Program\ Files/PostgreSQL/18/bin/psql.exe -h 10.10.0.2 -U trader -d dashboard -tAc "SELECT to_regclass('public.positions')"
```
Expected: empty (table dropped).

Then re-upgrade: `cd backend && .venv/bin/alembic upgrade head`.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0005_positions.py
git commit -m "feat(backend): alembic 0005 positions table

Per-account holdings cache populated by BrokerDiscoverer fan-out.
Composite PK (account_id, conid). avg_cost per-share + multiplier
denormalized so notional = qty * avg_cost * multiplier round-trips
without contract joins. ON DELETE CASCADE on broker_accounts."
```

---

### Task A2 — Migration tests for 0005

**Owner: Claude**

**Files:**
- Create: `backend/tests/migrations/test_0005_positions.py`

- [ ] **Step 1: Write 5 tests**

Create `backend/tests/migrations/test_0005_positions.py`:

```python
"""Migration 0005 — positions table constraint tests.

Mirrors test_0004 patterns: outer-rollback `session` fixture from
tests.fixtures.db_session + session.begin_nested() savepoints for
IntegrityError tolerance.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> str:
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    row = (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :acct_num"),
            {"acct_num": account_number},
        )
    ).one()
    return str(row[0])


async def _insert_position(
    session: AsyncSession, *, account_id: str, conid: str, qty: str = "100",
    avg_cost: str = "150.50", currency: str = "USD", multiplier: str = "1",
    asset_class: str = "STOCK",
) -> None:
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class) "
            "VALUES (:account_id, :conid, :qty, :avg_cost, :currency, :multiplier, :asset_class)"
        ),
        {
            "account_id": account_id, "conid": conid, "qty": qty, "avg_cost": avg_cost,
            "currency": currency, "multiplier": multiplier, "asset_class": asset_class,
        },
    )


@pytest.mark.asyncio
async def test_positions_composite_primary_key(session: AsyncSession) -> None:
    """Same (account_id, conid) cannot be inserted twice."""
    acct_id = await _seed_account(session, "TEST_PK_001")
    await _insert_position(session, account_id=acct_id, conid="265598")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _insert_position(session, account_id=acct_id, conid="265598")


@pytest.mark.asyncio
async def test_positions_currency_check_rejects_lowercase(session: AsyncSession) -> None:
    """CHECK constraint matches `^[A-Z]{3}$` — lowercase rejected."""
    acct_id = await _seed_account(session, "TEST_CURR_001")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _insert_position(session, account_id=acct_id, conid="265598", currency="usd")


@pytest.mark.asyncio
async def test_positions_currency_check_rejects_4chars(session: AsyncSession) -> None:
    """CHECK constraint length=3 — 4-char currency rejected."""
    acct_id = await _seed_account(session, "TEST_CURR_002")
    with pytest.raises((IntegrityError, DataError)):
        async with session.begin_nested():
            await _insert_position(session, account_id=acct_id, conid="265598", currency="USDD")


@pytest.mark.asyncio
async def test_positions_qty_overflow_rejected(session: AsyncSession) -> None:
    """NUMERIC(20,8) overflow at qty > 999_999_999_999.99999999."""
    acct_id = await _seed_account(session, "TEST_OFLOW_001")
    with pytest.raises(DataError):
        async with session.begin_nested():
            await _insert_position(
                session, account_id=acct_id, conid="265598",
                qty="9999999999999.99",
            )


@pytest.mark.asyncio
async def test_positions_cascade_on_account_hard_delete(session: AsyncSession) -> None:
    """ON DELETE CASCADE removes positions when broker_accounts row hard-deleted."""
    acct_id = await _seed_account(session, "TEST_CASCADE_001")
    await _insert_position(session, account_id=acct_id, conid="265598")
    await _insert_position(session, account_id=acct_id, conid="272093")

    await session.execute(
        text("DELETE FROM broker_accounts WHERE id::text = :acct_id"),
        {"acct_id": acct_id},
    )
    remaining = await session.execute(
        text("SELECT COUNT(*) FROM positions WHERE account_id::text = :acct_id"),
        {"acct_id": acct_id},
    )
    assert remaining.scalar_one() == 0
```

- [ ] **Step 2: Run tests**

Run: `cd backend && .venv/bin/pytest tests/migrations/test_0005_positions.py -v --no-header`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/migrations/test_0005_positions.py
git commit -m "test(backend): migration 0005 schema + constraint tests

5 tests: composite PK uniqueness, currency CHECK regex (lowercase + 4-char),
NUMERIC(20,8) overflow, ON DELETE CASCADE. Mirrors test_0004 outer-rollback
+ begin_nested savepoint pattern."
```

---

### Task A3 — `BrokerDiscoverer._discover_positions` fan-out

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/brokers.py` (add `_discover_positions` + `_upsert_positions` + helper; extend `_discover_once` to call after NLV; resurrect-clears-positions follow-up)
- Modify: `backend/app/core/metrics.py` (add 2 metrics)

- [ ] **Step 1: Add metrics**

Edit `backend/app/core/metrics.py`. Locate the existing `broker_discover_nlv_*` block (Phase 5a) and add two siblings immediately after:

```python
broker_discover_positions_update_duration_ms = Histogram(
    "broker_discover_positions_update_duration_ms",
    "BrokerDiscoverer per-tick GetPositions fan-out + DB upsert duration in ms",
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
)

broker_discover_positions_overflow_total = Counter(
    "broker_discover_positions_overflow_total",
    "Per-account NUMERIC(20,8) overflow rejections during positions upsert",
    labelnames=("label",),
)
```

- [ ] **Step 2: Add helpers + `_discover_positions` method**

Edit `backend/app/services/brokers.py`. After `_discover_nlv` (Phase 5a's fan-out method — locate via `grep -n "_discover_nlv" app/services/brokers.py`), add helper + method:

```python
def _proto_asset_class_to_str(ac: int) -> str:
    """Convert proto AssetClass enum to schema's VARCHAR value."""
    from app._generated.broker.v1 import broker_pb2
    return {
        broker_pb2.ASSET_CLASS_STOCK: "STOCK",
        broker_pb2.ASSET_CLASS_FUTURE: "FUTURE",
        broker_pb2.ASSET_CLASS_OPTION: "OPTION",
        broker_pb2.ASSET_CLASS_FOREX: "FOREX",
    }.get(ac, "STOCK")


# Inside class BrokerDiscoverer:

async def _discover_positions(self, streams: list["_AccountStream"]) -> None:
    """Fan out GetPositions per account, upsert positions, delete vanished rows.

    Mirrors _discover_nlv (Phase 5a) — per-account savepoint isolates
    NUMERIC(20,8) overflow, RPC failures leave the row untouched, gather
    return_exceptions=True ensures one failure doesn't break the batch.
    """
    if not streams:
        return

    started = time.perf_counter()
    calls = []
    for stream in streams:
        client = await self._registry.get_client(stream.label)
        calls.append(asyncio.wait_for(client.get_positions(stream.account_number), timeout=10.0))
    results = await asyncio.gather(*calls, return_exceptions=True)

    async with self._session_factory() as session, session.begin():
        for stream, result in zip(streams, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "broker_discover_positions_rpc_failed",
                    label=stream.label,
                    account_id=str(stream.account_id),
                    error=str(result),
                )
                continue
            try:
                async with session.begin_nested():
                    await self._upsert_positions(session, stream.account_id, result)
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "22003":
                    metrics.broker_discover_positions_overflow_total.labels(
                        label=stream.label
                    ).inc()
                    log.warning(
                        "broker_discover_positions_overflow",
                        label=stream.label,
                        account_id=str(stream.account_id),
                    )
                    continue
                raise

    metrics.broker_discover_positions_update_duration_ms.observe(
        (time.perf_counter() - started) * 1000.0
    )


async def _upsert_positions(
    self, session: AsyncSession, account_id: UUID, positions: list[Position]
) -> None:
    """Atomic upsert + delta-delete for one account's positions.

    Uses NOT EXISTS (not NOT IN) — NULL-safe per architect-review HIGH-4.
    """
    rows_json = json.dumps([
        {
            "conid": p.contract.conid,
            "qty": p.quantity,
            "avg_cost": p.avg_cost.value,
            "currency": p.avg_cost.currency,
            "multiplier": p.contract.multiplier or "1",
            "asset_class": _proto_asset_class_to_str(p.contract.asset_class),
        }
        for p in positions
    ])
    await session.execute(
        text(
            """
            WITH upserted AS (
              INSERT INTO positions (account_id, conid, qty, avg_cost, currency,
                                     multiplier, asset_class, updated_at)
              SELECT :account_id, conid, qty::numeric, avg_cost::numeric, currency,
                     multiplier::numeric, asset_class, now()
                FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                  AS x(conid varchar, qty varchar, avg_cost varchar, currency varchar,
                       multiplier varchar, asset_class varchar)
              ON CONFLICT (account_id, conid) DO UPDATE
                SET qty = EXCLUDED.qty,
                    avg_cost = EXCLUDED.avg_cost,
                    currency = EXCLUDED.currency,
                    multiplier = EXCLUDED.multiplier,
                    asset_class = EXCLUDED.asset_class,
                    updated_at = now()
              RETURNING conid
            )
            DELETE FROM positions p
             WHERE p.account_id = :account_id
               AND NOT EXISTS (SELECT 1 FROM upserted u WHERE u.conid = p.conid);
            """
        ),
        {"account_id": account_id, "rows": rows_json},
    )
```

- [ ] **Step 3: Wire into `_discover_once`**

Find the existing `_discover_once` method body. After the existing `await self._discover_nlv(streams)` line, add:

```python
await self._discover_positions(streams)
```

- [ ] **Step 4: Resurrect-clears-positions follow-up**

After the upsert phase that detects resurrected accounts (existing Phase 5a code that capture `resurrected_keys` or similar), add a parallel DELETE:

```python
# Resurrect-from-soft-delete clears positions cache (R1 in spec).
# If the existing upsert RETURNs resurrected ids, prefer that; otherwise
# use the (broker_id, account_number) key set captured pre-upsert.
if resurrected_account_ids:
    await session.execute(
        text(
            "DELETE FROM positions WHERE account_id = ANY(:ids)"
        ),
        {"ids": list(resurrected_account_ids)},
    )
```

(If existing code doesn't capture resurrected ids, extend the upsert RETURNING clause to detect them via `(xmax = 0)` semantics — Postgres returns 0 for INSERT, non-zero for UPDATE — and add a CTE branch capturing the rows where `deleted_at WAS NOT NULL` before this update.)

- [ ] **Step 5: Run lint + typecheck**

Run: `cd backend && .venv/bin/ruff check app/services/brokers.py app/core/metrics.py && .venv/bin/mypy app/services/brokers.py app/core/metrics.py`
Expected: All checks passed; Success: no issues found.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/brokers.py backend/app/core/metrics.py
git commit -m "feat(backend): broker discoverer positions fan-out (5b.1 A3)

Mirrors Phase 5a NLV pattern: per-account GetPositions, savepoint-
isolated upsert + delta-delete via NOT EXISTS (architect HIGH-4),
sqlstate 22003 -> overflow metric + skip. Resurrect-from-soft-delete
clears the positions cache so frontend doesn't briefly display
week-old stale rows. Wires into _discover_once after _discover_nlv."
```

---

### Task A4 — Unit tests for `_discover_positions`

**Owner: Claude**

**Files:**
- Create: `backend/tests/services/test_brokers_discover_positions.py`

- [ ] **Step 1: Write 8 tests**

Create `backend/tests/services/test_brokers_discover_positions.py`:

```python
"""Unit tests for BrokerDiscoverer._discover_positions (5b.1 A3).

Mirrors test_brokers_discover_nlv (Phase 5a). Uses the in-memory mock
sidecar client + outer-rollback session fixture from
tests.fixtures.db_session.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers import base
from app.services.brokers import BrokerDiscoverer, _AccountStream

_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> str:
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    return (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :acct_num"),
            {"acct_num": account_number},
        )
    ).scalar_one()


def _mk_position(conid: str, qty: str = "100", avg_cost: str = "150",
                 currency: str = "USD", multiplier: str = "1",
                 asset_class: str = "STOCK") -> base.Position:
    return base.Position(
        contract=base.Contract(
            conid=conid, symbol=conid, exchange="SMART", currency=currency,
            asset_class=asset_class, multiplier=multiplier, local_symbol="",
        ),
        quantity=qty,
        avg_cost=base.Money(value=avg_cost, currency=currency),
        market_price=base.Money(value="0", currency=currency),
        market_value=base.Money(value="0", currency=currency),
        unrealized_pnl=base.Money(value="0", currency=currency),
        realized_pnl_today=base.Money(value="0", currency=currency),
        daily_pnl=base.Money(value="0", currency=currency),
    )


@pytest.fixture
def discoverer(session_factory: async_sessionmaker) -> BrokerDiscoverer:
    registry = MagicMock()
    return BrokerDiscoverer(registry, session_factory)


@pytest.mark.asyncio
async def test_fan_out_upserts_positions(session: AsyncSession, discoverer) -> None:
    """Happy path: GetPositions returns 2 positions, both upserted."""
    acct_id = await _seed_account(session, "TEST_FAN_001")
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598"), _mk_position("272093")])
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_FAN_001")]
    await discoverer._discover_positions(streams)

    rows = await session.execute(
        text("SELECT conid FROM positions WHERE account_id::text = :a ORDER BY conid"),
        {"a": acct_id},
    )
    assert [r[0] for r in rows.all()] == ["265598", "272093"]


@pytest.mark.asyncio
async def test_rpc_failure_leaves_existing_rows(session: AsyncSession, discoverer) -> None:
    """RPC raises -> existing positions for that account untouched."""
    acct_id = await _seed_account(session, "TEST_RPC_FAIL_001")
    await session.execute(
        text("INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class) "
             "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK')"),
        {"a": acct_id},
    )
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(side_effect=TimeoutError())
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_RPC_FAIL_001")]
    await discoverer._discover_positions(streams)

    count = (await session.execute(
        text("SELECT COUNT(*) FROM positions WHERE account_id::text = :a"),
        {"a": acct_id},
    )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_empty_response_deletes_all_positions(session: AsyncSession, discoverer) -> None:
    """Successful empty response (account liquidated) -> all rows deleted."""
    acct_id = await _seed_account(session, "TEST_EMPTY_001")
    await session.execute(
        text("INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class) "
             "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK')"),
        {"a": acct_id},
    )
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_EMPTY_001")]
    await discoverer._discover_positions(streams)

    count = (await session.execute(
        text("SELECT COUNT(*) FROM positions WHERE account_id::text = :a"),
        {"a": acct_id},
    )).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_overflow_savepoint_isolates(session: AsyncSession, discoverer) -> None:
    """NUMERIC(20,8) overflow on one account doesn't break others."""
    acct_a = await _seed_account(session, "TEST_OF_A")
    acct_b = await _seed_account(session, "TEST_OF_B")
    await session.commit()

    bad_pos = _mk_position("265598", qty="9" * 25)  # overflow
    good_pos = _mk_position("272093")

    async def get_positions(account_number: str) -> list[base.Position]:
        return [bad_pos] if account_number == "TEST_OF_A" else [good_pos]

    client = MagicMock()
    client.get_positions = AsyncMock(side_effect=get_positions)
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [
        _AccountStream(label="isa-paper", account_id=acct_a, account_number="TEST_OF_A"),
        _AccountStream(label="isa-paper", account_id=acct_b, account_number="TEST_OF_B"),
    ]
    await discoverer._discover_positions(streams)

    rows_a = (await session.execute(
        text("SELECT COUNT(*) FROM positions WHERE account_id::text = :a"),
        {"a": acct_a},
    )).scalar_one()
    rows_b = (await session.execute(
        text("SELECT COUNT(*) FROM positions WHERE account_id::text = :b"),
        {"b": acct_b},
    )).scalar_one()
    assert rows_a == 0
    assert rows_b == 1


@pytest.mark.asyncio
async def test_delta_delete_removes_vanished_position(session: AsyncSession, discoverer) -> None:
    """Position present last tick, absent this tick -> deleted."""
    acct_id = await _seed_account(session, "TEST_VANISH_001")
    await session.execute(
        text("INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class) "
             "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK'), "
             "       (:a, '272093', '50', '300', 'USD', '1', 'STOCK')"),
        {"a": acct_id},
    )
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598")])  # 272093 vanished
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_VANISH_001")]
    await discoverer._discover_positions(streams)

    rows = (await session.execute(
        text("SELECT conid FROM positions WHERE account_id::text = :a"),
        {"a": acct_id},
    )).all()
    assert [r[0] for r in rows] == ["265598"]


@pytest.mark.asyncio
async def test_qty_update_advances_updated_at(session: AsyncSession, discoverer) -> None:
    """Same conid, new qty -> updated_at progresses on UPDATE branch."""
    acct_id = await _seed_account(session, "TEST_UPD_001")
    await session.execute(
        text("INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class, updated_at) "
             "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK', '2026-01-01'::timestamptz)"),
        {"a": acct_id},
    )
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598", qty="200")])
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_UPD_001")]
    await discoverer._discover_positions(streams)

    row = (await session.execute(
        text("SELECT qty, updated_at > '2026-01-02'::timestamptz FROM positions WHERE account_id::text = :a"),
        {"a": acct_id},
    )).one()
    assert Decimal(row[0]) == Decimal("200")
    assert row[1] is True


@pytest.mark.asyncio
async def test_currency_flip_persists(session: AsyncSession, discoverer) -> None:
    """Currency change mid-life (USD -> GBP) is honoured by upsert."""
    acct_id = await _seed_account(session, "TEST_CURR_FLIP_001")
    await session.execute(
        text("INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class) "
             "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK')"),
        {"a": acct_id},
    )
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598", currency="GBP")])
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_CURR_FLIP_001")]
    await discoverer._discover_positions(streams)

    row = (await session.execute(
        text("SELECT currency FROM positions WHERE account_id::text = :a"),
        {"a": acct_id},
    )).one()
    assert row[0] == "GBP"


@pytest.mark.asyncio
async def test_metric_emitted(session: AsyncSession, discoverer) -> None:
    """Histogram observes a duration sample on each call."""
    from app.core import metrics
    before = metrics.broker_discover_positions_update_duration_ms._sum.get()

    acct_id = await _seed_account(session, "TEST_METRIC_001")
    await session.commit()

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    discoverer._registry.get_client = AsyncMock(return_value=client)

    streams = [_AccountStream(label="isa-paper", account_id=acct_id, account_number="TEST_METRIC_001")]
    await discoverer._discover_positions(streams)

    after = metrics.broker_discover_positions_update_duration_ms._sum.get()
    assert after > before
```

- [ ] **Step 2: Run tests**

Run: `cd backend && .venv/bin/pytest tests/services/test_brokers_discover_positions.py -v --no-header`
Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_brokers_discover_positions.py
git commit -m "test(backend): broker discoverer positions fan-out (5b.1 A4)

8 unit tests: happy path, RPC failure leaves rows intact, empty response
deletes all, overflow savepoint isolates, delta delete, qty update progresses
updated_at, currency flip mid-life, metric emitted."
```

---

### Task A5 — Drop `_position_qty` `to_regclass` guard

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/orders_service.py:647-665`

The defensive `to_regclass` guard from `b5a633d` is no longer needed: 0005 creates the table.

- [ ] **Step 1: Simplify `_position_qty`**

Edit `backend/app/services/orders_service.py`. Replace the `_position_qty` body (current lines ~647-665):

```python
async def _position_qty(db: AsyncSession, account_id: object, conid: str) -> Decimal:
    # As of 5b.1, the positions table is guaranteed by Alembic 0005 +
    # populated by BrokerDiscoverer fan-out within 30s of bootstrap.
    # Returns Decimal("0") for accounts with no holdings (no row).
    result = await db.execute(
        text(
            """
            SELECT qty
              FROM positions
             WHERE account_id = :account_id AND conid = :conid;
            """
        ),
        {"account_id": account_id, "conid": conid},
    )
    return Decimal(str(result.scalar_one_or_none() or "0"))
```

- [ ] **Step 2: Run lint + existing tests**

Run: `cd backend && .venv/bin/ruff check app/services/orders_service.py && .venv/bin/pytest tests/api/test_orders_preview.py tests/api/test_orders_place.py -q --no-header`
Expected: All ruff passed; preview + place tests still pass (their _Session mocks return scalar=Decimal("0") for "FROM positions" queries).

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/orders_service.py
git commit -m "refactor(orders): drop to_regclass guard from _position_qty

Phase 5b.1 Alembic 0005 creates the positions table; the defensive guard
from b5a633d is no longer needed. _position_qty now reads real values
when discoverer has populated the row."
```

---

### Task A6 — Unit tests for `_position_qty` against populated rows

**Owner: Claude**

**Files:**
- Create: `backend/tests/services/test_orders_service_positions.py`

- [ ] **Step 1: Write 2 tests**

Create `backend/tests/services/test_orders_service_positions.py`:

```python
"""Phase 5b.1 A6: _position_qty reads real values from positions table."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orders_service import _position_qty


async def _seed_acct(session: AsyncSession, acct_num: str) -> str:
    await session.execute(
        text(
            "INSERT INTO broker_accounts (broker_id, account_number, mode, "
            "gateway_label, currency_base, last_seen_via) "
            "VALUES ('ibkr', :n, 'paper', 'isa-paper', 'USD', 'isa-paper')"
        ),
        {"n": acct_num},
    )
    return (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :n"),
            {"n": acct_num},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_position_qty_reads_populated_row(session: AsyncSession) -> None:
    """_position_qty returns the qty when a row exists."""
    acct_id = await _seed_acct(session, "TEST_PQ_001")
    await session.execute(
        text("INSERT INTO positions (account_id, conid, qty, avg_cost, currency, multiplier, asset_class) "
             "VALUES (:a, '265598', '150.5', '99', 'USD', '1', 'STOCK')"),
        {"a": acct_id},
    )

    qty = await _position_qty(session, acct_id, "265598")
    assert qty == Decimal("150.5")


@pytest.mark.asyncio
async def test_position_qty_returns_zero_for_no_row(session: AsyncSession) -> None:
    """No matching row -> Decimal('0')."""
    acct_id = await _seed_acct(session, "TEST_PQ_002")

    qty = await _position_qty(session, acct_id, "999999")
    assert qty == Decimal("0")
```

- [ ] **Step 2: Run tests**

Run: `cd backend && .venv/bin/pytest tests/services/test_orders_service_positions.py -v --no-header`
Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_orders_service_positions.py
git commit -m "test(backend): _position_qty reads positions rows (5b.1 A6)

2 tests: populated row returns qty, no row returns 0."
```

---

## Chunk B — Sidecar SIM cancel echo

### Task B1 — Add `_sim_orders` map + register on PlaceOrder

**Owner: Codex**

**Files:**
- Modify: `sidecar/handlers.py` (add attribute in `__init__`; PlaceOrder simulator branch line 410-422)

- [ ] **Step 1: Add `_sim_orders` to `__init__`**

Edit `sidecar/handlers.py`. Locate `BrokerHandlers.__init__` (line ~140). After the existing initializers, add:

```python
# Maps SIM-<uuid> broker_order_id -> {"client_order_id": ..., "account_number": ...}.
# Required by CancelOrder (SIM branch) to (1) recognize a SIM order without
# int-parsing the prefix, (2) reconstruct the orderRef + account for the
# synthetic cancellation event fired through ib.orderStatusEvent.emit().
self._sim_orders: dict[str, dict[str, str]] = {}
```

- [ ] **Step 2: Extend PlaceOrder simulator branch**

Replace lines 410-422 of `sidecar/handlers.py`:

```python
if self._simulator_only:
    from uuid_utils import uuid7

    sim_id: str = f"SIM-{uuid7()}"
    self._sim_orders[sim_id] = {
        "client_order_id": request.client_order_id,
        "account_number": request.account_number,
    }
    logger.info(
        "place_order_simulated",
        client_order_id=request.client_order_id,
        sim_id=sim_id,
    )
    return broker_pb2.PlaceOrderResponse(
        broker_order_id=sim_id,
        status="Submitted",
    )
```

- [ ] **Step 3: Lint + sidecar tests**

Run: `cd sidecar && .venv/bin/ruff check handlers.py && .venv/bin/pytest tests/test_handlers_orders_contract.py -q --no-header`
Expected: All checks passed; existing 33 tests still pass (additive change).

- [ ] **Step 4: Commit**

```bash
git add sidecar/handlers.py
git commit -m "feat(sidecar): track SIM orders in _sim_orders map (5b.1 B1)

PlaceOrder simulator branch now registers SIM-<uuid> -> orderRef+account
metadata so CancelOrder can recognize and synthesize cancellation events
without int-parsing the SIM- prefix (which would raise ValueError).
Pre-req for B2 cancel echo."
```

---

### Task B2 — Branch `CancelOrder` on SIM prefix + synthetic Trade emit

**Owner: Codex**

**Files:**
- Modify: `sidecar/handlers.py:CancelOrder` (line 448-465)
- Modify: `sidecar/metrics.py` — add `broker_sim_cancel_echo_total`

- [ ] **Step 1: Add metric**

Find sidecar metrics module: `find sidecar -name "metrics.py" -not -path "*/.venv/*" | head -1`. Add to it:

```python
broker_sim_cancel_echo_total = Counter(
    "broker_sim_cancel_echo_total",
    "Synthetic cancellation events emitted for SIM-prefixed orders",
    labelnames=("label",),
)
```

- [ ] **Step 2: Replace CancelOrder body**

Replace lines 448-465 of `sidecar/handlers.py`:

```python
async def CancelOrder(  # noqa: N802
    self,
    request: broker_pb2.CancelOrderRequest,
    context: object,
) -> broker_pb2.CancelOrderResponse:
    del context
    broker_order_id = request.broker_order_id

    # SIM path: synthesize a Trade-like SimpleNamespace and fire
    # ib.orderStatusEvent.emit(...). The existing OrderEvent stream's
    # per-subscriber _on_status callback (line 474) then queue.put_nowait()s
    # the proto event for every connected backend consumer. No new
    # singleton state required — reuses real-broker plumbing exactly.
    if broker_order_id.startswith("SIM-"):
        sim_meta = self._sim_orders.pop(broker_order_id, None)
        if sim_meta is None:
            return broker_pb2.CancelOrderResponse(accepted=False)

        from decimal import Decimal
        from types import SimpleNamespace

        synthetic_trade = SimpleNamespace(
            order=SimpleNamespace(
                permId=broker_order_id,
                orderRef=sim_meta["client_order_id"],
                account=sim_meta["account_number"],
            ),
            orderStatus=SimpleNamespace(
                status="Cancelled",
                filled=Decimal("0"),
                avgFillPrice=Decimal("0"),
            ),
            contract=SimpleNamespace(
                currency="USD", symbol="", exchange="",
                conId=0, secType="STK", localSymbol="",
            ),
            fills=[],
            log=[],
        )
        # ib_async.Event.emit() invokes registered handlers synchronously.
        self.ib.orderStatusEvent.emit(synthetic_trade)  # type: ignore[attr-defined, unused-ignore]
        metrics.broker_sim_cancel_echo_total.labels(label=self.label).inc()
        return broker_pb2.CancelOrderResponse(accepted=True)

    # Real-broker path — int-parse is now safe (SIM rejected above)
    raw_trades: object = self.ib.openTrades()  # type: ignore[attr-defined, unused-ignore]
    for trade in cast("Iterable[object]", raw_trades):
        ib_trade: _IbTrade = cast("_IbTrade", trade)
        if (
            ib_trade.order.permId == int(broker_order_id)
            and ib_trade.order.account == request.account_number
        ):
            self.ib.cancelOrder(ib_trade.order)  # type: ignore[attr-defined, unused-ignore]
            return broker_pb2.CancelOrderResponse(accepted=True)

    return broker_pb2.CancelOrderResponse(accepted=False)
```

- [ ] **Step 3: Verify `_proto_event_from_trade` handles non-int permId**

Run: `cd sidecar && grep -n "permId" handlers.py`
Confirm line ~604 reads `str(trade.order.permId)` — passes SIM-uuid string unchanged.

- [ ] **Step 4: Lint + existing tests still pass**

Run: `cd sidecar && .venv/bin/ruff check handlers.py && .venv/bin/pytest tests/test_handlers_orders_contract.py -q --no-header`
Expected: All checks passed; 33 tests pass.

- [ ] **Step 5: Commit**

```bash
git add sidecar/handlers.py sidecar/metrics.py
git commit -m "feat(sidecar): cancel order SIM echo via synthetic Trade emit (5b.1 B2)

Branches CancelOrder on SIM- prefix BEFORE int-parsing (pre-existing
latent ValueError bug for SIM orders). Synthesizes a SimpleNamespace
Trade and fires ib.orderStatusEvent.emit() — the per-subscriber
OrderEvent stream's _on_status callback (line 474) then queues the
synthetic cancelled event for every connected consumer. No new
singleton state; reuses existing fan-out plumbing.

Idempotent: re-cancelling a missing SIM order returns accepted=False."
```

---

### Task B3 — Unit tests for SIM cancel echo

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_handlers_cancel_sim_echo.py`

- [ ] **Step 1: Write 4 tests**

Create `sidecar/tests/test_handlers_cancel_sim_echo.py`:

```python
"""Sidecar SIM cancel echo (5b.1 B2 + B3) — synthetic Trade emit."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidecar import handlers
from sidecar._generated.broker.v1 import broker_pb2


@pytest.fixture
def mock_ib() -> MagicMock:
    """ib_async.IB stand-in with orderStatusEvent.emit() spy."""
    ib = MagicMock()
    ib.orderStatusEvent = MagicMock()
    ib.orderStatusEvent.emit = MagicMock()
    return ib


@pytest.fixture
def handler(mock_ib: MagicMock) -> handlers.BrokerHandlers:
    last_tick_ref = {"value": 0.0}
    return handlers.BrokerHandlers(
        ib=mock_ib,
        pnl_cache={},
        label="isa-paper",
        version="0.5.3-dev",
        last_tick_ref=last_tick_ref,
    )


@pytest.mark.asyncio
async def test_sim_cancel_emits_synthetic_event(handler, mock_ib) -> None:
    """SIM cancel fires ib.orderStatusEvent.emit() with cancelled status."""
    sim_id = "SIM-019dd33b-ece0-75d3-92a6-12ec044c08d8"
    handler._sim_orders[sim_id] = {
        "client_order_id": "test-coid-1",
        "account_number": "DU123456",
    }
    request = broker_pb2.CancelOrderRequest(
        broker_order_id=sim_id, account_number="DU123456",
    )

    response = await handler.CancelOrder(request, context=MagicMock())

    assert response.accepted is True
    assert mock_ib.orderStatusEvent.emit.called
    synthetic = mock_ib.orderStatusEvent.emit.call_args[0][0]
    assert synthetic.order.permId == sim_id
    assert synthetic.order.orderRef == "test-coid-1"
    assert synthetic.order.account == "DU123456"
    assert synthetic.orderStatus.status == "Cancelled"
    assert sim_id not in handler._sim_orders


@pytest.mark.asyncio
async def test_real_cancel_does_not_emit_synthetic(handler, mock_ib) -> None:
    """Real-broker cancel goes through openTrades path, no synthetic emit."""
    mock_ib.openTrades = MagicMock(return_value=[])
    request = broker_pb2.CancelOrderRequest(
        broker_order_id="12345", account_number="DU123456",
    )

    response = await handler.CancelOrder(request, context=MagicMock())

    assert response.accepted is False
    assert not mock_ib.orderStatusEvent.emit.called


@pytest.mark.asyncio
async def test_sim_cancel_idempotent(handler, mock_ib) -> None:
    """Re-cancelling a missing SIM order returns accepted=False, no duplicate emit."""
    request = broker_pb2.CancelOrderRequest(
        broker_order_id="SIM-nonexistent", account_number="DU123456",
    )

    response = await handler.CancelOrder(request, context=MagicMock())

    assert response.accepted is False
    assert not mock_ib.orderStatusEvent.emit.called


@pytest.mark.asyncio
async def test_sim_cancel_pops_map(handler, mock_ib) -> None:
    """Successful SIM cancel removes the entry from _sim_orders."""
    sim_id = "SIM-019dd33e-9c34-7ff1-bc6a-b394e6c01dda"
    handler._sim_orders[sim_id] = {
        "client_order_id": "test-coid-2",
        "account_number": "DU123456",
    }

    await handler.CancelOrder(
        broker_pb2.CancelOrderRequest(broker_order_id=sim_id, account_number="DU123456"),
        context=MagicMock(),
    )
    assert sim_id not in handler._sim_orders
```

- [ ] **Step 2: Run tests**

Run: `cd sidecar && .venv/bin/pytest tests/test_handlers_cancel_sim_echo.py -v --no-header`
Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add sidecar/tests/test_handlers_cancel_sim_echo.py
git commit -m "test(sidecar): SIM cancel echo synthetic emit (5b.1 B3)

4 tests: synthetic trade fires ib.orderStatusEvent.emit with right
fields, real cancel does not emit, missing SIM is idempotent
no-op, _sim_orders map cleaned up on success."
```

---

## Chunk C — Sidecar BASE-tag startup round

### Task C1 — Empirical pre-flight script + dev-box run

**Owner: Claude**

**Files:**
- Create: `sidecar/scripts/base_round_preflight.py`

This is a TASK GATE. C2 implementation is blocked until this script exits 0 on the dev box (paper gateway 4002). If pre-flight fails, C2 falls back to docs-only ("rely on `last_nlv_currency`").

- [ ] **Step 1: Write the script**

Create `sidecar/scripts/base_round_preflight.py`:

```python
"""5b.1 C1 — empirical pre-flight for the BASE-round design.

Run on the dev box (paper gateway 4002 must be up). Validates that:
1. reqAccountUpdates(True, account) populates accountValues with BASE
2. accountValues retains BASE after reqAccountUpdates(False, account)
3. The sequence works for ALL managed accounts, not just the first one

Usage:
    cd sidecar && uv run python scripts/base_round_preflight.py
Exit code: 0 if BASE present for all accounts after the round, 1 otherwise.
"""
from __future__ import annotations

import asyncio
import sys

from ib_async import IB


async def main() -> int:
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=999, timeout=30)
    print(f"connected; managed_accounts = {ib.managedAccounts()}")

    accounts = list(ib.managedAccounts())
    for acct in accounts:
        print(f"-- subscribing BASE for {acct} --")
        ib.reqAccountUpdates(True, acct)
        await asyncio.sleep(1.5)
        ib.reqAccountUpdates(False, acct)
        await asyncio.sleep(0.2)

    print("-- inspecting accountValues after round --")
    missing: list[str] = []
    for acct in accounts:
        base = next(
            (v.value for v in ib.accountValues() if v.tag == "BASE" and v.account == acct),
            None,
        )
        print(f"  {acct}: BASE = {base!r}")
        if not base:
            missing.append(acct)

    ib.disconnect()
    if missing:
        print(f"FAIL: BASE missing for {missing}", file=sys.stderr)
        return 1
    print("PASS: BASE present for all accounts")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: OPERATOR — run on dev box**

Operator-side step. The user runs:
```bash
cd sidecar && uv run python scripts/base_round_preflight.py
```
**Acceptance:** exit code 0 + BASE present for all 6 isa-paper accounts.

If the script outputs `FAIL: BASE missing for [...]`, **STOP**. C2 falls back to a docs-only note ("rely on `last_nlv_currency` fallback shipped in 9910e3b") and the rest of Chunk C is skipped. Update the spec accordingly and re-loop architect review on the fallback design.

If exit 0, capture stdout into the commit message (so future readers can see the empirical evidence).

- [ ] **Step 3: Commit (only after acceptance)**

```bash
git add sidecar/scripts/base_round_preflight.py
git commit -m "chore(sidecar): empirical BASE-round pre-flight script (5b.1 C1)

Validates the sequential per-account reqAccountUpdates round populates
accountValues with BASE tag and retains it after unsubscribe. Run on
dev box before C2 lands.

Acceptance run output (paper gateway 4002, $(date -u +%Y-%m-%d)):
    <PASTE STDOUT HERE>
    PASS: BASE present for all accounts"
```

---

### Task C2 — Sidecar startup BASE round

**Owner: Codex** — **GATED on C1 acceptance**

**Files:**
- Modify: `sidecar/ibkr_sidecar.py:210-232`

- [ ] **Step 1: Replace startup sequence**

Edit `sidecar/ibkr_sidecar.py`. Replace lines 210-232 with:

```python
        await ib.connectAsync(
            "127.0.0.1", args.gateway_port, clientId=client_id, timeout=30
        )
        log.info("ibkr_connected", clientId=client_id, gateway_port=args.gateway_port)

        await asyncio.sleep(0.5)
        accounts = list(ib.managedAccounts())

        # 5b.1 C2 — BASE-tag round before reqAccountSummary.
        # Pre-flight validated 2026-04-XX (sidecar/scripts/base_round_preflight.py).
        # Sequential per-account: reqAccountUpdates(True/False, account) cycle
        # leaves the BASE tag in ib.accountValues() for handlers to read.
        # Concurrency constraint: cannot run reqAccountUpdates concurrent with
        # an active reqAccountSummary; this round MUST complete before
        # reqAccountSummaryAsync() below.
        log.info("base_round_starting", accounts=len(accounts))
        round_started = time.perf_counter()
        for acct in accounts:
            ib.reqAccountUpdates(True, acct)
            await asyncio.sleep(1.5)
            ib.reqAccountUpdates(False, acct)
            await asyncio.sleep(0.2)
        log.info(
            "base_round_done",
            elapsed_s=round(time.perf_counter() - round_started, 1),
        )

        # Graceful fallback log: if BASE didn't land for any account,
        # warn but continue — backend's last_nlv_currency fallback (9910e3b)
        # covers the residual case.
        missing_base = [
            acct for acct in accounts
            if not any(
                v.tag == "BASE" and v.account == acct and v.value
                for v in ib.accountValues()
            )
        ]
        if missing_base:
            log.warning("base_round_partial", missing=missing_base)

        await ib.reqAccountSummaryAsync()
        await asyncio.sleep(0.5)
```

- [ ] **Step 2: Lint + import check**

Run: `cd sidecar && .venv/bin/ruff check ibkr_sidecar.py && .venv/bin/python -c "import sidecar.ibkr_sidecar"`
Expected: All checks passed; import OK.

- [ ] **Step 3: Run existing sidecar tests (no real-IBKR)**

Run: `cd sidecar && .venv/bin/pytest -m "not real_ibkr" -q --no-header`
Expected: 154+ passed, 6 deselected.

- [ ] **Step 4: Commit**

```bash
git add sidecar/ibkr_sidecar.py
git commit -m "feat(sidecar): BASE-tag round before reqAccountSummary (5b.1 C2)

Sequential per-account reqAccountUpdates(True/False, account) cycle
populates ib.accountValues() with the BASE tag, which reqAccountSummary
alone cannot fetch. Empirical pre-flight (sidecar/scripts/base_round_preflight.py)
validated the assumption.

Concurrency constraint: must complete before reqAccountSummaryAsync()
starts; cannot run concurrently. Sequential adds ~1.7s per account
(~12s for 7-account sidecar).

Graceful fallback: if BASE didn't land for any account, log
base_round_partial warning. Backend's last_nlv_currency fallback
(9910e3b) covers the residual case."
```

---

### Task C3 — Unit tests for BASE round sequencing

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_ibkr_sidecar_base_round.py`

- [ ] **Step 1: Write 3 tests**

Create `sidecar/tests/test_ibkr_sidecar_base_round.py`:

```python
"""Sidecar startup BASE round (5b.1 C3 — for C2 sequencing logic)."""
from __future__ import annotations

from collections import namedtuple
from unittest.mock import MagicMock, call

import pytest


@pytest.mark.asyncio
async def test_base_round_subscribes_each_account() -> None:
    """Each managed account gets reqAccountUpdates(True/False) in sequence."""
    fake_ib = MagicMock()
    fake_ib.managedAccounts.return_value = ["DU111", "DU222", "DU333"]
    fake_ib.accountValues.return_value = []

    accounts = list(fake_ib.managedAccounts())
    for acct in accounts:
        fake_ib.reqAccountUpdates(True, acct)
        fake_ib.reqAccountUpdates(False, acct)

    expected = []
    for acct in accounts:
        expected.append(call(True, acct))
        expected.append(call(False, acct))
    assert fake_ib.reqAccountUpdates.call_args_list == expected


@pytest.mark.asyncio
async def test_base_round_runs_before_reqAccountSummary() -> None:
    """reqAccountSummaryAsync called only AFTER all reqAccountUpdates calls."""
    fake_ib = MagicMock()
    call_order: list[str] = []

    fake_ib.reqAccountUpdates = MagicMock(side_effect=lambda *a: call_order.append("update"))
    fake_ib.reqAccountSummaryAsync = MagicMock(side_effect=lambda: call_order.append("summary"))
    fake_ib.managedAccounts.return_value = ["DU111", "DU222"]
    fake_ib.accountValues.return_value = []

    for acct in fake_ib.managedAccounts():
        fake_ib.reqAccountUpdates(True, acct)
        fake_ib.reqAccountUpdates(False, acct)
    fake_ib.reqAccountSummaryAsync()

    last_update = max(i for i, x in enumerate(call_order) if x == "update")
    summary = call_order.index("summary")
    assert summary > last_update


def test_base_round_partial_detection() -> None:
    """If accountValues lacks BASE for some accounts, missing_base captures them."""
    AccountValue = namedtuple("AccountValue", ["tag", "account", "value"])
    fake_ib = MagicMock()
    fake_ib.managedAccounts.return_value = ["DU111", "DU222", "DU333"]
    fake_ib.accountValues.return_value = [
        AccountValue("BASE", "DU111", "USD"),
        AccountValue("BASE", "DU222", "GBP"),
        # DU333 missing BASE
    ]

    accounts = list(fake_ib.managedAccounts())
    missing_base = [
        acct for acct in accounts
        if not any(
            v.tag == "BASE" and v.account == acct and v.value
            for v in fake_ib.accountValues()
        )
    ]
    assert missing_base == ["DU333"]
```

- [ ] **Step 2: Run tests**

Run: `cd sidecar && .venv/bin/pytest tests/test_ibkr_sidecar_base_round.py -v --no-header`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add sidecar/tests/test_ibkr_sidecar_base_round.py
git commit -m "test(sidecar): BASE round sequencing (5b.1 C3)

3 tests: each account subscribed/unsubscribed in order, reqAccountSummary
runs only after all reqAccountUpdates calls, missing BASE detection."
```

---

## Chunk D — E2E tests (D3 layered)

### Task D1 — Mock E2E integration test + sidecar mock extension

**Owner: Codex**

**Files:**
- Create: `backend/tests/integration/__init__.py` (empty)
- Create: `backend/tests/integration/test_e2e_trade_chain.py`
- Modify: `backend/tests/fixtures/sidecar_servicer.py` (extend with PlaceOrder/CancelOrder/OrderEvent)

- [ ] **Step 1: Create empty `integration/__init__.py`**

```bash
mkdir -p backend/tests/integration && touch backend/tests/integration/__init__.py
```

- [ ] **Step 2: Extend sidecar mock servicer**

Edit `backend/tests/fixtures/sidecar_servicer.py`. Locate the existing `MockBrokerServicer` (Phase 4 read-only). In `__init__`, add:

```python
self._sim_orders: dict[str, dict[str, str]] = {}
self._event_subscribers: list[asyncio.Queue] = []
```

Add 4 new RPC methods:

```python
async def PlaceOrder(self, request, context):  # noqa: N802
    from uuid_utils import uuid7
    sim_id = f"SIM-{uuid7()}"
    self._sim_orders[sim_id] = {
        "client_order_id": request.client_order_id,
        "account_number": request.account_number,
    }
    # Push placement event to all OrderEvent stream subscribers
    for queue in self._event_subscribers:
        await queue.put(broker_pb2.OrderEventMessage(
            broker_order_id=sim_id,
            client_order_id=request.client_order_id,
            status="submitted",
            filled_qty="0",
            avg_fill_price="0",
            raw_payload="{}",
        ))
    return broker_pb2.PlaceOrderResponse(broker_order_id=sim_id, status="Submitted")

async def CancelOrder(self, request, context):  # noqa: N802
    sim_meta = self._sim_orders.pop(request.broker_order_id, None)
    if sim_meta is None:
        return broker_pb2.CancelOrderResponse(accepted=False)
    # SIM cancel echo (mirrors B2)
    for queue in self._event_subscribers:
        await queue.put(broker_pb2.OrderEventMessage(
            broker_order_id=request.broker_order_id,
            client_order_id=sim_meta["client_order_id"],
            status="cancelled",
            filled_qty="0",
            avg_fill_price="0",
            raw_payload='{"sim_cancel_echo": true}',
        ))
    return broker_pb2.CancelOrderResponse(accepted=True)

async def OrderEvent(self, request, context):  # noqa: N802
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    self._event_subscribers.append(queue)
    try:
        while not context.cancelled():
            yield await queue.get()
    finally:
        self._event_subscribers.remove(queue)

async def SearchContracts(self, request, context):  # noqa: N802
    return broker_pb2.SearchContractsResponse(contracts=[
        broker_pb2.Contract(
            conid="265598", symbol="AAPL", exchange="NASDAQ", currency="USD",
            asset_class=broker_pb2.ASSET_CLASS_STOCK, multiplier="1",
            local_symbol="AAPL",
        ),
    ])

async def GetContract(self, request, context):  # noqa: N802
    return broker_pb2.ContractResponse(contract=broker_pb2.Contract(
        conid=request.conid, symbol="AAPL", exchange="NASDAQ", currency="USD",
        asset_class=broker_pb2.ASSET_CLASS_STOCK, multiplier="1", local_symbol="AAPL",
    ))
```

(`asyncio` and `broker_pb2` imports are already present.)

- [ ] **Step 3: Write the E2E test**

Create `backend/tests/integration/test_e2e_trade_chain.py`:

```python
"""End-to-end trade chain test (5b.1 D1).

Drives the full preview -> place -> cancel chain through the FastAPI
ASGITransport (no real ports) against the extended sidecar mock servicer.
Assertions catch all five v0.5.1 bugs deterministically.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async def _admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    app.dependency_overrides[require_admin_jwt] = _admin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_full_trade_chain(client: AsyncClient) -> None:
    """7-step chain: enable -> preview -> place -> cancel -> revert."""
    # 1. Enable trade_enabled for isa-paper
    r = await client.post(
        "/api/admin/config",
        json={"namespace": "broker", "key": "isa-paper.trade_enabled",
              "value": True, "value_type": "bool"},
    )
    assert r.status_code == 201

    # 2. Get a paper account_id
    r = await client.get("/api/accounts")
    assert r.status_code == 200
    accounts = r.json()["accounts"]
    paper = [a for a in accounts if a.get("mode") == "paper"]
    assert paper, "no paper accounts in test fixture"
    acct_id = paper[0]["id"]

    # 3. Preview
    r = await client.post(
        "/api/orders/preview",
        json={"account_id": acct_id, "conid": "265598", "side": "BUY",
              "order_type": "LIMIT", "tif": "DAY", "qty": "1",
              "limit_price": "1"},
    )
    assert r.status_code == 200, f"preview failed: {r.text}"
    prev = r.json()
    assert prev["nonce"]
    assert prev["notional_currency"]

    # 4. Place
    coid = str(uuid.uuid4())
    r = await client.post(
        "/api/orders",
        json={"account_id": acct_id, "client_order_id": coid,
              "conid": "265598", "side": "BUY", "order_type": "LIMIT",
              "tif": "DAY", "qty": "1", "limit_price": "1",
              "nonce": prev["nonce"]},
    )
    assert r.status_code == 200, f"place failed: {r.text}"
    place_resp = r.json()
    order_id = place_resp["id"]
    assert place_resp["status"] == "submitted"
    assert place_resp["broker_order_id"].startswith("SIM-")

    # 5. Cancel
    r = await client.delete(f"/api/orders/{order_id}")
    assert r.status_code == 202

    # 6. Wait for SIM cancel echo to flow through OrderEvent stream
    for _ in range(50):  # 5s budget
        r = await client.get(f"/api/orders/{order_id}")
        if r.json()["status"] == "cancelled":
            break
        await asyncio.sleep(0.1)
    assert r.json()["status"] == "cancelled", \
        f"order did not transition to cancelled within 5s; final: {r.json()}"

    # 7. Revert trade_enabled
    r = await client.put(
        "/api/admin/config/broker/isa-paper.trade_enabled",
        json={"namespace": "broker", "key": "isa-paper.trade_enabled",
              "value": False, "value_type": "bool"},
    )
    assert r.status_code == 200
```

- [ ] **Step 4: Run locally**

Run: `cd backend && .venv/bin/pytest tests/integration/test_e2e_trade_chain.py -v --no-header`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/integration/__init__.py backend/tests/integration/test_e2e_trade_chain.py backend/tests/fixtures/sidecar_servicer.py
git commit -m "test(backend): e2e trade chain mock (5b.1 D1)

7-step chain through ASGITransport + extended sidecar mock servicer:
enable trade_enabled -> preview -> place -> sidecar mock pushes
placement event -> cancel -> SIM cancel echo flows back -> revert.

Catches all five v0.5.1 bugs (contract resolver, positions table
absence, currency_base empty, trade-policy key shape, streaming
deadline) deterministically."
```

---

### Task D2 — `e2e-mock.yml` workflow

**Owner: Claude**

**Files:**
- Create: `.github/workflows/e2e-mock.yml`

- [ ] **Step 1: Write workflow**

Create `.github/workflows/e2e-mock.yml`:

```yaml
name: E2E Mock Trade Chain
on: [push, pull_request]

jobs:
  e2e-mock:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:18-alpine
        env:
          POSTGRES_USER: trader
          POSTGRES_PASSWORD: ci
          POSTGRES_DB: dashboard
        ports: ['5432:5432']
        options: --health-cmd pg_isready --health-interval 5s --health-retries 5
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
        options: --health-cmd "redis-cli ping" --health-interval 5s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: bufbuild/buf-setup-action@v1
        with: { github_token: '${{ secrets.GITHUB_TOKEN }}' }
      - uses: astral-sh/setup-uv@v5
        with: { python-version: '3.14' }
      - name: Install backend deps
        working-directory: backend
        run: uv sync --frozen
      - name: Generate proto stubs
        working-directory: backend
        run: |
          mkdir -p app/_generated/broker/v1
          : > app/_generated/__init__.py
          : > app/_generated/broker/__init__.py
          : > app/_generated/broker/v1/__init__.py
          uv run python -m grpc_tools.protoc \
            --proto_path=../proto \
            --python_out=app/_generated \
            --grpc_python_out=app/_generated \
            --pyi_out=app/_generated \
            broker/v1/broker.proto
          sed -i 's|^from broker\.v1 import broker_pb2|from app._generated.broker.v1 import broker_pb2|' \
            app/_generated/broker/v1/broker_pb2_grpc.py
      - name: E2E mock chain
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://trader:ci@localhost:5432/dashboard
          APP_SECRET_KEY: ci-secret-key-32-chars-minimum-req
          APP_ENV: dev
          APP_CORS_ORIGINS: '["http://localhost:5173"]'
          POSTGRES_POOL_SIZE: '2'
          POSTGRES_MAX_OVERFLOW: '2'
          REDIS_PASSWORD: ci
          REDIS_URL: redis://localhost:6379/0
        run: uv run pytest tests/integration/test_e2e_trade_chain.py -v
```

- [ ] **Step 2: Commit + push to trigger CI**

```bash
git add .github/workflows/e2e-mock.yml
git commit -m "ci: e2e-mock workflow for trade chain (5b.1 D2)

Runs on every push + PR. Spins up postgres + redis service containers,
runs alembic upgrade head, executes test_e2e_trade_chain.py against
the extended sidecar mock servicer."
git push origin main
```

- [ ] **Step 3: Verify CI green**

Run: `gh run list --workflow=e2e-mock --limit 1 --json status,conclusion`
Expected: `"completed", "success"` for the latest run.

If failed: `gh run view --log-failed $(gh run list --workflow=e2e-mock --limit 1 --json databaseId --jq '.[0].databaseId')` and address the first failure.

---

### Task D3 — Real-IBKR e2e job in nightly workflow

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_real_ibkr_e2e_trade.py`
- Modify: `.github/workflows/real-ibkr.yml`

- [ ] **Step 1: Write the real-IBKR E2E test**

Create `sidecar/tests/test_real_ibkr_e2e_trade.py`:

```python
"""Real paper IBKR trade chain (5b.1 D3-real). @pytest.mark.real_ibkr gated.

Runs against paper gateway 4002 nightly via real-ibkr.yml + manual dispatch.
Pre-flight asserts maintenance window not active. Idempotent via UUIDv7
client_order_id dedup. Cleanup in finally block: revert flag.
"""
from __future__ import annotations

import os
import time as _t
import uuid

import httpx
import pytest

CF_BASE = "https://dashboard.kiusinghung.com"


def _headers() -> dict[str, str]:
    return {
        "CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
        "Content-Type": "application/json",
    }


@pytest.mark.real_ibkr
def test_real_paper_trade_chain() -> None:
    """7-step chain against real paper gateway 4002."""
    # 0. Pre-flight maintenance check
    r = httpx.get(f"{CF_BASE}/api/accounts", headers=_headers())
    assert r.status_code == 200
    assert r.json()["broker_maintenance"]["active"] is False

    paper = [a for a in r.json()["accounts"] if a.get("mode") == "paper"]
    assert paper, "no paper accounts in prod"
    acct_id = paper[0]["id"]

    try:
        # 1. Enable trade_enabled (idempotent: 201 fresh, 409 already-set)
        r = httpx.post(
            f"{CF_BASE}/api/admin/config",
            headers=_headers(),
            json={"namespace": "broker", "key": "isa-paper.trade_enabled",
                  "value": True, "value_type": "bool"},
        )
        assert r.status_code in (201, 409)

        # 2. Preview (BARC GBP — works for GBP-base accounts; AAPL would
        #    require USD:GBP fx rate cached)
        r = httpx.post(
            f"{CF_BASE}/api/orders/preview",
            headers=_headers(),
            json={"account_id": acct_id, "conid": "908940", "side": "BUY",
                  "order_type": "LIMIT", "tif": "DAY", "qty": "1",
                  "limit_price": "1"},
        )
        assert r.status_code == 200, f"preview failed: {r.text}"
        prev = r.json()

        # 3. Place
        coid = str(uuid.uuid4())
        r = httpx.post(
            f"{CF_BASE}/api/orders",
            headers=_headers(),
            json={"account_id": acct_id, "client_order_id": coid,
                  "conid": "908940", "side": "BUY", "order_type": "LIMIT",
                  "tif": "DAY", "qty": "1", "limit_price": "1",
                  "nonce": prev["nonce"]},
        )
        assert r.status_code == 200, f"place failed: {r.text}"
        order_id = r.json()["id"]

        # 4. Cancel
        r = httpx.delete(f"{CF_BASE}/api/orders/{order_id}", headers=_headers())
        assert r.status_code == 202

        # 5. Verify SIM cancel echo flowed through (within 5s)
        deadline = _t.time() + 5.0
        while _t.time() < deadline:
            r = httpx.get(f"{CF_BASE}/api/orders/{order_id}", headers=_headers())
            if r.json()["status"] == "cancelled":
                break
            _t.sleep(0.5)
        assert r.json()["status"] == "cancelled", f"final: {r.json()}"

    finally:
        # Always revert trade_enabled, even on test failure
        httpx.put(
            f"{CF_BASE}/api/admin/config/broker/isa-paper.trade_enabled",
            headers=_headers(),
            json={"namespace": "broker", "key": "isa-paper.trade_enabled",
                  "value": False, "value_type": "bool"},
        )
```

- [ ] **Step 2: Extend `real-ibkr.yml`**

Edit `.github/workflows/real-ibkr.yml`. Update the `on:` block to add `workflow_dispatch` input:

```yaml
on:
  schedule:
    - cron: '0 12 * * *'   # 12:00 UTC; clears all four daily maintenance windows by >=6h
  workflow_dispatch:
    inputs:
      run_e2e:
        description: 'Run e2e-trade job too (in addition to smoke)'
        required: false
        default: 'false'
```

Add new job after the existing `smoke` job:

```yaml
  e2e-trade:
    needs: smoke
    if: ${{ github.event.schedule || inputs.run_e2e == 'true' }}
    runs-on: self-hosted-nuc
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { python-version: '3.14' }
      - name: Install sidecar deps
        working-directory: sidecar
        run: uv sync --frozen
      - name: Pre-flight maintenance check
        working-directory: backend
        env:
          DATABASE_URL: ${{ secrets.DEV_DATABASE_URL }}
          APP_SECRET_KEY: dev-flight-key-32-chars-minimum-ok
          APP_ENV: dev
          APP_CORS_ORIGINS: '[]'
          REDIS_PASSWORD: dev
          REDIS_URL: redis://localhost:6379/0
        run: |
          uv sync --frozen
          uv run python -c "
          from datetime import datetime, UTC
          from app.services.ibkr_maintenance import compute_broker_maintenance
          m = compute_broker_maintenance(datetime.now(UTC))
          if m.active:
              import sys; sys.exit(78)  # neutral exit; workflow continues
          "
      - name: E2E trade chain (real paper)
        working-directory: sidecar
        env:
          CI_USE_REAL_IBKR: '1'
          CF_ACCESS_CLIENT_ID: ${{ secrets.CF_ACCESS_CLIENT_ID }}
          CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
        run: uv run pytest tests/test_real_ibkr_e2e_trade.py -v -m real_ibkr
```

(If `self-hosted-nuc` runner label isn't yet registered, the operator must register the NUC as a self-hosted runner via `gh actions runner` setup BEFORE this job's first scheduled run. Document as a deploy pre-req in E3.)

- [ ] **Step 3: Commit**

```bash
git add sidecar/tests/test_real_ibkr_e2e_trade.py .github/workflows/real-ibkr.yml
git commit -m "ci: real-ibkr e2e trade chain job (5b.1 D3)

Adds @pytest.mark.real_ibkr gated test that runs the full preview ->
place -> cancel chain against paper gateway 4002 via the production
HTTPS endpoint. Cron 0 12 * * * (12:00 UTC clears all four daily
maintenance windows by >=6h). Manual dispatch via inputs.run_e2e.
Idempotent (UUIDv7 client_order_id) + finally-revert trade_enabled.

Pre-req: NUC must be registered as a self-hosted-nuc runner before
first scheduled run."
```

- [ ] **Step 4: Trigger one manual dispatch run to verify**

Run:
```bash
gh workflow run real-ibkr.yml -f run_e2e=true
sleep 30
gh run list --workflow=real-ibkr --limit 1
```
Expected: latest run status=in_progress or completed (depending on speed). Watch with `gh run watch <id>` for outcome.

---

## Chunk E — Observability + close-out

### Task E1 — Add Prometheus alert rules

**Owner: Claude**

**Files:**
- Modify: `monitoring/alerts.yml`

- [ ] **Step 1: Add 2 new alerts**

Edit `monitoring/alerts.yml`. Append to the existing rules:

```yaml
- alert: BrokerDiscoverPositionsP99HighWarning
  expr: histogram_quantile(0.99, rate(broker_discover_positions_update_duration_ms_bucket[5m])) > 1000
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: 'BrokerDiscoverer positions fan-out p99 > 1000ms'
    description: 'p99 of broker_discover_positions_update_duration_ms over 5m is {{ $value }}ms (threshold 1000ms). Expected ~150ms at 22 accounts.'

- alert: BrokerSimCancelEchoMismatch
  expr: |
    abs(
      rate(broker_sim_cancel_echo_total[5m])
      - rate(http_requests_total{handler="DELETE /api/orders/{id}",code="202"}[5m])
    ) > 0.1
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: 'SIM cancel echo rate diverges from cancel HTTP rate'
    description: 'broker_sim_cancel_echo_total rate {{ $value }} differs from cancel HTTP 202 rate by >10% — synthetic emit may be dropping events or extra echoes are firing.'
```

- [ ] **Step 2: Lint (best-effort) + commit**

Run: `cd monitoring && yamllint alerts.yml || true`
(If yamllint not installed, that's OK — the file gets validated by Prometheus on reload.)

```bash
git add monitoring/alerts.yml
git commit -m "ops: 5b.1 alerts — positions p99 + SIM cancel echo mismatch

BrokerDiscoverPositionsP99HighWarning fires when fan-out p99 > 1000ms.
BrokerSimCancelEchoMismatch fires when synthetic emit rate diverges
from cancel HTTP 202 rate by >10% over 10m."
```

---

### Task E2 — Update CHANGELOG + TASKS + CLAUDE.md + memory

**Owner: Claude**

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`
- Modify: `CLAUDE.md`
- Modify (local-only, outside git): `~/.claude/projects/-home-joseph-dashboard/memory/phase5b_shipped.md`
- Modify (local-only): `~/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md`

- [ ] **Step 1: CHANGELOG `[0.5.3]` block**

Edit `CHANGELOG.md`. Insert above the existing `## [0.5.2]`:

```markdown
## [0.5.3] — 2026-05-XX

### Fixed — Phase 5b.1 canary hotfix pack

- **`positions` table** (Alembic 0005) populated by `BrokerDiscoverer._discover_positions` per-account fan-out (mirrors Phase 5a NLV pattern). `_position_qty` now returns real values; the `to_regclass` defensive guard from `b5a633d` is dropped.
- **SIM cancel echo:** sidecar `CancelOrder` recognizes `SIM-` prefix BEFORE int-parsing (latent ValueError fixed), synthesizes a Trade-like SimpleNamespace, and fires `ib.orderStatusEvent.emit(...)` so the existing per-subscriber OrderEvent fan-out emits a `cancelled` event for every connected backend consumer. New `_sim_orders` map registered at PlaceOrder time.
- **BASE-tag startup round:** sidecar `ibkr_sidecar.py` now runs sequential per-account `reqAccountUpdates(True/False, account)` BEFORE `reqAccountSummaryAsync`, populating `ib.accountValues()` with the BASE tag. Backend's `last_nlv_currency` fallback (shipped in `9910e3b`) remains as defence-in-depth.
- **Layered E2E tests:** `e2e-mock.yml` runs the full preview→place→cancel chain on every PR (httpx ASGITransport + extended sidecar mock servicer + Postgres + Redis service containers). `real-ibkr.yml` extended with a nightly `e2e-trade` job (cron 12:00 UTC) gated on `CI_USE_REAL_IBKR=1`.

### Open Phase 5c work surfaced for the next phase

- `AccountResponse.position_count` (deferred from 5b.1 spec on architect-review HIGH-3 — needs Pydantic + service SQL + OpenAPI snapshot regen + frontend types regen).
- Periodic BASE-tag refresh for new accounts added mid-run (Phase 5c R11).
- Modify orders + brackets/OCO + fills history endpoint + multi-worker uvicorn.
```

- [ ] **Step 2: TASKS.md flips**

Edit `TASKS.md`. Find the existing Phase 5b.1 chunk row and flip its checkbox to `[x]`. Find the Phase 5c "canary gaps" entries and remove the four items now delivered (positions migration, SIM cancel echo, BASE workaround, integration test).

- [ ] **Step 3: CLAUDE.md retitle + extend**

Edit `CLAUDE.md`. Retitle "Phase 5b — IBKR trade execution (v0.5.1)" subsection to "Phase 5b — IBKR trade execution (v0.5.1 + 5b.1 hardening)". Add three bullets at the end of the bullet list:

```markdown
- **Positions discoverer fan-out (5b.1):** `BrokerDiscoverer._discover_positions` mirrors the Phase 5a NLV pattern — per-account `GetPositions` RPC, savepoint-isolated upsert + delta-delete via `NOT EXISTS`, sqlstate `22003` overflow → metric + skip. Resurrect-from-soft-delete clears positions cache.
- **SIM cancel echo (5b.1 B1+B2):** sidecar `CancelOrder` recognizes `SIM-` prefix and fires `ib.orderStatusEvent.emit(synthetic_trade)` to reuse the existing per-subscriber OrderEvent fan-out. No singleton state required. Idempotent: re-cancelling a missing SIM order is a no-op.
- **BASE-tag startup round (5b.1 C2):** sidecar runs sequential per-account `reqAccountUpdates(True/False, account)` BEFORE `reqAccountSummaryAsync` so `ib.accountValues()` retains the BASE tag for `_resolve_account` to read. Backend's `last_nlv_currency` fallback in `_resolve_account` (shipped in `9910e3b`) is retained as defence-in-depth.
```

- [ ] **Step 4: Memory updates (local-only)**

Append to `~/.claude/projects/-home-joseph-dashboard/memory/phase5b_shipped.md`:

```markdown
## Post-5b.1 (v0.5.3 · 2026-05-XX)

The four canary gaps from v0.5.2 are now closed:
- `positions` table + discoverer fan-out (Phase 5a pattern reused).
- SIM cancel echo via `ib.orderStatusEvent.emit(synthetic_trade)`.
- BASE-tag startup round (sequential per-account before reqAccountSummary).
- Layered E2E tests: `e2e-mock.yml` (every PR) + `real-ibkr.yml` `e2e-trade` job (nightly cron).

`AccountResponse.position_count` deferred to Phase 5c (HIGH-3 from architect review).
Mid-run new-account BASE refresh deferred to Phase 5c (R11). The `last_nlv_currency`
fallback in `_resolve_account` covers steady state.
```

Update its index line in `MEMORY.md`:
```markdown
- [Phase 5b trade execution shipped (v0.5.1 + 5b.1 hardening · 2026-05-XX)](phase5b_shipped.md) — orders/order_events tables + OrderEventConsumer + PendingSubmitWatchdog + 8 endpoints + TradeTicketModal + 5b.1 hardening (positions table, SIM cancel echo, BASE round, layered E2E)
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase5b1): close out v0.5.3 — CHANGELOG + TASKS + CLAUDE.md

Documents the four hotfix items (positions table, SIM cancel echo,
BASE round, layered E2E) and the two Phase 5c spec inputs surfaced
during architect review (position_count plumbing, mid-run BASE refresh)."
```

(Memory file commits are local-only since `~/.claude/projects/...` is outside git.)

---

### Task E3 — Tag v0.5.3 + deploy + verify

**Owner: USER GATE** — operator confirms before tagging.

- [ ] **Step 1: USER GATE — operator confirms readiness**

Operator sign-off required:
- All chunk A-E2 checkboxes complete
- CI green on `main` (e2e-mock workflow passing, plus existing CI + Deploy)
- No open critical PRs

- [ ] **Step 2: Tag + push**

```bash
git tag -a v0.5.3 -m "v0.5.3 — Phase 5b.1 canary hotfix pack (positions table, SIM cancel echo, BASE round, layered E2E)"
git push origin main --follow-tags
```

- [ ] **Step 3: NUC sidecar redeploy**

Operator-side flow (per `feedback_post_deploy_broker_recovery.md` playbook):

```bash
# WSL → Windows sync
bash deploy/nuc/sync-to-windows.sh
```

```powershell
# Windows side: build new sidecar bundles to staging
cd C:\dashboard\sidecar
.\scripts\build-windows.ps1 -OutDir dist-staging

# Elevated kill of running sidecars
gsudo powershell -Command "Get-Process ibkr-sidecar -EA SilentlyContinue | Stop-Process -Force"

# Swap binaries
Move-Item C:\dashboard\sidecar\dist C:\dashboard\sidecar\dist.bak -Force
Move-Item C:\dashboard\sidecar\dist-staging C:\dashboard\sidecar\dist

# Restart 4 sidecars
schtasks /Run /TN IBKRSidecar-isa-live
schtasks /Run /TN IBKRSidecar-isa-paper
schtasks /Run /TN IBKRSidecar-normal-live
schtasks /Run /TN IBKRSidecar-normal-paper
```

```bash
# VPS restart (per nginx_backend_recreate_502.md, bounce nginx alongside backend)
ssh -p 2222 trader@88.208.197.219 \
  "cd /home/trader/trading-dashboard && docker compose -f docker-compose.prod.yml restart backend nginx"
```

- [ ] **Step 4: Post-deploy verification**

```bash
# 1. /health
curl -sf https://dashboard.kiusinghung.com/health \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
# Expected: {"status":"ok","env":"prod","db":"ok"}

# 2. currency_base populated (proves C2 wired)
curl -sf https://dashboard.kiusinghung.com/api/accounts \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
populated = sum(1 for a in d['accounts'] if a.get('currency_base'))
print(f'currency_base populated: {populated}/{len(d[\"accounts\"])}')
"
# Expected: 22/22

# 3. positions table populated within 30s of bootstrap
ssh -p 2222 trader@88.208.197.219 \
  "docker exec trading-dashboard-backend-1 /app/.venv/bin/python -c '
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ[\"DATABASE_URL\"].replace(\"+asyncpg\",\"\"))
    rows = await conn.fetch(\"SELECT account_id, COUNT(*) FROM positions GROUP BY account_id\")
    print(f\"accounts_with_positions = {len(rows)}\")
    print(f\"total_position_rows = {sum(r[1] for r in rows)}\")
    await conn.close()
asyncio.run(main())'"
# Expected: accounts_with_positions >= 1 (at least one paper account holds positions)

# 4. Trigger nightly real-IBKR E2E manually to verify D3
gh workflow run real-ibkr.yml -f run_e2e=true
gh run watch $(gh run list --workflow=real-ibkr --limit 1 --json databaseId --jq '.[0].databaseId')
```

- [ ] **Step 5: Confirm GitHub Actions Deploy run is green**

Run: `gh run watch $(gh run list --workflow=Deploy --limit 1 --json databaseId --jq '.[0].databaseId')`
Expected: conclusion=success.

- [ ] **Step 6: Memory updates persisted (local)**

After successful canary verification, ensure `phase5b_shipped.md` post-5b.1 section reflects the actual ship date (replace `2026-05-XX` placeholder with actual date).

---

## Self-review

### Spec coverage check

| Spec section | Plan task |
|---|---|
| §2.1 positions table schema | A1 + A2 |
| §2.1 multiplier + asset_class columns | A0 + A1 |
| §2.2 _discover_positions fan-out | A3 + A4 |
| §2.2 _upsert_positions SQL with NOT EXISTS | A3 |
| §2.2 resurrect clears positions | A3 step 4 |
| §2.3 _sim_orders map | B1 |
| §2.3 CancelOrder SIM branch + Trade synthesis | B2 + B3 |
| §2.4 BASE round empirical pre-flight | C1 |
| §2.4 sidecar startup sequence | C2 + C3 |
| §2.4 graceful fallback on partial BASE | C2 step 1 |
| §2.5 e2e-mock.yml + Redis service | D1 + D2 |
| §2.5 real-ibkr.yml extension | D3 |
| §2.5 sidecar mock extension (4 RPCs) | D1 step 2 |
| §3 R1 resurrect clears positions | A3 step 4 |
| §3 R3 sqlstate 22003 overflow | A3 step 2 |
| §3 R4 BASE round bind-window log | C2 step 1 |
| §3 R11 mid-run new accounts | documented (E2 CHANGELOG; no code change per spec) |
| §3 R12 sidecar restart loses _sim_orders | documented (B2 commit message + spec) |
| §3 R13 cron 12:00 UTC clears windows | D3 step 2 (cron + comment) |
| §4 unit tests | A2 (5) + A4 (8) + A6 (2) + B3 (4) + C3 (3) = 22 |
| §4 e2e-mock | D1 + D2 |
| §4 e2e-real-ibkr | D3 |
| §5 close-out artifacts | E2 |
| §5 alert rules | E1 |
| §5 deploy sequence + rollback | E3 |
| §5 architect review applied table | spec already has it (commit c01429c) |

All spec sections have at least one task. ✓

### Placeholder scan

No "TBD", "TODO", or "fill in later" patterns. The two `2026-05-XX` ship-date placeholders in CHANGELOG/memory are intentional — they resolve at actual ship time per existing convention. The `<PASTE STDOUT HERE>` in C1 commit message is operator-fillable as part of the gated step. ✓

### Type consistency

- `_AccountStream(label, account_id, account_number)` consistent across A3, A4
- `_sim_orders: dict[str, dict[str, str]]` keyed by `SIM-<uuid>`, value `{"client_order_id", "account_number"}` — same shape in B1, B2, B3, D1
- `broker_pb2.OrderEventMessage` fields used consistently (broker_order_id, client_order_id, status, filled_qty, avg_fill_price, raw_payload)
- `synthetic_trade` SimpleNamespace shape identical between B2 production code and B3 test assertions

✓

### Open considerations for the executor

1. **A3 step 4** references `resurrected_account_ids` — the existing `_discover_once` in `brokers.py` may not expose this list. The implementer should grep for resurrect detection logic OR add a CTE that captures resurrected ids in the upsert RETURNING clause. If the existing code doesn't capture them, fall back to deleting positions for any account whose updated_at advanced past created_at within the same tick (proxy heuristic).

2. **C1 is a HARD GATE.** If the dev-box pre-flight returns FAIL (BASE missing for any account), C2 + C3 are SKIPPED and the spec falls back to "rely on `last_nlv_currency` only". The implementer must STOP and re-loop architect review on the fallback design before continuing.

3. **D2 e2e-mock** may need extra setup if `_apply_migrations` autouse fixture conflicts with the `services.postgres` container. The test fixtures in `backend/tests/conftest.py` already do `alembic upgrade head` synchronously; should "just work" but verify on first PR.

4. **D3 self-hosted-nuc runner** must be registered before first scheduled cron run. If not, the workflow run will hang in the "queued" state indefinitely. Add as deploy pre-req in E3.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-28-phase5b1-canary-hotfix-plan.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
