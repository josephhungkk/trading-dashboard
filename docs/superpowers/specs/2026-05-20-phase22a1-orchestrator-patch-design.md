# Phase 22a.1 — Orchestrator Patch: Sector Ingestion + Marginal-Variance Gate + Auto-Promote Veto Window (v0.22.0.1)

**Date:** 2026-05-20
**Status:** Architect-review pass applied (3 CRIT + 7 HIGH + 8 MED inline) — ready for /writing-plans
**Builds on:** Phase 22a (v0.22.0) — all chunks A–F shipped
**Version target:** v0.22.0.1

**Sub-phase contents (deferred from 22a architect review):**
- Sector ingestion pipeline (`instruments.sector` + `instruments.sub_sector`) → enable `per_sector` exposure limit type
- Marginal-variance-adjusted notional in `PortfolioExposureGate` (using `CorrelationService` matrix already computed in 22a)
- Telegram veto window for auto-promote (shadow-bot path), mirroring strategy-gen `paper_pending` flow

---

## 1. Context

Phase 22a shipped with three items explicitly deferred:

| Deferred item | Reason for deferral |
|---|---|
| `per_sector` exposure limits | `instruments` table had no `sector` column and no ingestion pipeline |
| Marginal-variance notional in `PortfolioExposureGate` | Formula correct but complex — required validated formula + backtest sanity check; raw notional used as safe conservative default |
| Telegram veto window for auto-promote | Nice-to-have parity with strategy-gen path; 22a shipped immediate-promote as working baseline |

This sub-phase closes all three.

---

## 2. Data Model (Alembic 0069.1)

Patch migration applied after 0069. **CRIT-1 fix:** 0069 created `shadow_promotion_events` with an anonymous inline CHECK `('success','reverted')`. The migration must resolve the actual constraint name via `information_schema` before dropping it.

```python
# In the alembic upgrade() function — resolve anonymous constraint name at runtime
def upgrade() -> None:
    # ---------- instruments: sector columns ----------
    op.add_column("instruments", sa.Column("sector", sa.Text(), nullable=True))
    op.add_column("instruments", sa.Column("sub_sector", sa.Text(), nullable=True))
    op.create_index(
        "instruments_sector_idx", "instruments", ["sector"],
        postgresql_where=sa.text("sector IS NOT NULL"),
    )

    # ---------- portfolio_exposure_limits: per_sector type ----------
    op.drop_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        type_="check",
    )
    op.create_check_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        "limit_type IN ('total_notional', 'per_instrument', 'per_sector')",
    )
    op.add_column(
        "portfolio_exposure_limits",
        sa.Column("sector", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_portfolio_exposure_sector",
        "portfolio_exposure_limits",
        ["account_id", "sector"],
        unique=True,
        postgresql_where=sa.text("limit_type = 'per_sector'"),
    )

    # ---------- shadow_promotion_events: veto window ----------
    op.add_column(
        "shadow_promotion_events",
        sa.Column("veto_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "shadow_promotion_events",
        sa.Column("veto_token", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("NULL"), nullable=True),
    )
    # CRIT-1: resolve anonymous constraint name, extend vocabulary preserving 'reverted'
    conn = op.get_bind()
    row = conn.execute(sa.text(
        "SELECT conname FROM pg_constraint"
        " JOIN pg_class ON conrelid = pg_class.oid"
        " WHERE pg_class.relname = 'shadow_promotion_events'"
        "   AND contype = 'c'"
        "   AND pg_get_constraintdef(pg_constraint.oid) LIKE '%status%'"
    )).fetchone()
    if row:
        op.drop_constraint(row[0], "shadow_promotion_events", type_="check")
    op.create_check_constraint(
        "shadow_promotion_events_status_check_v2",
        "shadow_promotion_events",
        "status IN ('success','reverted','promote_pending','vetoed')",
    )
    # MED-5: enforce veto_expires_at NOT NULL when status = 'promote_pending'
    op.create_check_constraint(
        "shadow_promotion_events_veto_expires_check",
        "shadow_promotion_events",
        "(status = 'promote_pending' AND veto_expires_at IS NOT NULL)"
        " OR (status <> 'promote_pending')",
    )
    # Prevent duplicate promote_pending rows for the same (live, shadow) pair
    op.create_index(
        "uq_shadow_promotion_pending",
        "shadow_promotion_events",
        ["live_bot_id", "shadow_bot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'promote_pending'"),
    )
```

**Notes:**
- **CRIT-1:** New `status` vocabulary is `('success','reverted','promote_pending','vetoed')` — preserves existing `'reverted'` rows from 22a. `'pending'` and `'failed'` are NOT added (they were spec errors; neither is used in any code path).
- **HIGH-5:** `veto_token UUID` (nullable, set only for `promote_pending` rows) is used in Telegram commands instead of integer PK to prevent enumeration.
- **MED-5:** CHECK constraint enforces `veto_expires_at IS NOT NULL` whenever `status='promote_pending'`.
- `portfolio_exposure_limits.sector` is a free-text label matching `instruments.sector`, not a FK — avoids CASCADE complexity and allows synthetic sector labels (see §3).
- The existing Redis HASH shape `portfolio:exposure:{account_id}` already reserves `sector:{sector_name}` keys; the Lua script just wasn't populating them.

---

## 3. Sector Ingestion Service

### 3.1 New Proto RPC (CRIT-2 fix)

**The existing `GetContract` RPC does NOT carry `industry`/`category`** — `proto/broker/v1/broker.proto` `ContractResponse` only has `{symbol, exchange, currency, asset_class, conid, local_symbol, multiplier}`. A new RPC is required.

Add to `proto/broker/v1/broker.proto`:
```protobuf
// New — sector/fundamentals lookup for sector ingestion
rpc GetContractFundamentals(ContractRef) returns (FundamentalsResponse);

message ContractRef {
  string conid = 1;   // IBKR conid
}

message FundamentalsResponse {
  string industry = 1;   // ContractDetails.industry
  string category  = 2;  // ContractDetails.category
  string subcategory = 3;
}
```

**Sidecar handler** (`sidecar_ibkr/handlers.py`):
- New gRPC handler `GetContractFundamentals(ContractRef)`:
  1. Call `await self.ib.reqContractDetailsAsync(Contract(conId=int(request.conid)))`.
  2. If result empty: return `FundamentalsResponse()` (all empty strings).
  3. Return `FundamentalsResponse(industry=details[0].industry, category=details[0].category, subcategory=details[0].subcategory)`.

**Backend client** (`app/brokers/ibkr_sidecar_client.py`):
- Add `async get_contract_fundamentals(conid: str) -> FundamentalsResponse`.

Run `buf generate` + commit cross-platform stubs (as per Phase 17 pattern). This is Chunk A deliverable.

### 3.2 Service: `app/services/orchestrator/sector_ingestion.py`

`SectorIngestionService` populates `instruments.sector` and `instruments.sub_sector`.

**MED-2 fix — synthetic sector namespace:** Synthetic sectors for non-equities use a `_class:` prefix to avoid collision with IBKR industry strings (e.g. `_class:FOREX`, `_class:CRYPTO`). Limits referencing `per_sector` can use either IBKR-style labels or `_class:*` prefixed labels.

**MED-4 / R4 fix — case normalisation:** All sector values are lower-cased and stripped at write time: `sector = details.industry.strip().lower()`. The same normalisation is applied at limit-CRUD time (§6).

**IBKR path (primary — equities/ETF/index):**
1. Look up `symbol_aliases WHERE broker='ibkr' AND instrument_id=:id` to get `conid`.
2. Call `BrokerSidecarClient.get_contract_fundamentals(conid)` (new RPC — CRIT-2).
3. Map (with normalisation): `industry.strip().lower()` → `instruments.sector`, `category.strip().lower()` → `instruments.sub_sector`.
4. Skip if both are empty strings (sidecar returned nothing); log warning, preserve existing value.
5. `UPDATE instruments SET sector=:s, sub_sector=:ss WHERE id=:id`.

**Schwab path (fallback — Schwab-only instruments with no IBKR conid):**
- Best-effort from Schwab `securitiesAccount` positions `fundamentalData.sector` if available.
- Apply same normalisation. Skip silently if empty or instrument not in any Schwab position.

**Synthetic sector for non-equities:** FOREX, CRYPTO, FUTURE, OPTION, BOND, MUTUAL_FUND, CFD:
- `sector = f"_class:{asset_class.value.lower()}"` (e.g. `'_class:forex'`).
- `sub_sector = None`.
- Written unconditionally; no broker API call.

**HIGH-3 fix — IBKR rate limit and batch concurrency:**
- Backfill uses **serial** requests (one at a time), not batch-parallel, to stay within IBKR pacing limits (~50 req/s ceiling, avoid thundering herd on market-data sidecar).
- 100ms sleep between requests.
- **Route via IBKR read-only account** (account 4 / paper sidecar) where available; document the explicit choice: sector lookup is read-only, should not compete with live order flow on account 1.
- The 01:30 schedule ensures markets are closed UTC overnight (explicit choice, documented here).

**Invocation points:**
- `POST /api/orchestrator/sector-refresh/{instrument_id}` — on-demand single instrument (admin JWT).
- `POST /api/orchestrator/sector-refresh/backfill` — batch all `sector IS NULL` (admin JWT). **MED-7 fix:** Returns `{processed: N, updated: M, skipped: K, errors: [{instrument_id, reason}, ...]}` (errors capped at 100 entries).
- APScheduler nightly batch at `"30 1 * * *"`. `max_instances=1`, `coalesce=True`.
- Hook in `InstrumentsService.create_instrument()`: best-effort, non-blocking.

**Backfill implementation:** `backfill_all(db)` iterates `sector IS NULL` instruments serially; 100ms sleep per request; logs progress every 200 instruments.

### 3.3 Lua Script Update (`exposure_gate_lua.py`)

**HIGH-1 fix:** Both EVALSHA and eval-fallback paths must pass three ARGVs. Add Lua guard for nil ARGV[3]. Bump a `_SCRIPT_VERSION` constant so unit tests can detect drift.

Updated Lua:
```lua
-- ARGV[1] = signed_delta_usd (total + instr)
-- ARGV[2] = instr:{instrument_id}
-- ARGV[3] = sector:{sector} or "" (HIGH-1: always passed, empty string when no sector)
local delta = tonumber(ARGV[1])
redis.call("HINCRBYFLOAT", KEYS[1], "total", delta)
redis.call("HINCRBYFLOAT", KEYS[1], ARGV[2], delta)
if ARGV[3] and ARGV[3] ~= "" then
    redis.call("HINCRBYFLOAT", KEYS[1], ARGV[3], delta)
end
```

`update_on_fill()` signature: `async def update_on_fill(self, account_id, instrument_id, signed_delta_usd, *, sector: str | None = None)`.

**HIGH-2 fix — caller audit:** All writers to `portfolio:exposure:{account_id}` must pass sector:
- `app/bot/fill_router.py` — primary caller; must fetch `instrument.sector` and pass to `update_on_fill`.
- `_read_exposure()` PG fallback path in `exposure_gate.py` — writes only `total` + `instr:*` keys directly to Redis (no sector). This is acceptable: the fallback is a cold-start reconstruction from `bot_orders` which has no sector column. Sector keys in Redis are populated lazily as fills arrive. Document this explicitly.
- `app/bot/supervisor.py` stop path — verify during Chunk C that it does NOT write to `portfolio:exposure:{acct}` directly. If it does, must be updated to pass sector.

### 3.4 Exposure Gate: per_sector check

`PortfolioExposureGate._fetch_limits()` query extended to also return `sector` column:

```sql
SELECT id, limit_type, instrument_id, max_notional, currency, enabled, sector
FROM portfolio_exposure_limits
WHERE account_id = :acct AND enabled = true
  AND (instrument_id IS NULL OR instrument_id = :iid
       OR limit_type = 'per_sector')
```

`check()` gains `instrument_sector: str | None = None` parameter. `per_sector` handling:

```python
elif limit_type == "per_sector" and limit_sector and limit_sector == instrument_sector:
    sector_key = f"sector:{instrument_sector}"
    projected = exposure.get(sector_key, Decimal("0")) + effective_notional
    if projected > max_notional:
        outcome = ExposureOutcome.BLOCK
        triggered_limit_type = "per_sector"
        break
    warn_threshold = max_notional * Decimal("0.8")
    if projected > warn_threshold and outcome == ExposureOutcome.ALLOW:
        outcome = ExposureOutcome.WARN
        triggered_limit_type = "per_sector"
```

---

## 4. Marginal-Variance-Adjusted Notional

### 4.1 Formula (CRIT-3 fix — correlation-discounted notional)

**CRIT-3 resolution:** The original Δσ²_p formula produces `USD² × dimensionless` (dollar variance of P&L), and taking `sqrt()` gives a "1σ daily dollar P&L" — not a notional. Comparing this against `max_notional` (a raw USD notional authored in 22a) is a category error: for typical σ≈0.20, the formula would make the gate ~5× more permissive than 22a.

**Correct interpretation — correlation-discounted notional (Approach ii from review):**

```
corr_sum = Σᵢ (wᵢ × ρᵢ,new) / w_new
effective_notional = raw_notional × sqrt(max(1 + 2 × corr_sum, 0))
```

Where:
- `raw_notional` = `qty × price × multiplier × fx_rate` (USD)
- `wᵢ` = **signed** current position notional for instrument `i` (from Redis `instr:{i}`; negative for net-short positions — Redis stores signed_delta_usd from fill_router.py)
- `w_new` = `raw_notional` (unsigned, same as above)
- `ρᵢ,new` = Pearson correlation from Redis `portfolio:correlation:{account_id}`

This is a **dimensionless damping factor** `∈ [0, ~1.4]` applied to raw notional:
- Perfectly correlated long adds: `corr_sum → 1`, factor `→ sqrt(3) ≈ 1.73` (more restrictive — correctly penalises concentration).
- Perfectly hedged position (ρ=+1, opposite-side): `corr_sum → -0.5`, factor `→ 0` (correctly allows).
- Uncorrelated trade: `corr_sum → 0`, factor `→ 1.0` (same as raw notional).
- `max(_, 0)` under the sqrt prevents negative argument on extreme hedge scenarios.

**Units:** `effective_notional` is in USD. `max_notional` limit on `portfolio_exposure_limits` was authored in USD. The comparison is dimensionally consistent.

**Sign correctness:** `wᵢ` must be the **signed** notional (long positive, short negative). Redis `portfolio:exposure:{acct}` stores signed delta from `fill_router.py:side_sign`, so the sign is correct at read time.

**Vol columns not needed for this formula** — the correlation-discounted approach does not require per-instrument volatility. The §4.2 vol cache in `CorrelationService` is still written (used by the FE heatmap and HealthDigestService for Sharpe ranking), but is **not** read by the gate.

### 4.2 Vol Cache in CorrelationService (for FE + HealthDigest)

`CorrelationService.compute_and_store()` writes per-instrument annualised vol to Redis for consumption by FE heatmap and `HealthDigestService`. Not used by `PortfolioExposureGate`.

**MED-3 fix — stable key includes window:**
```python
vol_key = f"vol:30d:{instrument_id}"   # window encoded in key name
vol = stdev(log_returns) * math.sqrt(252)
pipeline.set(vol_key, str(vol), ex=86400)
```

**MED-4 fix — atomic pipeline write:** Vol writes and matrix write are batched in a single Redis `pipeline()` (MULTI/EXEC) so readers always see consistent matrix + vol snapshot:

```python
async with redis.pipeline(transaction=True) as pipe:
    for iid, vol in vol_map.items():
        pipe.set(f"vol:30d:{iid}", str(vol), ex=86400)
    pipe.set(f"portfolio:correlation:{account_id}", json.dumps(matrix), ex=86400)
    await pipe.execute()
```

### 4.3 Gate Logic

`PortfolioExposureGate.check()` updated:

```python
effective_notional = order_notional  # default: raw notional (conservative)
if await self._mv_enabled(db):
    mv_notional = await self._compute_mv_notional(
        account_id, instrument_id, order_notional, exposure
    )
    if mv_notional is not None:
        effective_notional = mv_notional
    # else: fallback logged, raw notional already set
```

`_compute_mv_notional()` returns `None` on:
- Correlation matrix TTL check fails (key absent or age > 48h via Redis TTL inspection)
- Any exception during computation

Fallback metric: `orchestrator_marginal_variance_fallback_total{reason=stale_matrix|error}`.

**LOW-1 fix:** Raw FX rate miss (from `get_fx_rate`) already returns `Decimal("1.0")` (fail-safe per §3.2 of 22a spec). If FX rate returns 1.0 for a non-USD instrument, the notional is approximate but the gate still fires — it does not silently block. This is consistent with the 22a fx.py contract. No change needed; document explicitly.

**MED-6 fix — default boot semantics:** `_mv_enabled()` defaults to `True` when the `app_config` row is absent (same pattern as `_gate_enabled()`). The 0069.1 migration seeds the row:

```sql
INSERT INTO app_config (namespace, key, value_json)
VALUES ('orchestrator', 'marginal_variance_enabled', 'true')
ON CONFLICT (namespace, key) DO NOTHING;
```

**`app_config` key:**
- `orchestrator/marginal_variance_enabled` — default `true` (seeded by migration). Kill switch to revert to raw notional.

**LOW-2 — Metrics:** Use a single `orchestrator_exposure_gate_latency_seconds` histogram with a `path` label (`raw` / `mv`) rather than a separate histogram. The `orchestrator_marginal_variance_fallback_total` counter remains separate (different semantics).

---

## 5. Auto-Promote Telegram Veto Window

### 5.1 New Flow in AutoPromoteEvaluator

**HIGH-4 fix — scheduler durability:** APScheduler uses `MemoryJobStore` (main.py lifespan). On backend restart, in-memory `DateTrigger` jobs vanish. Recovery sweep runs at startup:

```python
# In lifespan startup, after APScheduler.start():
await _recover_pending_promotions(db, scheduler)
```

`_recover_pending_promotions()`:
1. `SELECT * FROM shadow_promotion_events WHERE status='promote_pending' AND veto_expires_at IS NOT NULL`.
2. For each row: if `veto_expires_at > now()` → re-schedule `DateTrigger` at `veto_expires_at`. If `veto_expires_at <= now()` → fire immediately (call `_expiry_promote(event_id, db)`).

**MED-5 CHECK ensures `veto_expires_at IS NOT NULL` on `promote_pending` rows** (defined in §2), so the recovery sweep can rely on it.

**HIGH-5 fix — veto token instead of integer PK:** `veto_token UUID` (set on insert, cleared on resolution) is used in Telegram command strings instead of BIGSERIAL PK. The Telegram handler looks up by `veto_token`, not by `id`.

**HIGH-6 fix — CAS status flip:** Both the expiry job and the veto handler use an atomic conditional UPDATE:

```sql
UPDATE shadow_promotion_events
   SET status = :new_status, veto_token = NULL
 WHERE id = :id AND status = 'promote_pending'
RETURNING id;
```

If `RETURNING` is empty, the other path won — exit silently (no error).

**HIGH-7 fix — promote() failure path:** If `ShadowPromoterService.promote()` raises inside the expiry job, flip `status = 'reverted'` (already in vocabulary from 22a), emit `orchestrator_auto_promote_total{outcome=error}`, send Telegram error notification.

**Full flow when criteria pass AND `auto_apply=True` AND master switch on AND `veto_enabled=True`:**

1. Generate `veto_token = uuid4()`.
2. Insert `shadow_promotion_events(live_bot_id, shadow_bot_id, status='promote_pending', promoted_via='auto', veto_expires_at=now() + N minutes, veto_token=veto_token)`.
   - `uq_shadow_promotion_pending` index prevents duplicate pending rows.
3. Post Telegram: `"Auto-promote candidate: shadow {shadow_bot_id} → live {live_bot_id}\nSharpe={s:.2f}, MaxDD={dd:.1%}, WinRate={wr:.1%}\nUse /veto_promote_{veto_token} to cancel. Expires in {N}m."` (HIGH-5: token, not integer ID).
4. Schedule APScheduler `DateTrigger` one-shot job ID `auto_promote_veto_{veto_token}` at `veto_expires_at`.

**Expiry job (`_expiry_promote`):**
```python
rows = await db.execute(CAS_UPDATE, {"id": event_id, "new_status": "success"})
if rows.rowcount == 0:
    return  # already vetoed
try:
    await promoter.promote(live_bot_id, shadow_bot_id, "auto", db)
    await telegram.send(f"Auto-promoted {shadow_bot_id} → {live_bot_id} (veto window expired)")
except Exception:
    await db.execute(CAS_UPDATE, {"id": event_id, "new_status": "reverted"})  # HIGH-7
    await telegram.send(f"Auto-promote FAILED for {live_bot_id}: {err}")
    metrics.inc(outcome="error")
```

**`/veto_promote_{veto_token}` Telegram handler:**
1. `SELECT id, live_bot_id FROM shadow_promotion_events WHERE veto_token = :token AND status = 'promote_pending'`.
2. CAS UPDATE to `vetoed` (HIGH-6). If no row returned: respond "This promote was already resolved."
3. Cancel APScheduler job `auto_promote_veto_{veto_token}`.
4. Respond: `"Auto-promote vetoed for live bot {live_bot_id}."`

**Immediate path (veto disabled):** If `app_config[orchestrator/auto_promote_veto_enabled]` is `false` (default `true`), `AutoPromoteEvaluator` calls `promote()` directly — identical to 22a behaviour. No `promote_pending` row inserted.

**MED-8 — KPI in Telegram:** Sharpe/MaxDD/WinRate KPI in the Telegram message is acceptable for single-user allowlist. Documented in §11 as a future hardening item when Phase 24 RBAC lands.

### 5.2 `app_config` Keys

| Key | Default | Description |
|---|---|---|
| `orchestrator/auto_promote_veto_enabled` | `true` | Enable veto window for auto-promote. If `false`: immediate promote (22a behaviour). |
| `orchestrator/auto_promote_veto_window_minutes` | `30` | Duration of veto window in minutes. |

### 5.3 State Machine

```
                        criteria pass +
                        auto_apply=True +
                        veto_enabled=True
                              │
                              ▼
                      ┌──────────────┐
                      │promote_pending│ ← uq_shadow_promotion_pending
                      └──────────────┘   prevents duplicates
                         │         │
          /veto_promote   │         │  veto_expires_at fires
          (CAS → vetoed)  │         │  (CAS → success)
                          ▼         ▼
                       vetoed     success   ← uq_shadow_promotion_success
                                             prevents double-promotion
                      promote() raises
                         │
                         ▼
                       reverted   (HIGH-7)
```

---

## 6. REST API

**MED-1 fix — sector validation on limit CRUD:** `POST /api/orchestrator/exposure-limits` and `PUT .../exposure-limits/{id}` for `limit_type='per_sector'` must validate that the supplied `sector` exists in known sectors:
```sql
SELECT DISTINCT sector FROM instruments WHERE sector IS NOT NULL
```
Return 422 if not found. Normalise (strip, lower-case) the incoming `sector` value before comparison and storage.

**MED-7 fix — backfill response shape:** `POST /api/orchestrator/sector-refresh/backfill` returns:
```json
{"processed": 120, "updated": 115, "skipped": 3, "errors": [{"instrument_id": 42, "reason": "sidecar_unavailable"}]}
```
(errors list capped at 100 entries)

**New endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/orchestrator/sector-refresh/{instrument_id}` | admin JWT | On-demand sector refresh for one instrument |
| `POST` | `/api/orchestrator/sector-refresh/backfill` | admin JWT | Batch backfill; returns `{processed, updated, skipped, errors}` |

**Updated endpoints:**

| Method | Path | Change |
|---|---|---|
| `POST /api/orchestrator/exposure-limits` | Accepts `limit_type='per_sector'` with required `sector` field; validates sector exists (MED-1) |
| `PUT /api/orchestrator/exposure-limits/{id}` | Same — per_sector rows require `sector` field |
| `GET /api/orchestrator/exposure` | Response includes `sector:*` keys in exposure map |

---

## 7. Implementation Chunks

**LOW-3 fix — Chunk A rerouted to Codex:** Schema chunk now includes proto changes (CRIT-2), constraint-rename logic (CRIT-1), and buf regeneration — empirical cleanup, not structured form. Reroute to Codex.

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema + Proto** | Alembic 0069.1 (instruments cols, per_sector type, promote_pending status, veto_token, veto_expires_at checks, recovery index); `proto/broker/v1/broker.proto` + `FundamentalsResponse` + sidecar handler + `BrokerSidecarClient.get_contract_fundamentals`; buf generate + cross-platform stubs; migration tests | **Codex** | — |
| **B — SectorIngestionService** | `orchestrator/sector_ingestion.py` (IBKR path via new RPC, Schwab fallback, synthetic `_class:*` prefix, case normalisation); backfill serial loop; APScheduler 01:30 wiring; `InstrumentsService` hook; unit tests (IBKR path, Schwab fallback, synthetic, backfill returns `{processed,updated,skipped,errors}`, sidecar unavailable preserves existing value) | Codex | after A |
| **C — Marginal-variance gate** | Extend `correlation.py` (vol cache `vol:30d:{iid}`, MULTI/EXEC pipeline write — MED-4); modify `exposure_gate.py` (correlation-discounted formula — CRIT-3, `instrument_sector` param, MED-6 boot seed, LOW-2 `path` label); update Lua script (3-ARGV, `_SCRIPT_VERSION` bump — HIGH-1); audit `fill_router.py` + `supervisor.py` for sector passthrough (HIGH-2); tests (opposite-side hedge ρ=+1 → effective~0, uncorrelated ρ=0 → effective≈raw, stale matrix fallback, kill switch, sector HASH key written, per_sector limit check, Lua nil-guard) | Codex | after A |
| **D — Auto-promote veto window** | Modify `orchestrator/auto_promote.py` (veto_token, promote_pending insert, DateTrigger job, CAS updates — HIGH-6, expiry promote() failure → reverted — HIGH-7); startup recovery sweep (HIGH-4); Telegram `/veto_promote_{token}` handler; config keys; tests (veto fires, expiry promotes, immediate path, promote() raise → reverted, CAS race won by expiry, CAS race won by veto, recovery sweep re-schedules vs fires immediately, duplicate pending blocked by index, regression test: veto_enabled=false reproduces 22a writes) | Qwen | after A |
| **E — REST API** | `api/orchestrator.py` — sector-refresh endpoints (MED-7 response shape), per_sector CRUD with sector validation (MED-1), exposure response update; tests | Qwen | after B/C/D |
| **F — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md (LOW-4: include all new config keys in operator runbook), tag v0.22.0.1 | Opus direct | after all |

---

## 8. Testing Targets

### Backend (~55 tests)

**Sector ingestion (~15):**
- IBKR path: conid found → `GetContractFundamentals` called → sector/sub_sector written (normalised lower-case)
- IBKR sidecar unavailable: warning logged, existing value preserved (not blanked)
- Schwab fallback: no IBKR conid → Schwab path; empty response → skip
- Synthetic: FOREX instrument → `sector='_class:forex'`, no API call
- Backfill: serial requests, 100ms sleep, returns `{processed,updated,skipped,errors}`, errors capped at 100
- APScheduler job registered at `"30 1 * * *"`, `max_instances=1`/`coalesce=True`
- `InstrumentsService.create_instrument()` calls refresh; failure does not raise

**Marginal-variance gate (~18):**
- Opposite-side hedge (ρ=+1, opposite position): `effective_notional → 0` (hedged, allowed)
- Diversifying trade (ρ=0): `effective_notional ≈ raw_notional`
- Concentrated addition (ρ=+1, same-side): `effective_notional > raw_notional` (penalised)
- Stale matrix: fallback to raw_notional + `fallback_total{reason=stale_matrix}` incremented
- `marginal_variance_enabled=false`: raw notional, no Redis reads
- `CorrelationService.compute_and_store()` writes `vol:30d:{iid}` via MULTI/EXEC pipeline
- Lua script: sector HASH key incremented on fill; not incremented when sector is `""`; nil-guard
- `per_sector` limit check: order blocked when sector notional projected > limit
- `_SCRIPT_VERSION` constant detectable by tests
- MED-6: absent `marginal_variance_enabled` app_config row defaults to `True`
- `path` label on latency histogram (`raw` / `mv`)

**Auto-promote veto window (~15):**
- Criteria pass + `auto_apply=True` + `veto_enabled=True` → `promote_pending` row inserted with `veto_token`, Telegram sent with token, job scheduled
- `/veto_promote_{token}` → CAS flip to `vetoed`, job cancelled, confirmation sent
- Expiry job fires → CAS flip to `success`, `promote()` called, Telegram sent
- `promote()` raises → CAS flip to `reverted`, error Telegram sent (HIGH-7)
- CAS race: expiry wins before veto → veto handler gets empty RETURNING, exits silently
- CAS race: veto wins before expiry → expiry job gets empty RETURNING, exits silently
- Recovery sweep: `promote_pending` row with future `veto_expires_at` → job re-scheduled
- Recovery sweep: `promote_pending` row with past `veto_expires_at` → fires immediately
- `veto_enabled=False` → immediate `promote()`, no `promote_pending` row (regression: same writes as 22a)
- Duplicate `promote_pending` blocked by `uq_shadow_promotion_pending` index
- `uq_shadow_promotion_success` prevents double-promotion after expiry

**REST API (~7):**
- `POST /exposure-limits` with `per_sector` + unknown sector → 422 (MED-1)
- `POST /exposure-limits` with `per_sector` + known sector → 201, normalised lower-case stored
- `POST /sector-refresh/backfill` → returns `{processed,updated,skipped,errors}` (MED-7)
- Exposure response includes `sector:*` keys when fills present

### Frontend (~5 tests)
- Exposure panel renders sector rows when `per_sector` limits exist
- `per_sector` limit create form: `sector` field required, submits correctly
- Exposure response with no sector limits: panel unchanged

---

## 9. Metrics (22a.1 additions)

| Metric | Type | Labels |
|---|---|---|
| `orchestrator_exposure_gate_latency_seconds` | Histogram | `path` (raw/mv) — LOW-2: reuse existing histogram with new label |
| `orchestrator_marginal_variance_fallback_total` | Counter | `reason` (stale_matrix, error) |
| `orchestrator_sector_ingestion_total` | Counter | `outcome` (updated, skipped, error), `source` (ibkr, schwab, synthetic) |

---

## 10. Invariants Preserved

| Invariant | This patch |
|---|---|
| **Fail-CLOSED for hard capacity constraints** | MV fallback is raw notional (conservative, never more permissive than 22a). FX miss returns `Decimal("1.0")` (22a fx.py contract) — approximate notional but gate still fires. |
| **No new money-moving paths without CSRF** | Sector-refresh and backfill are admin-only read/enrichment endpoints; no CSRF needed. |
| **Schema changes via Alembic only** | 0069.1 migration only. |
| **Veto window preserves audit trail** | `promote_pending` and `vetoed` rows kept; `veto_token` cleared on resolution. |
| **22a behaviour preserved** | `veto_enabled=false` + `marginal_variance_enabled=false` reproduce exact 22a runtime paths (regression test in §8). |
| **`'reverted'` rows from 22a preserved** | New CHECK vocabulary includes `'reverted'` (CRIT-1). |

---

## 11. Deferred

| Item | Target |
|---|---|
| Sector data for HKEX instruments (Futu) | No `reqContractDetails` equivalent in Futu API; defer to Phase 24 |
| Marginal-variance for options/futures | Raw notional used as conservative default for non-equity instruments |
| Per-bot marginal-variance threshold | Not needed for 22a.1 correctness |
| KPI leakage in Telegram veto message | Single-user allowlist today; harden with RBAC in Phase 24 (MED-8) |
| `vol:30d:{iid}` consumed by gate | Written for FE/HealthDigest; not yet read by gate — gate uses correlation-discounted notional which needs no vol |
