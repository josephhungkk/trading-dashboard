# Phase 22a.1 — Orchestrator Patch: Sector Ingestion + Marginal-Variance Gate + Auto-Promote Veto Window (v0.22.0.1)

**Date:** 2026-05-20
**Status:** Design approved — ready for /writing-plans
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

Patch migration applied after 0069.

```sql
-- instruments: sector classification columns (IBKR industry + category)
ALTER TABLE instruments
    ADD COLUMN sector      TEXT,    -- IBKR ContractDetails.industry (e.g. 'Technology')
    ADD COLUMN sub_sector  TEXT;    -- IBKR ContractDetails.category (e.g. 'Computers')

CREATE INDEX instruments_sector_idx
    ON instruments(sector)
    WHERE sector IS NOT NULL;

-- portfolio_exposure_limits: add per_sector limit type
-- per_sector rows reference instruments.sector (TEXT label, not FK)
ALTER TABLE portfolio_exposure_limits
    DROP CONSTRAINT portfolio_exposure_limits_limit_type_check,
    ADD CONSTRAINT portfolio_exposure_limits_limit_type_check
        CHECK (limit_type IN ('total_notional', 'per_instrument', 'per_sector'));

ALTER TABLE portfolio_exposure_limits
    ADD COLUMN sector TEXT;  -- non-NULL for per_sector rows; NULL for other types

CREATE UNIQUE INDEX uq_portfolio_exposure_sector
    ON portfolio_exposure_limits(account_id, sector)
    WHERE limit_type = 'per_sector';

-- shadow_promotion_events: veto window support
ALTER TABLE shadow_promotion_events
    ADD COLUMN veto_expires_at TIMESTAMPTZ;

ALTER TABLE shadow_promotion_events
    DROP CONSTRAINT shadow_promotion_events_status_check,
    ADD CONSTRAINT shadow_promotion_events_status_check
        CHECK (status IN ('pending', 'success', 'failed', 'vetoed', 'promote_pending'));

-- Prevent duplicate promote_pending rows for the same (live, shadow) pair
CREATE UNIQUE INDEX uq_shadow_promotion_pending
    ON shadow_promotion_events(live_bot_id, shadow_bot_id)
    WHERE status = 'promote_pending';
```

**Notes:**
- `portfolio_exposure_limits.sector` is a free-text label matching `instruments.sector`, not a FK — avoids CASCADE complexity and allows synthetic sector labels (see §3).
- The existing Redis HASH shape `portfolio:exposure:{account_id}` already reserves `sector:{sector_name}` keys; the Lua script just wasn't populating them. No Redis key shape changes needed.
- `promote_pending` is a new terminal state in `shadow_promotion_events.status` used during the veto window.

---

## 3. Sector Ingestion Service

### 3.1 Service: `app/services/orchestrator/sector_ingestion.py`

`SectorIngestionService` populates `instruments.sector` and `instruments.sub_sector` for all instruments.

**IBKR path (primary — equities/ETF/index):**
1. Look up `symbol_aliases WHERE broker='ibkr' AND instrument_id=:id` to get `conid`.
2. Call the IBKR sidecar's existing `reqContractDetailsAsync(Contract(conId=conid))` gRPC handler.
3. Map: `details.industry` → `instruments.sector`, `details.category` → `instruments.sub_sector`.
4. `UPDATE instruments SET sector=:s, sub_sector=:ss, updated_at=now() WHERE id=:id`.
5. On IBKR sidecar unavailable or empty response: log warning, skip (do not blank existing value).

**Schwab path (fallback — Schwab-only instruments with no IBKR conid):**
- Attempt best-effort from Schwab `securitiesAccount` positions `fundamentalData.sector` if available.
- Skip silently if Schwab returns empty or the instrument isn't in any Schwab account position.

**Synthetic sector for non-equities:** FOREX, CRYPTO, FUTURE, OPTION, BOND, MUTUAL_FUND, CFD:
- `sector = asset_class.value` (e.g. `'FOREX'`, `'CRYPTO'`, `'FUTURE'`).
- `sub_sector = None`.
- Written unconditionally (no broker API call needed).
- Allows `per_sector` limits on these asset classes without IBKR metadata.

**Invocation points:**
- `POST /api/orchestrator/sector-refresh/{instrument_id}` — on-demand single instrument (admin JWT).
- `POST /api/orchestrator/sector-refresh/backfill` — batch all `sector IS NULL` instruments (admin JWT).
- APScheduler nightly batch at `"30 1 * * *"` (01:30, between correlation at 01:00 and retrain at 02:00). `max_instances=1`, `coalesce=True`.
- Hook in `InstrumentsService.create_instrument()`: call `sector_ingestion.refresh(instrument_id, db)` after insert (non-blocking, best-effort — failure does not block instrument creation).

**Backfill implementation:** `backfill_all(db)` iterates instruments with `sector IS NULL` in batches of 50; 100ms sleep between batches to respect IBKR rate limits. Logs progress every 200 instruments.

### 3.2 Lua Script Update (`exposure_gate_lua.py`)

Extend the fill-update Lua script to also increment the sector HASH key when sector is non-null:

```lua
-- existing keys updated:
HINCRBYFLOAT KEYS[1] "total" ARGV[1]
HINCRBYFLOAT KEYS[1] ARGV[2] ARGV[1]   -- instr:{instrument_id}
-- new (only when ARGV[3] non-empty):
if ARGV[3] ~= "" then
    HINCRBYFLOAT KEYS[1] ARGV[3] ARGV[1]  -- sector:{sector}
end
```

`PortfolioExposureGate.update_on_fill()` is updated to pass `sector_key = f"sector:{sector}"` (or `""` if `sector` is null) as the third argument.

To read `sector`, `update_on_fill()` accepts an optional `sector: str | None = None` parameter. Callers (`BotFillRouter`) must pass the sector from the instrument row.

### 3.3 Exposure Gate: per_sector check

`PortfolioExposureGate._fetch_limits()` already returns all enabled limits for the account. Add `per_sector` handling in `check()`:

```python
elif limit_type == "per_sector" and sector_name and sector_name == instrument_sector:
    sector_key = f"sector:{sector_name}"
    projected = exposure.get(sector_key, Decimal("0")) + effective_notional
    if projected > max_notional:
        outcome = ExposureOutcome.BLOCK
        break
```

`check()` gains an optional `instrument_sector: str | None = None` parameter (passed by callers from the instrument row).

---

## 4. Marginal-Variance-Adjusted Notional

### 4.1 Formula

Replace raw `order_notional` in the gate comparison with a marginal-variance-adjusted effective notional when data is fresh:

```
Δσ²_p = 2·w_new·Σᵢ (wᵢ·ρᵢ,new·σᵢ·σ_new) + w²_new·σ²_new
effective_notional = sqrt(max(Δσ²_p, 0))
```

Where:
- `w_new` = raw order notional in USD (`qty × price × multiplier × fx_rate`)
- `wᵢ` = current position notional for instrument `i` (from Redis exposure HASH `instr:{i}`)
- `ρᵢ,new` = Pearson correlation between instrument `i` and new instrument (from Redis `portfolio:correlation:{account_id}`)
- `σᵢ` = 30d annualised volatility of instrument `i` (from Redis `vol:{instrument_id}`, see §4.2)
- `σ_new` = 30d annualised volatility of new instrument

`max(_, 0)` guards against floating-point precision giving a tiny negative value before sqrt.

### 4.2 Vol Cache in CorrelationService

`CorrelationService.compute_and_store()` is extended to store per-instrument annualised vol:

```python
vol = stdev(log_returns) * math.sqrt(252)
await redis.set(f"vol:{instrument_id}", str(vol), ex=86400)
```

Same TTL (86400s) and refresh cycle as the correlation matrix. No new table — Redis only.

### 4.3 Gate Logic

`PortfolioExposureGate.check()` updated:

```python
# Attempt marginal-variance effective notional if config enabled
effective_notional = order_notional  # default: raw notional
if await self._mv_enabled(db):
    mv_notional = await self._compute_mv_notional(
        account_id, instrument_id, order_notional, exposure
    )
    if mv_notional is not None:
        effective_notional = mv_notional
    # else: fallback to raw notional already set
```

`_compute_mv_notional()` returns `None` (triggering fallback) when:
- Correlation matrix age > 48h (checked via `orchestrator_correlation_matrix_age_seconds` gauge or direct Redis TTL check)
- `vol:{instrument_id}` not found in Redis
- Any exception during computation

Fallback is always raw notional (conservative). Logged with metric:

| Metric | Type | Labels |
|---|---|---|
| `orchestrator_marginal_variance_fallback_total` | Counter | `reason` (stale_matrix, no_vol_data, error) |
| `orchestrator_mv_gate_latency_seconds` | Histogram | — |

**`app_config` key:**
- `orchestrator/marginal_variance_enabled` — default `true`. Kill switch to revert to raw notional.

---

## 5. Auto-Promote Telegram Veto Window

### 5.1 New Flow in AutoPromoteEvaluator

When criteria pass AND `auto_apply=True` AND master switch on AND `veto_enabled=True`:

1. Insert `shadow_promotion_events(live_bot_id, shadow_bot_id, status='promote_pending', promoted_via='auto', veto_expires_at=now() + interval 'N minutes')` where N = `app_config[orchestrator/auto_promote_veto_window_minutes]` (default `30`).
2. Post Telegram notification:
   ```
   Auto-promote candidate: shadow {shadow_bot_id} → live {live_bot_id}
   Sharpe={s:.2f}, MaxDD={dd:.1%}, WinRate={wr:.1%}
   Use /veto_promote_{event_id} to cancel. Expires in {N}m.
   ```
3. Schedule APScheduler `DateTrigger` one-shot job at `veto_expires_at`:
   - If `shadow_promotion_events.status` is still `promote_pending` → call `ShadowPromoterService.promote()`, flip `status='success'`.
   - If already `vetoed`: no-op (cancelled by Telegram handler).

**`/veto_promote_{event_id}` Telegram handler:**
1. Look up event by `id`; assert `status='promote_pending'` (ignore if already `success`/`vetoed`).
2. Flip `status='vetoed'`.
3. Cancel the pending APScheduler job (by job ID `auto_promote_veto_{event_id}`).
4. Send Telegram confirmation: `"Auto-promote vetoed for {live_bot_id} (event {event_id})."`

**Immediate path (veto disabled):** If `app_config[orchestrator/auto_promote_veto_enabled]` is `false` (default `true`), `AutoPromoteEvaluator` calls `promote()` directly — identical to 22a behaviour.

### 5.2 New `app_config` Keys

| Key | Default | Description |
|---|---|---|
| `orchestrator/auto_promote_veto_enabled` | `true` | Enable veto window for auto-promote. If `false`: immediate promote (22a behaviour). |
| `orchestrator/auto_promote_veto_window_minutes` | `30` | Duration of veto window in minutes. Configurable per operator preference. |

### 5.3 Idempotency

The existing `uq_shadow_promotion_success` partial unique index on `(live_bot_id, shadow_bot_id) WHERE status='success'` prevents double-promotion. The new `promote_pending` status is distinct from `success`, so the index does not interfere with the two-phase flow. A separate unique partial index prevents duplicate `promote_pending` rows:

```sql
CREATE UNIQUE INDEX uq_shadow_promotion_pending
    ON shadow_promotion_events(live_bot_id, shadow_bot_id)
    WHERE status = 'promote_pending';
```

This index belongs in the Alembic 0069.1 migration (§2). The Chunk A implementer must include it there alongside the other `shadow_promotion_events` changes.

---

## 6. REST API

**New endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/orchestrator/sector-refresh/{instrument_id}` | admin JWT | On-demand sector refresh for one instrument |
| `POST` | `/api/orchestrator/sector-refresh/backfill` | admin JWT | Batch backfill all `sector IS NULL` instruments |

**Updated endpoints:**

| Method | Path | Change |
|---|---|---|
| `POST /api/orchestrator/exposure-limits` | Now accepts `limit_type='per_sector'` with required `sector` field |
| `PUT /api/orchestrator/exposure-limits/{id}` | Same update — per_sector rows require `sector` field |
| `GET /api/orchestrator/exposure` | Response now includes `sector:*` keys in the exposure map |

**No new FE pages.** The existing `/orchestration` page (22c) Panel 2 (exposure heatmap) renders sector rows automatically when `per_sector` limits exist. Sector column visible in the instrument × account matrix.

---

## 7. Implementation Chunks

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0069.1: `instruments.sector/sub_sector`, `per_sector` limit type + `sector` column, `promote_pending` status + `veto_expires_at`, `uq_shadow_promotion_pending` index; migration tests | Qwen | — |
| **B — SectorIngestionService** | `orchestrator/sector_ingestion.py`, sidecar gRPC call for `reqContractDetails`, synthetic sector for non-equities, backfill, APScheduler 01:30 wiring in `main.py`, `InstrumentsService` hook; unit tests (IBKR path, Schwab fallback, synthetic, backfill batch, sidecar unavailable) | Codex | after A |
| **C — Marginal-variance gate** | Extend `orchestrator/correlation.py` to write `vol:{iid}` per instrument; modify `orchestrator/exposure_gate.py` for Δσ²_p formula, stale/no-vol fallback, `instrument_sector` param; update `orchestrator/exposure_gate_lua.py` (sector HASH key); new metrics; `update_on_fill()` signature update; tests (full MV formula correct, stale matrix → raw notional, missing vol → raw notional, sector key written, sector limit check) | Codex | after A |
| **D — Auto-promote veto window** | Modify `orchestrator/auto_promote.py` — `promote_pending` insert, APScheduler one-shot `DateTrigger`, veto config keys; extend Telegram handler for `/veto_promote_{id}` command; `uq_shadow_promotion_pending` guard; tests (veto fires in window, expiry triggers promote, immediate path when veto_enabled=false, duplicate `promote_pending` blocked by index) | Qwen | after A |
| **E — REST API** | `api/orchestrator.py` — sector-refresh endpoints, `per_sector` limit type in CRUD, exposure response update; tests (per_sector limit CRUD, backfill endpoint, exposure response contains sector keys) | Qwen | after B/C/D |
| **F — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.22.0.1 | Opus direct | after all |

---

## 8. Testing Targets

### Backend (~45 tests)

**Sector ingestion (~15):**
- IBKR path: conid found → `reqContractDetails` called → `sector`/`sub_sector` written
- IBKR sidecar unavailable: warning logged, existing value preserved (not blanked)
- Schwab fallback: no IBKR conid → Schwab path attempted; empty response → skip
- Synthetic sector: FOREX/CRYPTO/FUTURE instruments → `sector=asset_class.value`, no API call
- Backfill: iterates `sector IS NULL` in batches of 50; stops at `sector IS NOT NULL` rows
- APScheduler job registered at 01:30; `max_instances=1`/`coalesce=True`
- `InstrumentsService.create_instrument()` calls refresh; failure does not raise

**Marginal-variance gate (~15):**
- Full Δσ²_p formula: 2-instrument portfolio → effective_notional < raw_notional when ρ < 1
- Negative ρ case: effective_notional still non-negative (sqrt guard)
- Stale matrix (age > 48h): fallback to raw notional + `orchestrator_marginal_variance_fallback_total{reason=stale_matrix}` incremented
- Missing vol key in Redis: fallback to raw notional + metric `reason=no_vol_data`
- `marginal_variance_enabled=false`: raw notional used, no Redis vol reads
- `CorrelationService.compute_and_store()` writes `vol:{iid}` per instrument to Redis
- Lua script: `sector:*` key incremented on fill when sector non-null; not incremented when sector null
- `per_sector` limit check: order blocked when sector notional projected > limit

**Auto-promote veto window (~10):**
- Criteria pass + `auto_apply=True` + `veto_enabled=True` → `promote_pending` row inserted, Telegram sent, one-shot job scheduled
- `veto_promote_{id}` Telegram command → `vetoed`, job cancelled, confirmation sent
- Window expiry one-shot job fires → `promote_pending` → `promote()` called, `success`
- `veto_enabled=False` → immediate `promote()` called (22a path), no `promote_pending` row
- Duplicate `promote_pending` blocked by `uq_shadow_promotion_pending` index
- Existing `uq_shadow_promotion_success` still prevents double-promotion after veto window expires

### Frontend (~5 tests)
- Exposure panel: renders `sector:*` rows when `per_sector` limits exist in response
- `per_sector` limit create form: `sector` field required, submits correctly
- Exposure response with no sector limits: panel unchanged (no regressions)

---

## 9. Metrics (22a.1 additions)

| Metric | Type | Labels |
|---|---|---|
| `orchestrator_marginal_variance_fallback_total` | Counter | `reason` (stale_matrix, no_vol_data, error) |
| `orchestrator_mv_gate_latency_seconds` | Histogram | — |
| `orchestrator_sector_ingestion_total` | Counter | `outcome` (updated, skipped, error), `source` (ibkr, schwab, synthetic) |

---

## 10. Invariants Preserved

| Invariant | This patch |
|---|---|
| **Fail-CLOSED for hard capacity constraints** | Marginal-variance fallback to raw notional (not fail-OPEN) — conservative under data absence |
| **No new money-moving paths without CSRF** | Sector-refresh and backfill endpoints are admin-only but not money-moving (no CSRF needed) |
| **Schema changes via Alembic only** | 0069.1 migration only — no raw model edits |
| **Veto window preserves audit trail** | `promote_pending` and `vetoed` rows kept; no DELETE on veto |
| **22a behaviour preserved** | `veto_enabled=false` + `marginal_variance_enabled=false` reproduce exact 22a runtime paths |

---

## 11. Deferred

| Item | Target |
|---|---|
| Sector data for HKEX instruments (Futu) | No equivalent `reqContractDetails` in Futu API; defer to Phase 24 |
| Marginal-variance formula for options/futures (position sizing adjustment) | Out of scope — gate uses raw notional for non-equity instruments as conservative default |
| Per-bot marginal-variance threshold (different max for each bot) | Phase 22b/22c — not needed for 22a.1 correctness |
