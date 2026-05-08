# Phase 10a — Risk engine + pre-trade gate (mandatory chokepoint)

**Status:** brainstorm complete — ARCHITECT-REVIEW pending
**Target version:** v0.12.0
**Date:** 2026-05-08
**Predecessor:** v0.11.0.1 (Phase 9.6 CI-debt close-out · `677dab9`)
**Phase split:** 10a (this spec) → 10b sizing calculator → 10c multi-account rollup UI
**Codex availability:** rate-limited until ~2026-05-12 — Claude main-thread implements per
`feedback_codex_fallback.md`; canary first chunk on Codex if it returns mid-phase.

---

## 0. Why this phase exists

ROADMAP architectural pillar #8 (`docs/ROADMAP.md:60`) locks: **"Risk engine before bots.
Phase 10 ships before Phase 20. Bots cannot bypass the pre-trade gate."** Today the order
write path (`backend/app/services/orders_service.py:124-149`) runs three validation
stations — `kill_switch_enabled` → `compute_broker_maintenance` → `capability.is_supported`
— and then dispatches straight to the broker. There is no notion of a per-account risk
budget, position concentration cap, day-trade counter, or buying-power buffer.

Phase 10a inserts a fourth station — the **risk gate** — between capability and dispatch,
and lays the schema + service + sidecar RPCs that future phases consume:

- Phase 11 (Alerts) listens on `pg_notify('risk_decision', ...)` for BLOCK fan-out.
- Phase 18 (Scanner) reads the same `risk_limits` to filter candidate trades.
- Phase 20-22 (Bot engine) cannot place orders that the gate would block — pillar #8.
- Phase 23 (UK CGT) extends the gate with "would trigger 30-day b&b matching" warnings.

Phase 10b will ship the position-sizing calculator (Kelly / fixed-fractional / vol-target)
that reads the same caps. Phase 10c ships the multi-account rollup UI that aggregates
risk-aware NLV / exposure / per-asset-class delta across brokers.

This spec covers **10a only**.

---

## 1. Scope

### In scope (10a)

- New tables `risk_limits` + `risk_limits_history` + `account_kill_switches` +
  `account_kill_switches_history` (Alembic 0026).
- `RiskService` — pure-logic evaluator that returns `GateVerdict` for an
  `EvaluationContext`.
- Seven checks: account-level kill switch (NEW) + broker-level kill switch (composes Phase
  5b H0) + max-daily-loss (realized + unrealized intraday P&L vs cap, in account base
  currency) + PDT (broker-reported `dayTradesRemaining` from Schwab / IBKR / Alpaca
  account fields) + position-concentration-pct (post-trade notional ÷ NLV; aggregates
  same-instrument across accounts of the same broker) + buying-power-buffer (require
  ≥ X% headroom of cached BP after the order's notional commit) + sidecar margin preview.
- Verdict shape: ALLOW / WARN / BLOCK per check; gate aggregates to `{final_verdict,
  blockers, warnings, lat_ms}`. WARN surfaces in `/api/orders/preview` as a yellow banner;
  BLOCK rejects `/api/orders` with HTTP 422.
- New sidecar RPC `PreviewOrder` — implemented in `sidecar_ibkr` (via
  `ib_async.placeOrder(whatIf=True)`) and `sidecar_schwab` (via `POST
  /trader/v1/accounts/{accountHash}/previewOrder`). `sidecar_alpaca` returns
  `UNIMPLEMENTED`; gate WARN-falls-back to cached buying power.
- `risk_decisions` audit table — populated on `place_order` / `modify_order` only (preview
  path is structlog-only). `pg_notify('risk_decision', ...)` on BLOCK for Phase 11 hook.
- Validation gate inserted at station 4 in `orders_service.py`, preserving the Phase 5b
  H0 + Phase 8a CRIT-3 ordering invariant.
- Atomic refactor of `orders_service.py::place_order` and `modify_order` extraction (the
  Phase 10 deferred-backlog item from `docs/ROADMAP.md:104`); extract + gate insert ship
  in the same chunk so the diff is one atomic before/after.
- Reconciliation of the FE/BE `BrokerCapabilitiesResponse` shape mismatch
  (`frontend/src/services/capabilities/types.ts` KNOWN ISSUE; the risk gate consumes the
  same matrix, so 10a is the natural fix site — `docs/ROADMAP.md:96-102`).
- Backend admin API: `/api/admin/risk-limits` CRUD + `/api/admin/accounts/{id}/kill-switch`
  toggle.
- FE: TradeTicket WARN/BLOCK banners + `/admin/risk` page (limits CRUD) +
  `/admin/risk/decisions` (recent decisions feed) + `useRiskLimits` /
  `useAccountKillSwitch` hooks.
- One-line fix to `docs/PHASE-WORKFLOW.md` line 42 — the stale "reviews fire at every
  commit boundary" sentence is superseded by the per-chunk rule
  (`feedback_review_per_chunk.md`).

### Out of scope (deferred)

| Item | Defer to |
|---|---|
| Position-sizing calculator (Kelly / fixed-fractional / vol-target) | **10b** |
| Multi-account portfolio rollup UI (cross-broker aggregate NLV / exposure) | **10c** |
| `risk_decisions` TimescaleDB hypertable | Phase 24 (single-user volume ≪ 1k rows/day) |
| Multi-worker uvicorn implications for the limits cache | Phase 24 |
| Phase 23 CGT pre-trade hook ("would trigger 30-day b&b matching") | Phase 23 |
| Risk-engine-driven flatten-all on max-loss-exceeded | Operator action only in 10a |
| Two-tick guard before `BrokerDiscoverer` position wipe | Defer to a 10a follow-up; not in
  the gate code path itself, but informs concentration-check robustness — flagged for
  10a.5 if the integration tests surface a window |
| Custom per-instrument concentration caps | Phase 12+ (when polymorphic `contract_details`
  lands; current scope_type ENUM only covers `global` / `broker` / `account`) |

### Success criteria

- Pre-trade gate p95 latency ≤ **150ms** for cached cases; ≤ **3.5s** when sidecar margin
  preview is required (3s sidecar timeout + budget for 6 other checks).
- ≥ **80%** test coverage per CLAUDE.md; reviewer chain at end of every chunk.
- Zero unaddressed CRIT/HIGH/MED findings from architect review or per-chunk reviewer
  chain at phase tag.
- Validation gate ordering invariant preserved — proven by integration test
  `test_risk_gate_orders.py::test_station_ordering`.
- Admin can create + edit + delete risk limits + toggle account kill switches via the
  `/admin/risk` UI; FE TradeTicket honors WARN and BLOCK without manual reload.

---

## 2. Architecture

```
                        existing chokepoint                         NEW in 10a
                  ┌─────────────────────────┐         ┌────────────────────────┐
  POST /api/orders/preview                            │  RiskService (pure)    │
  POST /api/orders                                    │   evaluate(ctx) →      │
  POST /api/orders/{id}/modify    ──────►  ──────────►│   GateVerdict          │
                                  validation gate     │   {final, blockers,    │
                                  (orders_service)    │    warnings, lat_ms}   │
                                                      └────────┬───────────────┘
                                                               │ reads
                                                  ┌────────────┴────────────┐
                                                  │                         │
                                          risk_limits + kill_switches    live state:
                                          (hybrid scope, cached 60s)       - positions snapshot (existing)
                                                                            - NLV / BP cache (Phase 5a)
                                                                            - day-trade counter (broker-reported)
                                                                            - sidecar PreviewOrder RPC (NEW)
```

**Validation gate ordering** (preserves 5b H0 + 8a CRIT-3):

| Station | Check | Failure |
|---|---|---|
| 1 | `broker.kill_switch_enabled` (Phase 5b, `app_config`) | 503 |
| 2 | `compute_broker_maintenance(label).active` (Phase 5b daily-window guard) | 503 + `Retry-After` |
| 3 | `capability.is_supported(broker_id, asset_class, order_type, tif)` (Phase 8a) | 422 capability_not_supported |
| 4 | **`RiskService.evaluate(ctx)`** (NEW) | 422 risk_gate_blocked / pass through with WARN |
| 5 | dispatch to broker (existing) | broker errors |

**Key invariants:**

- The risk gate is a **fourth station**, not a replacement for any prior check.
- `RiskService` is pure — takes `EvaluationContext` + injected dependencies (db session,
  NLV cache, sidecar client). No global singletons. Unit-testable without HTTP / broker
  RPCs.
- One sidecar RPC added per broker that supports it. Alpaca falls back to cached BP +
  WARN.
- Audit asymmetry: preview → structlog only; `place_order` / `modify_order` → DB row +
  `pg_notify` on BLOCK.
- Account-level kill switch is independent of broker-level (Phase 5b stays in
  `app_config`; Phase 10a adds `account_kill_switches` table). Gate composes both.

---

## 3. Data model (Alembic 0026)

### `risk_limits` — hybrid-scope cap config

```sql
CREATE TYPE risk_scope_type AS ENUM ('global', 'broker', 'account');
CREATE TYPE risk_limit_kind AS ENUM (
  'max_daily_loss_currency_base',
  'max_position_concentration_pct',
  'pdt_warn_remaining',
  'min_buying_power_buffer_pct'
);

CREATE TABLE risk_limits (
  id            BIGSERIAL PRIMARY KEY,
  scope_type    risk_scope_type NOT NULL,
  scope_id      TEXT,
  limit_kind    risk_limit_kind NOT NULL,
  limit_value   NUMERIC(20, 8) NOT NULL,
  warn_at_pct   NUMERIC(5, 2),
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  notes         TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by    TEXT NOT NULL,

  CHECK ( (scope_type = 'global') = (scope_id IS NULL) ),
  CHECK ( warn_at_pct IS NULL OR (warn_at_pct >= 0 AND warn_at_pct <= 100) ),
  CHECK ( length(notes) <= 1000 ),
  UNIQUE (scope_type, scope_id, limit_kind)
);

CREATE INDEX idx_risk_limits_lookup ON risk_limits (scope_type, scope_id, limit_kind)
  WHERE is_active;

CREATE TABLE risk_limits_history (
  history_id    BIGSERIAL PRIMARY KEY,
  limit_id      BIGINT NOT NULL,
  scope_type    risk_scope_type NOT NULL,
  scope_id      TEXT,
  limit_kind    risk_limit_kind NOT NULL,
  limit_value   NUMERIC(20, 8) NOT NULL,
  warn_at_pct   NUMERIC(5, 2),
  is_active     BOOLEAN NOT NULL,
  notes         TEXT NOT NULL,
  changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  changed_by    TEXT NOT NULL
);
```

**Lookup walk** (`RiskService._resolve_limit`): `(account, kind)` → `(broker, kind)` →
`(global, kind)`. First active hit wins. Cached 60s in-process; bust via Redis pubsub
`app_config:invalidate:risk_limits` (mirrors `OrderCapabilityService` from Phase 8a).

### `account_kill_switches` — separate table per user direction (2026-05-08 brainstorm)

```sql
CREATE TABLE account_kill_switches (
  account_id    UUID PRIMARY KEY REFERENCES broker_accounts(id) ON DELETE CASCADE,
  is_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
  reason        TEXT NOT NULL DEFAULT '',
  enabled_at    TIMESTAMPTZ,
  enabled_by    TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  CHECK ( length(reason) <= 1000 ),
  CHECK ( (is_enabled IS FALSE) OR (enabled_at IS NOT NULL AND enabled_by IS NOT NULL) )
);

CREATE TABLE account_kill_switches_history (
  history_id    BIGSERIAL PRIMARY KEY,
  account_id    UUID NOT NULL,
  is_enabled    BOOLEAN NOT NULL,
  reason        TEXT NOT NULL,
  changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  changed_by    TEXT NOT NULL
);
```

Phase 5b's broker-level `app_config.broker.kill_switch_enabled` stays put — Phase 10a does
not migrate shipped Phase 5b infrastructure. Gate reads both sources and composes (BLOCK
if either is on).

### `risk_decisions` — audit trail

```sql
CREATE TYPE risk_verdict AS ENUM ('allow', 'warn', 'block');

CREATE TABLE risk_decisions (
  id              BIGSERIAL PRIMARY KEY,
  account_id      UUID NOT NULL REFERENCES broker_accounts(id),
  instrument_id   BIGINT REFERENCES instruments(id),
  side            TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  qty             NUMERIC(20, 8) NOT NULL,
  price           NUMERIC(20, 8),
  order_type      TEXT NOT NULL,
  time_in_force   TEXT NOT NULL,
  verdict         risk_verdict NOT NULL,
  blockers        JSONB NOT NULL DEFAULT '[]'::jsonb,
  warnings        JSONB NOT NULL DEFAULT '[]'::jsonb,
  evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  latency_ms      INT NOT NULL,
  attempt_kind    TEXT NOT NULL CHECK (attempt_kind IN ('place_order', 'modify_order')),
  request_id      TEXT NOT NULL
);

CREATE INDEX idx_risk_decisions_account_time ON risk_decisions (account_id, evaluated_at DESC);
CREATE INDEX idx_risk_decisions_blocked ON risk_decisions (evaluated_at DESC)
  WHERE verdict = 'block';
```

`pg_notify('risk_decision', json_build_object(...))` fires via INSERT trigger on
`verdict='block'`. Retention: plain table; monthly cleanup cron deferred to Phase 24.

---

## 4. Data flow

### Preview path (high-frequency, low-stakes)

```
FE TradeTicket.tsx
  └─ debounced 200ms
POST /api/orders/preview {account_id, side, qty, price, order_type, tif}
  └─ orders_service.preview_order  (stations 1-3 unchanged)
        └─ NEW station 4: RiskService.evaluate(ctx, mode='preview')
              ├─ load_caps(account_id, broker_id)  ← cached 60s
              ├─ check_account_kill_switch         → BLOCK if on
              ├─ check_broker_kill_switch          → BLOCK if on (composes Phase 5b)
              ├─ check_max_daily_loss              → BLOCK if exceeded; WARN @ warn_at_pct
              ├─ check_pdt                         → broker-reported counter
              ├─ check_position_concentration      → reads positions snapshot
              ├─ check_buying_power                → cached BP from Phase 5a
              └─ check_margin                      → sidecar PreviewOrder RPC
              ↓
              GateVerdict {final, blockers, warnings, lat_ms}
              ↓
              structlog.info("risk.evaluated", verdict=…, kind="preview", account=…)
              (NO DB row in preview path)
        ↓
PreviewResponse {ok, warnings, blockers, …}  → FE banner
```

### `place_order` / `modify_order` path (low-frequency, high-stakes)

```
FE confirms (CSRF nonce)
  └─ POST /api/orders {nonce, …}
        └─ orders_service.place_order  (stations 1-3 unchanged)
              └─ station 4: RiskService.evaluate(ctx, mode='place_order')
                    ↓ on ALLOW + WARN: continue
                    ↓ on BLOCK: raise RiskGateBlocked
                    ↓
              INSERT INTO risk_decisions (…)   ← in same DB transaction as the gate eval
                    ↓ trigger
              pg_notify('risk_decision', json) WHERE verdict='block'
                    ↓ on BLOCK
              HTTPException(422, {"error": {"code": "risk_gate_blocked",
                                            "blockers": […]}})
                    ↓ on ALLOW / WARN
              dispatch to broker (existing Phase 5b path)
```

### Cap-edit / kill-switch invalidation path

```
PUT /api/admin/risk-limits/{id}  (CSRF nonce required)
  └─ risk_limit_service.update(…)
        └─ INSERT INTO risk_limits_history (…)   ← UPDATE trigger
        └─ redis.publish('app_config:invalidate:risk_limits', {scope_type, scope_id})
        └─ all backend workers' RiskService cache → bust matching keys
```

Same pattern for `account_kill_switches`. Reuses existing `app_config_cache` infrastructure
from Phase 2.

### Failure-mode policy

| Failure | Policy | Reasoning |
|---|---|---|
| `risk_limits` DB unreachable | **fail-CLOSED → BLOCK** | Gate is the chokepoint; degraded gate ≠ open gate. |
| Sidecar `PreviewOrder` RPC timeout (3s) | **fail-OPEN with WARN** | Margin is one of seven checks; remaining six already evaluated; surfacing a blocker on broker hiccup would over-block. |
| Sidecar RPC returns "insufficient margin" | **BLOCK** | Authoritative broker answer. |
| Sidecar returns `UNIMPLEMENTED` (Alpaca) | **WARN** + use cached BP | Documented per-broker fallback. |
| NLV cache stale (> 60s per Phase 5a invariant) | **WARN** + use stale value | Don't block on cache freshness alone. |
| Positions snapshot stale (> last discoverer tick) | **WARN** | Concentration on stale snapshot still useful as sanity check. |
| `risk_decisions` INSERT fails | **fail-OPEN** for the order, raise alert | Audit failure must not block trades; metric `risk_audit_insert_failures_total` triggers alert. |

---

## 5. Sidecar RPC contract

`proto/broker.proto` — extend the existing service. `sidecar_ibkr` and `sidecar_schwab`
implement; `sidecar_alpaca` returns `UNIMPLEMENTED`.

```proto
message PreviewOrderRequest {
  string account_hash    = 1;
  string side            = 2;   // "buy" | "sell"
  string symbol          = 3;
  string asset_class     = 4;
  string order_type      = 5;
  string time_in_force   = 6;
  double qty             = 7;
  optional double limit_price = 8;
  optional double stop_price  = 9;
  string idempotency_key = 10;
}

message PreviewOrderResponse {
  bool   accepted               = 1;
  string reject_reason          = 2;
  optional double initial_margin     = 3;
  optional double maintenance_margin = 4;
  optional double commission         = 5;
  optional double available_funds_after = 6;
  optional double buying_power_after = 7;
  repeated string warnings           = 8;
  string raw_provider_payload        = 9;
}

rpc PreviewOrder(PreviewOrderRequest) returns (PreviewOrderResponse);
```

**Per-broker implementation:**

- **IBKR** — `ib_async.placeOrder(whatIf=True)`; existing connection pool (Phase 4); 3s
  timeout; per-client token bucket from Phase 9.
- **Schwab** — REST `POST /trader/v1/accounts/{accountHash}/previewOrder`; existing OAuth
  pool (Phase 7a); 3s timeout; 401-retry-once (Phase 9 pattern).
- **Alpaca** — handler returns `UNIMPLEMENTED`; gate code path catches and translates to
  WARN ("alpaca preview unavailable, BP cache only").

**Idempotency key**: `f"preview:{request_id}:{account_id}:{symbol}:{qty}:{side}"` — 5xx +
retry doesn't double-charge provider rate-limit budget.

---

## 6. Backend API surface

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `GET` | `/api/risk/limits` | List `risk_limits` (admin UI) | admin JWT |
| `POST` | `/api/admin/risk-limits` | Create one row (PUT-semantics) | admin JWT + CSRF nonce |
| `PUT` | `/api/admin/risk-limits/{id}` | Update one row | admin JWT + CSRF nonce |
| `DELETE` | `/api/admin/risk-limits/{id}` | Soft-delete (`is_active=false`); idempotent | admin JWT + CSRF nonce |
| `GET` | `/api/admin/accounts/{account_id}/kill-switch` | Read kill-switch status | admin JWT |
| `POST` | `/api/admin/accounts/{account_id}/kill-switch` | Toggle (`{is_enabled, reason}`) | admin JWT + CSRF nonce |
| `GET` | `/api/risk/decisions?account_id=&limit=` | Recent decisions feed | admin JWT |

`/api/orders/preview` and `/api/orders` (existing) extend response shape:

```ts
type PreviewResponse = {
  ok: boolean;
  warnings: Array<{check: string, message: string, value?: number, threshold?: number}>;
  blockers: Array<{check: string, message: string, code: string}>;
  // …existing fields
};
```

---

## 7. Frontend surface

- **TradeTicketModal** (`frontend/src/features/trade/`) — yellow WARN banner aggregates
  gate warnings; existing red error banner now renders 422 `risk_gate_blocked` with
  per-check rows.
- **`/admin/risk`** (NEW) — table of `risk_limits` rows with inline edit + scope picker.
  Reuses Phase 3 `DataTable` + `ColumnCustomizerDialog`.
- **Account-level kill-switch** — row-level switch on the existing `/admin/accounts` page
  using Phase 3 `Switch` primitive.
- **`/admin/risk/decisions`** (NEW) — recent decisions feed (last 50, filterable by
  account + verdict). Pure read-only.
- **`useRiskLimits`** hook — TanStack Query, 30s stale time, invalidates on admin write.
- **`useAccountKillSwitch(account_id)`** — same shape; mutates via POST.
- **`useBrokerCapabilities` / `BrokerCapabilitiesResponse` shape reconciliation** —
  fix the runtime mismatch documented in
  `frontend/src/services/capabilities/types.ts`. Backend returns flat list / asset-class
  dict; FE expects `combos`. Risk gate consumes the same matrix → reconciliation lands in
  10a Chunk D atomic refactor.

---

## 8. Testing strategy

### Unit tests — `backend/tests/services/test_risk_service.py`

Parameterized per check. ≥ 30 unit tests on `RiskService` alone:
- Account-kill-switch: 3 cases (off → ALLOW; on → BLOCK; toggle propagates after pubsub).
- Broker-kill-switch composes: 2 cases (off+off → ALLOW; off+on → BLOCK; on+off → BLOCK).
- Max-daily-loss: 4 cases (under cap; @ warn_at_pct; over cap; realized + unrealized
  composed).
- PDT broker-reported: 3 cases (>warn_remaining → ALLOW; ≤warn_remaining → WARN; 0 → BLOCK).
- Position-concentration: 5 cases (under; @ warn; over; missing positions snapshot →
  WARN; cross-account same-instrument aggregate).
- Buying-power: 4 cases (within buffer; below buffer → WARN; insufficient → BLOCK; cache
  >60s stale → WARN).
- Margin sidecar: 3 cases (sidecar OK + accepted; sidecar OK + reject_reason → BLOCK;
  sidecar timeout → fail-OPEN WARN).

### Integration tests — `backend/tests/integration/`

- `test_risk_gate_orders.py` — full preview + place_order round-trip; asserts station
  ordering (kill_switch → maintenance → capability → **risk** → dispatch). 8 cases.
- `test_risk_decisions_audit.py` — places blocked order; asserts row + `pg_notify`
  envelope shape. Outer-transaction fixture per
  `feedback_pytest_session_begin_commits.md` to avoid prod-DB leak.
- `test_alembic_0026.py` — migration up/down on real PG; asserts CHECK constraints + enum
  membership. Outer-transaction wrap.
- `test_risk_limits_admin.py` — CRUD round-trip; CSRF nonce required; history table
  populated; Redis invalidation pubsub fires.
- `test_account_kill_switch_admin.py` — toggle round-trip; history populated; gate honors
  immediately (no cache-stale window).

### Sidecar tests — `sidecar_ibkr/tests/`, `sidecar_schwab/tests/`

- `test_preview_order.py` — IBKR `WhatIfOrder` against fake `ib_async`; Schwab against
  recorded fake-server response (existing fake-server infra from Phase 8a).
- `test_preview_order_idempotency.py` — same `idempotency_key` twice → one provider call.

### E2E — `frontend/tests/e2e/`

- `phase10-risk-gate.spec.ts` — 4 flows: BLOCK on account-kill-switch enabled; WARN at
  80% concentration; BLOCK on insufficient BP; WARN on Alpaca margin-fallback. Mocks
  sidecar via existing fixture.
- `phase10-admin-risk.spec.ts` — operator creates a limit, edits a limit, toggles a kill
  switch; gate honors the change.

### Coverage target

≥ 80% per CLAUDE.md. RiskService is pure logic → expect 95%+. Integration uses
`httpx.AsyncClient` against in-process app (existing pattern).

---

## 9. Per-chunk reviewer plan (per-chunk, NOT per-commit)

| Chunk | Reviewer chain at chunk boundary |
|---|---|
| A — Schema | spec-compliance (haiku) + database-reviewer (sonnet) + python-reviewer (haiku) |
| B — RiskService + 7 checks | spec-compliance + python-reviewer + code-quality-reviewer (sonnet) + silent-failure-hunter (sonnet) |
| C — Sidecar PreviewOrder RPCs (IBKR + Schwab) | spec-compliance + python-reviewer + code-quality-reviewer + security-reviewer (sonnet) |
| D — orders_service extract + gate insert | **full 5-reviewer chain** — spec/py/code-quality/security/silent-failure (highest blast-radius chunk; gate-ordering invariant) |
| E — FE (TradeTicket WARN, /admin/risk pages) | spec-compliance + typescript-reviewer (haiku) + code-quality-reviewer + a11y-architect |
| F — Tests + close-out + PHASE-WORKFLOW.md L42 fix | spec-compliance + python-reviewer (test code) |

**Phase-level**: ARCHITECT-REVIEW (opus, user-scope) on this spec **before Chunk A**.
spec-compliance (haiku) at phase end before tag. The per-chunk cadence is reaffirmed by
user 2026-05-08 mid-brainstorm — explicitly NOT per-commit.

---

## 10. Chunk breakdown

| Chunk | Est. commits | Test scope | Notes |
|---|---|---|---|
| A — Alembic 0026 + models | 3-4 | migration test + model tests | history triggers; pubsub channel |
| B — RiskService + 7 checks | 5-7 | 30 unit tests | pure logic; main-thread-friendly while Codex out |
| C — Sidecar PreviewOrder RPCs | 5-6 | sidecar tests + fake-server | proto regen + two sidecar diffs |
| D — orders_service extract + gate insert | 6-8 | integration tests | **highest risk**; pre-flight gate-ordering test mandatory; FE/BE capability shape reconciled here |
| E — FE pages + hooks | 5-6 | E2E + Storybook | if Codex returns mid-phase, prefer Codex here |
| F — Tests catch-up + close-out | 3-4 | full sweep + reviewer chain | CLAUDE.md / CHANGELOG.md / TASKS.md updates + PHASE-WORKFLOW.md L42 fix |

**Total: ~30 commits across 6 chunks.** Estimated ~7-9 days at one-week-per-phase pacing
with main-thread implementation (Codex would compress).

---

## 11. Operational + security guardrails

- Repo public since 2026-05-08 (`feedback_public_repo_discipline.md`). New tables hold no
  secrets; `risk_decisions.request_id` is opaque correlation only. No new `.env` keys.
- All admin endpoints require CF Access JWT + CSRF nonce (Phase 8a MED-7 pattern).
  `updated_by` / `enabled_by` / `changed_by` populated server-side from JWT email claim,
  never from request body.
- structlog continues to redact secrets. `risk_decisions.blockers` / `warnings` JSONB are
  schema-typed (no free-text user input).
- `risk_limits.notes` and `account_kill_switches.reason` are `TEXT NOT NULL CHECK
  length(notes) <= 1000` — bounded operator free-text; FE escapes via React text-node
  rendering (no `dangerouslySetInnerHTML`).
- Pre-trade margin check uses sidecar idempotency keys to protect provider rate-limit
  budgets. IBKR `WhatIfOrder` consumes per-client token bucket from Phase 9.
- Test discipline: all migration / admin-CRUD / pubsub tests use the outer-transaction
  fixture (`feedback_pytest_session_begin_commits.md`) to avoid the
  `feedback_pytest_prod_db_wipe.md` foot-gun.

---

## 12. Open questions / risks

- **Two-tick guard on positions snapshot** — `docs/ROADMAP.md:103-105` flags this as a
  Phase 10 deferred item. The concentration check reads positions snapshot directly. If
  Chunk D integration tests surface a window where a buggy sidecar response zeros
  positions and the gate stops blocking, a 10a.5 follow-up adds the two-tick guard. Not
  in 10a's primary scope; integration tests will surface or absolve.
- **Schwab `previewOrder` REST coverage** — Schwab's preview endpoint may not return all
  fields uniformly across asset classes (equity vs option); spec assumes equity coverage
  in 10a. Option preview semantics revisit in Phase 12.
- **PDT counter source** — broker-reported only in 10a. Local fills-derived counter
  deferred (low value when broker is authoritative; revisit if broker counter proves
  unreliable).

---

## 13. Architect review — applied

*To be populated after `ARCHITECT-REVIEW` skill (opus, user-scope) runs against this
spec. Per `feedback_architect_findings_apply_through_medium.md`, all CRIT + HIGH + MED
findings get applied inline before Chunk A; only LOWs may defer.*
