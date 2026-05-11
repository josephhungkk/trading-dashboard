# Phase 10a.5 — Risk-gate effectivity + test infrastructure cleanup

**Status:** Architect-review applied (2026-05-11) — awaiting user spec review
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
only self-heal via the 30-second `BrokerDiscoverer` poll — every rejected
order leaves a phantom debit until the next cycle. (Earlier drafts said
"120-second"; verified `brokers.py:1036` `interval_seconds=30.0`.)

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

**Known limitation surfacing here, completed in Phase 10b:** multi-currency
accounts (IBKR holding USD positions on a GBP/HKD/CAD base) get a
*known-degraded* gate, not a silently-wrong one. The writer drops position
rows whose currency differs from `broker_accounts.currency_base` and
emits `pnl_intraday_currency_skip_total{broker_id}` so ops can see the
gate is partially blind. Phase 10b lands FX conversion + a per-currency
sub-aggregate; until then, single-currency accounts are fully covered.

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
       ├─── Chunk D ─── D1 ───────────┤ D1  ROADMAP.md +2 forward-projection
       │                              │      + CLAUDE.md/CHANGELOG.md/TASKS.md
       │                              │
       └─── Chunk D2 ─────────────────┤ D2  Phase close-out commit + memory file
                                      │      + git tag v0.12.1 push
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
    account_id           UUID  FK → broker_accounts.id    │
    day_start_utc        TIMESTAMPTZ                      │ composite PK
    realized_today       NUMERIC(20, 8)  NOT NULL
    unrealized           NUMERIC(20, 8)  NOT NULL
    currency             CHAR(3)         NOT NULL
                                         CHECK (currency ~ '^[A-Z]{3}$')
    summary_updated_at   TIMESTAMPTZ     NOT NULL
    updated_at           TIMESTAMPTZ     NOT NULL DEFAULT now()
    source_label         TEXT            NOT NULL
```

Rationale:

- Composite PK `(account_id, day_start_utc)` keeps natural daily history
  without a second table; supports the Phase 10b rollup query.
- **Column is `realized_today`, not `realized`** — `Summary.realized_pnl`
  (proto field 3) is **cumulative since account-open** for IBKR
  (`AccountValue('RealizedPnL', currency)`), whereas Schwab's
  `currentDayProfitLoss` and Alpaca's intraday `equity − last_equity` are
  per-day. The writer **must source from per-position intraday**
  (`SUM(Position.realized_pnl_today)`, proto field 7) — never from
  `Summary.realized_pnl` — so the gate compares like to like across brokers.
- `currency` per row because cross-broker rollup needs conversion (Phase 10b).
  Until 10b lands, the writer **drops rows where the broker-reported currency
  differs from `broker_accounts.currency_base`** and increments
  `pnl_intraday_currency_skip_total{broker_id}` — a multi-currency account
  gets a known-degraded gate, not a silently-wrong one. Documented in §1
  goals as a Phase-10b-completes-this caveat.
- `summary_updated_at` carries the proto's `Summary.updated_at` (or the
  positions-list aggregate timestamp). UPSERT guards with
  `WHERE excluded.summary_updated_at >= pnl_intraday.summary_updated_at` —
  stale data can't overwrite fresh data on the BASE-tag-refresh race
  (`brokers.py:1262-1287`).
- `source_label` records which sidecar fed the row so Schwab vs Alpaca vs
  IBKR semantic divergence stays traceable. Spec §5 A2 declares: writer
  accepts the broker's intraday-realized field at face value; residual
  semantic drift (commission inclusion, Alpaca T+1 session boundary in ET,
  IBKR currency-conversion noise) is a known limitation noted in
  `phase10a5_shipped.md`. The metric
  `pnl_intraday_writer_source_drift_seconds{broker_id}` surfaces the
  time-of-day at which each broker's intraday counter resets.

### `v_account_intraday_pnl` (rewritten)

Drop the 10a zero-stub body. The view must surface **staleness**, not
silently substitute zero — a fail-OPEN at exactly the wrong time
(post-discoverer-crash) is the failure mode 10a's stub had:

```sql
CREATE OR REPLACE VIEW v_account_intraday_pnl AS
SELECT
  p.account_id                          AS account_id,
  p.day_start_utc                       AS day_start_utc,
  p.realized_today                      AS realized,
  p.unrealized                          AS unrealized,
  p.summary_updated_at                  AS summary_updated_at,
  (now() - p.summary_updated_at)        AS staleness
FROM pnl_intraday p
WHERE p.day_start_utc = (date_trunc('day', now() AT TIME ZONE 'UTC')
                          AT TIME ZONE 'UTC')
```

**No LEFT JOIN.** Missing row → `risk_service.py:_check_max_daily_loss` sees
`row is None` → returns **WARN** (not silent ALLOW) with reason
`max_daily_loss_pnl_stale`. The gate is informed-but-not-blocking on
cold-cache or post-deploy starts. Same posture if `staleness > INTERVAL '90 seconds'`
(3× the 30s discoverer cycle) — staleness gate surfaces a WARN.

`risk_service.py:192` query updates: new `staleness` + `summary_updated_at`
columns selected; one new branch (`row is None or row.staleness > 90s`
returns `RiskCheckResult.WARN(...)`). Output column names `realized` +
`unrealized` are preserved so the existing aggregation stays.

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
    """Per-account-per-day INSERT … ON CONFLICT DO UPDATE on pnl_intraday.

    Source-field invariant: ``realized_today`` MUST come from
    SUM(positions[*].realized_pnl_today) (proto Position field 7),
    NEVER from Summary.realized_pnl (proto Summary field 3 — that is
    cumulative since open for IBKR and would invert the gate).
    """

    async def upsert(
        self,
        *,
        account_id: uuid.UUID,
        realized_today: Decimal,
        unrealized: Decimal,
        currency: str,
        summary_updated_at: datetime,
        source_label: str,
    ) -> None: ...

    async def prune_older_than(self, *, days: int) -> int: ...
```

**Integration point.** Wired into the **existing** Phase 5a NLV fan-out
inside `_discover_once` at `backend/app/services/brokers.py:1298-1393` —
not a second `get_account_summary` RPC. The existing fan-out already
collects `Summary` + `Positions` per account at line 1320; A2 reads the
positions list off that result and aggregates locally:

```python
# inside _discover_once, parallel to the NLV update at line 1377
# (same `async with session.begin()` outer scope; own savepoint so a
# PnL DB failure does NOT roll back the NLV update)
if summary_result is not None and positions_result is not None:
    realized_today_total = sum(
        Decimal(p.realized_pnl_today.value)
        for p in positions_result.positions
        if p.realized_pnl_today.currency == account.currency_base
    )
    unrealized_total = sum(
        Decimal(p.unrealized_pnl.value)
        for p in positions_result.positions
        if p.unrealized_pnl.currency == account.currency_base
    )
    skipped = len(positions_result.positions) - matching_count
    if skipped > 0:
        metrics.pnl_intraday_currency_skip_total.labels(
            broker_id=account.broker_id
        ).inc(skipped)

    try:
        async with session.begin_nested():
            await pnl_intraday_writer.upsert(
                account_id=account.id,
                realized_today=realized_today_total,
                unrealized=unrealized_total,
                currency=account.currency_base,
                summary_updated_at=summary_result.updated_at.ToDatetime(),
                source_label=client.label,
            )
    except SQLAlchemyError:
        metrics.pnl_intraday_upsert_failures_total.inc()
        _log.warning("pnl_intraday_upsert_failed", account_id=str(account.id))
        # do NOT poison the NLV update; the outer transaction continues

# IBKR sidecar 503 / maintenance window: summary_result is None at line 1362
# in the existing flow — A2 skips upsert (NOT writes zero, which would
# fail-OPEN the gate per CRIT-2).

# prune every Nth cycle (~30 minutes at 30s interval)
if self._cycle_count % RETENTION_SWEEP_EVERY_N_CYCLES == 0:
    await pnl_intraday_writer.prune_older_than(days=30)
```

`RETENTION_SWEEP_EVERY_N_CYCLES = 60` (60 cycles × 30 s = 30 minutes).

Writer shares the discoverer's `AsyncEngine` via the existing
`session` at line 1360 — no new connection pool. UPSERT in pseudo-SQL:

```sql
INSERT INTO pnl_intraday (
  account_id, day_start_utc, realized_today, unrealized,
  currency, summary_updated_at, source_label
) VALUES (:aid, :day, :r, :u, :c, :sua, :sl)
ON CONFLICT (account_id, day_start_utc) DO UPDATE
   SET realized_today     = EXCLUDED.realized_today,
       unrealized         = EXCLUDED.unrealized,
       currency           = EXCLUDED.currency,
       summary_updated_at = EXCLUDED.summary_updated_at,
       source_label       = EXCLUDED.source_label,
       updated_at         = now()
 WHERE EXCLUDED.summary_updated_at >= pnl_intraday.summary_updated_at
   AND (pnl_intraday.realized_today, pnl_intraday.unrealized)
       IS DISTINCT FROM (EXCLUDED.realized_today, EXCLUDED.unrealized)
```

The first WHERE clause stops a stale BASE-tag-refresh tick from clobbering
fresh data (MED-6). The second IS-DISTINCT-FROM clause makes the UPDATE a
no-op when values are unchanged — eliminates 2880 dead-row writes per
account per day pre-vacuum (MED-1).

Side-effect: now that PDT/BP reconcile is on the same 30s cycle, **all**
risk-counter reconciles run in this fan-out. A4's `risk_counters.reconcile`
contract is invoked here too — see HIGH-9 fix in A4 below.

**A3. View rewrite verification.** Covered by A1 + A2. The `risk_service.py`
test seeds flip from "zero stub" to "pnl_intraday row exists".

**A4. Token-based counter choreography in `risk_inflight_counters.py`.**
Extend the existing module at `backend/app/services/risk_inflight_counters.py`
— do **not** create a new `risk_counters.py`. Today the module exposes
tokenless `decrement_pdt(account_id)`, `revert_pdt(account_id)`,
`commit_bp(account_id, notional)`, `revert_bp(account_id, notional)`,
plus `reconcile_pdt` / `reconcile_bp_committed` writing with 120s TTL.

API change (BACKWARD-INCOMPATIBLE — applied in one commit; both call sites
in `orders_service.py` update in the same commit):

```python
# Token-bearing variants — return an opaque token to thread through dispatch.

async def decrement_pdt(redis, account_id, *, broker_reported=None) -> tuple[int, str]:
    """Returns (post_decrement_value, token). Writes the sentinel
       risk:pdt:tok:{token_uuid} = '1' EX 86400 before the DECR.
    """

async def revert_pdt(redis, account_id, token: str) -> int:
    """Atomic Lua: IF GET(token_key) THEN DEL(token_key); INCR(counter_key);
                   ELSE no-op. Returns post-revert value (or current value)."""

async def commit_pdt(redis, account_id, token: str) -> None:
    """Atomic Lua: IF GET(token_key) THEN DEL(token_key); ELSE no-op.
       Token consumed without rollback — counter stays decremented (broker
       accepted; next reconcile carries through)."""

async def commit_bp(redis, account_id, notional: Decimal) -> tuple[Decimal, str]:
    """Returns (post_commit_total, token). Writes sentinel
       risk:bp:tok:{token_uuid} = str(notional) EX 86400 with the notional
       payload so revert can replay the exact amount."""

async def revert_bp(redis, account_id, token: str) -> Decimal:
    """Atomic Lua: IF GET(token_key) IS NOT NIL THEN
                     INCRBYFLOAT(counter_key, -GET(token_key));
                     DEL(token_key); RETURN INCRBYFLOAT(counter_key, 0);
                   ELSE return current value."""

async def commit_bp_finalize(redis, account_id, token: str) -> None:
    """Atomic Lua: IF GET(token_key) THEN DEL(token_key); ELSE no-op.
       Token consumed; committed-BP total stays. Next reconcile carries
       through."""
```

Token contract:

- **Token key shape**: `risk:pdt:tok:{uuid}` / `risk:bp:tok:{uuid}` where
  `uuid` is generated server-side by the decrement/commit caller.
- **Token TTL**: 86400 s — matches the counter TTL on the seed write, so
  a backend crash between decrement and dispatch-result can't leak past
  the next trading session.
- **Double-revert idempotency**: Lua `GET-DEL-INCR` is atomic; second revert
  finds nothing under the token key and returns the current counter value
  unchanged. Test `test_risk_counters.py::test_double_revert_idempotent`.
- **Crash between gate-pass and dispatch**: token key + counter both
  decremented, dispatch never runs → next `reconcile_pdt`/`reconcile_bp`
  on the discoverer cycle (30 s) overwrites the counter with broker truth
  → token key still exists with 86400s TTL → operator-facing metric
  `risk_counter_orphan_tokens_total` (new gauge from a periodic SCAN MATCH
  inside the discoverer fan-out, observable but not alarming until
  growth-rate crosses threshold).
- **Reconcile-aware tokens (HIGH-9 fix)**: each cycle, the reconcile path
  in `discover_loop` runs:
  ```
  UNLINK risk:pdt:tok:* matching this account_id (best-effort SCAN MATCH)
  SET risk:pdt:{aid} = broker_reported EX 120
  ```
  Orphaned tokens are reaped before the counter is overwritten. A token
  whose decrement has fired but whose dispatch hasn't completed gets
  double-charged — acceptable since the broker value is authoritative and
  the dispatch will resolve before the next cycle.

Call-site rewiring in `orders_service.py`:

```python
# place_order: after gate ALLOW and before broker dispatch
pdt_token = bp_token = None
if pdt_required:
    _, pdt_token = await decrement_pdt(redis, account_id, broker_reported=...)
if bp_required:
    _, bp_token = await commit_bp(redis, account_id, notional)

try:
    broker_response = await broker.place_order(...)
except Exception:
    if pdt_token: await revert_pdt(redis, account_id, pdt_token)
    if bp_token:  await revert_bp(redis, account_id, bp_token)
    raise
else:
    if pdt_token: await commit_pdt(redis, account_id, pdt_token)
    if bp_token:  await commit_bp_finalize(redis, account_id, bp_token)
```

`modify_order` follows the same shape; the `preview_order` path does NOT
call decrement/commit (preview is dry-run — no counter side-effect).

Single-replica assumption stays; multi-worker upgrade still owned by
Phase 24.

**A5. ALLOW/WARN audit emission.** Audit emission stays inside
`_audit_risk_decision` (orders_service.py:387) and
`_audit_risk_decision_modify` (orders_service.py:515) — both already open
their own dedicated `SessionLocal()` to avoid mutating the caller's
transaction (10a D9-fix). A5 only widens the **call-site guard** in
`place_order` / `modify_order` from `verdict == 'block'` to **unconditional**.
The 10a session-isolation invariant is preserved; A5 does **not** inline
audit-insert into `RiskService.evaluate` (which holds the caller's `db`).
10a fail-OPEN policy (insert failure → `risk_audit_insert_failures_total`,
request proceeds) survives — applies to all three actions.

Per-mode emission policy (controls audit volume blow-up — HIGH-4):

| Path | ALLOW | WARN | BLOCK |
|---|---|---|---|
| `place_order` / `modify_order` | audit | audit | audit (10a today) |
| `preview_order` | **no audit** | audit | audit |

Rationale: preview is called on every TradeTicketModal keystroke; emitting
ALLOW for each one would explode the table 50–200× by daily volume. The
gate already returns the WARN/BLOCK detail synchronously in the
`PreviewResponse.risk_warnings` / `risk_blockers` lists, so the operator
sees them; the audit row is for *post-hoc* analysis where keystroke-level
ALLOWs are noise. WARN/BLOCK on preview do audit so the operator-cap-edit
flow can be traced.

Dedupe protection for the place/modify paths: a 30-second Redis SETNX on
`risk_audit_dedupe:{account_id}:{conid}:{side}:{rounded_qty}` skips a
duplicate ALLOW row when the same logical order is replayed (a tab refresh
or a network retry shouldn't double-count). The metric
`risk_audit_dedupe_skipped_total` surfaces dedupe activity.

`risk_decisions` schema gets a new index in the same migration (0037,
adjacent to `pnl_intraday`):

```sql
CREATE INDEX CONCURRENTLY idx_risk_decisions_verdict_time
  ON risk_decisions (verdict, evaluated_at DESC)
```

— so the `/api/risk/decisions?verdict=block` admin feed doesn't seq-scan
the new ALLOW rows. Retention: 0037 also creates a helper
`prune_risk_decisions_allow(days int)` that deletes rows where
`verdict='allow' AND evaluated_at < now() - INTERVAL ':d days'` — bound to
30 days. WARN/BLOCK kept indefinitely. Discoverer hooks the prune at the
same Nth-cycle cadence as `pnl_intraday.prune_older_than`.

### Chunk B — Resolver wiring

**B1. `_resolve_instrument_id` helper + read-only resolver method.**
`InstrumentResolver` today exposes only `resolve_or_create` (asymmetric
create) — the risk gate must **not** author instruments. B1 adds a new
read-only method to the resolver:

```python
# new method on InstrumentResolver
async def find_by_alias(self, *, source: str, raw_symbol: str) -> int | None:
    """Pure SELECT over symbol_aliases; no upsert, no lock acquisition.
    Returns the resolved instrument_id or None when no alias row exists.
    """
```

Then a small wrapper in `orders_service.py` (kept here, not extracted —
10a's "no abstractions beyond what task requires" principle):

```python
async def _resolve_instrument_id(
    db: AsyncSession, *, broker_id: str, conid: str
) -> int | None:
    """conid → instruments.id via read-only alias lookup. Returns None
    when no alias exists. No transaction work; caller's session unchanged.

    Eager creation strategy: the lookup is followed by a fallback call to
    QuoteEngine's existing 'resolve_or_create' path using the canonical_id
    derived from the contract lookup that already runs at
    orders_service.py:1525 — accepting the 1ms write cost on the cold path
    rather than degrading concentration permanently for un-subscribed
    symbols (MED-4).
    """
    resolver = InstrumentResolver(db)
    instrument_id = await resolver.find_by_alias(
        source=broker_id, raw_symbol=conid
    )
    if instrument_id is not None:
        return instrument_id
    # Cold path: eager creation. Uses the canonical_id derived from
    # contract details. Cost: one INSERT-on-conflict round trip.
    contract = await _lookup_contract(db, conid)
    if contract is None:
        return None
    return (
        await resolver.resolve_or_create(
            source=broker_id,
            raw_symbol=conid,
            canonical_id=contract.canonical_id,
            asset_class=contract.asset_class,
        )
    ).id
```

Optional cache (LOW-6): per-request memoization of conid → instrument_id
via FastAPI `Depends`-scoped `dict` — same preview-then-place call within
~30s avoids the round trip on the second hit. Defer if rate-of-preview
is low.

Transaction-ownership invariant: resolver methods never commit; this is
documented in `instrument_resolver.py:13` and is preserved here.

**B2. orders_service.py 7-site swap.** Replace **7** `instrument_id=None`
markers (5 gate-context sites + 2 audit-row sites). Gate-context sites at
lines 316, 365, 493 swap to `_resolve_instrument_id(db, broker_id=...,
conid=...)`. Lines 428 and 544 are inside `_audit_risk_decision*` helpers
(MED-7) — they receive a new `instrument_id: int | None` parameter
threaded through from the call site that just resolved it for the gate
context. Tracing invariant: when concentration BLOCKs, the audit row's
`instrument_id` matches the gate's evaluated instrument. All 7 sites are
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
with fakes that fully implement the `AsyncSession` Protocol shape the gate
needs (`execute`, `scalar`, `commit`, `rollback`). **C1 is NOT
transparent-by-construction** — tests that today rely on `db.commit` being
a no-op will see flushes, and tests that previously short-circuited the
gate via `isinstance` will now run real evaluation. C1 lands as a sequence
of small commits, one per impacted test family, with the commit message
listing the behavior delta per file. New pytest marker
`@pytest.mark.no_risk_gate` is introduced for tests that explicitly want
the legacy short-circuit (use sparingly; primary goal is to retire the
marker entirely).

Shadow-run intermediate state: C1 lands the upgraded stub *and* the
`isinstance(db, AsyncSession)` guard stays in place. The next CI run is
observed for unexpected test failures or pass-for-wrong-reason changes
*before* C2 removes the guard. Avoids a flag-day big-bang.

**C2. Drop `isinstance(db, AsyncSession)` gate.** Remove the 10a guard at
the `RiskService.evaluate` call site in `orders_service.py`. Gate runs for
every request once C1 lands.

Verification gate before merge: `git grep -F 'isinstance' backend/app/services/orders_service.py`
must show 0 risk-related matches. CI step added in same commit:

```
- name: verify risk-gate isinstance guard removed
  run: |
    if rg -n 'isinstance\(db,\s*AsyncSession\).*risk' backend/app/services/orders_service.py; then
      echo "::error::C2 verification: risk-gate isinstance guard still present"
      exit 1
    fi
```

C1+C2 expand to ~4-5 commits (one per impacted test family + the
gate-drop + the shadow-run observation commit + the verification CI step).

**C3. `@playwright/test` + 4 specs.** Promote `@playwright/test` from
transitive (`@vitest/browser-playwright`) to a direct devDependency in
`frontend/package.json`. Reuse the existing `frontend/playwright.config.ts`
(`testDir: 'e2e'`, chromium + iphone-se projects). Specs renamed to use
`phase10a5-` prefix (matching the phase that ships them):

- `phase10a5-risk-warn.spec.ts` — submit a near-cap order, see WARN row in
  TradeTicketModal, click acknowledge gate, place succeeds.
- `phase10a5-risk-block.spec.ts` — submit a kill-switch-active order, see
  BLOCK row, place button stays disabled.
- `phase10a5-admin-risk-crud.spec.ts` — `/admin/risk` page: create cap →
  edit → soft-delete; verify pubsub-driven cache invalidation (FE refetches
  within 5s).
- `phase10a5-kill-switch.spec.ts` — `/admin/accounts` page: toggle
  kill-switch on a paper account, verify subsequent preview blocks.

**Auth strategy.** A new dev-only middleware in the FastAPI app honors
`X-E2E-Token: <secret>` and creates a synthetic CF Access identity. Gated
on `APP_ENV=e2e` (asserts on app startup: dev-bypass MUST be off in any
non-e2e environment). Secret is a 32-byte URL-safe token stored in
`app_secrets.system.e2e_bypass_token`; rotated independently from prod
secrets. The CF Access JWT replacement is generated by the middleware on
demand from the request's `X-E2E-Token` so the rest of the auth chain
behaves identically to a real CF Access request.

**CSRF strategy.** New GET endpoint `/api/csrf/nonce` returns the current
order-cap-prefix CSRF nonce. Fixture helper in `frontend/e2e/fixtures.ts`
fetches it once per spec setup, attaches `X-CSRF-Token` to every
state-changing request.

**Seed strategy.** New `frontend/e2e/seed.sql` runs once via the existing
fixture pattern from `phase9-charting.spec.ts`. Seeds:

- one paper broker_account UUID with known alias.
- one `risk_limit` row with known caps (BP=$10k, daily-loss=$1k).
- pre-resolved instruments + symbol_aliases for SPY conid 756733.
- `account_kill_switch` row in disabled state.

The seed is **idempotent** (uses ON CONFLICT) — re-running before each
spec is safe.

**Race / mutex.** The deployed `https://dashboard.kiusinghung.com` is the
same surface that nightly-real-broker workflows hit. The new workflow
adds:

```yaml
concurrency:
  group: e2e-${{ env.E2E_BASE_URL }}
  cancel-in-progress: false
```

so PR Playwright doesn't race nightly real-IBKR. The seed runs in a
dedicated `e2e_isolated_` prefix namespace (separate broker_account UUIDs)
so even if mutex fails, real-broker tests use different accounts.

**Workflow shape.** New file `.github/workflows/playwright-e2e.yml`:
ubuntu-latest, headless, chromium + iphone-se. Triggers: `pull_request`
(any branch touching `frontend/**` or `backend/app/api/risk*`,
`backend/app/api/admin_risk*`, `backend/app/services/risk_service.py`,
`backend/app/services/account_kill_switch_service.py`,
`backend/app/services/risk_limits_service.py`,
`backend/app/services/orders_service.py`) + `schedule: 'cron: 17 3 * * *'`
(nightly).

Workflow steps: install pnpm/node 24 → `pnpm install` → seed via `psql`
(uses the `e2e_bypass_token` as a secret) → `pnpm playwright install
chromium webkit` → `pnpm playwright test` → upload artifacts on failure
(traces, screenshots, videos).

§10 C3 commit estimate raised from 5 to 8–10 (auth middleware + CSRF
endpoint + seed.sql + workflow + 4 specs + reviewer fixes).

**C4. `tests/real_broker/pyproject.toml` split.** Move
`[dependency-groups].real-broker` from `backend/pyproject.toml` into a new
uv project at `backend/tests/real_broker/pyproject.toml`. New project
shape:

```toml
[project]
name = "trading-dashboard-real-broker-tests"
version = "0.0.0"
requires-python = "==3.14.*"
dependencies = [
    "alpaca-py>=0.30",
    "schwabdev==3.0.3",
    "pytest>=9",
    "pytest-asyncio>=0.25",
    "httpx>=0.27",
    "structlog>=24",
]

[tool.uv.sources]
app = { path = "../../", editable = true }
```

New `backend/tests/real_broker/conftest.py` ensures the parent `backend/`
is on `sys.path`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
```

Workflows that previously ran `uv sync --group real-broker` from `backend/`
switch to `uv sync && uv run pytest` from `backend/tests/real_broker/`.
The 7 nightly workflows (`nightly-real-alpaca-crypto`, `-equity`, `-futu`,
`-ibkr`, `-schwab`, `-schwab-trade`, `weekly-real-schwab-drift`) each get
the same path change.

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

**ROADMAP.md "Tag history" appendix** (NEW — MED-5). A short section after
the table mapping pre-rewrite versions to phases so old commit-message /
memory citations stay decodable:

```
### Tag history (new ROADMAP.md appendix)

The version ladder above reflects shipped reality as of 2026-05-11. Earlier
drafts of this table listed:

  Phase 8 = v0.8.0  (subsequently split into 8a/8b/8c at 0.8.0 / 0.9.0 / 0.10.0)
  Phase 9 = v0.9.0  (actually shipped as v0.11.0)
  Phase 10 = v0.10.0 (Phase 10a shipped as v0.12.0; 10a.5 + 10b backlog)

Any commit message, CHANGELOG entry, or memory file dated before
2026-05-11 referring to those legacy tag names is correct-for-the-time;
this appendix maps them forward.
```

This breadcrumb deletes the renumbering ambiguity permanently.

## 6. Control flow at a request boundary

```
place_order(req)  [preview_order is similar; counter side-effects skipped]
    │
    ▼
station 1: parse + validate                              (unchanged)
station 2: account resolve                               (unchanged)
station 3: capability check                              (unchanged)
station 4: risk gate
    │
    │   ┌── ctx.instrument_id = await _resolve_instrument_id(
    │   │       db, broker_id=ctx.broker_id, conid=req.conid)
    │   │                          ← B2 lights up; eager-creates on cold path
    │   │
    │   └── RiskService.evaluate(ctx, mode=PLACE)
    │           │
    │           ├── kill_switch                           (10a, unchanged)
    │           │
    │           ├── max_daily_loss                        ← A1+A2+A3: real numbers
    │           │      ├─ row is None        → WARN (pnl_stale)
    │           │      ├─ staleness > 90s    → WARN (pnl_stale)
    │           │      └─ realized+unrealized vs cap
    │           │
    │           ├── pdt                                    ← A4: token issued
    │           ├── position_concentration                 ← B2: fires now
    │           ├── buying_power_buffer                    ← A4: token issued
    │           └── margin_preview (sidecar RPC)           (10a, unchanged)
    │
    ▼
[gate passed; tokens (pdt_token, bp_token) held by caller]
    │
    ▼
station 5: dispatch
    │     ┌── try: broker.place_order(...)
    │     │
    │     ├── success → commit_pdt(token) + commit_bp_finalize(token)
    │     │            (DEL the token; counter stays decremented; reconcile
    │     │             carries through on next 30s discoverer cycle)
    │     │
    │     └── exception → revert_pdt(token) + revert_bp(token)
    │                     (atomic Lua: DEL token + INCR counter; idempotent
    │                      if called twice)
    │
    ▼
audit row in risk_decisions                              ← A5: per per-mode table
   (place/modify: always; preview: WARN+BLOCK only)
   (dedupe: 30s SETNX on account+conid+side+rounded_qty)
   (insert via dedicated SessionLocal — orders_service.py:387 isolation
    invariant preserved)
```

## 7. Error handling

1. **`pnl_intraday` upsert failure → log + counter, don't break discoverer.**
   Failure mirrors how the existing summary upsert handles SQL errors:
   structured log, `pnl_intraday_upsert_failures_total`, continue cycle.
   Risk-gate sees row-not-found via the view (no LEFT JOIN coalesce now) →
   `_check_max_daily_loss` returns **WARN** (`pnl_stale`), not silent
   ALLOW. Same posture if view row exists but `staleness > 90s`.

2. **Counter `revert` / `commit` failure → log + counter, don't fail
   dispatch.** Dispatch already succeeded (commit path) or already failed
   (revert path); stale counter self-heals in 30s via the discoverer poll.
   Adding a dispatch failure on counter-cleanup failure trades small
   accounting drift for a hard outage — wrong trade-off.
   `risk_counter_cleanup_failures_total` counter.

3. **InstrumentResolver returns `None` → concentration check returns ALLOW**
   on the warmup path only. Eager creation in B1 lights up subsequent
   requests; the gate writes `risk_gate_concentration_skipped_unresolved_total`
   when this path fires so ops can see the warmup-rate.

4. **Audit row insert failure → metric + drop.** Mirrors 10a's BLOCK-path
   fail-OPEN policy. `risk_audit_insert_failures_total` counter (already
   exists, extended to cover all three actions). New §8 alert
   (`risk_audit_insert_failures_total > 10/min for 5m`) catches the
   higher-blast-radius failure case now that ALLOW path also writes.

5. **Test stub upgrade is NOT transparent.** C1 lands as a sequence of
   small commits with explicit per-test deltas; tests that previously
   short-circuited via `isinstance` now run real evaluation. Shadow-run
   state (C1 lands stubs; `isinstance` guard stays) lets us observe the
   change before C2 removes the guard. Pre-C2 verification grep ensures
   no `isinstance.*AsyncSession.*risk` references survive in
   `orders_service.py`.

6. **Multi-currency account → writer drops mismatched-currency positions
   with `pnl_intraday_currency_skip_total` counter.** Gate operates on
   the base-currency aggregate; multi-currency operators get a
   known-degraded WARN-only gate until Phase 10b's FX conversion lands.

7. **Sidecar 503 / maintenance window → A2 writer skips the upsert
   (does NOT write zero).** Existing `_fetch_summary == None` branch at
   `brokers.py:1362` already skips on this path; the writer inherits
   that branch. Combined with §4 view's no-LEFT-JOIN, the gate gives a
   WARN during maintenance.

8. **Crash between gate-pass and dispatch-result → token leak +
   counter-double-decrement.** Token TTL = 86400s caps the leak; next
   discoverer reconcile (30 s) overwrites the counter with broker truth
   and clears matching tokens via `UNLINK risk:*:tok:*`. Operator-facing
   metric `risk_counter_orphan_tokens_total` exposes the rate.

## 8. Observability

New metrics:

- `pnl_intraday_rows_total{account_id}` — gauge per account so a single
  account's growth anomaly is observable.
- `pnl_intraday_upsert_failures_total` — counter.
- `pnl_intraday_last_update_seconds{account_id}` — gauge of age-of-newest
  row; alert if **>90s** during market hours (3× the 30s cycle).
- `pnl_intraday_currency_skip_total{broker_id}` — counter of position rows
  dropped at writer due to mismatched currency vs `currency_base`. Surfaces
  multi-currency degradation rate.
- `pnl_intraday_writer_source_drift_seconds{broker_id}` — gauge; time-of-day
  at which each broker's intraday counter resets. Operator-facing
  visibility into Schwab/Alpaca/IBKR session-boundary divergence.
- `risk_counter_cleanup_failures_total` — counter.
- `risk_counter_orphan_tokens_total` — gauge from periodic SCAN MATCH in
  the discoverer fan-out. Surfaces rate of crash-between-gate-and-dispatch.
- `risk_audit_dedupe_skipped_total` — counter; new audit-row dedupe.
- `risk_decisions_rows_total{verdict}` — gauge of audit-row growth; alert
  on velocity > 100k rows/24h.
- `risk_gate_concentration_skipped_unresolved_total` — counter; B1 cold-path
  warmup rate.

Existing (extended):

- `risk_audit_insert_failures_total` — now covers ALLOW/WARN paths; alert
  threshold added: `> 10/min for 5m`.

## 9. Testing

| Chunk | New tests | Existing tests touched |
|---|---|---|
| A1 | `tests/integration/test_alembic_0037.py` — upgrade idempotency, view column contract (`realized`, `unrealized`, `summary_updated_at`, `staleness`), downgrade safety, `idx_risk_decisions_verdict_time` exists, `prune_risk_decisions_allow(30)` deletes only ALLOW > 30d | — |
| A2 | `tests/services/test_pnl_intraday_writer.py` — upsert idempotent, summary_updated_at guard rejects stale, IS-DISTINCT-FROM no-op when unchanged, prune drops >30d rows, currency-mismatch dropped + counter inc, source_label set | `tests/services/test_brokers.py` — extend discoverer-loop test to assert one upsert per cycle per account, savepoint isolation (PnL failure doesn't roll back NLV), maintenance-window 503 skips upsert (not writes zero) |
| A3 | covered by A1 + A2; new staleness-branch test in `test_risk_service.py::test_max_daily_loss_pnl_stale_warn` | `tests/services/test_risk_service.py::test_max_daily_loss_*` — flip seed pattern from "zero stub" to "row exists"; add row-missing case returns WARN |
| A4 | `tests/services/test_risk_inflight_counters.py` — token roundtrip (decrement → commit → counter stays; decrement → revert → counter restored), **double-revert idempotent**, reconcile UNLINKs tokens before counter overwrite, orphan token gauge increments on crash-simulating skip | `tests/services/test_risk_service.py::test_pdt_*` + `test_buying_power_*`; `tests/integration/test_orders_service_dispatch.py` — token threaded through success/failure paths |
| A5 | `tests/integration/test_risk_decisions_audit.py` — add ALLOW path (place_order), WARN path (place_order), WARN path (preview_order audited), ALLOW path (preview_order NOT audited — dedupe NaN), 30s SETNX skips duplicate ALLOW within window, dedicated SessionLocal invariant preserved | existing BLOCK case unchanged |
| B1+B2 | `tests/services/test_instrument_id_resolution.py` — `find_by_alias` happy path, no-alias returns None, eager-create cold path INSERTs, idempotent (concurrent callers don't duplicate); audit row carries instrument_id when concentration BLOCKs | the 5 `test_risk_service.py` callers swap from `None` to expecting an int; the 2 `_audit_risk_decision*` test sites assert instrument_id present |
| B3 | `tests/integration/test_concentration_check_e2e.py` — concentrated position triggers BLOCK; fresh conid no-ops (one-cycle warmup); audit row shows correct instrument_id | — |
| C1+C2 | New `@pytest.mark.no_risk_gate` marker behavior tests; verification CI step (grep gate); per-test-family transparency commit messages list deltas | every existing test must still pass; shadow-run intermediate state observed before C2 lands |
| C3 | 4 specs in `frontend/e2e/`: phase10a5-risk-warn, phase10a5-risk-block, phase10a5-admin-risk-crud, phase10a5-kill-switch | shared fixtures from `frontend/e2e/fixtures.ts` extended (auth + CSRF + seed.sql); concurrency mutex blocks nightly real-broker overlap |
| C4 | `tests/real_broker/conftest.py` + `pyproject.toml` (with `app` editable source) + path adjustments | 7 nightly workflows |

Coverage target: ≥80% per `~/.claude/rules/testing-another.md`. Each chunk
lands with tests in the same commit (TDD).

**Explicitly NOT tested in 10a.5 (and why):**

- Multi-worker race on Redis counters — still owned by Phase 24.
- Real broker PnL accuracy (Schwab `realized_pnl` vs Alpaca `today_pnl`
  semantic divergence) — spot-check during operator validation, not in CI.
- Playwright cross-browser — chromium + iphone-se only, per existing config.
- Failure-mode chaos for the discoverer — the 30s self-heal is already
  tested-in-prod from 10a.

## 10. Deliverables

| Chunk | Deliverable | Files touched | ~Commits |
|---|---|---|---|
| A0 | Spec-revision commit (this revision applied) | `docs/superpowers/specs/2026-05-11-phase10a5-cleanup-design.md` | 1 |
| A1 | Alembic 0037 (`pnl_intraday` + view + `idx_risk_decisions_verdict_time` + `prune_risk_decisions_allow`) | `backend/alembic/versions/0037_*.py`, `backend/tests/integration/test_alembic_0037.py` | 2 |
| A2 | `PnlIntradayWriter` + BrokerDiscoverer fan-in (existing line-1320 site) + 30-day prune + 90s alert + multi-currency drop | `backend/app/services/pnl_intraday_writer.py`, `backend/app/services/brokers.py`, `backend/app/core/metrics.py`, `backend/tests/services/test_pnl_intraday_writer.py`, `backend/tests/services/test_brokers.py` | 4 |
| A3 | `risk_service` test flip + staleness branch | `backend/app/services/risk_service.py`, `backend/tests/services/test_risk_service.py` | 1 |
| A4 | Token-bearing counter API + reconcile-aware UNLINK + orders_service wiring | `backend/app/services/risk_inflight_counters.py`, `backend/app/services/orders_service.py`, `backend/app/services/brokers.py`, `backend/tests/services/test_risk_inflight_counters.py` | 4 |
| A5 | ALLOW/WARN audit widening + per-mode policy + dedupe SETNX | `backend/app/services/orders_service.py`, `backend/tests/integration/test_risk_decisions_audit.py` | 2 |
| B1+B2 | `InstrumentResolver.find_by_alias` + `_resolve_instrument_id` + **7-site** swap (5 gate + 2 audit) | `backend/app/services/quotes/instrument_resolver.py`, `backend/app/services/orders_service.py`, `backend/tests/services/test_instrument_id_resolution.py` | 3 |
| B3 | Concentration integration tests | `backend/tests/integration/test_concentration_check_e2e.py` | 1 |
| C1+C2 | Stub upgrade (per-test deltas) + shadow-run + `isinstance` gate drop + CI verification grep | `backend/tests/services/_fakes.py`, `backend/app/services/orders_service.py`, `.github/workflows/main-ci.yml` | 4–5 |
| C3 | Playwright direct devDep + auth middleware + CSRF endpoint + seed.sql + workflow + 4 specs + concurrency mutex | `frontend/package.json`, `frontend/e2e/{fixtures.ts, seed.sql, phase10a5-*.spec.ts}`, `backend/app/api/e2e_bypass.py`, `backend/app/api/csrf.py`, `.github/workflows/playwright-e2e.yml` | 8–10 |
| C4 | `tests/real_broker/pyproject.toml` split | `backend/tests/real_broker/{pyproject.toml,conftest.py}`, 7 nightly workflows | 2 |
| D1 | ROADMAP rewrite + Tag history appendix + docs sweep | `docs/ROADMAP.md`, `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` | 1 |
| D2 | Phase close-out commit + tag | memory `phase10a5_shipped.md` + `phase10_status_clarification.md` update, git tag v0.12.1 push | 1 |

**Total: ~34–37 commits over 4–6 days** (revised up from ~25 after
applying architect findings).

## 11. Sequencing + parallelism

**Merge-conflict reality (HIGH-7 fix):** A4 and B2 **both** modify
`orders_service.py`. They cannot run in parallel branches and merge
naively. Two safe approaches:

1. **Snippet-file parallelism** (memory `feedback_snippet_file_parallelism.md`):
   both Codex agents emit patches to `/tmp/orders_service_a4.patch` and
   `/tmp/orders_service_b2.patch`; Opus controller dedupes imports and
   applies in one commit.
2. **Sequential split**: A4 lands first (token threading + dispatch wiring),
   then B2 rebases on top (5-site instrument_id swap + 2 audit-row swaps).

Recommend approach 1; falls back to 2 if patch dedupe is non-trivial.

Day-by-day plan:

```
                  Day 1                Day 2                Day 3              Day 4               Day 5-6
                  ──────────────       ──────────────       ──────────────     ──────────────      ──────────────
Codex parallel-1  Chunk A0+A1+A2       Chunk A3             Chunk A4 ┐         Chunk A5            Phase close-out:
                  (spec rev,           (view+risk_service                                          C4 deps split,
                   table+writer)        staleness branch)                                          D1 ROADMAP,
                                                                                                   D2 tag v0.12.1
Codex parallel-2  Chunk B1             Chunk B3                      ┴── merge
                  (resolver method                                       (snippet-
                   + helper)                                              file
                                                                          dedupe)
                                                                         Chunk B2
                                                                         (7-site swap)

Codex parallel-3                                            Chunk C1+C2       Chunk C3
                                                            (stubs +         (Playwright)
                                                             shadow-run)

Reviewer chain    end-of-day:                               end-of-day:        end-of-day:        end-of-day:
                  spec/code/sec/db                          spec/code/sec/db   spec/code/sec/db   final ARCHITECT
                  for A1+A2                                 for A4+B2 bundle   for C3
                                                                               (Playwright)
```

A and B touch overlapping `orders_service.py`; merge gate on Day 3 enforces
the order. C waits on A+B because Playwright specs assert real gate
behavior. Per-chunk reviewer chains run end-of-chunk per
`feedback_review_per_chunk.md` (haiku spec/py/ts → sonnet code/security/db
→ opus final ARCHITECT-REVIEW).

## 12. Open questions (none blocking — all reduced to tactical after architect-review)

1. **Per-request memoization cache for `_resolve_instrument_id`** —
   FastAPI `Depends`-scoped `dict` to dedupe preview+place_order+modify
   conid → instrument_id lookups within one request scope (LOW-6). Defer
   if preview rate is low. Final call during B1 impl.
2. **Snippet-file vs sequential merge for A4 + B2 `orders_service.py`
   overlap** — pick at the start of Day 3. Recommend snippet-file
   per `feedback_snippet_file_parallelism.md`.
3. **`risk_audit_dedupe_skipped_total` alert threshold** — set after
   first week of production volume. Tactical.

Architectural decisions that were "open" in the draft but are now
**locked**:

- **PnL source field** — `SUM(Position.realized_pnl_today)` (proto field
  7), never `Summary.realized_pnl` (proto field 3). CRIT-1 fix.
- **View shape** — no LEFT JOIN; missing row → WARN. CRIT-2 fix.
- **Discoverer cycle** — 30 s (matches `brokers.py:1036`). CRIT-3 fix.
- **A2 integration point** — existing fan-out at `brokers.py:1320`, own
  savepoint. CRIT-3 fix.
- **Counter token contract** — token sentinel + Lua GET-DEL-INCR for
  revert + GET-DEL for commit. 86400s TTL matching counter. HIGH-2 fix.
- **Reconcile-aware tokens** — `UNLINK risk:*:tok:*` before counter
  overwrite. HIGH-9 fix.
- **Multi-currency policy** — writer drops mismatched-currency rows with
  `pnl_intraday_currency_skip_total`. Gate is known-degraded for
  multi-currency, not silently wrong. HIGH-1 fix.
- **Audit emission policy** — `place_order`/`modify_order` audit all 3
  actions; `preview_order` audits WARN+BLOCK only (not ALLOW). 30s SETNX
  dedupe. HIGH-4 fix.
- **Resolver API** — read-only `find_by_alias` + eager-create on cold
  path. HIGH-3 fix.
- **`PnlIntradayWriter` engine ownership** — share `session` at
  `brokers.py:1360`, separate savepoint per upsert.
- **C3 auth strategy** — dev-only `X-E2E-Token` middleware gated on
  `APP_ENV=e2e`, synthetic CF Access identity. HIGH-6 fix.
- **Playwright workflow trigger** — PR + nightly with concurrency mutex.

## 13. Phase exit criteria

- All 7 in-scope items shipped to `main` (effectivity blockers ×4 + test
  infra ×3).
- Main CI green.
- Playwright workflow green for the 4 new specs (PR + nightly).
- 5 of 7 nightly real-broker workflows green (ibkr + schwab-trade stay
  deferred per operator-runbook scope; they failed at v0.12.0 too).
- D1: ROADMAP.md table reflects shipped reality (+ Tag history appendix);
  `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` updated.
- D2: `phase10a5_shipped.md` memory written;
  `phase10_status_clarification.md` updated to mark 10a.5 done + flag
  10b as remaining; git tag `v0.12.1` pushed to origin.
- Verification grep gate passes: `rg -n 'isinstance\(db,\s*AsyncSession\).*risk'
  backend/app/services/orders_service.py` is empty.
- `pnl_intraday_last_update_seconds < 90s` for all paper accounts in 24h
  post-deploy monitoring (proves A2 fan-in is actually writing).
- `risk_decisions` rows for ALLOW + WARN appear in the admin feed for at
  least one paper-mode test order (proves A5 widening took effect).
- `risk_counter_orphan_tokens_total` < 10 in 24h post-deploy (proves A4
  reconcile-aware cleanup is functioning).
