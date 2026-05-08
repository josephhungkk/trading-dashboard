# Phase 10a — Risk engine + pre-trade gate (mandatory chokepoint)

**Status:** brainstorm + architect-review applied (3 CRIT + 4 HIGH + 10 MED inline; 4 LOW per §13) — awaiting user spec approval
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
- Seven checks:
  1. **Account-level kill switch** (NEW) — read from `account_kill_switches`.
  2. **Broker-level kill switch** — composes Phase 5b H0 (`app_config.broker.kill_switch_enabled`).
  3. **Max-daily-loss** (realized + unrealized intraday P&L vs cap, in account base
     currency). **Day boundary** = 00:00 in the broker's primary-exchange timezone (read
     from `app/services/market_calendar.py::market_close_tz` per account's broker; falls
     back to UTC when the account spans markets — documented per-broker default in
     `risk_limits.notes`). [M2]
  4. **PDT** — broker-reported `dayTradesRemaining` from Schwab / IBKR / Alpaca account
     fields, **plus an in-flight optimistic counter** in Redis (`risk:pdt:{account_id}`)
     that decrements at place_order time and reconciles to the broker-reported value at
     each discoverer poll. Closes the staleness window between polls. [H1]
  5. **Position-concentration-pct** (post-trade notional ÷ NLV) **aggregating same
     `instrument_id` across all accounts under the operator** (single-user dashboard;
     not just same broker). Cross-broker AAPL exposure caps as one. [H2]
  6. **Buying-power-buffer** — require ≥ X% headroom of `(cached BP − sum of in-flight
     LMT/STOP order commitments)`. The `orders` table is queried for OPEN/PENDING orders
     and their notional is subtracted from cached BP before the buffer check. [H3]
  7. **Sidecar margin preview** — `PreviewOrder` RPC (IBKR + Schwab) with **asymmetric
     fail-mode policy**: preview path fails OPEN with WARN (UX-friendly); place_order
     path fails CLOSED with `503 + Retry-After` so a broker hiccup never lets a
     possibly-margin-violating order through. [H4]
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
| **0** | **CF Access JWT verify + CSRF nonce consume** (existing Phase 2 + 5b infrastructure; explicit in the table to forbid leaking broker state to unauthenticated requests) [M1] | 401 / 403 |
| 1 | `broker.kill_switch_enabled` (Phase 5b, `app_config`) | 503 |
| 2 | `compute_broker_maintenance(label).active` (Phase 5b daily-window guard) | 503 + `Retry-After` |
| 3 | `capability.is_supported(broker_id, asset_class, order_type, tif)` (Phase 8a) | 422 capability_not_supported |
| 4 | **`RiskService.evaluate(ctx)`** (NEW) | 422 risk_gate_blocked / pass through with WARN |
| 5 | dispatch to broker (existing) | broker errors |

**Key invariants:**

- The risk gate is a **fourth station**, not a replacement for any prior check.
- `RiskService` is **deterministic given its inputs** (not pure in the FP sense — it does
  I/O via injected dependencies: db session, NLV cache, sidecar client, Redis counter).
  No global singletons. Unit-testable with mocked dependencies; integration-testable
  against real DB / Redis. [L1]
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
  CHECK ( length(notes) <= 1000 )
);

-- Two partial unique indexes — Postgres treats NULLs as distinct in plain UNIQUE,
-- which would let two `(global, NULL, max_daily_loss)` rows coexist. [C1]
CREATE UNIQUE INDEX uq_risk_limits_global_kind ON risk_limits (limit_kind)
  WHERE scope_type = 'global' AND scope_id IS NULL;
CREATE UNIQUE INDEX uq_risk_limits_scoped ON risk_limits (scope_type, scope_id, limit_kind)
  WHERE scope_id IS NOT NULL;

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

-- UPDATE trigger snapshots the OLD row into history on every change. [M3]
CREATE OR REPLACE FUNCTION fn_risk_limits_history() RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO risk_limits_history
    (limit_id, scope_type, scope_id, limit_kind, limit_value, warn_at_pct,
     is_active, notes, changed_at, changed_by)
  VALUES
    (OLD.id, OLD.scope_type, OLD.scope_id, OLD.limit_kind, OLD.limit_value,
     OLD.warn_at_pct, OLD.is_active, OLD.notes, now(), NEW.updated_by);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_risk_limits_history
  BEFORE UPDATE ON risk_limits
  FOR EACH ROW
  WHEN (OLD.* IS DISTINCT FROM NEW.*)
  EXECUTE FUNCTION fn_risk_limits_history();
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

-- Symmetric UPDATE trigger for account_kill_switches. [M3]
CREATE OR REPLACE FUNCTION fn_account_kill_switches_history() RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO account_kill_switches_history
    (account_id, is_enabled, reason, changed_at, changed_by)
  VALUES
    (OLD.account_id, OLD.is_enabled, OLD.reason, now(),
     COALESCE(NEW.enabled_by, OLD.enabled_by, 'system'));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_account_kill_switches_history
  BEFORE UPDATE ON account_kill_switches
  FOR EACH ROW
  WHEN (OLD.* IS DISTINCT FROM NEW.*)
  EXECUTE FUNCTION fn_account_kill_switches_history();
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
  latency_ms      INT NOT NULL CHECK (latency_ms >= 0),  -- [L2]
  attempt_kind    TEXT NOT NULL CHECK (attempt_kind IN ('place_order', 'modify_order')),
  request_id      TEXT NOT NULL,
  order_id        BIGINT REFERENCES orders(id) ON DELETE SET NULL  -- [M5] populated post-dispatch
);

CREATE INDEX idx_risk_decisions_account_time ON risk_decisions (account_id, evaluated_at DESC);
CREATE INDEX idx_risk_decisions_blocked ON risk_decisions (evaluated_at DESC)
  WHERE verdict = 'block';
```

**Minimal `pg_notify` payload** [M4] — Postgres NOTIFY has an 8KB payload cap; large
`blockers` JSONB could blow it. The trigger emits only
`{"id": <bigint>, "verdict": "block", "account_id": "<uuid>"}`; subscribers fetch the
full row by id when they need the detail.

```sql
CREATE OR REPLACE FUNCTION fn_risk_decisions_notify() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.verdict = 'block' THEN
    PERFORM pg_notify('risk_decision', json_build_object(
      'id', NEW.id, 'verdict', NEW.verdict, 'account_id', NEW.account_id::text
    )::text);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_risk_decisions_notify
  AFTER INSERT ON risk_decisions
  FOR EACH ROW EXECUTE FUNCTION fn_risk_decisions_notify();
```

**Orphan-row policy** [M5] — `order_id` is nullable; INSERT into `risk_decisions`
happens **after** broker dispatch returns (so `order_id` is set on dispatch success;
left NULL on dispatch failure with `verdict` reflecting the gate's verdict).
Audit reflects "what the gate decided"; `order_id IS NULL` means "gate allowed but
broker rejected" — operator can join with order-attempt logs to forensicate.

Retention: plain table; monthly cleanup cron deferred to Phase 24.

---

## 4. Data flow

### Preview path (high-frequency, low-stakes)

[C3] The preview path runs the six fast checks synchronously; the **margin RPC runs in
parallel with a 500ms soft-deadline**. If the RPC hasn't returned by then, the response
includes a "margin check pending" WARN (not a blocker) and the preview returns. A
short-lived in-process LRU (60s, key = `(account_id, symbol, qty_bucket, side)`) absorbs
keystroke spam so a typing user issues at most one RPC per qty bucket.

```
FE TradeTicket.tsx
  └─ debounced 200ms
POST /api/orders/preview {account_id, side, qty, price, order_type, tif}
  └─ orders_service.preview_order  (station 0: JWT+CSRF; stations 1-3 unchanged)
        └─ NEW station 4: RiskService.evaluate(ctx, mode='preview')
              ├─ asyncio.gather() the 6 fast checks below:
              │   ├─ check_account_kill_switch         → BLOCK if on
              │   ├─ check_broker_kill_switch          → BLOCK if on (composes Phase 5b)
              │   ├─ check_max_daily_loss              → BLOCK if exceeded; WARN @ warn_at_pct
              │   ├─ check_pdt                         → broker-reported + in-flight Redis counter [H1]
              │   ├─ check_position_concentration      → cross-broker aggregate by instrument_id [H2]
              │   └─ check_buying_power                → (cached BP − in-flight commitments) [H3]
              └─ check_margin (parallel, 500ms soft-deadline)
                    ├─ on cache hit (60s LRU)          → use cached value
                    ├─ on RPC return ≤ 500ms           → fold result into verdict
                    └─ on RPC pending > 500ms          → WARN "margin check pending"; abandon RPC waiter
              ↓
              GateVerdict {final, blockers, warnings, lat_ms}
              ↓
              structlog.info("risk.evaluated", verdict=…, kind="preview", account=…)
              (NO DB row in preview path)
        ↓
PreviewResponse {ok, warnings, blockers, …}  → FE banner
```

### `place_order` / `modify_order` path (low-frequency, high-stakes)

[H4] place_order runs the **full margin RPC synchronously, fail-CLOSED**. A timeout or
sidecar `UNAVAILABLE` returns `503 + Retry-After` to the client; only IBKR/Schwab
`UNIMPLEMENTED` (Alpaca) takes the documented WARN-and-continue branch. The
`risk_decisions` row is written **after** broker dispatch settles so `order_id` is
populated on success. [M5]

```
FE confirms (CSRF nonce)
  └─ POST /api/orders {nonce, …}
        └─ orders_service.place_order  (station 0: JWT+CSRF; stations 1-3 unchanged)
              └─ station 4: RiskService.evaluate(ctx, mode='place_order')
                    ├─ 6 fast checks (same as preview)
                    └─ margin RPC FULLY AWAITED (3s timeout)
                          ├─ timeout / sidecar UNAVAILABLE → 503 + Retry-After [H4]
                          ├─ Alpaca UNIMPLEMENTED          → WARN + cached BP only
                          └─ accepted / reject_reason      → fold into verdict
                    ↓ on ALLOW + WARN: continue
                    ↓ on BLOCK: raise RiskGateBlocked → 422 (NO broker dispatch)
                    ↓ optimistic Redis decrement: risk:pdt:{account_id}, risk:bp:{account_id}
                    ↓
              dispatch to broker (existing Phase 5b path) → returns order_id OR error
                    ↓
              INSERT INTO risk_decisions (…, order_id = <broker order_id or NULL>)  [M5]
                    ↓ AFTER INSERT trigger (verdict='block' only — won't fire here on dispatch failure
                      because verdict was 'allow'; still fires for gate-blocked rows written from
                      the BLOCK branch above)
              pg_notify('risk_decision', {id, verdict, account_id})  [M4 minimal payload]
                    ↓ on BLOCK
              HTTPException(422, {"error": {"code": "risk_gate_blocked",
                                            "blockers": […]}})
                    ↓ on dispatch failure
              optimistic Redis decrement reverted; HTTPException propagates broker error
```

**In-flight Redis counters** [H1, H3] — `risk:pdt:{account_id}` (PDT remaining) and
`risk:bp_committed:{account_id}` (sum of in-flight LMT/STOP order notional) are
optimistically decremented at place_order time and **reconciled at every discoverer
poll** (~30s) against broker-reported truth. On dispatch failure or order cancel, the
counters revert. Single-replica today (Phase 24 multi-worker concern).

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

### Failure-mode policy [H4 asymmetric preview vs place_order]

| Failure | Preview | place_order / modify | Reasoning |
|---|---|---|---|
| `risk_limits` DB unreachable | **fail-CLOSED → BLOCK** | **fail-CLOSED → BLOCK** | Gate is the chokepoint; degraded gate ≠ open gate. |
| Sidecar `PreviewOrder` RPC timeout (3s) | **WARN** "margin check pending" (500ms soft-deadline) | **503 + Retry-After** (fail-CLOSED) [H4] | Preview UX must not block on broker hiccup; place_order must not let margin-violating order through. |
| Sidecar RPC returns reject_reason | **BLOCK** | **BLOCK** | Authoritative broker answer. |
| Sidecar returns `UNIMPLEMENTED` (Alpaca) | **WARN** + cached BP | **WARN** + cached BP | Documented per-broker fallback. |
| NLV cache stale (> 60s per Phase 5a invariant) | **WARN** + stale value | **WARN** + stale value | Don't block on cache freshness alone. |
| Positions snapshot stale (> last discoverer tick) | **WARN** | **WARN** | Concentration on stale snapshot still useful as sanity check. |
| `risk_decisions` INSERT fails | n/a (no DB write) | **fail-OPEN** for the order, alert | Audit failure must not block trades; metric `risk_audit_insert_failures_total`. |
| Redis in-flight counter unreachable | **WARN** "PDT/BP in-flight tracking degraded" | **WARN** + use broker-reported only | Redis is best-effort; broker truth is authoritative. |

---

## 5. Sidecar RPC contract

`proto/broker.proto` — extend the existing service. `sidecar_ibkr` and `sidecar_schwab`
implement; `sidecar_alpaca` returns `UNIMPLEMENTED`.

[C2] Money fields use **`string`** carrying a Decimal-stringified value (matches the
project-wide `NUMERIC(20, 8)` convention from `docs/CONVENTIONS.md`; protobuf `double`
loses precision at 8 decimals × 12-digit notional). The sidecar handlers parse with
`Decimal(str)`; backend serializes via the existing Phase 5a `_format_nlv` helper.

```proto
message PreviewOrderRequest {
  string account_hash    = 1;
  string side            = 2;   // "buy" | "sell"
  string symbol          = 3;
  string asset_class     = 4;
  string order_type      = 5;
  string time_in_force   = 6;
  string qty             = 7;   // Decimal-string [C2]
  optional string limit_price = 8;  // Decimal-string [C2]
  optional string stop_price  = 9;  // Decimal-string [C2]
  string idempotency_key = 10;
}

message PreviewOrderResponse {
  bool   accepted               = 1;
  string reject_reason          = 2;
  optional string initial_margin     = 3;  // Decimal-string [C2]
  optional string maintenance_margin = 4;  // Decimal-string [C2]
  optional string commission         = 5;  // Decimal-string [C2]
  optional string available_funds_after = 6;  // Decimal-string [C2]
  optional string buying_power_after = 7;  // Decimal-string [C2]
  repeated string warnings           = 8;
  string raw_provider_payload        = 9;
}

rpc PreviewOrder(PreviewOrderRequest) returns (PreviewOrderResponse);
```

**Per-broker implementation:**

- **IBKR** — `ib_async.placeOrder(whatIf=True)`; existing connection pool (Phase 4); 3s
  timeout; per-client token bucket from Phase 9.
  - **Async-to-sync wait pattern** [M7]: `whatIf=True` returns a `Trade` object that
    fills via the `ib.client.orderStatusEvent` callback. Sidecar handler awaits the
    callback with `await asyncio.wait_for(trade.filledEvent.wait(), timeout=2.5)`
    (leaving 500ms budget for serialization back to caller). On timeout, the sidecar
    returns `gRPC DEADLINE_EXCEEDED`; the gate translates per the failure-mode table.
- **Schwab** — REST `POST /trader/v1/accounts/{accountHash}/previewOrder`; existing OAuth
  pool (Phase 7a); 3s timeout; 401-retry-once (Phase 9 pattern).
  - **Rate-limit budget** [M8]: Schwab's documented limit is 120 req/min/app shared
    across trade endpoints. Phase 10a reserves a separate token bucket for `previewOrder`
    (`schwab_preview_token_bucket`, 60 req/min — half budget) so preview spam can never
    starve actual `placeOrder` capacity. Counter exposed as
    `schwab_preview_rate_limited_total` metric; alert at 5/min.
- **Alpaca** — handler returns `UNIMPLEMENTED`; gate code path catches and translates to
  WARN ("alpaca preview unavailable, BP cache only").

**Idempotency key** [M6] — content-hash, not request-id-based. The hash is over the
canonical request payload **excluding** `idempotency_key` itself:

```python
import hashlib, json
canonical = json.dumps({
    "account_hash": req.account_hash,
    "side": req.side, "symbol": req.symbol, "asset_class": req.asset_class,
    "order_type": req.order_type, "time_in_force": req.time_in_force,
    "qty": req.qty, "limit_price": req.limit_price, "stop_price": req.stop_price,
}, sort_keys=True, separators=(",", ":"))
idempotency_key = "preview:" + hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()
```

Same input across two retries → same key → sidecar in-process LRU (60s) returns the
cached response without re-hitting the broker. Protects provider rate-limit budgets.

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
- **`useRiskLimits`** hook — TanStack Query, 30s stale time. After every admin
  write (POST/PUT/DELETE), the mutation's `onSuccess` calls
  `queryClient.invalidateQueries({queryKey: ['risk-limits']})` to bust the FE cache —
  the backend's Redis pubsub only invalidates server-side caches; the FE has its own. [M9]
- **`useAccountKillSwitch(account_id)`** — same shape; mutates via POST. Also calls
  `queryClient.invalidateQueries({queryKey: ['account-kill-switches', account_id]})` on
  success. [M9]
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

### Chaos / failure-mode tests [M10] — `backend/tests/chaos/test_risk_chaos.py`

- **Sidecar timeout mid-flight on preview** — RPC takes 600ms (just over soft-deadline);
  preview returns within budget with WARN "margin check pending". Asserts no
  user-visible delay > 500ms.
- **Sidecar timeout on place_order** — RPC times out after 3s; assert `503 + Retry-After`
  with `RuntimeError` not raised; assert no `risk_decisions` row written; assert
  optimistic Redis counters NOT decremented.
- **DB connection lost mid-evaluation** — drop pool connection during `_resolve_limit`;
  assert fail-CLOSED → BLOCK on both preview and place_order paths.
- **Redis pubsub message dropped** — admin writes a new cap; one of two backend workers
  misses the invalidation; assert worker's stale cache TTL bounded at 60s (no infinite
  staleness).
- **History trigger failure** — set `risk_limits_history` to read-only; assert UPDATE on
  `risk_limits` raises and the original UPDATE is rolled back (history is part of the
  transaction).
- **Optimistic counter revert on dispatch failure** — gate ALLOWs, broker rejects; assert
  `risk:bp_committed:{account_id}` decremented back; assert `risk_decisions` row written
  with `order_id IS NULL`.

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
- structlog continues to redact secrets **at log-emit time** (the redactor processes log
  events; it does not act on DB inserts). `risk_decisions.blockers` / `warnings` JSONB
  are **schema-typed** — every entry is `{check, reason, value?, threshold?}` populated
  from server-side check logic, never from user input — so there is no operator free-text
  surface that needs redaction at insert time. [L4]
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

## 13. Architect review — applied (2026-05-08)

Run via `ARCHITECT-REVIEW` skill (opus, user-scope) on the committed spec. **3 CRIT + 4
HIGH + 10 MED applied inline; 4 LOW documented (3 applied, 1 deferred).** Tags marked in
the body of each section.

| Tag | Severity | Title | Applied where |
|---|---|---|---|
| C1 | CRIT | `risk_limits` UNIQUE breaks under NULL `scope_id` (Postgres treats NULLs as distinct → non-deterministic `_resolve_limit`) | §3 — replaced single UNIQUE with two partial unique indexes (`uq_risk_limits_global_kind` + `uq_risk_limits_scoped`) |
| C2 | CRIT | `double` for money in protobuf RPC violates `CONVENTIONS.md` and breaks 8-decimal crypto/forex precision | §5 — all money fields changed to Decimal-string; `_format_nlv` helper for serialization |
| C3 | CRIT | Margin RPC on every 200ms-debounced preview keystroke destroys UX and burns broker rate-limit | §4 — margin runs in parallel with 500ms soft-deadline; cached in 60s LRU keyed by `(account, symbol, qty_bucket, side)`; preview returns "margin check pending" WARN if RPC late |
| H1 | HIGH | PDT counter staleness window between discoverer polls allows fast-double-trade gate-bypass | §1, §4 — Redis in-flight counter `risk:pdt:{account_id}` decrements optimistically; reconciles to broker-reported on each poll |
| H2 | HIGH | Concentration aggregation only within same broker | §1, §4 — aggregates by `instrument_id` across **all accounts under the operator** (single-user dashboard) |
| H3 | HIGH | Pending-order BP commitment not subtracted | §1, §4 — `(cached BP − sum of in-flight LMT/STOP commitments)` from `orders` table OPEN/PENDING; Redis counter `risk:bp_committed:{account_id}` for cross-evaluation consistency |
| H4 | HIGH | Fail-OPEN on `place_order` margin RPC too permissive | §1, §4 — asymmetric policy: preview fail-OPEN with WARN; place_order fail-CLOSED with `503 + Retry-After`; failure-mode table now has separate columns |
| M1 | MED | CSRF nonce + JWT verification ordering not in gate-ordering table | §2 — added station 0 explicitly |
| M2 | MED | `max_daily_loss` "intraday" timezone underspecified | §1 — pinned to broker's primary-exchange timezone via `market_calendar.market_close_tz`; UTC fallback; per-broker default in `notes` |
| M3 | MED | `risk_limits_history` UPDATE trigger DDL missing | §3 — added `fn_risk_limits_history` + `fn_account_kill_switches_history` triggers verbatim |
| M4 | MED | `pg_notify` payload size cap (8KB) | §3 — minimal payload `{id, verdict, account_id}`; consumer fetches by id |
| M5 | MED | `risk_decisions` orphan rows on broker-dispatch failure | §3, §4 — added `order_id BIGINT REFERENCES orders(id)`; INSERT happens **after** broker dispatch settles; documented `order_id IS NULL` semantics |
| M6 | MED | Idempotency key includes per-request UUID (defeats idempotency) | §5 — replaced with content-hash via `blake2b` over canonical request payload |
| M7 | MED | IBKR `WhatIfOrder` async-to-sync wait pattern unspecified | §5 — `await asyncio.wait_for(trade.filledEvent.wait(), timeout=2.5)` documented |
| M8 | MED | Schwab `previewOrder` rate-limit budget unaddressed | §5 — separate token bucket (60 req/min, half of 120 shared budget); `schwab_preview_rate_limited_total` metric |
| M9 | MED | FE TanStack Query invalidation post-admin-write missing | §7 — `queryClient.invalidateQueries(...)` in `onSuccess` for both `useRiskLimits` and `useAccountKillSwitch` |
| M10 | MED | Chaos / failure-mode tests not explicit | §8 — added `backend/tests/chaos/test_risk_chaos.py` with 6 scenarios (sidecar timeout × 2, DB drop, pubsub miss, trigger failure, counter revert) |
| L1 | LOW | "RiskService is pure" terminology inaccurate | §2 — softened to "deterministic given inputs"; documented I/O dependencies |
| L2 | LOW | `risk_decisions.latency_ms >= 0` CHECK missing | §3 — added |
| L3 | LOW | TradeTicket WARN-acknowledge UX flow not specified | **deferred** — UX detail for the `frontend-design` skill during Chunk E; default behavior is "click to confirm" pattern from existing CSRF nonce flow |
| L4 | LOW | structlog redaction scope vs `risk_decisions` JSONB persistence | §11 — clarified: structlog redacts log events; `risk_decisions.blockers/warnings` JSONB is schema-typed (no free text) so no redaction needed at insert time |
