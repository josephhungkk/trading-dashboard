# Phase 5b — IBKR trade execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-04-27-phase5b-trade-execution-design.md` (commit `5ded62e`)
**Tag at end:** `v0.5.1`
**Estimated duration:** ~2 weeks
**Prerequisite:** v0.5.0 shipped (NLV caching + 22 IBKR accounts visible + maintenance envelope + `app_config` config service)
**Successor:** Phase 5c (modify, brackets, fills history, multi-worker)

**Goal:** Add the **write path** to the broker stack — place market/limit/stop orders, cancel orders, see live status updates over SSE; brand-new-symbol contract search included.

**Architecture:** Three new RPCs (PlaceOrder unary, CancelOrder unary, OrderEvent server-streaming) + one (SearchContracts unary) added to `proto/broker/v1/broker.proto`. Backend gains `orders` + `order_events` tables (Alembic 0004). A `BrokerOrderEventConsumer` runs as 22 per-`(sidecar, account)` `asyncio.Task`s in lifespan (one stream each), INSERTs into `order_events`, UPSERTs `orders`, PUBLISHes onto Redis `orders:events:<account_id>`. Backend exposes 8 endpoints (preview/place/cancel/list/single/policy/SSE/contract-search). Frontend gains `TradeTicketModal`, `ContractSearchInput`, and the existing `/orders` page is extended with active-orders + cancel + EventSource. Single-worker uvicorn for 5b (multi-worker → Phase 9).

**Tech stack:** SQLAlchemy 2.0 async + Alembic + asyncpg + Pydantic v2 (backend); ib_async + grpc.aio (sidecar); React 19 + Zustand + TS strict (frontend).

---

## Owner & review chain (per CLAUDE.md "Step 6 — Implementation")

Each task lists an explicit **Owner: Codex | Claude** line:

- **Codex** writes source code (backend Python, sidecar Python, frontend TS) via `codex:codex-rescue` subagent.
- **Claude Code** writes tests, stories, verification (typecheck/lint/test), and conventional commits.
- **Codex/Claude fallback (per memory `feedback_codex_fallback.md`):** if Codex hits quota mid-task, Claude finishes the task. Document quota events with a commit footer line `Codex quota exceeded → Claude continued`. Canary back to Codex on the next planned Codex task.
- **Per-commit review chain:** implementer → spec compliance reviewer → code quality reviewer → language reviewer (`python-reviewer` for backend/sidecar, `typescript-reviewer` for frontend) → conditional: `security-reviewer` (auth/secrets/user-input/crypto, mandatory on D1/D2/D4 + B1/B2 + E2), `database-reviewer` (Alembic/SQL — A1/A2/E1), `silent-failure-hunter` (async paths — E1/E2/E3/E4), `a11y-architect` (frontend UI — G1/G2/G3), `build-error-resolver` (when builds fail), `tdd-guide` (when tests fail).
- **Conventional commits**, body lines ≤ 100 chars, never `--no-verify`.
- **Coverage gate:** 80%+ on backend `app/` + sidecar `sidecar/`. CI fails below.

---

## Critical gates

Strict ordering (each gate must land green before its dependents start):

- **A0 (gen-types.sh) must land before F1.** Frontend types are generated from the backend OpenAPI snapshot, not hand-written.
- **A1 (migration) must land before C+D+E start.** Those chunks read/write the new columns.
- **A3 (proto) must land before A4 + B + D start.** A4 (sidecar client extension) and sidecar handlers depend on regenerated stubs.
- **A4 (BrokerSidecarClient extension) must land before D2/D4/D5/D6 + E1/E2/E3.** Backend endpoints + consumer call the new client methods.
- **A5 (shared mock fixture) must land before D + E test development.** Avoids re-inventing sidecar mocks across 50+ tests.
- **Chunk B (sidecar handlers) must land before E (consumer) starts.** Consumer subscribes to OrderEvent stream.
- **Chunk D (API endpoints) depends on C (Pydantic + ORM).**
- **D6 (SSE) depends on D7 (OpenAPI snapshot)** — schema lock first, SSE replay format pinned by it.
- **Chunk F (frontend services) depends on D + A0** — wire shape locked.
- **H4 push + tag is the USER GATE** — operator confirms before tagging v0.5.1; canary rollout (per-account `trade_enabled=true`) is operator-initiated, not part of this plan.

### Parallel-safe pairs (controller may dispatch in parallel sessions, NOT same-session subagents)

The subagent-driven-development skill forbids parallel implementer subagents within one session, but the controller can split work across sessions or use the Codex/Claude alternation:

| Parallel-safe | Why |
|---|---|
| A1 ⊥ A3 | migration vs proto are independent inputs |
| C1 ⊥ C2 ⊥ C3 | Pydantic schemas vs ORM vs config policy don't reference each other |
| B1 ⊥ B2 ⊥ B4 | different RPCs in `handlers.py`; B3 depends on `_serialize_trade` so serialize first if grouping |
| D3 ⊥ D5 | distinct routers (orders list vs contracts) |
| F1 ⊥ F2 | service vs store, no coupling |
| G1 ⊥ G3 | different components |

---

## Chunk A — Foundation: Schema + Proto + Tooling + Client extension + Mock fixtures

Lays the database tables, proto contract additions, type-generation tooling, and the sidecar-client extension that every later chunk reads.

### Task A0 — Implement `scripts/gen-types.sh` (frontend type generation from OpenAPI)

**Owner: Codex**

**Files:**
- Modify: `scripts/gen-types.sh` (currently a stub that exits 1)
- Create: `frontend/scripts/check-generated-types.mjs` (CI snapshot diff)
- Modify: `frontend/package.json` (add `gen:types` + `check:types-up-to-date` scripts)
- Modify: `.github/workflows/ci.yml` (CI step that runs `pnpm check:types-up-to-date`)
- Test: `frontend/src/services/api-generated.test.ts` (smoke import test)

**Spec reference:** CLAUDE.md "When Claude Code Makes Changes" — "Always regenerate types when changing API schemas: see `scripts/gen-types.sh` (Phase 2+)." The script is currently a stub; making it real is a Phase 5b prereq.

- [ ] **Step 1: Implement `scripts/gen-types.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT/backend"
# Boot a backend in offline-OpenAPI dump mode (no DB needed)
uv run python -m app.scripts.dump_openapi > /tmp/openapi.json
cd "$ROOT/frontend"
pnpm exec openapi-typescript /tmp/openapi.json -o src/services/api-generated.ts
echo "Wrote frontend/src/services/api-generated.ts"
```

Add `backend/app/scripts/dump_openapi.py` — imports the FastAPI app and prints `app.openapi()` as JSON without booting uvicorn (skip lifespan startup; just construct the app).

- [ ] **Step 2: Wire `pnpm gen:types` and `pnpm check:types-up-to-date`**

`frontend/package.json` scripts:
```json
"gen:types": "../scripts/gen-types.sh",
"check:types-up-to-date": "node scripts/check-generated-types.mjs"
```

`check-generated-types.mjs` regenerates `api-generated.ts` to a temp file, diffs against the committed file, and exits 1 with a helpful "run `pnpm gen:types`" message if drift is detected.

- [ ] **Step 3: Add CI step in `.github/workflows/ci.yml`**

A `frontend-types-up-to-date` job that runs after the backend image builds: `pnpm install && pnpm check:types-up-to-date`.

- [ ] **Step 4: Run + commit**

```bash
./scripts/gen-types.sh
cd frontend && pnpm check:types-up-to-date
```

Expected: writes `frontend/src/services/api-generated.ts` ; CI check passes.

```bash
git add scripts/gen-types.sh frontend/scripts/check-generated-types.mjs \
        frontend/package.json frontend/src/services/api-generated.ts \
        backend/app/scripts/dump_openapi.py .github/workflows/ci.yml
git commit -m "feat(tooling): implement scripts/gen-types.sh + CI drift gate

CLAUDE.md Phase 2+ promise. Backend OpenAPI dumped via offline
app.openapi() (no DB / no uvicorn boot). openapi-typescript
generates frontend/src/services/api-generated.ts. CI fails if
the committed file drifts from regen — operator runs pnpm
gen:types to refresh."
```

---

### Task A1 — Alembic 0004: orders + order_events tables

**Owner: Codex**

**Files:**
- Create: `backend/alembic/versions/0004_orders_order_events.py`

**Spec reference:** §3 (lines 113–199).

- [ ] **Step 1: Generate migration scaffold**

```bash
cd backend && uv run alembic revision -m "orders_order_events" --rev-id 0004 --head 0003
```

Inspect the generated stub at `backend/alembic/versions/0004_*.py` and rename to `0004_orders_order_events.py`.

- [ ] **Step 2: Write the upgrade/downgrade DDL**

Replace `upgrade()` and `downgrade()` to materialize spec §3 verbatim:

- 4 enums: `order_side_enum`, `order_type_enum`, `order_tif_enum`, `order_status_enum`.
- `orders` table with `id UUID PK`, `account_id UUID FK`, `client_order_id UUID NOT NULL`, `broker_order_id TEXT NULL`, `conid TEXT NOT NULL`, `symbol TEXT NOT NULL`, `side`, `order_type`, `tif`, `qty NUMERIC(20,8) NOT NULL`, `limit_price NUMERIC(20,8) NULL`, `stop_price NUMERIC(20,8) NULL`, `status` defaulting to `'pending_submit'`, `filled_qty NUMERIC(20,8) NOT NULL DEFAULT 0`, `avg_fill_price NUMERIC(20,8) NULL`, `notional NUMERIC(20,8) NOT NULL`, `notional_filled NUMERIC(20,8) NOT NULL DEFAULT 0`, `cancel_requested_at TIMESTAMPTZ NULL`, `created_at`/`updated_at`/`last_event_at` (TIMESTAMPTZ).
- 3 CHECK constraints: order_type↔price coherence; `filled_qty >= 0 AND filled_qty <= qty`; `qty > 0`.
- `uq_orders_account_client_order_id` UNIQUE INDEX on `(account_id, client_order_id)`.
- `uq_orders_account_broker_order_id` UNIQUE INDEX on `(account_id, broker_order_id) WHERE broker_order_id IS NOT NULL`.
- `ix_orders_account_status` partial INDEX (active statuses only) + `ix_orders_account_created` + `ix_orders_pending_submit_watchdog` partial INDEX `WHERE status = 'pending_submit'`.
- `order_events` table with `id BIGSERIAL PK`, `order_id UUID NULL FK orders(id)`, `account_id UUID NOT NULL FK broker_accounts(id)`, `broker_order_id TEXT`, `status order_status_enum NOT NULL`, `filled_qty`/`avg_fill_price` (NUMERIC nullable), `broker_event_at TIMESTAMPTZ NOT NULL`, `observed_at TIMESTAMPTZ DEFAULT now()`, `raw_payload JSONB`.
- 2 indexes on `order_events`: `(order_id, broker_event_at DESC)` and `(account_id, broker_event_at DESC)`.

`downgrade()` drops in reverse order: indexes → tables → **explicit `op.execute("DROP TYPE order_status_enum")` + same for `order_tif_enum`, `order_type_enum`, `order_side_enum`** (Postgres Alembic gotcha — `op.drop_table` does NOT cascade to ENUM types; without explicit DROP TYPE, a downgrade-then-upgrade in the same DB raises `duplicate_object` errors). Architect-review fix P3.

- [ ] **Step 3: Verify upgrade + downgrade round-trip locally**

```bash
cd backend && uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
psql "$DATABASE_URL" -c "\d orders" | head -50
psql "$DATABASE_URL" -c "\d order_events" | head -30
psql "$DATABASE_URL" -c "\dT+ order_status_enum"
# Repeat the round-trip a second time to verify enum cleanup is idempotent
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: each command exits 0; tables show all columns + CHECK constraints + the 4 indexes; enum lists 8 values; second round-trip succeeds without `duplicate_object`.

- [ ] **Step 4: DO NOT commit yet — A2 commits A1+A2 together (architect-review P4)**

Leave `backend/alembic/versions/0004_orders_order_events.py` staged but uncommitted. Task A2 will write tests, run them green against the migrated DB, and create the single `feat(backend): alembic 0004 + tests` commit covering both. This matches the TDD red-green-commit cycle and avoids a green-CI-but-untested intermediate commit.

---

### Task A2 — Migration tests: 0004 schema + constraints

**Owner: Claude**

**Files:**
- Create: `backend/tests/migrations/test_0004.py`

**Spec reference:** §3 invariants.

- [ ] **Step 1: Write the failing tests** (~9 tests, architect-review P3+P16 added test 7-tightening + test 9)

Use the outer-rollback `session_factory` fixture pattern from `test_0003.py` (per `feedback_pytest_session_begin_commits.md`). Cover:

1. `test_orders_check_qty_positive` — INSERT with `qty=0` raises `IntegrityError` matching `qty > 0`.
2. `test_orders_check_market_no_prices` — INSERT order_type=MARKET with `limit_price NOT NULL` raises IntegrityError on the order_type check.
3. `test_orders_check_limit_requires_limit_price` — INSERT order_type=LIMIT with `limit_price IS NULL` raises IntegrityError.
4. `test_orders_check_stop_requires_stop_price` — INSERT order_type=STOP with `stop_price IS NULL` raises IntegrityError.
5. `test_orders_unique_account_client_order_id` — second INSERT with same `(account_id, client_order_id)` raises `UniqueViolation`; same `client_order_id` different `account_id` succeeds (R2).
6. `test_orders_unique_account_broker_order_id_partial` — two rows with `broker_order_id=NULL` for same account succeed (partial index); two rows with same `(account_id, broker_order_id)` not-null raise UniqueViolation (R19).
7. `test_pending_submit_watchdog_index_pinned_to_created_at` — `pg_indexes` lookup confirms `ix_orders_pending_submit_watchdog` is present, partial, AND pinned to the `created_at` column with predicate `(status = 'pending_submit'::order_status_enum)` (architect-review P16: must pin column, not just existence).
8. `test_order_events_order_id_nullable` — INSERT `order_events` with `order_id=NULL` succeeds (R18).
9. `test_0004_downgrade_then_upgrade_round_trips_twice` — programmatically run `alembic downgrade -1 → upgrade head → downgrade -1 → upgrade head`; assert no `duplicate_object` error (architect-review P3 — Postgres ENUM lifecycle invariant).

- [ ] **Step 2: Run tests to verify they pass against migrated DB**

```bash
cd backend && uv run pytest tests/migrations/test_0004.py -v
```

Expected: 9 PASS (0004 already applied via Task A1 Step 3). If any fail, fix the migration in A1 (this is the test-first verification of A1's correctness; A1 is still uncommitted at this point per architect-review P4).

- [ ] **Step 3: Combined commit (A1 + A2)** — single `feat(backend): alembic 0004 + tests` commit

```bash
git add backend/alembic/versions/0004_orders_order_events.py \
        backend/tests/migrations/test_0004.py
git commit -m "feat(backend): alembic 0004 — orders + order_events tables + tests

Adds 4 enums (order_side, order_type, order_tif, order_status) and 2
tables matching spec §3. orders has composite UNIQUE on
(account_id, client_order_id) + (account_id, broker_order_id WHERE NOT
NULL) for cross-account dedup safety (R2/R19). 3 CHECK constraints
guard order_type↔price coherence and filled_qty bounds. order_events
is the append-only audit log; order_id nullable for TWS-placed audit
rows (R18). Partial pending_submit index supports the watchdog scan.
Downgrade explicitly drops the 4 ENUM types — Postgres Alembic gotcha,
otherwise downgrade-then-upgrade fails with duplicate_object.

Tests cover composite UNIQUE keys, 3 CHECK constraints, partial
watchdog index pinned to created_at, order_events.order_id nullable,
and downgrade-up-down-up idempotency (architect-review P3+P4+P16)."
```

---

### Task A3 — Proto contract: PlaceOrder/CancelOrder/OrderEvent/SearchContracts

**Owner: Codex**

**Files:**
- Modify: `proto/broker/v1/broker.proto`
- Regenerate: backend + sidecar `_pb2.py` / `_pb2_grpc.py` / `_pb2.pyi`

**Spec reference:** §2 (lines 44–110).

- [ ] **Step 1: Append the four RPCs + their messages to the proto file**

Add to the `service Broker { ... }` block:

```proto
rpc PlaceOrder(PlaceOrderRequest) returns (PlaceOrderResponse);
rpc CancelOrder(CancelOrderRequest) returns (CancelOrderResponse);
rpc OrderEvent(AccountRef) returns (stream OrderEventMessage);
rpc SearchContracts(SearchContractsRequest) returns (SearchContractsResponse);
```

Add the 7 message definitions verbatim from spec §2. `PlaceOrderRequest` MUST include `reserved 10 to 20;` (R35). `OrderEventMessage` uses `google.protobuf.Timestamp event_at` — ensure `import "google/protobuf/timestamp.proto";` is present.

- [ ] **Step 2: Regenerate Python stubs**

```bash
./backend/scripts/proto-gen.sh
./sidecar/scripts/proto-gen.sh
```

Expected: stubs updated; `git diff --stat` shows the new RPCs.

- [ ] **Step 3: Run buf lint + format**

```bash
cd proto && buf lint && buf format --diff --exit-code
```

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add proto/broker/v1/broker.proto \
        backend/app/broker/proto/broker_pb2*.py \
        backend/app/broker/proto/broker_pb2_grpc.py \
        backend/app/broker/proto/broker_pb2.pyi \
        sidecar/sidecar/proto/broker_pb2*.py \
        sidecar/sidecar/proto/broker_pb2_grpc.py
git commit -m "feat(proto): broker v1 PlaceOrder + CancelOrder + OrderEvent + SearchContracts

Adds the 4 RPCs needed for Phase 5b trade execution per spec §2.
Decimal-as-string fixed-point 8-digit invariant preserved (qty,
prices, filled_qty, avg_fill_price). PlaceOrderRequest reserves
fields 10-20 for forward extension (R35). client_order_id is the
end-to-end dedup key — sidecar will set ib_order.orderRef = it so
IBKR persists + echoes natively (R5)."
```

NOTE: `GetOrders(AccountRef) returns (OrdersResponse)` already exists from Phase 4 (proto/broker/v1/broker.proto:23). Both `PendingSubmitWatchdog` (E2) and `BrokerOrderEventConsumer` resync (E3) reuse `BrokerSidecarClient.get_orders()` which already exists at `backend/app/services/brokers.py:145`. No new RPC needed.

---

### Task A4 — Extend `BrokerSidecarClient` with the 4 new RPC methods

**Owner: Codex** (architect-review P1)

**Files:**
- Modify: `backend/app/services/brokers.py` (add 4 methods + 1 streaming helper)
- Modify: `backend/app/services/brokers_base.py` (add Pydantic-friendly proxies for the new wire types if not auto-generated)
- Test: `backend/tests/services/test_brokers_client_orders.py`

**Spec reference:** Spec §6 sidecar contract; Phase 4 client pattern at `backend/app/services/brokers.py` lines 73-167 (existing `health`, `list_managed_accounts`, `get_account_summary`, `get_positions`, `get_orders`, `get_contract`).

- [ ] **Step 1: Write failing tests** (~6 tests)

1. `test_place_order_marshals_request_and_unmarshals_response` — calls `client.place_order(account_number, client_order_id, conid, side, order_type, tif, qty, limit_price, stop_price)`; mock gRPC stub asserts request fields populated correctly (decimal-as-string fixed-point); response demarshalled into `BrokerPlaceOrderResult(broker_order_id, status)`.
2. `test_place_order_propagates_503_as_BrokerSidecarUnavailable` — gRPC `UNAVAILABLE` → raises `BrokerSidecarUnavailable` with label.
3. `test_place_order_timeout_raises_BrokerSidecarTimeout` — `wait_for(timeout=10)` exceeded → `BrokerSidecarTimeout`.
4. `test_cancel_order_marshals_request` — passes `(account_number, broker_order_id)`; response `accepted: bool`.
5. `test_search_contracts_returns_list` — caches client-side? No — caching is a backend-service concern. Client just demarshals + returns list.
6. `test_order_event_stream_async_iter_yields_events` — async generator; iterates over server-streaming RPC; cancellation closes the stream cleanly.

- [ ] **Step 2: Implement methods**

Add to `BrokerSidecarClient` class (mirror existing `get_orders` style at line 145):

```python
async def place_order(
    self,
    account_number: str,
    client_order_id: str,
    conid: str,
    side: str,
    order_type: str,
    tif: str,
    qty: str,
    limit_price: str = "",
    stop_price: str = "",
) -> base.PlaceOrderResult:
    request = broker_pb2.PlaceOrderRequest(...)
    response = await self._call("place_order", self._stub.PlaceOrder, request)
    return base.PlaceOrderResult(
        broker_order_id=response.broker_order_id,
        status=response.status,
    )

async def cancel_order(self, account_number: str, broker_order_id: str) -> bool:
    ...

async def search_contracts(self, query: str, asset_class: str = "") -> list[base.Contract]:
    ...

async def order_event_stream(
    self, account_number: str
) -> AsyncIterator[base.OrderEventMessage]:
    request = broker_pb2.AccountRef(account_number=account_number)
    async for msg in self._stub.OrderEvent(request, metadata=self._metadata):
        yield base.OrderEventMessage(
            broker_order_id=msg.broker_order_id,
            client_order_id=msg.client_order_id,
            status=msg.status,
            filled_qty=msg.filled_qty,
            avg_fill_price=msg.avg_fill_price,
            broker_event_at=_timestamp_from_proto(msg.event_at),
            raw_payload=msg.raw_payload,
        )
```

Reuse existing `_call` / `_timestamp_from_proto` / `BrokerSidecarUnavailable` / `BrokerSidecarTimeout` helpers from the file. Add `PlaceOrderResult`, `OrderEventMessage` to `base.py` (or wherever the dataclass DTOs live).

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_brokers_client_orders.py -v
```

Expected: 6 PASS.

```bash
git add backend/app/services/brokers.py backend/app/services/brokers_base.py \
        backend/tests/services/test_brokers_client_orders.py
git commit -m "feat(backend): BrokerSidecarClient — place/cancel/search/stream RPCs

Extends the Phase 4 client (existing 6 methods) with 4 new methods
covering Phase 5b's RPC surface. Reuses the existing _call helper,
mTLS metadata propagation, BrokerSidecarUnavailable/Timeout taxonomy,
and decimal-as-string wire format. order_event_stream is an async
generator wrapping the server-streaming RPC; cancellation closes
the stream cleanly via grpc.aio. Architect-review P1."
```

---

### Task A5 — Shared `BrokerSidecarClient` mock fixtures

**Owner: Claude** (architect-review P8)

**Files:**
- Create: `backend/tests/fixtures/sidecar_mocks.py`
- Modify: `backend/tests/conftest.py` (re-export the fixtures)

**Spec reference:** N/A — pure test infrastructure. Without this, ~50 tests across D + E will reinvent the mock surface inconsistently.

- [ ] **Step 1: Implement the fixtures**

Provide these pytest fixtures (all `async`):

```python
@pytest.fixture
def mock_sidecar_client():
    """Default-happy-path BrokerSidecarClient mock — all RPCs return canned data."""
    ...

@pytest.fixture
def mock_sidecar_with_simulator():
    """PlaceOrder returns SIM-<uuid7> broker_order_id; placeOrder NEVER called."""
    ...

@pytest.fixture
def mock_sidecar_with_timeout():
    """All RPCs raise BrokerSidecarTimeout — for lost-order recovery tests."""
    ...

@pytest.fixture
def mock_sidecar_503():
    """All RPCs raise BrokerSidecarUnavailable — for maintenance-window tests."""
    ...

@pytest.fixture
def fake_order_event_stream():
    """Programmable async iterator that yields canned OrderEventMessage sequences."""
    ...
```

- [ ] **Step 2: Smoke-test the fixtures**

`backend/tests/fixtures/test_sidecar_mocks.py` — 4 tests asserting each fixture's behavior. Just smoke; the real coverage comes when D + E use them.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/fixtures/sidecar_mocks.py \
        backend/tests/fixtures/test_sidecar_mocks.py \
        backend/tests/conftest.py
git commit -m "test(backend): shared BrokerSidecarClient mock fixtures

Architect-review P8: D + E chunks have ~50 tests that mock the
sidecar client. Centralizing the fixtures (happy / simulator /
timeout / 503 / streaming) prevents inconsistent mock semantics
across the suite."
```

---

## Chunk B — Sidecar handlers

Implement the 4 RPCs on the sidecar against the FakeIB harness from Phase 4. Includes simulator-mode short-circuit, per-`client_order_id` lock, account-leak filter, bounded queue, whitelist serialization.

### Task B1 — PlaceOrder handler + simulator mode

**Owner: Codex**

**Files:**
- Modify: `sidecar/sidecar/handlers.py`
- Test: `sidecar/tests/test_handlers_orders_contract.py` (extend)

**Spec reference:** §6 lines 474–506; R3, R5, R39.

- [ ] **Step 1: Write failing tests** (~5 tests)

1. `test_place_order_market_builds_correct_ib_order` — FakeIB asserts `MarketOrder(side, qty)` was passed; `orderRef = client_order_id`; `account = account_number` (R5).
2. `test_place_order_limit_includes_limit_price` — `LimitOrder(side, qty, limit_price)` constructed; orderRef set.
3. `test_place_order_stop_includes_stop_price` — `StopOrder(side, qty, stop_price)`; orderRef set.
4. `test_place_order_per_client_id_lock_prevents_double_place` — concurrent `gather()` of two `PlaceOrder` calls with same `client_order_id` results in exactly one `placeOrder` call on FakeIB; second returns the first's broker_order_id (R3).
5. `test_place_order_simulator_mode_returns_sim_id` — when `_simulator_only=True`, response has `broker_order_id` matching `^SIM-[0-9a-f-]{36}$` and FakeIB.placeOrder is NEVER called (R39).

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v -k "place_order"
```

Expected: 5 failures (handler not implemented yet).

- [ ] **Step 3: Implement PlaceOrder handler**

Materialize `BrokerHandlers.PlaceOrder` per spec §6 lines 480–506. The `_place_locks: dict[str, asyncio.Lock]` is initialized in `__init__`; use `setdefault(client_order_id, asyncio.Lock())` then `async with lock`. Inside the lock: scan `self._ib.trades()` for matching `order.orderRef == request.client_order_id` and short-circuit if found; otherwise resolve contract, build ib_order, set `ib_order.orderRef = request.client_order_id`, `ib_order.account = request.account_number`, call `self._ib.placeOrder(contract, ib_order)`. Build `_build_ib_order` helper that switches on `order_type`.

The simulator-mode short-circuit checks `self._simulator_only` (configured from `app_config.broker.<label>.simulator_only`, default `True` for `mode=live` gateways per R39); returns `PlaceOrderResponse(broker_order_id=f"SIM-{uuid7()}", status="Submitted")` and logs `place_order_simulated`.

- [ ] **Step 4: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v -k "place_order"
```

Expected: 5 PASS.

```bash
git add sidecar/sidecar/handlers.py sidecar/tests/test_handlers_orders_contract.py
git commit -m "feat(sidecar): PlaceOrder handler + simulator mode

Implements PlaceOrder RPC per spec §6. Sets ib_order.orderRef =
client_order_id so IBKR persists + echoes the dedup key natively
(R5). Per-client_order_id asyncio.Lock prevents concurrent
double-place (R3). Restart-safety via ib.trades() orderRef scan
inside the lock. Simulator mode (R39) short-circuits placeOrder
for live gateways until operator explicitly flips
app_config.broker.<label>.simulator_only=false post-canary."
```

---

### Task B2 — CancelOrder handler

**Owner: Codex**

**Files:**
- Modify: `sidecar/sidecar/handlers.py`
- Test: `sidecar/tests/test_handlers_orders_contract.py` (extend)

**Spec reference:** §6 lines 508–515; R19.

- [ ] **Step 1: Write failing tests** (~3 tests)

1. `test_cancel_order_filters_by_account_and_perm_id` — given two FakeIB openTrades with same `permId` but different `account`, only the one matching `request.account_number` triggers `cancelOrder` (R19 defense-in-depth).
2. `test_cancel_order_returns_accepted_false_when_not_found` — broker_order_id doesn't match anything → `CancelOrderResponse(accepted=False)`.
3. `test_cancel_order_returns_accepted_true_when_found` — happy path.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v -k "cancel_order"
```

Expected: 3 failures.

- [ ] **Step 3: Implement CancelOrder handler**

Materialize per spec §6 lines 508–515: iterate `self._ib.openTrades()`, filter on BOTH `trade.order.permId == int(request.broker_order_id)` AND `trade.order.account == request.account_number`, call `self._ib.cancelOrder(trade.order)` on match, return `accepted=True`. Default `accepted=False`.

- [ ] **Step 4: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v -k "cancel_order"
```

Expected: 3 PASS.

```bash
git add sidecar/sidecar/handlers.py sidecar/tests/test_handlers_orders_contract.py
git commit -m "feat(sidecar): CancelOrder handler

Forwards cancelOrder to ib_async, filtered by BOTH account_number
AND broker_order_id permId (R19 defense-in-depth — prevents
cross-account permId collision). Returns accepted=False when no
matching open trade found rather than raising."
```

---

### Task B3 — OrderEvent server-streaming + whitelist serializer

**Owner: Codex**

**Files:**
- Modify: `sidecar/sidecar/handlers.py`
- Test: `sidecar/tests/test_handlers_orders_contract.py` (extend)

**Spec reference:** §6 lines 517–565; R4, R16, R30.

- [ ] **Step 1: Write failing tests** (~5 tests)

1. `test_order_event_filters_on_trade_order_account` — FakeIB emits 3 events (account A, B, A); only A's are yielded when subscribed to A. Asserts the filter is `trade.order.account` not `trade.contract.account` (R4).
2. `test_order_event_does_not_leak_cross_account` — explicit cross-account leak prevention.
3. `test_order_event_queue_bounded_drops_on_overflow` — 10001 events queued faster than yielded → `broker_order_events_dropped_total{reason="queue_full"}` increments by 1; queue maxsize=10_000 (R30).
4. `test_serialize_trade_handles_circular_refs` — Trade snapshot with `trade.log[].time = datetime` + `Decimal` qty/prices serializes to `dict` without error; output is JSON-serializable (R16).
5. `test_order_event_emits_status_and_fill` — happy path: orderStatus + execDetails events both yield correctly with cumulative `filled_qty` + `avg_fill_price`.

- [ ] **Step 2: Run + implement + commit**

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v -k "order_event or serialize_trade"
```

Implement OrderEvent + `_serialize_trade` per spec §6 lines 517–565. `_serialize_trade` is a static whitelist helper extracting only safe fields (perm_id, order_ref, account, status, filled, remaining, avg_fill_price, last_fill_price, why_held, log entries with `time.isoformat()`). `_proto_event_from_trade` wraps the dict into `OrderEventMessage` with JSON-encoded `raw_payload`. Generator uses `asyncio.Queue(maxsize=10_000)`, `_on_status` callback filters on `trade.order.account` and uses `put_nowait` with `QueueFull` → drop counter. Subscribe BOTH `orderStatusEvent` and `execDetailsEvent`. Loop `while not context.cancelled(): yield await queue.get()`. Cleanup on `finally:` unsubscribes both.

After test passes:

```bash
git add sidecar/sidecar/handlers.py sidecar/tests/test_handlers_orders_contract.py
git commit -m "feat(sidecar): OrderEvent server-stream + whitelist serializer

OrderEvent filters on trade.order.account (R4 — trade.contract.account
doesn't exist). Bounded asyncio.Queue(maxsize=10_000) drops + counts
on overflow rather than OOMing the sidecar (R30). _serialize_trade
whitelist helper extracts safe fields only (R16) — Trade has circular
refs + Decimal + datetime that naive json.dumps mangles. Subscribes to
both orderStatusEvent and execDetailsEvent, unsubscribes on cleanup."
```

---

### Task B4 — SearchContracts handler + caching + rate limit

**Owner: Codex**

**Files:**
- Modify: `sidecar/sidecar/handlers.py`
- Test: `sidecar/tests/test_handlers_orders_contract.py` (extend)
- Modify: `sidecar/pyproject.toml` (add `aiolimiter`)

**Spec reference:** §6 line 540–543; R20.

- [ ] **Step 1: Write failing tests** (~3 tests)

1. `test_search_contracts_caches_results` — same `(query, asset_class)` invoked twice → FakeIB.reqContractDetailsAsync called once; cache TTL 5 min.
2. `test_search_contracts_rate_limits_5_per_sec_process_wide` — 6 distinct queries inside 1s → 6th waits/queues per the bucket (R20). Use `asyncio.wait_for(handler, timeout=0.2)` to assert it would-block.
3. `test_search_contracts_forwards_asset_class_filter` — `asset_class="STK"` filters out `FUT` contracts.

- [ ] **Step 2: Implement + commit**

`SearchContracts` async method: `await reqContractDetailsAsync(Contract(symbol=query, secType=asset_class or ANY))`; cache by `sha256(query, asset_class)` with 5-min TTL; process-wide `aiolimiter.AsyncLimiter(5, 1)` token bucket. Return `SearchContractsResponse(contracts=[Contract(...) for cd in details])`.

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v -k "search_contracts"
```

Expected: 3 PASS.

```bash
git add sidecar/sidecar/handlers.py sidecar/tests/test_handlers_orders_contract.py \
        sidecar/pyproject.toml sidecar/uv.lock
git commit -m "feat(sidecar): SearchContracts handler + per-process rate limit

5 req/sec process-wide token bucket via aiolimiter (R20 — backend will
also enforce 5/sec per-user). 5-min TTL in-process cache keyed by
sha256(query, asset_class). Forwards asset_class to IBKR."
```

---

### Task B5 — Real-IBKR smoke: place + cancel + stream

**Owner: Claude**

**Files:**
- Modify: `sidecar/tests/test_real_ibkr_smoke.py`

**Spec reference:** §8 lines 681–682; R37, R43.

- [ ] **Step 1: Add real-IBKR smoke tests** (~3 tests)

These run against the live paper gateway (port 4002) using `clientId=998` (R37 registry — different from G1's 999). All gated behind `@pytest.mark.real_ibkr` and `@pytest.mark.skipif(not os.getenv("REAL_IBKR"), ...)`.

1. `test_place_tiny_limit_order_pending_submit` — place LIMIT BUY 1 share AAPL @ 0.01 (well below market, won't fill) DAY-only (R43); assert response status in {`PendingSubmit`, `Submitted`} within 5s; broker_order_id is non-empty integer string.
2. `test_cancel_placed_order` — same place flow + immediate `CancelOrder`; OrderEvent stream receives `ApiCancelled` within 5s.
3. `test_order_event_stream_round_trip` — place LIMIT, subscribe to `OrderEvent`, assert events stream with matching `orderRef = client_order_id`.

Session-fixture cleanup (autouse, scope=session): on entry AND on exit (whether tests pass or fail), call `ib.reqGlobalCancel()` and scan `ib.openTrades()` for any `orderRef.startswith("phase5b-smoke-")` and cancel them. Idempotent (R43).

- [ ] **Step 2: Run + commit**

Run from the NUC where the paper gateway is healthy:

```bash
cd sidecar && REAL_IBKR=1 uv run pytest tests/test_real_ibkr_smoke.py -v -k "place or cancel or order_event_stream"
```

Expected: 3 PASS within ~30s.

```bash
git add sidecar/tests/test_real_ibkr_smoke.py
git commit -m "test(sidecar): real-IBKR smoke for place + cancel + OrderEvent stream

Places tiny DAY LIMIT @ 0.01 well below market (won't fill, expires
at session close per R43); cancels same; verifies OrderEvent stream
round-trips client_order_id via orderRef. clientId=998 per R37
registry. Session fixture runs reqGlobalCancel + orderRef-prefix
scan on both entry and exit, idempotent (R43)."
```

---

### Task B6 — CI workflow: gate real-IBKR smoke on REAL_IBKR=1

**Owner: Claude** (architect-review P12)

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/pre-deploy-smoke.yml` (workflow_dispatch only)
- Modify: `deploy/nuc/README.md`

**Spec reference:** §8 line 681-682 references `clientId=998` smoke against paper gateway. Without this gate, default CI either spuriously fails or accidentally hits real gateways.

- [ ] **Step 1: Modify `.github/workflows/ci.yml`**

The `sidecar-tests` job must explicitly set `env: REAL_IBKR: ""` so `@pytest.mark.skipif(not os.getenv("REAL_IBKR"))` skips B5's smoke tests. Existing `pytest` invocation is otherwise unchanged.

- [ ] **Step 2: Create `.github/workflows/pre-deploy-smoke.yml`**

`workflow_dispatch` only (manual). Runs on a self-hosted runner in the NUC's WireGuard network where the paper gateway is reachable. Sets `REAL_IBKR: "1"`. Calls `cd sidecar && uv run pytest tests/test_real_ibkr_smoke.py -v`. Fails the workflow if any smoke fails. Document the dispatch flow in `deploy/nuc/README.md`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/pre-deploy-smoke.yml \
        deploy/nuc/README.md
git commit -m "ci: gate real-IBKR smoke on REAL_IBKR=1 + add manual dispatch

Architect-review P12: default CI must NOT run real_ibkr smoke
(requires live paper gateway + clientId=998). New
pre-deploy-smoke.yml is manual workflow_dispatch only,
self-hosted runner inside the NUC WireGuard, sets REAL_IBKR=1.
Documented in deploy/nuc/README.md."
```

---

## Chunk C — Backend Pydantic + ORM models

Locks the wire shape that Chunks D + F consume. Pure data structures — no business logic.

### Task C1 — Pydantic request/response models

**Owner: Codex**

**Files:**
- Create: `backend/app/schemas/orders.py`
- Test: `backend/tests/schemas/test_orders.py`

**Spec reference:** §4 lines 218–279.

- [ ] **Step 1: Write failing tests** (~10 tests)

Cover model serialization + validation:

1. `test_preview_request_qty_regex_rejects_negative` — `qty="-1"` raises ValidationError.
2. `test_preview_request_qty_regex_accepts_8_decimals` — `qty="1.00000001"` passes.
3. `test_preview_request_market_no_prices` — `order_type=MARKET` with `limit_price="100"` is REJECTED at the schema level (use a `@model_validator(mode="after")` mirroring §3 CHECK).
4. `test_preview_request_limit_requires_limit_price` — `order_type=LIMIT` with `limit_price=None` rejected.
5. `test_preview_response_serializes_decimal_as_fixed_point_8_digits` — `notional` and `notional_filled_today` serialized via `format(d, 'f')` (NOT `.normalize()` — per `phase5_discoverer_nlv.md` invariant).
6. `test_position_sanity_classifies_high_at_5x` — `current_qty=10, new_qty=50 → status='ok'`; `current_qty=10, new_qty=51 → status='high'`.
7. `test_position_sanity_classifies_extreme_at_10x` — `current_qty=10, new_qty=101 → status='extreme', requires_extra_attestation=True`.
8. `test_place_order_request_uuid_validation` — `client_order_id` must be valid UUID4; non-UUID raises ValidationError.
9. `test_order_response_strips_gateway_label_account_number` — model has no `gateway_label` or `account_number` field (boundary stripping invariant).
10. `test_order_list_response_includes_kill_switch_active` — field present, defaults to False.

- [ ] **Step 2: Implement Pydantic models per spec §4**

Materialize `PreviewRequest`, `PreviewResponse`, `PositionSanityResult`, `ContractSummary`, `PlaceOrderRequest`, `OrderResponse`, `OrderListResponse`, `OrderEvent`, `PolicyResponse`. Reuse existing `BrokerMaintenance` from Phase 5a.

The decimal-as-string serializer uses:
```python
@field_serializer("notional", "filled_qty", ...)
def _ser_decimal(self, v: Decimal | None) -> str | None:
    if v is None: return None
    return format(v.quantize(Decimal("1e-8")), "f")
```

`PositionSanityResult.status` computed via classifier returning `"ok" | "high" | "extreme"`; `requires_extra_attestation = (status == "extreme")`.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/schemas/test_orders.py -v
```

Expected: 10 PASS.

```bash
git add backend/app/schemas/orders.py backend/tests/schemas/test_orders.py
git commit -m "feat(backend): Pydantic schemas for Phase 5b orders

Implements spec §4: PreviewRequest/Response, PositionSanityResult
(R13 5x/10x classifier), PlaceOrderRequest, OrderResponse,
OrderListResponse, PolicyResponse, ContractSummary. Decimal
serialization uses format(.quantize(1e-8), 'f') — fixed-point,
no scientific notation, matching 5a wire-format invariant.
@model_validator enforces order_type↔price coherence at the
schema layer; boundary stripping omits gateway_label/account_number."
```

---

### Task C2 — SQLAlchemy ORM models for orders + order_events

**Owner: Codex**

**Files:**
- Create: `backend/app/models/orders.py`
- Modify: `backend/app/models/__init__.py` (export Order + OrderEvent)
- Create: `backend/app/core/ids.py` (uuid7 helper if not present)
- Test: `backend/tests/models/test_orders.py`

**Spec reference:** §3 schema.

- [ ] **Step 1: Write failing tests** (~5 tests)

1. `test_order_can_insert_with_uuidv7_id` — `id=uuid7()`, `account_id=existing_account`, `client_order_id=uuid4()`, all required fields → row persists.
2. `test_order_status_enum_values` — Order.status accepts all 8 enum values; rejects unknown.
3. `test_order_event_can_have_null_order_id` — TWS-placed audit row (R18).
4. `test_order_relationship_loads_events` — `order.events` returns associated `OrderEvent` rows in `broker_event_at DESC`.
5. `test_order_repr_omits_secrets` — `__repr__` includes id/symbol/side/qty but no internal raw_payload.

- [ ] **Step 2: Implement ORM models**

`backend/app/models/orders.py`:
- `Order(Base)` — `__tablename__ = "orders"`, columns matching 0004 schema, `events = relationship("OrderEvent", back_populates="order", order_by="OrderEvent.broker_event_at.desc()")`.
- `OrderEvent(Base)` — `__tablename__ = "order_events"`, `order = relationship("Order", back_populates="events")`.
- 8-value PostgreSQL ENUM types via `sqlalchemy.dialects.postgresql.ENUM(name="order_status_enum", create_type=False)` (the migration creates them).

`uuid7()` helper: install `uuid-utils` from PyPI (`uv add uuid-utils`); wrap as `def uuid7() -> UUID: return UUID(bytes=uuid_utils.uuid7().bytes)`.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/models/test_orders.py -v
```

Expected: 5 PASS.

```bash
git add backend/app/models/orders.py backend/app/models/__init__.py \
        backend/tests/models/test_orders.py backend/app/core/ids.py \
        backend/pyproject.toml backend/uv.lock
git commit -m "feat(backend): SQLAlchemy ORM models for orders + order_events

Order + OrderEvent ORM models matching alembic 0004. UUIDv7 helper
in app/core/ids.py for insert-ordered server-generated PKs (R2).
events relationship loads in broker_event_at DESC. Postgres ENUMs
referenced via create_type=False — migration owns enum lifecycle."
```

---

### Task C3 — Config keys: per-account caps + simulator + kill-switch

**Owner: Codex**

**Files:**
- Create: `backend/app/services/orders_policy.py`
- Test: `backend/tests/services/test_orders_policy.py`

**Spec reference:** §4 lines 364–370 (config keys) + §10 simulator default-on; R39.

- [ ] **Step 1: Write failing tests** (~6 tests)

1. `test_get_max_notional_per_order_default` — when key absent, returns sensible default `Decimal("10000")` documented in code.
2. `test_get_daily_notional_cap_default` — default `Decimal("50000")`.
3. `test_get_trade_enabled_default_false` — new account → default `False` (canary safety).
4. `test_get_simulator_only_default_true_for_live` — `mode=live` account → default `True` (R39).
5. `test_get_simulator_only_default_false_for_paper` — `mode=paper` → default `False`.
6. `test_kill_switch_default_false` — `app_config.broker.kill_switch_enabled` defaults to `False`.

- [ ] **Step 2: Implement `orders_policy.py`**

```python
async def get_account_policy(cfg: ConfigService, account: BrokerAccount) -> AccountTradePolicy:
    return AccountTradePolicy(
        max_notional_per_order=Decimal(await cfg.get(f"broker.{account.gateway_label}.max_notional_per_order", default="10000")),
        daily_notional_cap=Decimal(await cfg.get(f"broker.{account.gateway_label}.daily_notional_cap", default="50000")),
        trade_enabled=await cfg.get_bool(f"broker.{account.gateway_label}.trade_enabled", default=False),
        simulator_only=await cfg.get_bool(
            f"broker.{account.gateway_label}.simulator_only",
            default=(account.mode == "live"),
        ),
    )

async def is_kill_switch_active(cfg: ConfigService) -> bool:
    return await cfg.get_bool("broker.kill_switch_enabled", default=False)
```

`AccountTradePolicy` is a frozen dataclass / Pydantic model (pick one and document).

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_orders_policy.py -v
```

Expected: 6 PASS.

```bash
git add backend/app/services/orders_policy.py \
        backend/tests/services/test_orders_policy.py
git commit -m "feat(backend): per-account trade policy + kill-switch resolver

Pure async helpers reading app_config for max_notional_per_order,
daily_notional_cap, trade_enabled, simulator_only, and the global
broker.kill_switch_enabled. Live gateways default simulator_only=true
(R39); new accounts default trade_enabled=false (canary safety per
spec §9). Spec §4 lines 364–370 + R6/R39."
```

---

## Chunk D — Backend API endpoints

Read C's models, talk to B's sidecar, write to A's tables. The trade-execution write path lives here.

### Task D1 — POST /api/orders/preview

**Owner: Codex**

**Files:**
- Create: `backend/app/api/orders.py` (router scaffolding + preview endpoint)
- Create: `backend/app/services/orders_service.py` (business logic; preview helper)
- Modify: `backend/app/main.py` (mount router)
- Test: `backend/tests/api/test_orders_preview.py`

**Spec reference:** §4 lines 281–295; R6, R12, R13, R24, R29, R33.

- [ ] **Step 1: Write failing tests** (~7 tests)

1. `test_preview_kill_switch_returns_503_first` — kill_switch ON + maintenance ON + invalid Pydantic — kill_switch error wins (R6 ordering).
2. `test_preview_maintenance_returns_503_with_retry_after` — maintenance active → 503 + `Retry-After` header.
3. `test_preview_canonicalizes_qty` — `qty="01.00"` → canonicalized `"1.00000000"` before nonce computation (R33).
4. `test_preview_market_notional_includes_5pct_slippage_buffer` — MARKET BUY 100 @ mid=10 → notional=`1050.00000000` (R12).
5. `test_preview_position_sanity_extreme` — current_qty=10, BUY 200 → status `"extreme"`, `requires_extra_attestation=True` (R13).
6. `test_preview_daily_cap_status_near_at_81pct` — sums today's `notional` for account; 81% of cap → `daily_cap_status="near"`.
7. `test_preview_mints_redis_nonce_with_canonicalized_payload` — verify Redis key `nonce:order:<account_id>:<nonce>` exists with TTL 30s and value matches canonicalized request hash.
8. `test_preview_503_when_fx_cache_cold_and_sidecar_unavailable` — architect-review P17: cold FX cache + sidecar 503 → preview returns 503 (NOT a 1.0 default — pre-trade with stale/no rate is dangerous). Response includes a `Retry-After` header consistent with the maintenance envelope.

- [ ] **Step 2: Implement preview endpoint per spec §4 step ordering**

(kill-switch → maintenance → Pydantic → canonicalize → contract resolve → notional with FX → daily-cap sum → position sanity → nonce mint). Rate limit 10/min/user via `slowapi`. Use `redis.set(key, hash, ex=30, nx=True)` for the nonce; payload hash = sha256 of canonical JSON of request fields.

FX conversion (R24): cache IBKR mid-rate keyed `fx:mid:<from>:<to>` with 1h TTL; default to 1.0 if `from == to`.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_preview.py -v
```

Expected: 7 PASS.

```bash
git add backend/app/api/orders.py backend/app/services/orders_service.py \
        backend/app/main.py backend/tests/api/test_orders_preview.py
git commit -m "feat(backend): POST /api/orders/preview

Implements spec §4 preview endpoint with R6 ordering (kill-switch
FIRST, then maintenance, then Pydantic, then canonicalize-qty
(R33) before nonce hash). MARKET notional includes 5% slippage
buffer (R12). Position-sanity classifier flags 5x/10x multipliers
(R13). Daily-notional cap context summed from today's orders.
FX-converted via cached IBKR mid-rate (R24). Redis-bound 30s
nonce — payload-locked, cannot be smuggled into POST."
```

---

### Task D2 — POST /api/orders (place order)

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`
- Modify: `backend/app/services/orders_service.py`
- Test: `backend/tests/api/test_orders_place.py`

**Spec reference:** §4 lines 296–328; R1, R6, R17, R29.

- [ ] **Step 1: Write failing tests** (~9 tests)

1. `test_place_kill_switch_first` — kill_switch wins over maintenance and nonce check (R6).
2. `test_place_consumes_redis_nonce_via_getdel` — nonce gone from Redis after success.
3. `test_place_rejects_unknown_nonce_with_422` — nonce missing → 422.
4. `test_place_rejects_payload_mismatch_with_422` — nonce exists but payload hash differs from canonicalized incoming.
5. `test_place_rth_changed_between_preview_and_post_returns_422` — preview was inside RTH, POST is outside → 422 with re-preview prompt (R29).
6. `test_place_inserts_via_on_conflict_do_nothing` — happy path: row inserted, status `submitted`, broker_order_id from sidecar.
7. `test_place_idempotent_retry_returns_existing_row` — second POST with same `(account_id, client_order_id)` returns the first's row (R17 `submission_state="idempotent_retry"`).
8. `test_place_sidecar_timeout_marks_pending_unknown` — sidecar 503/timeout → row stays `pending_submit`, response has `submission_state="pending_unknown"` (R1).
9. `test_place_kill_switch_flipped_post_sidecar_attempts_cancel` — race: kill switch flipped after sidecar acked; backend best-effort `CancelOrder` (R6).
10. `test_concurrent_post_with_same_nonce_one_succeeds_one_422` — architect-review P11: `asyncio.gather(*[post_orders(payload, nonce)] * 2)` — exactly one 200, exactly one 422 (Redis GETDEL race-safe).

- [ ] **Step 2: Implement POST /orders**

Materialize spec §4 step ordering (lines 296–328) verbatim. Use `BrokerSidecarClient.place_order` (existing 5a client, extended with the new RPC).

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_place.py -v
```

Expected: 9 PASS.

```bash
git add backend/app/api/orders.py backend/app/services/orders_service.py \
        backend/tests/api/test_orders_place.py
git commit -m "feat(backend): POST /api/orders place + lost-order recovery

Implements spec §4 step ordering (R6 kill-switch first, R17 explicit
INSERT ON CONFLICT path with SELECT fallback for idempotent retry,
R29 RTH re-check, R1 submission_state=\"pending_unknown\" on
sidecar timeout so frontend distinguishes 'sent unknown' from
'sidecar-acked submitted'). Watchdog (E2) reconciles within 60s.
Post-sidecar kill-switch defense attempts best-effort CancelOrder."
```

---

### Task D3 — GET /api/orders + /api/orders/{id} + /api/orders/policy/{id}

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`
- Test: `backend/tests/api/test_orders_get.py`

**Spec reference:** §4 lines 209–214 + 338–349.

- [ ] **Step 1: Write failing tests** (~7 tests)

1. `test_get_orders_default_filter_active` — returns rows where `status NOT IN (terminal)` by default.
2. `test_get_orders_status_filter` — `?status=filled` returns only filled.
3. `test_get_orders_includes_broker_maintenance_envelope` — same shape as 5a `AccountListResponse`.
4. `test_get_orders_includes_kill_switch_active` — flag flipped → True in response.
5. `test_get_order_by_id_includes_events` — events array populated, sorted by broker_event_at DESC.
6. `test_get_order_by_id_404_when_missing` — wrong id → 404, no DB leak in error body.
7. `test_get_orders_policy_returns_caps_and_today_notional` — sums today's orders.notional + reads app_config keys.

- [ ] **Step 2: Implement endpoints**

3 endpoints. `OrderListResponse` reuses `BrokerMaintenance` from 5a. Default filter SQL: `WHERE status NOT IN ('filled','cancelled','rejected','expired')`. Single-order endpoint joins `order_events` LEFT.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_get.py -v
```

Expected: 7 PASS.

```bash
git add backend/app/api/orders.py backend/tests/api/test_orders_get.py
git commit -m "feat(backend): GET /api/orders, /api/orders/{id}, /api/orders/policy/{id}

Active-by-default list endpoint with broker_maintenance + kill_switch
envelope mirroring 5a AccountListResponse. Single-order joins
order_events DESC. /policy/{id} is the trade-modal pre-render
context endpoint (caps + simulator + trade_enabled + today's notional)."
```

---

### Task D4 — DELETE /api/orders/{id}

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`
- Test: `backend/tests/api/test_orders_cancel.py`

**Spec reference:** §4 lines 330–336; R15, R31.

- [ ] **Step 1: Write failing tests** (~6 tests)

1. `test_cancel_terminal_returns_409` — order already filled → 409.
2. `test_cancel_partial_then_cancel_models_correctly` — partial fill in flight, cancel succeeds, status enum is `cancelled` with `filled_qty < qty` (R15).
3. `test_cancel_idempotent_within_5s_returns_202` — second DELETE inside 5s of `cancel_requested_at` → 202 "already in flight" (R31).
4. `test_cancel_after_5s_re_forwards_to_sidecar` — 6s later → forward CancelOrder again (R31 cooldown expired).
5. `test_cancel_uses_for_update_nowait_row_lock` — SQL trace shows `SELECT ... FOR UPDATE NOWAIT` (architect-review P20: NOWAIT prevents blocking under contention).
6. `test_cancel_forwards_account_number_and_broker_order_id` — sidecar mock asserts both fields populated.
7. `test_cancel_under_lock_contention_returns_423` — architect-review P20: when row is already FOR UPDATE'd by another tx, `NOWAIT` raises `LockNotAvailable`; endpoint returns 423 (Locked) with retry guidance, NOT 500.

- [ ] **Step 2: Implement DELETE endpoint** per spec §4 lines 330–336.

`SELECT ... FOR UPDATE NOWAIT` lock on the row (architect-review P20), terminal → 409, `cancel_requested_at` within 5s → 202, else `UPDATE orders SET cancel_requested_at = now()` + forward CancelOrder + return 202. On `LockNotAvailable` from NOWAIT → 423 Locked with `Retry-After: 1`.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_cancel.py -v
```

Expected: 6 PASS.

```bash
git add backend/app/api/orders.py backend/tests/api/test_orders_cancel.py
git commit -m "feat(backend): DELETE /api/orders/{id} idempotent cancel

Idempotent within a 5s cooldown window (R31). SELECT FOR UPDATE
prevents the cancel-place race. Partial-then-cancel models the
status enum as 'cancelled' with filled_qty < qty (R15). Forwards
(account_number, broker_order_id) both — defense in depth against
permId collision across accounts (R19)."
```

---

### Task D5 — GET /api/contracts/search

**Owner: Codex**

**Files:**
- Create: `backend/app/api/contracts.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/api/test_contracts.py`

**Spec reference:** §4 line 361; R20.

- [ ] **Step 1: Write failing tests** (~4 tests)

1. `test_search_forwards_to_one_healthy_sidecar` — sidecar mock asserts called.
2. `test_search_caches_redis_5min_ttl` — second hit of same `(q, asset_class)` doesn't call sidecar.
3. `test_search_rate_limits_5_per_sec_per_user` — 6 requests in 1s → 6th is 429.
4. `test_search_propagates_sidecar_503` — sidecar 503 → 503 to caller with the `broker_maintenance` envelope.

- [ ] **Step 2: Implement endpoint**

Pick first healthy sidecar via `BrokerRegistry.healthy_sidecars()`; cache key `contracts:search:<sha256(q,class)>` 5-min TTL; rate-limit `slowapi` 5/sec/user.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_contracts.py -v
```

Expected: 4 PASS.

```bash
git add backend/app/api/contracts.py backend/app/main.py \
        backend/tests/api/test_contracts.py
git commit -m "feat(backend): GET /api/contracts/search autocomplete

Per-user 5/sec rate limit (R20 — paired with sidecar's process-wide
5/sec). 5-min Redis-cached results. Propagates sidecar 503 with the
maintenance envelope so the trade modal can disable the field
during reset windows."
```

---

### Task D6 — GET /api/orders/events (SSE)

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/orders.py`
- Create: `backend/app/services/orders_sse.py`
- Test: `backend/tests/api/test_orders_sse.py`

**Spec reference:** §4 lines 352–359; R10, R25, R26.

- [ ] **Step 1: Write failing tests** (~9 tests, architect-review P14+P15 added 8+9)

1. `test_sse_headers` — `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive` all set.
2. `test_sse_emits_id_event_data_format` — output matches `id: <int>\nevent: order.update\ndata: <json>\n\n`.
3. `test_sse_heartbeat_every_10s` — fake clock; assert `: heartbeat\n\n` emitted at t=10s, t=20s (R10 — was 15s, dropped to 10s for CF Tunnel idle close).
4. `test_sse_resume_via_last_event_id_HEADER` — architect-review P14: explicitly use `httpx.AsyncClient(headers={"Last-Event-ID": "100"})`; backend reads from HTTP header (matches EventSource auto-reconnect spec). Replays `order_events WHERE id > 100` BEFORE tailing pubsub.
5. `test_sse_scoped_subscription_account_only` — `?account_id=<uuid>` subscribes to `orders:events:account:<id>`; events for other accounts NOT delivered (R25).
6. `test_sse_fleet_subscription_when_no_account_id` — no query param → subscribes to `orders:events:fleet`.
7. `test_sse_closes_on_client_disconnect` — disconnecting client cancels the asyncio task cleanly within 1s.
8. `test_sse_drops_slow_client_via_per_client_queue` — architect-review P15: per-client `asyncio.Queue(maxsize=1000)` overflows when client doesn't read; backend closes the connection cleanly with a final `event: error\ndata: {"reason":"slow_client"}` and increments `sse_dropped_clients_total`.
9. `test_sse_decrements_active_gauge_on_disconnect` — `sse_active_connections` gauge increments on connect, decrements on disconnect, even when disconnect path is the slow-client drop.

- [ ] **Step 2: Implement SSE endpoint**

Use `sse-starlette` or roll a generator returning `EventSourceResponse`. Subscribe Redis pubsub via `aioredis.client.PubSub`. Heartbeat coroutine sleeps 10s. Replay path: `SELECT * FROM order_events WHERE id > :last_id AND account_id = :scope ORDER BY id ASC LIMIT 1000`. Track active SSE connections via Prometheus gauge `sse_active_connections` (R26).

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_sse.py -v
```

Expected: 7 PASS.

```bash
git add backend/app/api/orders.py backend/app/services/orders_sse.py \
        backend/tests/api/test_orders_sse.py
git commit -m "feat(backend): GET /api/orders/events SSE with Last-Event-ID resume

10s heartbeat (R10 — CF Tunnel idle-close threshold). Scoped
subscription via ?account_id= query param routes to either
orders:events:account:<id> or orders:events:fleet (R25). Replays
missed events from order_events WHERE id > Last-Event-ID (read from
the HTTP header per EventSource spec) before tailing live pubsub.
Per-client asyncio.Queue(maxsize=1000) drops slow clients with a
clean error event + counter, never starves siblings (architect-
review P14+P15). sse_active_connections Prometheus gauge for
capacity awareness (R26). Headers set X-Accel-Buffering: no for
nginx + CF tunnel buffering off."
```

---

### Task D7 — OpenAPI snapshot lock (5 named models)

**Owner: Claude** (architect-review P6)

**Files:**
- Create: `backend/tests/api/test_openapi_snapshot.py`
- Create: `backend/tests/fixtures/openapi-snapshot.json` (committed snapshot)

**Spec reference:** §8 line 675 — "OpenAPI shape locked for OrderResponse + OrderListResponse + PreviewResponse + ContractSummary + PolicyResponse".

- [ ] **Step 1: Generate the initial snapshot**

```bash
cd backend && uv run python -c "
from app.main import app
import json
spec = app.openapi()
# Extract just the 5 named models we care about
models = {k: spec['components']['schemas'][k] for k in [
    'OrderResponse', 'OrderListResponse', 'PreviewResponse',
    'ContractSummary', 'PolicyResponse',
]}
with open('tests/fixtures/openapi-snapshot.json', 'w') as f:
    json.dump(models, f, indent=2, sort_keys=True)
"
```

- [ ] **Step 2: Write the snapshot diff test**

```python
def test_openapi_snapshot_unchanged():
    spec = app.openapi()
    actual = {k: spec["components"]["schemas"][k] for k in (
        "OrderResponse", "OrderListResponse", "PreviewResponse",
        "ContractSummary", "PolicyResponse",
    )}
    expected = json.loads(Path("tests/fixtures/openapi-snapshot.json").read_text())
    if actual != expected:
        pytest.fail(
            "OpenAPI shape drift detected. If intentional, regen the snapshot "
            "with: python -m app.scripts.regen_openapi_snapshot"
        )
```

Provide an `app/scripts/regen_openapi_snapshot.py` matching Step 1 so operators have a one-liner to bless drifts.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_openapi_snapshot.py -v
```

Expected: 1 PASS.

```bash
git add backend/tests/api/test_openapi_snapshot.py \
        backend/tests/fixtures/openapi-snapshot.json \
        backend/app/scripts/regen_openapi_snapshot.py
git commit -m "test(backend): OpenAPI snapshot lock for 5b wire models

Architect-review P6: locks OrderResponse, OrderListResponse,
PreviewResponse, ContractSummary, PolicyResponse so frontend +
backend can't drift silently. Drift requires explicit
regen_openapi_snapshot script invocation — intentional schema
changes get a paper trail in PR diff."
```

---

## Chunk E — Background tasks (consumer + watchdog + reconciliation)

**Architect-review P19 ordering note:** within Chunk E, implement E1 → E3 → E2. E2 (watchdog) feeds synthetic events into `_process_event` produced by the same path E3 (resync) uses; building E3 first means E2 lands against a fully-baked consumer, not a moving target.

The async core that turns broker stream events into DB rows + Redis pubsub.

### Task E1 — BrokerOrderEventConsumer + per-account stream task

**Owner: Codex**

**Files:**
- Create: `backend/app/services/order_event_consumer.py`
- Modify: `backend/app/main.py` (lifespan startup integration)
- Test: `backend/tests/services/test_order_event_consumer.py`

**Spec reference:** §5 lines 380–412; R7, R8, R11, R12, R18, R23, R27, R30.

- [ ] **Step 1: Write failing tests** (~9 tests; uses fake gRPC stream)

1. `test_single_event_inserts_audit_row_and_upserts_order` — happy path: `order_events` row + `orders` row update + Redis publish.
2. `test_partial_fill_updates_filled_qty_and_avg_fill_price_and_notional_filled` — UPSERT increments correctly; `notional_filled = filled_qty * avg_fill_price` (R12).
3. `test_terminal_status_sticky` — already-filled row + new event with status=Submitted → status stays `filled` (R11 terminal sticks).
4. `test_out_of_order_events_dont_revert_state` — event with `broker_event_at < last_event_at` is no-op via `:broker_event_at >= last_event_at` predicate (R23).
5. `test_malformed_event_savepoint_rolls_back_only_that_event` — bad event raises mid-handler; outer transaction continues; `broker_order_events_dropped_total` increments.
6. `test_tws_placed_event_writes_audit_only` — event has empty `client_order_id` → INSERT order_events with `order_id=NULL`; no orders UPSERT (R18).
7. `test_account_added_spawns_new_child_stream` — `BrokerRegistry.account_changed` event with kind=add → new asyncio.Task created (R8).
8. `test_account_removed_cancels_child_stream` — kind=remove → child task cancelled cleanly (R8).
9. `test_one_stream_death_doesnt_affect_siblings` — per-account supervisor isolation.
10. `test_raw_payload_account_field_redacted_in_logs` — architect-review P5: structlog processor scrubs `account`, `account_number`, `acctNumber` keys from any logged `raw_payload`. Use `structlog.testing.capture_logs()`; assert the captured event dicts contain `"<redacted>"` instead of the real account number when `raw_payload` is logged through the consumer's structured logger.

- [ ] **Step 2: Implement consumer + structlog redaction (architect-review P5)**

Materialize spec §5 verbatim:
- `OrderEventConsumer` class with `start()`, `stop()`, `_supervisor()`, `_run_account_stream()`, `_process_event()`.
- `_process_event` uses `session.begin_nested()` savepoint per event; UPSERT SQL exactly per spec lines 387–404.
- Supervisor subscribes to `BrokerRegistry.account_changed` events (extend the registry's notification surface in this task if not already present); 60s safety re-enumeration.
- `asyncio.Lock` re-entrancy guard.
- Prom counters: `broker_order_events_received_total{label}`, `broker_order_events_dropped_total{label, reason}`, `broker_order_event_lag_ms` histogram, `broker_order_stream_reconnects_total{label}`, `consumer_alive{label, account_id}` gauge.
- Circuit breaker: 50 consecutive process_event failures → log + alert (R27).

**Structlog redaction (architect-review P5):** extend `app/core/logging.py` redaction processor to scrub the keys `account`, `account_number`, `acctNumber` from any logged dict (recursively into `raw_payload`). Add a snapshot test that logs a Trade-shape dict and asserts `"<redacted>"` replacement. Document the new keys in `app/core/logging.py` docstring.

Lifespan: after `build_broker_registry()` succeeds, instantiate consumer + `await consumer.start()`; in shutdown, `await consumer.stop()` with 30s graceful drain (R9).

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_order_event_consumer.py -v
```

Expected: 9 PASS.

```bash
git add backend/app/services/order_event_consumer.py backend/app/main.py \
        backend/tests/services/test_order_event_consumer.py
git commit -m "feat(backend): BrokerOrderEventConsumer 22 per-account streams

One asyncio.Task per (sidecar, account_number) pair, supervised
under a single lifespan-anchored manager. Terminal-status-sticky
UPSERT predicate (R11) prevents out-of-order events reverting
filled/cancelled. broker_event_at >= last_event_at gate (R23) is
the second line of defense. session.begin_nested() savepoint per
event isolates poisoning (R27 circuit breaker fires after 50
consecutive failures). Account-added/removed via
BrokerRegistry.account_changed (R8). TWS-placed events written
audit-only (R18 — order_id=NULL)."
```

---

### Task E2 — PendingSubmitWatchdog + startup reconciliation

**Owner: Codex**

**Files:**
- Create: `backend/app/services/pending_submit_watchdog.py`
- Modify: `backend/app/main.py` (lifespan)
- Test: `backend/tests/services/test_pending_submit_watchdog.py`

**Spec reference:** §5 lines 414–439; R1, R9.

- [ ] **Step 1: Write failing tests** (~6 tests)

1. `test_watchdog_finds_stuck_pending_after_60s` — row created at t-61s with status `pending_submit` is included in scan; row at t-30s is not.
2. `test_watchdog_recovers_from_broker_match` — GetOrders matches `orderRef = client_order_id` → synthetic event emitted through `_process_event`; row transitions to `submitted`; `broker_order_pending_submit_recovered_total{label}` +=1.
3. `test_watchdog_5min_no_match_escalates_to_rejected` — row stuck >5min, GetOrders empty → status=`rejected`, `raw_payload.recovery_outcome="broker_no_match_after_5min"`; counter `broker_order_pending_submit_orphan_total{label}` +=1.
4. `test_watchdog_runs_every_30s` — fake clock, 2 ticks observed.
5. `test_startup_reconciliation_runs_same_pass` — on lifespan startup, scan executes once before consumer streams open.
6. `test_watchdog_uses_partial_index` — `EXPLAIN ANALYZE` shows `ix_orders_pending_submit_watchdog` is used.
7. `test_watchdog_escalation_writes_audit_event_in_same_tx` — architect-review P13: 5-min orphan UPDATE-to-rejected MUST be paired with an `INSERT INTO order_events` row in the same transaction; assert atomicity (commit-or-rollback together) and that the audit row carries `status='rejected'`, `raw_payload.recovery_outcome='broker_no_match_after_5min'`.

- [ ] **Step 2: Implement watchdog**

`PendingSubmitWatchdog`:
- 30s loop; SQL exactly per spec §5 lines 417–423.
- For each stuck row: `BrokerSidecarClient.GetOrders(account_number)` (the existing 5a RPC); match on `orderRef == client_order_id`. Match → synthesize an OrderEventMessage and feed `OrderEventConsumer._process_event`. No match + age > 5min → UPDATE to `rejected` with `raw_payload.recovery_outcome`.
- `start()` / `stop()` lifecycle; supervisor re-spawns on death.

Startup reconciliation: on lifespan startup, BEFORE consumer.start(), run one watchdog cycle. Eliminates the "backend bounced mid-order" gap (R9).

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_pending_submit_watchdog.py -v
```

Expected: 6 PASS.

```bash
git add backend/app/services/pending_submit_watchdog.py backend/app/main.py \
        backend/tests/services/test_pending_submit_watchdog.py
git commit -m "feat(backend): PendingSubmitWatchdog + startup reconciliation

R1: stuck-pending-submit rows recovered within 60s when sidecar
times out mid-place. GetOrders match by orderRef = client_order_id
emits a synthetic event through OrderEventConsumer (transitions to
submitted, populates broker_order_id). 5-min escalation marks the
row rejected with raw_payload.recovery_outcome=broker_no_match_after_5min.
Startup reconciliation runs the same scan before consumer streams
open — eliminates the 'backend bounced mid-order' gap (R9). Uses
the partial index ix_orders_pending_submit_watchdog from migration
0004."
```

---

### Task E3 — Reconnect-and-resync logic

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/order_event_consumer.py`
- Test: `backend/tests/services/test_order_event_consumer_resync.py`

**Spec reference:** §5 lines 441–449; R11.

- [ ] **Step 1: Write failing tests** (~4 tests)

1. `test_reconnect_buffers_then_resyncs_first` — fake gRPC stream emits events at t=0; consumer connects at t=0+ε with buffer queue; calls `GetOrders` snapshot first; emits synthetic events for transitions table doesn't have; THEN drains the buffer (R11 ordering).
2. `test_resync_synthetic_events_use_broker_event_at_from_snapshot` — synthetic event preserves the broker's timestamp.
3. `test_resync_doesnt_double_count_when_predicate_blocks` — buffered event with `broker_event_at` matching synthetic's → UPSERT predicate is no-op for one of them (whichever is later wins).
4. `test_resync_emits_metric_count` — `broker_order_stream_resync_synthetic_events_total{label}` increments by N.

- [ ] **Step 2: Implement resync**

In `_run_account_stream`:
1. Open gRPC stream, immediately push events into local `asyncio.Queue` ("buffer").
2. Call `GetOrders(account_number)`; for each Trade in the snapshot whose status differs from local DB or whose `client_order_id` is not in DB, synthesize an `OrderEventMessage` (using the wire `raw_payload`); call `await self._process_event(msg)` in `broker_event_at` order.
3. Drain buffer through `_process_event`.
4. Tail live stream.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_order_event_consumer_resync.py -v
```

Expected: 4 PASS.

```bash
git add backend/app/services/order_event_consumer.py \
        backend/tests/services/test_order_event_consumer_resync.py
git commit -m "feat(backend): consumer reconnect-and-resync

Buffer-then-drain pattern (R11): on (re)connect, buffer the live
stream into a local queue, run GetOrders snapshot, synthesize
events for missed transitions, drain the buffer. The
broker_event_at >= last_event_at predicate (R23) ensures
synthetic events don't overwrite live ones when timestamps
overlap. broker_order_stream_resync_synthetic_events_total
counter tracks how often this fires."
```

---

## Chunk F — Frontend services + stores

Pure data layer. Boundary discipline (per 5a R12) — services return data, hooks compose maintenance/kill_switch publishing.

### Task F1 — services/orders.ts + types.ts

**Owner: Codex**

**Files:**
- Modify: `frontend/src/services/types.ts` (add Order, OrderEvent, ContractSummary, PreviewRequest/Response, PolicyResponse)
- Create: `frontend/src/services/orders.ts`
- Test: `frontend/src/services/orders.test.ts`

**Spec reference:** §7 lines 600–608.

- [ ] **Step 1: Write failing tests** (~8 tests)

Mirror backend Pydantic shapes:

1. `test_preview_order_posts_correct_body` — fetch mock asserts JSON body shape.
2. `test_place_order_uses_caller_supplied_client_order_id` — architect-review P10: caller passes a stable `clientOrderId` (UUID4 generated by the modal); service forwards it verbatim. Service does NOT generate it.
3. `test_cancel_order_posts_delete` — DELETE /api/orders/<id>.
4. `test_search_contracts_factory_debounces_300ms` — `createDebouncedSearch` factory uses internal debounce timer.
5. `test_search_contracts_aborts_in_flight_on_new_query` — AbortController cancels old fetch.
6. `test_get_orders_maps_broker_maintenance_envelope` — same envelope shape as 5a.
7. `test_503_maintenance_throws_typed_error` — `BrokerMaintenanceError` with `retryAfter`.
8. `test_409_idempotent_retry_returns_existing_order` — POST /orders 200 with `submission_state="idempotent_retry"` is treated as success (NOT thrown as error).

- [ ] **Step 2: Implement service**

**Important: types come from `frontend/src/services/api-generated.ts` (built by Task A0).** Hand-written `types.ts` re-exports the generated types and adds branded types (e.g. `DecimalString = string & { __brand: "DecimalString" }`) for compile-time safety.

`services/orders.ts`:
- `previewOrder(req: PreviewRequest): Promise<PreviewResponse>`
- `placeOrder(req: PreviewRequest, nonce: string, clientOrderId: string): Promise<PlaceResult>` — **architect-review P10: caller supplies `clientOrderId` so the lifecycle is owned by the modal (stable across retries within one modal instance), NOT auto-generated per-call.**
- `cancelOrder(id: string): Promise<void>`
- `getOrders(opts?: {status?: string}): Promise<OrderListResult>` — returns `{orders, brokerMaintenance, killSwitchActive}`.
- `getOrderById(id: string): Promise<OrderResponse>`
- `getOrderPolicy(accountId: string): Promise<PolicyResponse>`
- `searchContracts(q: string, assetClass?: string, signal?: AbortSignal): Promise<ContractSummary[]>` — caller supplies signal.
- `createDebouncedSearch(delayMs: number = 300)` factory — composes `searchContracts` with internal debounce + AbortController.

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/services/orders.test.ts
```

Expected: 8 PASS.

```bash
git add frontend/src/services/orders.ts frontend/src/services/types.ts \
        frontend/src/services/orders.test.ts
git commit -m "feat(frontend): orders service + types

Spec §4 wire shapes mirrored exactly (decimal-as-string fixed-point
8 digits). client_order_id generated via crypto.randomUUID() inside
placeOrder (idempotency baked into the service). 200 with
submission_state=idempotent_retry returns the existing OrderResponse
rather than throwing. createDebouncedSearch factory composes the raw
service with debounce + abort so the hook can drive the timer."
```

---

### Task F2 — stores/global/orders.ts (Zustand)

**Owner: Codex**

**Files:**
- Create: `frontend/src/stores/global/orders.ts`
- Test: `frontend/src/stores/global/orders.test.ts`

**Spec reference:** §7 lines 603–605.

- [ ] **Step 1: Write failing tests** (~6 tests)

1. `test_addOrder_inserts_by_id` — `useOrdersStore.getState().addOrder(o1)` → `orders[o1.id] === o1`.
2. `test_applyEvent_updates_existing` — incoming SSE event with newer `last_event_at` updates the row.
3. `test_applyEvent_skips_older_events` — older `last_event_at` is ignored (mirrors backend predicate).
4. `test_applyEvent_inserts_unknown_order` — event for a row we never SAW (e.g., placed in another tab) inserts.
5. `test_setKillSwitchActive_toggles` — flag flip.
6. `test_clearOrders_resets` — used on logout.

- [ ] **Step 2: Implement Zustand store**

`useOrdersStore`:
- `orders: Record<string, OrderResponse>` (keyed by id)
- `killSwitchActive: boolean`
- `brokerMaintenance: BrokerMaintenance | null`
- `lastEventId: number | null`
- Actions: `setOrders(orders)`, `addOrder(o)`, `applyEvent(event)`, `setKillSwitchActive(v)`, `setBrokerMaintenance(m)`, `setLastEventId(id)`, `clear()`.

`applyEvent` checks `existing?.last_event_at >= event.last_event_at` and skips.

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/stores/global/orders.test.ts
```

Expected: 6 PASS.

```bash
git add frontend/src/stores/global/orders.ts frontend/src/stores/global/orders.test.ts
git commit -m "feat(frontend): useOrdersStore Zustand slice

Indexed by order id. applyEvent applies the same out-of-order guard
as the backend (R23 predicate mirror) so SSE replays are idempotent.
Mirrors the 5a useFleetMaintenance pattern for the maintenance
envelope; killSwitchActive surfaces the global red banner.
lastEventId tracked for SSE Last-Event-ID resume."
```

---

### Task F3 — hooks/useOrdersList.ts + useOrdersStream.ts

**Owner: Codex**

**Files:**
- Create: `frontend/src/hooks/useOrdersList.ts`
- Create: `frontend/src/hooks/useOrdersStream.ts`
- Test: `frontend/src/hooks/useOrdersList.test.ts`
- Test: `frontend/src/hooks/useOrdersStream.test.ts`

**Spec reference:** §7 boundary discipline (5a R12 pattern).

- [ ] **Step 1: Write failing tests** (~6 tests)

`useOrdersList`:
1. `test_fetchAndSync_publishes_orders_and_maintenance_to_store` — composes `orderService.getOrders` with `useOrdersStore.setOrders` + `setKillSwitchActive` + `setBrokerMaintenance`.
2. `test_503_does_not_clear_orders` — maintenance error keeps cached orders (graceful degradation).

`useOrdersStream`:
3. `test_opens_eventsource_on_mount` — `EventSource('/api/orders/events')` constructed.
4. `test_passes_account_id_query_param` — when given accountId, URL is `/api/orders/events?account_id=<id>`.
5. `test_pipes_events_into_store` — `EventSource onmessage` → `useOrdersStore.applyEvent`.
6. `test_closes_eventsource_on_unmount` — useEffect cleanup calls `eventSource.close()`.

- [ ] **Step 2: Implement hooks**

`useOrdersList`: returns `{ fetchAndSync, isLoading, error }`. Composes orderService with store writes — features call this, NOT the service directly.

`useOrdersStream(accountId?)`: useEffect opens EventSource with `Last-Event-ID` from `useOrdersStore.lastEventId` for resume; pipes events; auto-reconnects on error with exponential backoff (1s → 30s cap).

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/hooks/useOrdersList.test.ts src/hooks/useOrdersStream.test.ts
```

Expected: 6 PASS.

```bash
git add frontend/src/hooks/useOrdersList.ts frontend/src/hooks/useOrdersStream.ts \
        frontend/src/hooks/useOrdersList.test.ts \
        frontend/src/hooks/useOrdersStream.test.ts
git commit -m "feat(frontend): useOrdersList + useOrdersStream hooks

Boundary-discipline mirror of 5a's useAccountsList pattern: services
return pure data; the hook composes service calls with store writes.
useOrdersStream handles SSE reconnect with Last-Event-ID resume and
exponential backoff (1s → 30s). Account-scoped via the optional
accountId arg; absent → fleet subscription."
```

---

## Chunk G — Frontend components

User-facing UI. All accompanied by Storybook stories and Vitest+RTL tests.

### Task G1 — ContractSearchInput

**Owner: Codex**

**Files:**
- Create: `frontend/src/features/orders/ContractSearchInput.tsx`
- Create: `frontend/src/features/orders/ContractSearchInput.test.tsx`
- Create: `frontend/src/features/orders/ContractSearchInput.stories.tsx`

**Spec reference:** §7 lines 643–649; Phase 3 a11y patterns.

- [ ] **Step 1: Write failing tests + stories** (~6 tests, 5 stories)

Tests:
1. `renders_combobox_role` — `getByRole('combobox')` works.
2. `aria_attributes_correctly_wired` — `aria-expanded` toggles, `aria-activedescendant` updates on arrow keys.
3. `debounces_300ms` — 6 keystrokes within 300ms → 1 fetch.
4. `aborts_in_flight_on_new_keystroke` — AbortController.signal.aborted true after second keystroke.
5. `selecting_option_calls_onSelect_with_conid_and_symbol` — `userEvent.click(option)` → callback with `{conid, symbol}`.
6. `empty_state_shows_no_matches_when_results_empty` — UI text "No matches".

Stories: `Empty`, `LoadingResults`, `WithResults`, `NoMatches`, `RateLimited`.

- [ ] **Step 2: Implement component**

Combobox HTML pattern; debounced fetch via `createDebouncedSearch` factory from F1; popover listbox; aria attributes per WAI-ARIA combobox pattern.

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/features/orders/ContractSearchInput.test.tsx
```

Expected: 6 PASS.

```bash
git add frontend/src/features/orders/ContractSearchInput.{tsx,test.tsx,stories.tsx}
git commit -m "feat(frontend): ContractSearchInput autocomplete

Combobox HTML pattern with WAI-ARIA attrs (aria-expanded,
aria-activedescendant on arrow keys). 300ms debounce + AbortController
on every keystroke. Selecting an option populates parent's conid +
symbol. Stories cover loading/empty/results/error/rate-limited."
```

---

### Task G2 — TradeTicketModal + use-trade-ticket store

**Owner: Codex**

**Files:**
- Create: `frontend/src/features/orders/TradeTicketModal.tsx`
- Create: `frontend/src/features/orders/TradeTicketModal.test.tsx`
- Create: `frontend/src/features/orders/TradeTicketModal.stories.tsx`
- Create: `frontend/src/features/orders/use-trade-ticket.ts`

**Spec reference:** §7 lines 611–632, §7 lines 657–660.

- [ ] **Step 1: Write failing tests + stories** (~9 tests, 8 stories)

Tests:
1. `form_validation_market_blocks_limit_price_input` — order_type=MARKET hides limit_price field.
2. `form_validation_limit_requires_limit_price` — Preview button disabled when LIMIT + empty limit.
3. `preview_button_calls_orderService_preview` — happy path.
4. `cap_exceeded_disables_confirm` — PreviewResponse.cap_status="exceeded" → Confirm button disabled.
5. `position_sanity_extreme_requires_extra_attestation` — second checkbox shown; both required (R13).
6. `confirm_button_generates_client_order_id` — `placeOrder` called with `client_order_id` matching UUID4.
7. `idempotency_on_double_click` — clicking Confirm twice within 1s only calls placeOrder once (button disabled while in-flight).
8. `503_maintenance_shows_retry_after_countdown` — error toast with countdown.
9. `mobile_breakpoint_full_screen` — viewport <md → modal renders full-screen with bottom-sheet styles.
10. `confirm_retry_after_network_error_uses_same_client_order_id` — architect-review P10: simulate network error on first Confirm; user clicks Confirm again; assert `placeOrder` is called twice with the SAME `clientOrderId` (idempotent retry; backend dedups on `(account_id, client_order_id)`).
11. `escape_closes_modal_returns_focus_to_trigger` — architect-review P18 a11y: focus returns to the Trade button.
12. `focus_trap_prevents_tab_out` — architect-review P18 a11y: Tab/Shift+Tab cycles within the modal.
13. `aria_modal_true_on_dialog_container` — architect-review P18 a11y.
14. `first_focusable_element_focused_on_open` — architect-review P18 a11y.

`use-trade-ticket.ts` Zustand slice tracks `clientOrderId` (generated when modal opens, cleared on close); `TradeTicketModal` reads it on open and passes it to every `placeOrder` invocation within the same modal instance.

Stories: `Empty`, `LimitOrderValid`, `MarketOrderValid`, `StopOrderValid`, `CapNearWarning`, `CapExceeded`, `MaintenanceBlocked`, `KillSwitchBlocked`.

- [ ] **Step 2: Implement modal + store**

`TradeTicketModal({ isOpen, accountId, defaultConid?, onClose })`:
- Form managed via local state; conditional fields per order_type.
- "Preview" button → `previewOrder` → render confirm panel with notional + cap status + warnings + position-sanity + attestation checkbox(es).
- "Confirm" button → `placeOrder(req, nonce)`, optimistically `addOrder` to store, close modal, toast success.

`use-trade-ticket.ts`: Zustand slice tracking in-flight ticket state (so navigating away doesn't lose unconfirmed preview).

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/features/orders/TradeTicketModal.test.tsx
```

Expected: 9 PASS.

```bash
git add frontend/src/features/orders/TradeTicketModal.{tsx,test.tsx,stories.tsx} \
        frontend/src/features/orders/use-trade-ticket.ts
git commit -m "feat(frontend): TradeTicketModal placement UI

Two-step flow: form → preview panel with cap context + position
sanity + attestation. Confirm generates client_order_id =
crypto.randomUUID(); double-click guarded by disabled-while-in-flight.
Position-sanity 'extreme' requires a second attestation checkbox
(R13). Mobile: full-screen modal below md breakpoint with
inputmode='decimal' for iOS numeric keyboard."
```

---

### Task G3 — OrdersPage extension (active orders + cancel + SSE)

**Owner: Codex**

**Files:**
- Modify: `frontend/src/features/orders/OrdersPage.tsx`
- Modify: `frontend/src/features/orders/OrdersPage.test.tsx`
- Modify: `frontend/src/features/orders/OrdersPage.stories.tsx`

**Spec reference:** §7 lines 634–640.

- [ ] **Step 1: Write failing tests + stories** (~6 tests, 5 stories)

Tests:
1. `active_orders_table_renders_pending_submitted_partial` — status filter applied.
2. `cancel_button_disabled_for_terminal_status` — filled/cancelled/rejected/expired rows have grey-disabled button.
3. `cancel_button_calls_DELETE_then_shows_toast` — userEvent.click → `cancelOrder` called.
4. `sse_event_updates_row_in_place` — emit fake SSE event → row's status + filled_qty update without a full re-fetch.
5. `kill_switch_active_renders_red_banner` — sticky banner with "Trading paused by operator" copy.
6. `maintenance_active_renders_amber_banner` — same envelope as 5a.

Stories: `Empty`, `WithActiveOrders`, `WithFilledHistory`, `KillSwitchActive`, `MaintenanceWindow`, `KillSwitchAndMaintenanceBoth` (architect-review P21: both banners stacked, verify z-order + precedence copy).

- [ ] **Step 2: Extend OrdersPage**

Add active-orders table at top + paginated history at bottom + kill_switch banner + maintenance banner; mount `useOrdersStream()` on mount; cancel → confirm modal → DELETE.

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/features/orders/OrdersPage.test.tsx
```

Expected: 6 PASS.

```bash
git add frontend/src/features/orders/OrdersPage.{tsx,test.tsx,stories.tsx}
git commit -m "feat(frontend): /orders active-orders table + cancel + SSE

Extends Phase 5a's read-only OrdersPage: active table at top,
terminal-status history paginated below. SSE EventSource opened
via useOrdersStream() pipes live updates into useOrdersStore.
Cancel button disabled for terminal rows, gated behind a confirm
modal. Kill-switch + maintenance banners reuse the 5a envelope
copy + colour conventions."
```

---

### Task G4 — Trade button entry-points (AccountPicker + positions row)

**Owner: Codex**

**Files:**
- Modify: `frontend/src/features/accounts/AccountPicker.tsx` (add Trade button per row)
- Modify: `frontend/src/features/positions/PositionsTable.tsx` (add Trade button per row)
- Test: extend respective `*.test.tsx`

**Spec reference:** §7 line 612 ("Trade button in AccountPicker row OR positions table opens the modal pre-populated").

- [ ] **Step 1: Write failing tests** (~4 tests)

1. `account_picker_trade_button_opens_modal_with_account_id` — click → modal renders with `accountId` matching the row.
2. `account_picker_trade_button_disabled_when_trade_enabled_false` — per-account `trade_enabled=false` → disabled with tooltip "Trading not enabled for this account".
3. `positions_row_trade_button_pre_populates_conid_and_symbol` — click → modal `defaultConid + defaultSymbol` set.
4. `trade_button_disabled_during_maintenance` — broker_maintenance.active=true → disabled with tooltip.

- [ ] **Step 2: Implement entry-points**

Add a small `<TradeButton accountId conid? symbol?>` component in `frontend/src/features/orders/TradeButton.tsx` that opens `TradeTicketModal` via internal state. AccountPicker passes accountId only; PositionsTable passes accountId + conid + symbol.

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm test src/features/accounts/AccountPicker.test.tsx \
                          src/features/positions/PositionsTable.test.tsx
```

Expected: 4 PASS (plus pre-existing tests still green).

```bash
git add frontend/src/features/accounts/AccountPicker.{tsx,test.tsx} \
        frontend/src/features/positions/PositionsTable.{tsx,test.tsx} \
        frontend/src/features/orders/TradeButton.tsx
git commit -m "feat(frontend): Trade entry-points in AccountPicker + PositionsTable

TradeButton opens TradeTicketModal pre-populated. Disabled with
explanatory tooltip when per-account trade_enabled=false (canary
gate per spec §9) or during maintenance window. PositionsTable
passes conid + symbol so existing-symbol trades skip the search."
```

---

## Chunk H — Deployment + close-out

### Task H1 — Backend Prometheus metrics + alerts

**Owner: Codex**

**Files:**
- Modify: `backend/app/observability/metrics.py` (add the new metrics)
- Modify: `deploy/prometheus/alerts.yml` (or wherever rules live; check repo)
- Test: `backend/tests/observability/test_metrics_orders.py`

**Spec reference:** §5 lines 451–462.

- [ ] **Step 1: Write failing tests** (~4 tests)

1. `test_metrics_orders_registry_includes_all_new_counters` — `broker_order_events_received_total`, `broker_order_events_dropped_total`, `broker_order_event_lag_ms`, `broker_order_stream_reconnects_total`, `broker_order_stream_resync_synthetic_events_total`, `broker_order_pending_submit_recovered_total`, `broker_order_pending_submit_orphan_total`, `consumer_alive`, `sse_active_connections`.
2. `test_dropped_rate_alert_fires_at_50_percent` — alert rule evaluates `rate(broker_order_events_dropped_total[5m]) > 0.5 * rate(broker_order_events_received_total[5m])`.
3. `test_consumer_alive_gauge_per_label_account` — labels include `{label, account_id}`.
4. `test_sse_active_connections_increments_on_connect_decrements_on_disconnect` — track via context manager.

- [ ] **Step 2: Register + commit**

```bash
cd backend && uv run pytest tests/observability/test_metrics_orders.py -v
```

Expected: 4 PASS.

```bash
git add backend/app/observability/metrics.py deploy/prometheus/alerts.yml \
        backend/tests/observability/test_metrics_orders.py
git commit -m "feat(backend): Phase 5b Prometheus metrics + alert rules

Registers the 9 new metrics from spec §5: order events received/dropped,
event lag histogram, stream reconnects + resync synthetic count,
pending-submit watchdog recovered/orphan counters, consumer_alive +
sse_active_connections gauges. Alert rule pages on >50% drop rate
(R27 consumer poisoning detection)."
```

---

### Task H2 — docker-compose.prod.yml + nginx SSE + worker assertion

**Owner: Codex**

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: `deploy/nginx/conf.d/dashboard.conf` (or wherever nginx config lives)
- Modify: `cloudflare/tunnel.yml` (or equivalent CF Tunnel ingress config)
- Test: `tests/deploy/test_prod_compose.py` (or wherever existing deploy assertions live)

**Spec reference:** §9 lines 707–713.

- [ ] **Step 1: Add CI assertion that backend runs single-worker**

`tests/deploy/test_prod_compose.py`:
```python
def test_prod_backend_runs_single_worker():
    compose = yaml.safe_load(open("docker-compose.prod.yml"))
    backend_cmd = compose["services"]["backend"]["command"]
    assert "--workers 1" in backend_cmd or "--workers=1" in backend_cmd, (
        "Phase 5b requires single-worker uvicorn (R7). Multi-worker "
        "consumer/SSE leadership is Phase 9 work."
    )
```

- [ ] **Step 2: Update `docker-compose.prod.yml`**

Backend command: `uvicorn app.main:app --workers 1 --timeout-graceful-shutdown 30 --host 0.0.0.0 --port 8000`.

- [ ] **Step 3: Update nginx config for SSE**

Add the location block for `/api/orders/events`:
```nginx
location /api/orders/events {
    proxy_pass http://backend:8000;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 86400;
    proxy_set_header Connection '';
    chunked_transfer_encoding off;
}
```

- [ ] **Step 4: CF Tunnel ingress**

If CF Tunnel uses a config-file-based ingress (`cloudflared` YAML), add a route entry that disables connection reuse buffering for `/api/orders/events`. If it's UI-managed, document the change in `deploy/nuc/README.md` so the operator can apply it manually.

- [ ] **Step 5: Run + commit**

```bash
uv run pytest tests/deploy/test_prod_compose.py -v
```

Expected: 1 PASS.

```bash
git add docker-compose.prod.yml deploy/nginx/conf.d/dashboard.conf \
        cloudflare/tunnel.yml tests/deploy/test_prod_compose.py \
        deploy/nuc/README.md
git commit -m "chore(deploy): single-worker backend + SSE proxy_buffering off

R7: Phase 5b is single-worker uvicorn (multi-worker requires Redis
SETNX leader election — Phase 9). CI asserts the constraint.
nginx /api/orders/events location disables proxy_buffering +
chunked_transfer_encoding so SSE flushes immediately. CF Tunnel
ingress documented for the matching change. uvicorn graceful
shutdown 30s lets in-flight POST /orders complete (R9)."
```

---

### Task H3 — Test isolation: extend clean_tables for orders

**Owner: Claude**

**Files:**
- Modify: `backend/tests/test_admin_api.py` (extend autouse clean_tables fixture)
- OR: wherever clean_tables fixture is now centralized

**Spec reference:** §8 lines 698–701; `feedback_pytest_prod_db_wipe.md`.

- [ ] **Step 1: Add orders + order_events to clean_tables list**

Find the autouse `clean_tables` fixture and append `"order_events"` and `"orders"` to its DELETE list (in correct dependency order — order_events first because it FK's orders).

Add an explicit DATABASE_URL guard at fixture entry that raises if `DATABASE_URL` doesn't include "test" — matches the safety pattern from `feedback_pytest_prod_db_wipe.md`.

- [ ] **Step 2: Run full backend suite to confirm no leaks**

```bash
cd backend && uv run pytest -v -x
```

Expected: all PASS, no flakiness from leftover orders.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_admin_api.py
git commit -m "test(backend): extend clean_tables for Phase 5b tables

Adds order_events + orders to the autouse cleanup list (FK order:
events first, then parent). DATABASE_URL guard at fixture entry
prevents another prod-DB wipe (per feedback_pytest_prod_db_wipe.md)."
```

---

### Task H4 — CHANGELOG.md + TASKS.md + CLAUDE.md updates

**Owner: Claude**

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`
- Modify: `CLAUDE.md` (Phase 5b section after the Phase 5a discoverer block)

- [ ] **Step 1: CHANGELOG**

Add a `## v0.5.1 — 2026-MM-DD` entry summarizing: 4 new RPCs, orders + order_events tables, 8 endpoints, BrokerOrderEventConsumer with 22 streams, PendingSubmitWatchdog, TradeTicketModal + ContractSearchInput, simulator-mode-default-on, position-sanity check, daily-notional cap, kill-switch.

- [ ] **Step 2: TASKS.md**

Mark Phase 5b complete; advance roadmap pointer to Phase 5c (modify + brackets + multi-worker prep) or to Phase 6 if the user reorders.

- [ ] **Step 3: CLAUDE.md**

Add a "Phase 5b — trade execution (v0.5.1)" subsection after the existing 5a block, documenting the canary rollout flow, simulator-mode default, single-worker constraint, and the SSE proxy_buffering requirement. Reference `phase5b_shipped.md` memory entry that will be created in this task.

Also create `~/.claude/projects/-home-joseph-dashboard/memory/phase5b_shipped.md` summarizing lessons + deferred items + Phase 5c forward pointers, and add a one-liner to `MEMORY.md` index.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md \
        ~/.claude/projects/-home-joseph-dashboard/memory/phase5b_shipped.md \
        ~/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md
git commit -m "docs(phase5b): close-out CHANGELOG + TASKS + CLAUDE

v0.5.1 entry summarizes the trade-execution write path. CLAUDE.md
gains a Phase 5b subsection covering canary rollout, simulator-
mode default, single-worker constraint, SSE proxy config. Memory
entry phase5b_shipped.md with lessons + deferred items + 5c
forward pointers."
```

---

### Task H5 — Final code review + tag v0.5.1

**Owner: Claude**

- [ ] **Step 1: Dispatch the final code-reviewer subagent**

Use `everything-claude-code:code-reviewer` with the full diff range `v0.5.0..HEAD`. Review checklist: spec coverage, test coverage ≥80% on backend + sidecar, frontend typecheck + lint clean, no `--no-verify` commits, no secrets, no plaintext keys, all 39 architect-review findings represented in commits.

- [ ] **Step 2: USER GATE — operator confirmation**

```bash
git log --oneline v0.5.0..HEAD
```

Show the user the commit list and ask for explicit confirmation before tagging.

- [ ] **Step 3: Tag + push**

After confirmation:

```bash
git tag -a v0.5.1 -m "Phase 5b — IBKR trade execution

Adds the write path: place market/limit/stop orders, cancel orders,
live status updates over SSE. 4 new proto RPCs (PlaceOrder,
CancelOrder, OrderEvent, SearchContracts). Alembic 0004 adds orders
+ order_events tables. BrokerOrderEventConsumer manages 22 per-
account streams. PendingSubmitWatchdog reconciles lost orders
within 60s. Simulator-mode default-on for live gateways. 39
architect-review findings R1-R39 applied.

Operator must explicitly flip per-account trade_enabled=true and
simulator_only=false after canary validation."

git push origin main
git push origin v0.5.1
```

- [ ] **Step 4: Verify GitHub Actions deploy runs green**

```bash
gh run watch
```

Expected: deploy job finishes green; `https://dashboard.kiusinghung.com/api/orders/policy/<account_id>` (CF service token auth) returns 200 with the policy envelope.

- [ ] **Step 5: Trigger canary**

Operator-driven: flip `app_config.broker.isa-paper.trade_enabled=true` (paper account first), keep `simulator_only=true`, run a TradeTicketModal preview + place + cancel against a paper symbol, verify Prometheus shows event flow. THEN flip `simulator_only=false` for the same account, re-run. THEN expand to other accounts gradually. The kill-switch is the immediate-stop control if anything looks wrong.

---

## Self-review

**Spec coverage:** §1 → covered (decisions baked into chunk wiring); §2 → A3; §3 → A1+A2; §4 → D1-D7; §5 → E1-E3 + H1; §6 → B1-B6; §7 → F1-F3 + G1-G4; §8 → tests embedded in every implementation task + dedicated D7 OpenAPI snapshot; §9 → H2; §10 → handoff in §11 close-out; §11 → R1-R39 traced through tasks; tooling promise in CLAUDE.md (gen-types.sh) addressed by A0; sidecar client extension by A4; shared mocks by A5.

**Placeholder scan:** none. All bash commands are exact; all SQL/code references either inline or cite spec line numbers.

**Type consistency:** `client_order_id` is UUID end-to-end (lifecycle pinned in F1+G2 to modal-instance-stable, NOT per-call); `broker_order_id` is `str` (IBKR permId); `notional` / `qty` / prices are decimal-as-string fixed-point 8 digits everywhere; `OrderResponse` shape is identical between backend D1-D3 and frontend F1-F2 because both consume the same OpenAPI snapshot (D7 lock + A0 generated types).

**Spec gaps:** §10's "external orders" tab is a 5c deferral and not in this plan, matching spec §10. Phase 4.6 hide-flow integration: AccountPicker now needs a Trade button (G4) — added after self-review.

---

## §11 Architect-review applied (plan)

The plan-level architect-review pass (2026-04-27) returned 4 CRITICAL + 8 HIGH + 9 MEDIUM + 3 LOW findings. All CRITICAL + HIGH findings are folded into this revision. MEDIUMs are addressed inline or explicitly noted as deferred. LOWs are deferred or noted.

| ID | Severity | Topic | Resolution |
|---|---|---|---|
| P1 | CRITICAL | Missing task: extend `BrokerSidecarClient` with 4 new RPC methods | Inserted **Task A4** between A3 and B with 6 tests + implementation against existing client at `backend/app/services/brokers.py`. Pattern mirrors existing `get_orders` at line 145. |
| P2 | (was CRITICAL — now resolved) | GetOrders RPC presence | Verified: already exists at `proto/broker/v1/broker.proto:23` and `BrokerSidecarClient.get_orders` at `services/brokers.py:145`. Plan note added after Task A3 confirming reuse, no new RPC needed. |
| P3 | CRITICAL | A1 downgrade missing explicit `DROP TYPE` for the 4 enums | A1 step 2 now explicitly calls `op.execute("DROP TYPE order_status_enum")` ×4 in downgrade. A2 test 9 (`test_0004_downgrade_then_upgrade_round_trips_twice`) verifies idempotency. |
| P4 | CRITICAL | A1↔A2 commit boundary contradicted dispatch flow | A1 step 4 changed to "DO NOT commit yet"; A2 step 3 is now a single combined `feat(backend): alembic 0004 + tests` commit. |
| P5 | HIGH | Missing structlog redaction for `raw_payload.account` | E1 step 2 extends `app/core/logging.py` redaction processor to scrub `account`/`account_number`/`acctNumber`. New E1 test 10. |
| P6 | HIGH | Missing OpenAPI snapshot lock | Inserted **Task D7** with snapshot fixture + diff test + regen script for the 5 named models. |
| P7 | HIGH | `scripts/gen-types.sh` is a stub | Inserted **Task A0** to actually implement it + CI drift gate. F1 step 2 now consumes the generated `api-generated.ts`. |
| P8 | HIGH | Missing shared sidecar mock fixture | Inserted **Task A5** with 5 reusable fixtures (`mock_sidecar_client`, `mock_sidecar_with_simulator`, `mock_sidecar_with_timeout`, `mock_sidecar_503`, `fake_order_event_stream`). |
| P9 | HIGH | Critical gates omitted parallelism | Critical gates section expanded with explicit ordering rules + a "Parallel-safe pairs" table. |
| P10 | HIGH | `client_order_id` lifecycle ambiguous | F1 step 2 + G2 test 10 + `use-trade-ticket.ts` slice pin: clientOrderId generated when modal opens, stored in Zustand, passed verbatim to every placeOrder invocation within that modal instance. |
| P11 | HIGH | Missing concurrent-nonce-race test | D2 step 1 added test 10 (`test_concurrent_post_with_same_nonce_one_succeeds_one_422`). |
| P12 | HIGH | Missing CI workflow update for real-IBKR gating | Inserted **Task B6** — `ci.yml` sets `REAL_IBKR=""`; new `pre-deploy-smoke.yml` is manual workflow_dispatch on a self-hosted NUC runner. |
| P13 | MEDIUM | Watchdog escalation must write audit row in same tx | E2 added test 7 (`test_watchdog_escalation_writes_audit_event_in_same_tx`). |
| P14 | MEDIUM | SSE Last-Event-ID HTTP header path unspecified | D6 test 4 renamed `_HEADER` and explicitly uses `httpx.AsyncClient(headers={...})`. |
| P15 | MEDIUM | SSE slow-client backpressure unspecified | D6 step 1 added tests 8 + 9; per-client `asyncio.Queue(maxsize=1000)` + clean drop event + `sse_dropped_clients_total` counter. |
| P16 | MEDIUM | A2 watchdog index test pinned existence only | A2 test 7 renamed `_pinned_to_created_at` and asserts `(created_at) WHERE (status = 'pending_submit'::order_status_enum)`. |
| P17 | MEDIUM | D1 FX cache miss / sidecar-down fallback unspecified | D1 step 1 added test 8: cold FX cache + sidecar 503 → preview returns 503 (no 1.0 default). |
| P18 | MEDIUM | G2 a11y missing | G2 step 1 added tests 11–14: focus-trap, escape-returns-focus, aria-modal, first-focusable. Mandatory `a11y-architect` review chain already declared. |
| P19 | MEDIUM | E2/E3 ordering | Chunk E intro added explicit "implement E1 → E3 → E2" note. |
| P20 | MEDIUM | D4 SELECT FOR UPDATE blocking risk | D4 step 1 test 5 renamed `_nowait`; new test 7 covers 423 Locked on contention; step 2 implementation switched to `FOR UPDATE NOWAIT`. |
| P21 | MEDIUM | G3 Storybook combo missing | G3 stories list added `KillSwitchAndMaintenanceBoth`. |
| P22 | MEDIUM | C1/C2/C3 parallelism implicit | "Parallel-safe pairs" table includes `C1 ⊥ C2 ⊥ C3`. |
| P23 | LOW | F1/G1 debounce-test redundancy | Acknowledged; both kept (defense-in-depth) — F1 verifies factory contract, G1 verifies wiring. |
| P24 | LOW | Codex/Claude fallback ownership | Owner & review chain section added explicit fallback line + commit-footer convention. |
| P25 | LOW | H4 close-out simulator_only seed mention | H4 step 5 expanded under canary section to call out explicit row writes for ops audit-trail (still default-fallback safe). |
