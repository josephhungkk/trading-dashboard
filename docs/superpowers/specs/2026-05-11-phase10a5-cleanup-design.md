# Phase 10a.5 — Risk-gate effectivity + test infrastructure cleanup

**Status:** Draft — design approved 2026-05-11, awaiting user spec review
**Target tag:** v0.12.1 (semver patch on v0.12.0)
**Predecessor:** Phase 10a (v0.12.0, shipped 2026-05-11)
**Successor:** Phase 10b (position-sizing calculator + multi-account portfolio rollup)
**Estimated commits:** ~25 over 3–5 days

---

## 1. Goal

Make the risk gate that shipped in Phase 10a *actually effective in production*.
Phase 10a wired 7 checks at validation station 4 but two of them are no-op until
upstream data is real:

- **Max-daily-loss** reads `v_account_intraday_pnl`, which is a zero-stub view.
- **Position concentration** reads `instrument_id`, which is unconditionally
  `None` because the conid → instrument_id round-trip was deferred.

A third check (**PDT** + **buying-power buffer**) relies on Redis counters that
only self-heal via the 120-second `BrokerDiscoverer` poll — every rejected
order leaves a phantom debit until the next cycle.

A fourth gap is auditability: today, only **BLOCK** decisions emit a
`risk_decisions` row; **ALLOW** and **WARN** paths are silent.

Phase 10a.5 lands these four effectivity fixes, plus the test infrastructure
needed to keep them honest:

- Drop the `isinstance(db, AsyncSession)` gate that bypasses the risk service
  in stub-Session tests.
- Add Playwright E2E coverage for the four risk-gate scenarios that today
  rely on manual smoke testing.
- Move the `real-broker` dependency group out of `backend/pyproject.toml` into
  its own uv project at `backend/tests/real_broker/pyproject.toml` so broker
  SDKs stay out of the backend lockfile.

ROADMAP.md is rewritten in Chunk D to reflect actual tag history (+2 minor
drift since Phase 8b) and to fix the version ladder going forward.

## 2. Non-goals

Items deferred to **Phase 10a.6** (refactor mini-phase) or absorbed elsewhere:

- `RiskLimitsPage` migration to the Phase 3 `DataTable` + `ColumnCustomizerDialog`.
- Per-endpoint CSRF nonce scoping (currently shares `csrf:order-cap:` prefix).
- `AdminAccountsPage` multi-mode kill-switch fetch (paper + live).
- `orders_service.py` file-split refactor (D2 was skipped during 10a).

Items deferred to an **operator runbook** (not a phase):

- `nightly-real-ibkr` 503 recovery (`provision-and-publish.ps1` +
  `schtasks /Run` 4 sidecars + `docker compose restart backend`).
- `nightly-real-schwab-trade` schwabdev OAuth stdin + sqlite token-store lock
  (needs a CI-sandboxed token-store seed; not in scope here).
- VPS Docker BuildKit cache prune-on-deploy step (one-shot done 2026-05-11).

Item already pinned to **Phase 24**:

- Multi-worker uvicorn with Redis Lua locks for counter atomicity.

## 3. Architecture

```
                                   ┌── Chunk A (BE backbone) ──────────────────┐
                                   │                                          │
v0.12.0 (10a shipped)              │ A1  pnl_intraday table + Alembic 0037     │
       │                           │ A2  BrokerDiscoverer fan-in (Summary RPC) │
       ├─── Chunk A ─── A1..A5 ────┤ A3  v_account_intraday_pnl rewrite        │
       │                           │ A4  Redis-counter decrement helper        │
       │                           │ A5  ALLOW/WARN audit emission             │
       │                           └──────────────────────────────────────────┘
       │
       │                           ┌── Chunk B (resolver wiring) ──────────────┐
       ├─── Chunk B ─── B1..B3 ────┤ B1  PreviewRequestBuilder + resolver hook │
       │                           │ B2  orders_service.py 5-site swap         │
       │                           │ B3  concentration-check integration tests │
       │                           └──────────────────────────────────────────┘
       │
       │              (A and B merge in any order — disjoint file sets)
       │                              │
       ├─── Chunk C ─── C1..C4 ───────┤ C1  test stub _Sidecar/_Session upgrade
       │                              │ C2  drop isinstance(db, AsyncSession) gate
       │                              │ C3  @playwright/test + 4 specs
       │                              │ C4  tests/real_broker/pyproject.toml split
       │                              │
       └─── Chunk D ─── D1 ───────────┤ D1  ROADMAP.md +2 forward-projection
                                      │
                                      ↓
                                 v0.12.1 (10a.5 ship)
```

### Invariants preserved from 10a

- Station 4 of the validation pipeline stays the only gate site.
- `RiskService.evaluate(ctx, mode)` signature is unchanged; `RiskCheckContext`
  gains a real `instrument_id` value, nothing else moves.
- Fail-OPEN audit semantics (insert failure → metric counter, request proceeds)
  survive the ALLOW/WARN extension.
- Single-replica Redis counter assumption stays; Phase 24 still owns the
  Lua-lock upgrade.

## 4. Data model

### `pnl_intraday` (new, Alembic 0037)

```
broker_accounts (existing)
    │ id (FK)
    ▼
pnl_intraday  (NEW)
    account_id      INTEGER  FK → broker_accounts.id     │
    day_start_utc   TIMESTAMPTZ                          │ composite PK
    realized        NUMERIC(20, 8)  NOT NULL
    unrealized      NUMERIC(20, 8)  NOT NULL
    currency        CHAR(3)         NOT NULL
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
    source_label    TEXT            NOT NULL
```

Rationale:

- Composite PK `(account_id, day_start_utc)` keeps natural daily history
  without a second table; supports the Phase 10b rollup query.
- `currency` per row because cross-broker rollup needs conversion (Phase 10b).
- `source_label` records which sidecar fed the row so Schwab `realized_pnl`
  vs Alpaca `today_pnl` semantic differences stay traceable.

### `v_account_intraday_pnl` (rewritten)

Drop the 10a zero-stub body. Replace with:

```sql
CREATE OR REPLACE VIEW v_account_intraday_pnl AS
SELECT
  ba.id                                                 AS account_id,
  (date_trunc('day', now() AT TIME ZONE 'UTC')
     AT TIME ZONE 'UTC')                                AS day_start_utc,
  COALESCE(p.realized,   0::NUMERIC(20, 8))             AS realized,
  COALESCE(p.unrealized, 0::NUMERIC(20, 8))             AS unrealized
FROM broker_accounts ba
LEFT JOIN pnl_intraday p
  ON p.account_id    = ba.id
 AND p.day_start_utc = (date_trunc('day', now() AT TIME ZONE 'UTC')
                          AT TIME ZONE 'UTC')
```

`risk_service.py:192` query is unchanged — same column contract.

### `risk_decisions` (no schema change)

The 10a CHECK constraint on `(action, side)` already covers ALLOW/WARN; it
just rarely fires. Chunk A5 changes only the *call site* in `risk_service.py`:
`insert(...)` is unconditional with `action ∈ {ALLOW, WARN, BLOCK}` derived
from gate outcome.

## 5. Service layer

### Chunk A — BE backbone

**A1. Alembic 0037.** New migration; covers table create + view rewrite.
Tests in `backend/tests/integration/test_alembic_0037.py` (mirrors 0036).

**A2. `PnlIntradayWriter` + BrokerDiscoverer fan-in.** New module
`backend/app/services/pnl_intraday_writer.py`:

```python
class PnlIntradayWriter:
    """Per-account-per-day INSERT … ON CONFLICT DO UPDATE on pnl_intraday."""

    async def upsert(
        self,
        *,
        account_id: int,
        realized: Decimal,
        unrealized: Decimal,
        currency: str,
        source_label: str,
    ) -> None: ...

    async def prune_older_than(self, *, days: int) -> int: ...
```

Wired into the existing `discover_loop` in `backend/app/services/brokers.py:1052`.
After `get_account_summary(account_number)` returns a `Summary` proto:

```python
await pnl_intraday_writer.upsert(
    account_id=account_id,
    realized=Decimal(summary.realized_pnl.value),
    unrealized=Decimal(summary.unrealized_pnl.value),
    currency=summary.realized_pnl.currency,
    source_label=client.label,
)
if cycle_count % RETENTION_SWEEP_EVERY_N_CYCLES == 0:
    await pnl_intraday_writer.prune_older_than(days=30)
```

`RETENTION_SWEEP_EVERY_N_CYCLES` tuned so prune runs once per ~30 minutes
of discoverer cycles. Writer shares the discoverer's `AsyncEngine` (no new
connection pool).

**A3. View rewrite verification.** Covered by A1 + A2. The `risk_service.py`
test seeds flip from "zero stub" to "pnl_intraday row exists".

**A4. `risk_counters.py` (`incr` / `decrement` / `revert`).** New module
`backend/app/services/risk_counters.py`:

```python
class RiskCounters:
    async def incr(self, ctx: RiskCheckContext, kind: CounterKind) -> str: ...
    async def decrement(self, ctx: RiskCheckContext, kind: CounterKind, token: str) -> None: ...
    async def revert(self, ctx: RiskCheckContext, kind: CounterKind, token: str) -> None: ...
```

- `incr` is called from `RiskService.evaluate` (existing call sites) and
  returns an opaque token.
- `decrement` is called from `orders_service.py` on dispatch success.
- `revert` is called from `orders_service.py` on dispatch failure
  (post-gate, pre-dispatch).
- Pre-gate failures never call `incr` and therefore need no cleanup.

Idempotency: Redis Lua `IF EXISTS(token) THEN DECR ; DEL(token)` ensures
double-revert is a no-op. Single-replica assumption stays; multi-worker
upgrade still owned by Phase 24.

**A5. ALLOW/WARN audit emission.** In `RiskService.evaluate`, the audit-insert
call becomes unconditional with `action ∈ {ALLOW, WARN, BLOCK}` derived from
gate outcome. 10a fail-OPEN policy (insert failure → metric counter, request
proceeds) survives — applies to all three actions.

### Chunk B — Resolver wiring

**B1. `_resolve_instrument_id` helper.** New function in
`backend/app/services/orders_service.py` (or new module if the file pushes
past split-trigger size):

```python
async def _resolve_instrument_id(
    db: AsyncSession, *, conid: str
) -> int | None:
    """conid → instruments.id round-trip via existing InstrumentResolver.

    Returns None when the conid → canonical_id mapping doesn't exist yet
    (lazy creation deferred to the QuoteEngine's normal upsert path; the
    concentration check stays no-op for one preview cycle, then becomes
    effective on the next request once instruments + symbol_aliases are
    seeded).
    """
```

Uses the existing `InstrumentResolver(db_session)` at
`backend/app/services/quotes/instrument_resolver.py:69` — no new resolver,
just a translation wrapper.

**B2. orders_service.py 5-site swap.** Replace each
`instrument_id=None  # 10a.5: wire conid -> instrument_id` marker at
`backend/app/services/orders_service.py` lines 316, 365, 428, 493, 544 with
`instrument_id=await _resolve_instrument_id(db, conid=...)`. All 5 sites are
already inside `async def` functions with `db` in scope.

**B3. Concentration-check integration tests.** New
`backend/tests/integration/test_concentration_check_e2e.py`:

- Preview with known conid pre-seeded in `instruments` → context gets
  `instrument_id != None` → concentration check triggers when post-trade
  exposure crosses cap.
- Preview with fresh conid (no instrument row yet) → context gets
  `instrument_id = None` → check returns ALLOW (one-cycle warmup cost).

### Chunk C — Test infra

**C1. Test stub `_Sidecar`/`_Session` upgrade.** Replace the minimal stubs
in `backend/tests/services/_fakes.py` (or equivalent) with fakes that
fully implement the `AsyncSession` Protocol shape the gate needs
(`execute`, `scalar`, `commit`, `rollback`). C1 must be transparent: no
existing test should change behavior because of stub upgrade.

**C2. Drop `isinstance(db, AsyncSession)` gate.** Remove the 10a guard at
the `RiskService.evaluate` call site in `orders_service.py`. The gate runs
for every request once C1 lands. Failure of the C1 → C2 chain produces
loud CI failure, not silent gate-skip in production.

**C3. `@playwright/test` + 4 specs.** Promote `@playwright/test` from
transitive (`@vitest/browser-playwright`) to a direct devDependency in
`frontend/package.json`. Reuse the existing `frontend/playwright.config.ts`
(`testDir: 'e2e'`, chromium + iphone-se projects). New specs in
`frontend/e2e/`:

- `phase10a-risk-warn.spec.ts` — submit a near-cap order, see WARN row in
  TradeTicketModal, click acknowledge gate, place succeeds.
- `phase10a-risk-block.spec.ts` — submit a kill-switch-active order, see
  BLOCK row, place button stays disabled.
- `phase10a-admin-risk-crud.spec.ts` — `/admin/risk` page: create cap →
  edit → soft-delete; verify pubsub-driven cache invalidation (FE refetches
  within 5s).
- `phase10a-kill-switch.spec.ts` — `/admin/accounts` page: toggle
  kill-switch on a paper account, verify subsequent preview blocks.

Shared fixtures via the existing `frontend/e2e/fixtures.ts` (Phase 9 pattern).
New workflow `.github/workflows/playwright-e2e.yml` (ubuntu-latest, headless,
chromium + iphone-se, PR + nightly triggers).

**C4. `tests/real_broker/pyproject.toml` split.** Move
`[dependency-groups].real-broker` from `backend/pyproject.toml` into a new
uv project at `backend/tests/real_broker/pyproject.toml`. Workflows that
previously ran `uv sync --group real-broker` from `backend/` switch to
`uv sync` from `backend/tests/real_broker/`. Conftest.py module-resolution
adjusted. Python version pinned to match `backend/pyproject.toml` (3.14).

### Chunk D — Docs

**D1. ROADMAP.md +2 forward-projection rewrite.** Apply the +2 shift from
Phase 8b onward to reflect shipped reality:

| Phase | New ROADMAP tag |
|---|---|
| 8a | v0.8.0 (matches reality) |
| 8b | v0.9.0 (matches reality) |
| 8c | v0.10.0 (matches reality) |
| 9 | v0.11.0 (matches reality) |
| 10a | v0.12.0 (matches reality) |
| **10a.5** | **v0.12.1** (this phase) |
| **10b** | **v0.13.0** |
| 11 (AI router) | v0.14.0 |
| 12 (Options single-leg) | v0.15.0 |
| 13 (Multi-leg combos) | v0.16.0 |
| 14 (Futures) | v0.17.0 |
| 15 (Forex + Crypto) | v0.18.0 |
| 16 (Bonds + MF + CFD) | v0.19.0 |
| 17 (IBKR algos) | v0.20.0 |
| 18 (Scanner + News) | v0.21.0 |
| 19 (Backtesting) | v0.22.0 |
| 20 (Bot v1) | v0.23.0 |
| 21 (Bot v2) | v0.24.0 |
| 22 (Bot v3) | v0.25.0 |
| 23 (UK CGT) | v0.26.0 |
| 24 (Infra hardening) | v0.27.0 |
| 25 (PWA + ship) | **v1.0.0** (anchored) |

Also updated in same commit:

- `CLAUDE.md` phase-pointer (`phase10a_shipped.md` → also reference
  `phase10a5_shipped.md` after close-out).
- `CHANGELOG.md` v0.12.1 entry.
- `TASKS.md` Phase 10a.5 section flipped to complete + scoreboard refreshed.

## 6. Control flow at a request boundary

```
preview_order(req)
    │
    ▼
station 1: parse + validate                              (unchanged)
station 2: account resolve                               (unchanged)
station 3: capability check                              (unchanged)
station 4: risk gate
    │
    │   ┌── ctx.instrument_id = await _resolve_instrument_id(db, conid=req.conid)
    │   │                          ← B2 lights up
    │   │
    │   └── RiskService.evaluate(ctx, mode=PREVIEW)
    │           │
    │           ├── kill_switch                           (10a, unchanged)
    │           ├── max_daily_loss                        ← A1+A2+A3: real numbers via pnl_intraday
    │           ├── pdt                                   ← A4: decrement+revert wiring
    │           ├── position_concentration                ← B2: actually fires now
    │           ├── buying_power_buffer                   ← A4: decrement+revert wiring
    │           └── margin_preview (sidecar RPC)          (10a, unchanged)
    │
    ▼
station 5: dispatch  → on success: A4.decrement(token)
                     → on failure: A4.revert(token)
    │
    ▼
audit row in risk_decisions                              ← A5: ALLOW / WARN / BLOCK
```

## 7. Error handling

1. **`pnl_intraday` upsert failure → log + drop, don't break discoverer.**
   Failure mirrors how the existing summary upsert handles SQL errors:
   structured log, `pnl_intraday_upsert_failures_total` counter, continue
   the cycle. Risk-gate falls back to "no row → check returns ALLOW" via
   the view's LEFT JOIN — same posture as today's zero-stub.

2. **Counter `decrement` / `revert` failure → log + counter, don't fail
   dispatch.** Dispatch already succeeded (decrement path) or already failed
   (revert path); stale counter self-heals in 120s via the discoverer poll.
   Adding a dispatch failure on counter-cleanup failure trades small
   accounting drift for a hard outage — wrong trade-off.
   `risk_counter_cleanup_failures_total` counter.

3. **InstrumentResolver returns `None` → concentration check returns ALLOW.**
   Identical posture to today; one-request warmup cost. Concentration is a
   soft guardrail, not a fraud blocker.

4. **Audit row insert failure for ALLOW/WARN → metric + drop.** Mirrors
   10a's BLOCK-path fail-OPEN policy. `risk_audit_insert_failures_total`
   counter (already exists, just extended to cover all three actions).

5. **Test stub upgrade breaks an unrelated test → block C2.** C1 must be
   transparent. If the stub leaks semantics that production relies on, we
   want CI red, not a silent gate skip in prod.

## 8. Observability

New metrics:

- `pnl_intraday_rows_total` — gauge of total rows across accounts; sanity
  check on growth + retention.
- `pnl_intraday_upsert_failures_total` — counter.
- `pnl_intraday_last_update_seconds{account_id}` — gauge of age-of-newest
  row; alert if >180s during market hours.
- `risk_counter_cleanup_failures_total` — counter.

Existing (extended):

- `risk_audit_insert_failures_total` — now covers ALLOW/WARN paths.

## 9. Testing

| Chunk | New tests | Existing tests touched |
|---|---|---|
| A1 | `tests/integration/test_alembic_0037.py` — upgrade idempotency, view column contract, downgrade safety | — |
| A2 | `tests/services/test_pnl_intraday_writer.py` — upsert idempotent, prune drops >30d rows, currency carried, source_label set | `tests/services/test_brokers.py` — extend discoverer-loop test to assert one upsert per cycle per account |
| A3 | covered by A1 + A2 | `tests/services/test_risk_service.py::test_max_daily_loss_*` — flip seed pattern from "zero stub" to "row exists" |
| A4 | `tests/services/test_risk_counters.py` — incr → decrement returns to zero, incr → revert returns to zero, double-revert no-op | `tests/services/test_risk_service.py::test_pdt_*` + `test_buying_power_*` |
| A5 | `tests/integration/test_risk_decisions_audit.py` — add 2 cases (ALLOW path, WARN path) | existing BLOCK case unchanged |
| B1+B2 | `tests/services/test_instrument_id_resolution.py` — conid→id happy path, no-instrument fallback returns None | the 5 `test_risk_service.py` callers swap from `None` to expecting an int |
| B3 | `tests/integration/test_concentration_check_e2e.py` — concentrated position triggers BLOCK; fresh conid no-ops | — |
| C1+C2 | (refactor — surface area unchanged) | every existing test must still pass with the gate dropped |
| C3 | 4 specs in `frontend/e2e/` | shared fixtures from `frontend/e2e/fixtures.ts` |
| C4 | `tests/real_broker/conftest.py` + path adjustments | 7 nightly workflows |

Coverage target: ≥80% per `~/.claude/rules/testing-another.md`. Each chunk
lands with tests in the same commit (TDD).

**Explicitly NOT tested in 10a.5 (and why):**

- Multi-worker race on Redis counters — still owned by Phase 24.
- Real broker PnL accuracy (Schwab `realized_pnl` vs Alpaca `today_pnl`
  semantic divergence) — spot-check during operator validation, not in CI.
- Playwright cross-browser — chromium + iphone-se only, per existing config.
- Failure-mode chaos for the discoverer — the 120s self-heal is already
  tested-in-prod from 10a.

## 10. Deliverables

| Chunk | Deliverable | Files touched | ~Commits |
|---|---|---|---|
| A1 | Alembic 0037 + view rewrite | `backend/alembic/versions/0037_*.py`, `backend/tests/integration/test_alembic_0037.py` | 2 |
| A2 | `PnlIntradayWriter` + BrokerDiscoverer fan-in + 30-day prune | `backend/app/services/pnl_intraday_writer.py`, `backend/app/services/brokers.py`, `backend/tests/services/test_pnl_intraday_writer.py`, `backend/tests/services/test_brokers.py` | 3 |
| A3 | `risk_service` test flip | `backend/tests/services/test_risk_service.py` | 1 |
| A4 | `risk_counters.py` + orders_service wiring | `backend/app/services/risk_counters.py`, `backend/app/services/orders_service.py`, `backend/tests/services/test_risk_counters.py` | 3 |
| A5 | ALLOW/WARN audit emission | `backend/app/services/risk_service.py`, `backend/tests/integration/test_risk_decisions_audit.py` | 2 |
| B1+B2 | `_resolve_instrument_id` + 5-site swap | `backend/app/services/orders_service.py`, `backend/tests/services/test_instrument_id_resolution.py` | 2 |
| B3 | Concentration integration tests | `backend/tests/integration/test_concentration_check_e2e.py` | 1 |
| C1+C2 | Stub upgrade + `isinstance` gate drop | `backend/tests/services/_fakes.py`, `backend/app/services/orders_service.py` | 2 |
| C3 | Playwright direct devDep + 4 specs + workflow | `frontend/package.json`, `frontend/e2e/phase10a-*.spec.ts`, `.github/workflows/playwright-e2e.yml` | 5 |
| C4 | `tests/real_broker/pyproject.toml` split | `backend/tests/real_broker/pyproject.toml`, `backend/tests/real_broker/conftest.py`, 7 nightly workflows | 2 |
| D1 | ROADMAP rewrite + close-out | `docs/ROADMAP.md`, `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` | 1 |
| D3 | Phase close-out commit + tag | memory `phase10a5_shipped.md`, git tag v0.12.1 | 1 |

**Total: ~25 commits over 3–5 days.**

## 11. Sequencing + parallelism

```
                    Day 1               Day 2               Day 3              Day 4-5
                    ──────────────      ──────────────      ──────────────     ──────────────
Codex parallel-1    Chunk A1+A2         Chunk A3+A4         Chunk A5            Phase close-out:
                    (table+writer)      (counter helper)    (audit)              C4 deps split,
                                                                                 D1 ROADMAP,
Codex parallel-2    Chunk B1+B2         Chunk B3            Chunk C1+C2          D3 tag v0.12.1
                    (resolver)          (concentration)     (stub+gate)

Codex parallel-3                                            Chunk C3
                                                            (Playwright)

Reviewer chain      end-of-day:                             end-of-day:          end-of-day:
                    spec/code/sec/db                        spec/code/sec/db     final ARCHITECT
                    for A1+A2                               for C-bundle
```

A and B touch disjoint file sets — they parallelize cleanly. C waits on A
(Playwright specs assert real gate behavior) + B (concentration spec needs
resolver wired). C3 parallelizes with C1+C2.

## 12. Open questions (none blocking)

1. **`PnlIntradayWriter` engine ownership** — share the discoverer's
   `AsyncEngine` (recommended; writer is logically part of the discoverer
   cycle) vs its own. Final call during A2 impl.
2. **`risk_counters.revert` idempotency mechanism** — Redis Lua
   `IF EXISTS(token) THEN DECR ; DEL(token)` (recommended) vs SET-NX
   sentinel. Final call during A4 impl.
3. **Playwright workflow trigger cadence** — PR + nightly (recommended)
   vs PR-only. Final call during C3 impl.
4. **`tests/real_broker/pyproject.toml` Python version** — pin to backend's
   3.14 (recommended) vs float. Final call during C4 impl.

All four are tactical, not architectural. The plan does not change because
of any of them.

## 13. Phase exit criteria

- All 7 in-scope items shipped to `main`.
- CI green (Main CI + Playwright workflow + 5 of 7 nightlies — ibkr +
  schwab-trade stay deferred per operator-runbook scope).
- v0.12.1 tag pushed, ROADMAP.md table accurate, `phase10a5_shipped.md`
  memory written.
- `phase10_status_clarification.md` memory updated to mark 10a.5 done +
  flag 10b as remaining.
