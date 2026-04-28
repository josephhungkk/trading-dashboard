# Phase 5b.1 — Canary Hotfix Pack — Design Spec

**Date:** 2026-04-28
**Phase:** 5b.1 (point release between 5b and 5c)
**Tag at end:** `v0.5.3`
**Estimated duration:** ~7 working days (target ship 2026-05-05 ± 2 days)
**Prerequisite:** v0.5.2 shipped + first paper canary verified end-to-end
**Successor:** Phase 5c (Modify orders + Brackets + Fills history; multi-worker stays Phase 9)

---

## 1. Goal

Plug four production gaps surfaced by the v0.5.2 paper canary so Phase 5b feature work is unblocked and CI catches regressions of this class going forward. Strictly a hardening release — no new user-facing features.

### In scope

| ID | Item | Source bug |
|---|---|---|
| **A2** | Alembic 0005: `positions` table + `BrokerDiscoverer._discover_positions` per-account fan-out + upsert/delete delta | `_position_qty` returned 0 unconditionally because `positions` table never existed |
| **B1** | Sidecar `CancelOrder` handler emits synthetic `cancelled` OrderEvent for `SIM-…` orders | Two prod orders (`019dd33b-…`, `019dd33e-…`) sat at `submitted` after canary cancel because simulator didn't echo cancel through the OrderEvent stream |
| **C2** | Sidecar `ibkr_sidecar.py` startup: brief BASE-tag round before `reqAccountSummary` subscribes | All 22 prod accounts had `currency_base=''` because BASE tag is unreachable concurrent with reqAccountSummary; backend's `last_nlv_currency` fallback (shipped in `9910e3b`) is defensive but not principled |
| **D3** | Layered E2E tests: D1 mock chain on every PR + D2 real-IBKR chain nightly cron | v0.5.1 had complete unit coverage but five distinct production-blocking bugs shipped because no test exercised the full chain |

### Out of scope (deferred to Phase 5c proper or later)

- Modify orders, brackets/OCO, fills history endpoint, multi-worker uvicorn (all Phase 5c)
- Admin force-cancel endpoint (rejected as foot-gun — operator one-liner is documented in `feedback_post_deploy_broker_recovery.md`)
- Stale-row watchdog escalation (`submitted > 24h` → `expired`) — overkill for paper-only feature with zero financial impact
- Position-history table or P&L computation (Phase 5c+ or later)
- Backend resolver fallback removal (`last_nlv_currency` → `currency_base`) — defence-in-depth keeps it in 5b.1

### Why now

- Production canary already exposed five bugs that unit tests missed; ship the fixes before they accumulate.
- Phase 5c features (modify, brackets) will compound the gaps if built on top of broken position-sanity / stranded SIM cancels.
- Integration test (D3) is the structural fix for the "tests pass, prod breaks" pattern that defined v0.5.1 → v0.5.2.

---

## 2. Architecture (5 components)

### 2.1 `positions` table (Alembic 0005)

```sql
CREATE TABLE positions (
  account_id    UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  conid         VARCHAR(32) NOT NULL,
  qty           NUMERIC(20,8) NOT NULL,        -- signed: +long, -short
  avg_cost      NUMERIC(20,8) NOT NULL,        -- per-share, in `currency`
  currency      VARCHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_id, conid)
);
CREATE INDEX positions_account_id_idx ON positions(account_id);
```

- **Composite PK** `(account_id, conid)` — natural key, no synthetic UUID.
- **No soft-delete:** zero-qty broker positions are deleted by the upsert/delete delta; mirrors broker truth.
- **`avg_cost` per-share:** frontend converts for futures/options multipliers later.
- **Currency CHECK** matches `last_nlv_currency` regex in `broker_accounts` for consistency.
- **`ON DELETE CASCADE`** so hard-deleting a `broker_accounts` row also removes its positions.

### 2.2 `BrokerDiscoverer._discover_positions` — new method

Runs after the existing NLV fan-out in `_discover_once`. Reuses the per-`(label, account_number)` `_AccountStream` list:

```python
async def _discover_positions(self, streams: list[_AccountStream]) -> None:
    """Fan out GetPositions per account, upsert positions, delete vanished rows.

    Mirrors the Phase 5a NLV fan-out pattern. Per-account savepoint isolates
    NUMERIC(20,8) overflow on exotic markets so one bad position doesn't
    break the outer transaction.
    """
    started = time.perf_counter()
    calls = [
        asyncio.wait_for(
            self._registry.get_client(stream.label).get_positions(stream.account_number),
            timeout=10.0,
        )
        for stream in streams
    ]
    results = await asyncio.gather(*calls, return_exceptions=True)

    async with self._session_factory() as session, session.begin():
        for stream, result in zip(streams, results, strict=True):
            if isinstance(result, BaseException):
                # RPC failure: leave positions table untouched for this account
                log.warning(
                    "broker_discover_positions_rpc_failed",
                    label=stream.label,
                    account_id=stream.account_id,
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
                        account_id=stream.account_id,
                    )
                    continue
                raise

    metrics.broker_discover_positions_update_duration_ms.observe(
        (time.perf_counter() - started) * 1000.0
    )
```

`_upsert_positions` SQL (single round-trip per account, atomic upsert + delta delete):

```sql
WITH upserted AS (
  INSERT INTO positions (account_id, conid, qty, avg_cost, currency, updated_at)
  SELECT :account_id, conid, qty, avg_cost, currency, now()
    FROM jsonb_to_recordset(:rows::jsonb)
      AS x(conid varchar, qty numeric, avg_cost numeric, currency varchar)
  ON CONFLICT (account_id, conid) DO UPDATE
    SET qty = EXCLUDED.qty,
        avg_cost = EXCLUDED.avg_cost,
        currency = EXCLUDED.currency,
        updated_at = now()
  RETURNING conid
)
DELETE FROM positions
 WHERE account_id = :account_id
   AND conid NOT IN (SELECT conid FROM upserted);
```

**Resurrect-from-soft-delete clears positions** — the existing upsert path's `ON CONFLICT DO UPDATE` for `broker_accounts` gets a parallel `DELETE FROM positions WHERE account_id = …` when `deleted_at IS NOT NULL` (resurrect branch). Mirrors how `last_nlv*` is nulled on resurrect.

### 2.3 Sidecar `CancelOrder` — synthetic SIM cancel echo

In `sidecar/handlers.py:cancel_order`, after the existing simulator path:

```python
async def cancel_order(self, request: CancelOrderRequest) -> CancelOrderResponse:
    broker_order_id = request.broker_order_id

    if broker_order_id.startswith("SIM-"):
        # Simulator path: cancel locally + emit synthetic cancelled event
        # so the backend's OrderEventConsumer transitions the order row.
        # Real-broker cancels arrive through the OrderEvent stream
        # naturally; this branch mirrors that contract for SIM orders.
        if broker_order_id not in self._sim_orders:
            return CancelOrderResponse(status="not_found")
        self._sim_orders.pop(broker_order_id)
        await self._order_event_queue.put(broker_pb2.OrderEventMessage(
            broker_order_id=broker_order_id,
            client_order_id=…,  # carried from sim order map
            status="cancelled",
            filled_qty="0",
            avg_fill_price="0",
            broker_event_at=Timestamp.from_datetime(datetime.now(UTC)),
            raw_payload=json.dumps({"sim_cancel_echo": True}),
        ))
        metrics.broker_sim_cancel_echo_total.labels(label=self.label).inc()
        return CancelOrderResponse(status="cancelled")

    # Real broker path: ib_async cancelOrder, real cancel event arrives via stream
    …
```

Idempotent: re-cancelling an already-cancelled SIM order returns `status="not_found"` and does NOT emit a duplicate event.

### 2.4 Sidecar `ibkr_sidecar.py` startup — BASE round before reqAccountSummary

Replace lines 210-222 of `ibkr_sidecar.py`:

```python
await ib.connectAsync("127.0.0.1", args.gateway_port, clientId=client_id, timeout=30)
log.info("ibkr_connected", clientId=client_id, gateway_port=args.gateway_port)

await asyncio.sleep(0.5)
accounts = list(ib.managedAccounts())

# BASE-tag round: reqAccountUpdates(True, account) populates ib.accountValues()
# with the BASE tag (the account's settlement currency). reqAccountSummary alone
# cannot fetch BASE — IBKR's accountSummary RPC excludes it.
#
# Concurrency constraint: IBKR's API permits one active reqAccountUpdates
# subscription at a time. We subscribe each managed account in turn, wait
# briefly for the BASE tag, then unsubscribe BEFORE starting reqAccountSummary
# (which we DO want running for the lifetime of the sidecar).
#
# accountValues() retains the BASE tag after unsubscribe — verified via
# tests/test_ibkr_sidecar_base_round.py.
log.info("base_round_starting", accounts=len(accounts))
for acct in accounts:
    ib.reqAccountUpdates(True, acct)
    await asyncio.sleep(1.5)  # BASE arrives within ~1s; allow margin
    ib.reqAccountUpdates(False, acct)
    await asyncio.sleep(0.2)  # let unsubscribe settle
log.info("base_round_done", elapsed_s=…)

await ib.reqAccountSummaryAsync()
await asyncio.sleep(0.5)
```

**Sequential per-account** (not parallel) — IBKR's "one active subscription at a time" rule means parallel-subscribe-all may hang. Sequential adds ~1.7s × N accounts at startup (10s for 6-account isa-paper, ~37s for 22-account fleet). Acceptable one-time cost.

### 2.5 CI workflows — `e2e-mock` + nightly real-IBKR E2E

**New: `.github/workflows/e2e-mock.yml`** (runs on every push to main + every PR):

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
    steps:
      - uses: actions/checkout@v4
      - uses: bufbuild/buf-setup-action@v1
        with: { github_token: ${{ secrets.GITHUB_TOKEN }} }
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

**Existing `real-ibkr.yml` extended** with new `e2e-trade` job after the existing read-only smoke job:

```yaml
e2e-trade:
  needs: smoke
  if: ${{ github.event.schedule || inputs.run_e2e == 'true' }}
  runs-on: self-hosted-nuc  # NUC runner with paper gateway access
  steps:
    - uses: actions/checkout@v4
    - name: Pre-flight maintenance check
      run: |
        # Skip if maintenance window active
        backend/.venv/bin/python -c "
        from datetime import datetime, UTC
        from app.services.ibkr_maintenance import compute_broker_maintenance
        m = compute_broker_maintenance(datetime.now(UTC))
        if m.active: raise SystemExit(78)  # 78 = neutral exit (workflow continues)
        "
    - name: E2E trade chain (real paper)
      env:
        CI_USE_REAL_IBKR: '1'
      run: |
        cd sidecar
        uv run pytest tests/test_real_ibkr_e2e_trade.py -v -m real_ibkr
```

Cron: `0 12 * * *` (12:00 UTC = clear of all IBKR maintenance windows). Manual dispatch via `workflow_dispatch.inputs.run_e2e=true`.

---

## 3. Risks + edge cases (decisions applied)

| ID | Risk | Decision |
|---|---|---|
| R1 | Resurrect-from-soft-delete keeps stale positions | Add `DELETE FROM positions WHERE account_id = …` to the resurrect branch of `broker_accounts` upsert (mirrors `last_nlv*` null-out) |
| R2 | Empty positions list vs RPC failure | `gather(return_exceptions=True)`: only successful empty responses reach upsert/delete; RPC failures skip the account, leaving prior positions intact |
| R3 | NUMERIC(20,8) overflow on exotic markets | Per-account `session.begin_nested()` savepoint; sqlstate `22003` → `broker_discover_positions_overflow_total{label}++` + skip that account |
| R4 | BASE-round delays sidecar bind by ~10-37s | gRPC server isn't bound during the round; callers get connection-refused (NOT UNAVAILABLE); backend reconnect-and-resync handles it; document the startup window in `phase4_sidecar_topology.md` |
| R5 | Parallel `reqAccountUpdates(True, account)` may hang | **Sequential per-account** is the design (decided). 1.5s subscribe + 0.2s unsubscribe per account |
| R6 | SIM cancel echo races with auto-fill | Terminal-status-sticky CASE in `_process_event` already protects: synthetic `cancelled` arriving after real `filled` is rejected (no-op audit row) |
| R7 | e2e-mock CI port races | Use `httpx.ASGITransport(app=app)` for backend (no real port); sidecar mock binds `127.0.0.1:0` (kernel-allocated free port); existing pattern in `tests/fixtures/sidecar_servicer.py` |
| R8 | Nightly real-IBKR fires during maintenance | Cron `0 12 * * *` (12:00 UTC, well clear of all windows); pre-flight asserts `compute_broker_maintenance(now).active == False` and skips with neutral exit 78 if not |
| R9 | First-tick lag before positions populated | Acceptable 30s window post-bootstrap where sanity returns "ok" (qty=0) — document |
| R10 | CI Postgres race with discoverer migration | Test fixture runs `alembic upgrade head` synchronously before backend boots (existing `_apply_migrations` pattern) |

---

## 4. Testing strategy (D3 layered)

### Unit tests (22 new)

| File | Count | Coverage |
|---|---|---|
| `backend/tests/migrations/test_0005_positions.py` | 5 | Schema lands, FK + composite PK + currency CHECK + NUMERIC overflow rejection + ON DELETE CASCADE |
| `backend/tests/services/test_brokers_discover_positions.py` | 8 | Fan-out succeeds, savepoint isolates overflow, RPC failure leaves rows untouched, upsert delta deletes vanished, resurrect clears positions, gather timeout, currency CHECK, metrics emitted |
| `backend/tests/services/test_orders_service_positions.py` | 2 | `_position_qty` reads real values when populated, returns 0 when account has no rows (table-exists path) |
| `sidecar/tests/test_handlers_cancel_sim_echo.py` | 4 | SIM cancel emits synthetic event; real cancel does NOT emit; idempotent re-cancel is no-op; payload carries `sim_cancel_echo: true` |
| `sidecar/tests/test_ibkr_sidecar_base_round.py` | 3 | Startup subscribes/unsubscribes BASE round before reqAccountSummary; BASE tag retained in accountValues; sequential ordering verified |

### Integration tests (D3)

**`backend/tests/integration/test_e2e_trade_chain.py`** (every PR via `e2e-mock.yml`):

7-step chain with ~12 assertions (~30s wall):
1. `POST /api/admin/config` → flip `broker.isa-paper.trade_enabled=true`, verify policy reflects flip
2. `POST /api/orders/preview` → assert 200 + nonce + `notional_currency` matches account base
3. `POST /api/orders` w/ nonce + UUIDv7 client_order_id → assert 200 + `submitted` row
4. Sidecar mock pushes placement OrderEvent → `orders.broker_order_id` populated
5. `DELETE /api/orders/{id}` → assert 202
6. SIM cancel echo arrives → `orders.status='cancelled'` within 5s (proves B1)
7. Revert `trade_enabled=false`

Catches all five v0.5.1 bugs deterministically. Sidecar mock extended in `tests/fixtures/sidecar_servicer.py` to handle PlaceOrder/CancelOrder/OrderEvent.

**`sidecar/tests/test_real_ibkr_e2e_trade.py`** (nightly via `real-ibkr.yml`):

Same 7 steps against real paper gateway 4002. `@pytest.mark.real_ibkr` gated. Pre-flight asserts maintenance not active. Idempotent (UUIDv7 client_order_id dedup). Cleanup in finally block: cancel any leftover orders + revert `trade_enabled=false`.

### Coverage gates

- Backend: `--cov=app --cov-fail-under=80`
- Sidecar: `--cov=sidecar --cov-fail-under=80`

---

## 5. Rollout + close-out

### Pre-deploy checklist

1. CI green on `main` (e2e-mock workflow passing on every PR)
2. **Architect review** of this spec applied (CLAUDE.md Step 3 — invoke `ARCHITECT-REVIEW` skill, log findings table inline before plan)
3. Plan generated via `superpowers:writing-plans`, reviewed
4. Per-task subagent-driven implementation; per-commit review chain:
   - implementer → spec-compliance reviewer → code-quality reviewer
   - `python-reviewer` (backend/sidecar)
   - `database-reviewer` (Alembic 0005, _upsert_positions SQL)
   - `silent-failure-hunter` (async fan-out paths in _discover_positions)
   - `security-reviewer` (no auth/secrets touched, but the integration test exercises admin endpoint — confirm no token-leak in CI logs)

### Deploy sequence (mirrors `feedback_post_deploy_broker_recovery.md`)

1. Update CHANGELOG `[0.5.3]` block + TASKS.md checkbox flip + CLAUDE.md Phase 5b extension → commit `docs(phase5b1): close out v0.5.3`
2. `git tag -a v0.5.3` + `git push --follow-tags` → triggers Deploy workflow
3. Backend image rebuild + push; new Alembic 0005 runs in entrypoint pre-uvicorn
4. **NUC sidecar redeploy** (operator-side):
   - `bash deploy/nuc/sync-to-windows.sh` (WSL → C:\dashboard)
   - `cd C:\dashboard\sidecar; .\scripts\build-windows.ps1 -OutDir dist-staging`
   - elevated kill (`gsudo Stop-Process -Name ibkr-sidecar -Force`)
   - `Move-Item C:\dashboard\sidecar\dist C:\dashboard\sidecar\dist.bak`
   - `Move-Item C:\dashboard\sidecar\dist-staging C:\dashboard\sidecar\dist`
   - `schtasks /Run /TN IBKRSidecar-{isa-live,isa-paper,normal-live,normal-paper}`
   - SSH `docker compose -f docker-compose.prod.yml restart backend nginx` on VPS
5. Verify:
   - `/health` ok
   - `/api/accounts` returns 22 accounts with non-empty `currency_base` (proves C2 wired)
   - `/api/contracts/search?q=AAPL` 200
   - `positions` table populated within 30s of bootstrap (verify via operator probe below)
   - Zero `OrderEvent` stream errors in backend logs

### Observability (extends Phase 5b alerts.yml)

**New Prometheus metrics:**
- `broker_discover_positions_update_duration_ms{label}` histogram (buckets 10/25/50/100/250/500/1000/2500/5000 ms; p99 expected ~150 ms at 22 accounts)
- `broker_discover_positions_overflow_total{label}` counter
- `broker_sim_cancel_echo_total{label}` counter

**New alert rules:**
- `BrokerDiscoverPositionsP99HighWarning` — p99 > 1000ms over 5min
- `BrokerSimCancelEchoMismatch` — `rate(broker_sim_cancel_echo_total[5m])` diverges from SIM cancel HTTP rate by >10% over 10min

### Operator probe (post-deploy verification)

```bash
curl -sf https://dashboard.kiusinghung.com/api/accounts \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
populated = sum(1 for a in d['accounts'] if a.get('position_count', 0) > 0)
total = len(d['accounts'])
print(f'positions_populated_accounts = {populated}/{total}')
print(f'currency_base_populated = {sum(1 for a in d[\"accounts\"] if a.get(\"currency_base\"))}/{total}')
"
```

`AccountResponse.position_count` is added in 5b.1 alongside the discoverer extension (one extra column in the existing `JOIN positions` aggregate).

### Close-out artifacts

- **CHANGELOG.md** `[0.5.3] — 2026-04-29` block (or shipped date) with the four hotfix items grouped under "Fixed — Phase 5b.1 canary hotfix pack"
- **TASKS.md:** Phase 5b.1 chunk checkbox flips to `[x]`; new entry under Phase 5c marking the four items removed (no longer canary gaps)
- **CLAUDE.md:** extend "Phase 5b — IBKR trade execution (v0.5.1)" subsection with positions discoverer + SIM cancel echo + BASE startup round, retitle to "Phase 5b — IBKR trade execution (v0.5.1 + 5b.1 hardening)"
- **Memory:** append "Post-5b.1" section to `phase5b_shipped.md`
- **Tag** `v0.5.3` published; `git log v0.5.2..v0.5.3` clean

### Rollback plan

- Backend image: `docker compose -f docker-compose.prod.yml pull backend:v0.5.2 && up -d` (compose has digest pin from prior tag).
- Alembic 0005 has `downgrade()` defined: drops `positions` table — safe because no production code reads from it before this deploy (5b.1 is the first deploy that does).
- Sidecar binaries: `Move-Item dist dist-broken; Move-Item dist.bak dist; schtasks /Run × 4` (binary swap script always preserves `.bak` per operator playbook).

### Architect review — applied

*(populated after `ARCHITECT-REVIEW` skill runs against this spec; CRITICAL + HIGH findings fixed inline before plan-generation)*

---

## 6. Sequencing + dependencies

Strict ordering to avoid wasted work:

| Phase | Tasks | Days |
|---|---|---|
| Foundation | A2 migration + schema tests | 1 |
| Sidecar | C2 BASE round + B1 SIM cancel echo | 1.5 |
| Backend | A2 discoverer fan-out + tests | 2 |
| Tests | D3 mock E2E + nightly real-IBKR E2E | 1.5 |
| Close-out | docs + tag + canary verification | 1 |
| **Total** | | **~7d** |

**Parallel-safe pairs:** A2 schema ⊥ B1+C2 sidecar (independent inputs); D3 mock ⊥ D3 nightly (independent workflow files).

**Critical gate:** A2 migration must land before A2 discoverer extension (consumer of the schema). C2 must land before D3 nightly E2E (real chain depends on `currency_base` being non-empty).
