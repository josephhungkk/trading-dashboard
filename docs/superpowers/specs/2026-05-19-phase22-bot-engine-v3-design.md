# Phase 22 — Bot Engine v3: Autonomous, Self-Refining (v0.22.x)

**Date:** 2026-05-19
**Status:** ARCHITECT-REVIEW applied (3 CRIT + 5 HIGH + 6 MED inline; 4 LOW noted) — ready for /writing-plans
**Builds on:** Phase 19 (Bot engine v1) · Phase 20 (Backtesting) · Phase 21a/21a.1 (LLM Advisor) · Phase 21b (LLM-in-loop: ParamTuner + ShadowPromoter) · Phase 21c (Advisor attribution)
**Next phases:** Phase 23 (UK CGT)

**Sub-phases:**
- **22a** — BotOrchestrator + PortfolioExposureGate + CorrelationService + AutoPromoteEvaluator + NightlyRetrainJob → v0.22.0
- **22b** — StrategyGenerator + child-process sandbox + Bot worker integration → v0.22.1
- **22c** — HealthDigestService + FE OrchestrationPage → v0.22.2

**ARCHITECT-REVIEW applied:** C1–C3 + H1–H5 + M1–M6 inline. L1–L4 noted in §8 / §9.

---

## 1. Goal

Close the Phase 22 ROADMAP deliverable: **autonomous, self-refining bot engine**. Four pillars:

1. **Multi-bot orchestration** — portfolio-level exposure gate (station 5.75), marginal-variance-adjusted notional, cross-bot kill switch.
2. **Auto-promotion rules** — replace the always-False Phase 21b stub with real configurable criteria (Sharpe/drawdown/win-rate); starts gated, config flag enables autonomy.
3. **Nightly retrain** — scheduled parallel trigger of Phase 21b `ParamTunerService` across all active bots; consolidated Telegram report.
4. **LLM-driven strategy generation** — LLM generates new `BaseStrategy` Python subclasses; child-process sandbox (spawn + setrlimit + seccomp) validates; gated human approval (auto-approve config flag for future autonomy); loads in isolated child process per bot.

**No raw RL.** Self-refinement ceiling = parameter tuning + LLM-suggested refinements + shadow-mode promotion (ROADMAP invariant).

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  BotOrchestrator  (app/services/orchestrator/)           [22a]  │
│  ├── PortfolioExposureGate  — pre-trade station 5.75            │
│  │     Redis HASH portfolio:exposure:{account_id}               │
│  │     Checks: total_notional, per-sector, per-instrument       │
│  │     Raw notional in 22a; marginal-variance deferred to 22a.1 │
│  │     Fail-CLOSED on Redis miss: PG fallback → BLOCK           │
│  ├── CorrelationService  — daily update via bars_1d             │
│  │     Pearson correlation matrix, N-day rolling window         │
│  │     Redis TTL 86400s; full symmetric matrix stored           │
│  └── AutoPromoteEvaluator  — replaces 21b always-False stub     │
│        Fire-once-per-shadow guard via shadow_promotion_events   │
│        Applies validated AutoPromoteCriteria (Pydantic model)   │
│        Promotes when criteria met AND app_config flag enabled   │
│                                                                 │
│  NightlyRetrainJob  (APScheduler 02:00, max_instances=1) [22a]  │
│  ├── asyncio.gather with semaphore (N=retrain_max_parallel)     │
│  └── On retrain complete: post Telegram ranked report           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  StrategyGenerator  (app/services/strategy_gen/)         [22b]  │
│  LLM → child-process sandbox (spawn+setrlimit+seccomp)          │
│  → AST allowlist + RestrictedPython compile                     │
│  → BacktestRunner → ranked report → gated promotion             │
│  Generated bots run in strategy_worker child process:           │
│  BarEvent/TickEvent in → OrderIntent out (narrow IPC queue)     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  HealthDigestService  (app/services/orchestrator/digest.py)[22c]│
│  Nightly cross-bot Sharpe/drawdown/win-rate ranking             │
│  Telegram digest + /orchestration dashboard page                │
└─────────────────────────────────────────────────────────────────┘
```

**Order-path integration:** `PortfolioExposureGate` is station 5.75 — after `BotRiskCapService` (station 5, per-bot caps) and before the advisor gate (station 5.5 from Phase 21a). Every bot order passes through portfolio-level exposure checks with zero changes to broker adapters.

**Shared state:** Portfolio exposure totals live in Redis (`portfolio:exposure:{account_id}`) as a HASH updated atomically by Lua script on every fill event. Keys: `total`, `sector:{sector_name}`, `instr:{instrument_id}`. Values: notional in USD (FX-converted via last known rate). PG fallback on Redis miss (§3.2 — C3 fix).

---

## 3. Phase 22a — BotOrchestrator + Auto-Promotion

### 3.1 Data Model (Alembic 0069)

**C1 note:** `instruments.id` is `BIGINT` (verified: alembic 0009). `bots.id` is `UUID` (verified: alembic 0061). All FK columns below use the correct types.

```sql
-- Portfolio-level exposure limits (extends existing risk_limits pattern)
CREATE TABLE portfolio_exposure_limits (
    id              BIGSERIAL PRIMARY KEY,
    account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
    limit_type      TEXT NOT NULL
        CHECK (limit_type IN ('total_notional','per_sector','per_instrument')),
    sector          TEXT,           -- NULL for total_notional + per_instrument rows
    instrument_id   BIGINT REFERENCES instruments(id) ON DELETE CASCADE,
                                    -- NULL for total_notional + per_sector rows
    max_notional    NUMERIC(20,8) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- M5: partial unique indexes prevent duplicate limit rows
CREATE UNIQUE INDEX uq_portfolio_exposure_total
    ON portfolio_exposure_limits(account_id)
    WHERE limit_type = 'total_notional';
CREATE UNIQUE INDEX uq_portfolio_exposure_sector
    ON portfolio_exposure_limits(account_id, sector)
    WHERE limit_type = 'per_sector';
CREATE UNIQUE INDEX uq_portfolio_exposure_instr
    ON portfolio_exposure_limits(account_id, instrument_id)
    WHERE limit_type = 'per_instrument';

-- Auto-promotion criteria per bot (replaces always-False stub in 21b)
ALTER TABLE bots
    ADD COLUMN auto_promote_criteria  JSONB
        -- M1: DB-side presence check; full validation at API boundary via Pydantic AutoPromoteCriteria
        CHECK (auto_promote_criteria IS NULL
            OR (auto_promote_criteria ? 'min_sharpe'
            AND auto_promote_criteria ? 'max_drawdown'
            AND auto_promote_criteria ? 'min_win_rate')),
    ADD COLUMN last_auto_promote_check_at  TIMESTAMPTZ;

-- Correlation matrix snapshot (audit trail; live state in Redis)
CREATE TABLE portfolio_correlation_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    instrument_ids  BIGINT[] NOT NULL,
    -- M2: full symmetric matrix stored (N² doubles JSON ≈ 8KB for N=30; clarity > savings)
    matrix_json     JSONB NOT NULL,   -- {instrument_id: {instrument_id: pearson_r}} full symmetric
    window_days     INT NOT NULL DEFAULT 30
);
CREATE INDEX portfolio_correlation_snapshots_account_computed_idx
    ON portfolio_correlation_snapshots (account_id, computed_at DESC);
```

### 3.2 PortfolioExposureGate (station 5.75)

**Pre-trade check flow:**
1. `HGETALL portfolio:exposure:{account_id}` — single Redis read.
2. Compute order notional = `qty × price × multiplier × fx_rate`.
3. **Raw notional check (H1: correlation adjustment deferred to 22a.1):** Use `qty × price × multiplier × fx_rate` as the notional contribution directly. The marginal-variance formula (Δσ²_p = 2·w_new·Σᵢ wᵢ·ρᵢ,new·σᵢ·σ_new + w²_new·σ²_new) is mathematically correct but complex enough that a wrong implementation would ship a subtly bad number rather than a safe conservative one. Raw notional is conservative (overstates correlated contribution) and never wrong in the dangerous direction. The correlation matrix is still computed by `CorrelationService` (for the FE heatmap and health digest), but not used in the gate calculation until 22a.1 provides a validated formula + backtest sanity check.
4. Check against each enabled `portfolio_exposure_limits` row for this account.
5. ALLOW / WARN / BLOCK — same three-outcome model as Phase 10 risk gate. Writes to `risk_audit_log` with `gate='portfolio_exposure'`.

**Fill update — Lua script (H2 fix):** Writer is `order_event_consumer` consuming `OrderFillEvent`. Lua signature:
```
HINCRBYFLOAT portfolio:exposure:{account_id} total <signed_delta_usd>
HINCRBYFLOAT portfolio:exposure:{account_id} sector:{sector} <signed_delta_usd>
HINCRBYFLOAT portfolio:exposure:{account_id} instr:{instrument_id} <signed_delta_usd>
```
where `signed_delta_usd = side_sign × qty × fill_price × multiplier × fx_rate`. `side_sign = +1` for buys, `−1` for sells. Partial sells and full closes both use negative delta — no separate "close" event needed. Sector is resolved from a Redis-cached `instr→sector` map (TTL 3600s, populated from `instruments.sector`). Test: buy-then-partial-sell flow produces correct net exposure.

**C3 fix — Fail-CLOSED on Redis miss (two-tier degrade):**
- Redis read miss → recompute exposure from `bot_orders` + `positions` in PG (~10–50ms, acceptable occasionally). Cache result back to Redis.
- PG also unavailable → **fail-CLOSED** with metric `orchestrator_exposure_gate_pg_fallback_total{outcome=block}`. Same fail-CLOSED posture as Phase 11d `check_trade` bucket.
- Kill switch: `app_config[orchestrator/exposure_gate_enabled]` (default `true`). Disabling the kill switch bypasses both Redis and PG path — explicit operator action.

### 3.3 CorrelationService

Daily job (APScheduler, configurable cron): reads `bars_1d` for all instruments held across active bots, computes Pearson correlation matrix over rolling `window_days` (default 30), stores **full symmetric matrix** to Redis (`portfolio:correlation:{account_id}`, TTL 86400s) and writes a `portfolio_correlation_snapshots` row for audit.

Matrix format: `{str(instrument_id): {str(instrument_id): float}}` — full symmetric, both `{i:{j}}` and `{j:{i}}` present. No diagonal mirroring needed at read time (M2 fix).

Stale matrix (>48h): `PortfolioExposureGate` falls back to raw notional, logs metric `orchestrator_correlation_matrix_age_seconds`.

### 3.4 AutoPromoteEvaluator

Replaces `check_auto_promote_eligibility()` always-False stub in `ShadowPromoterService`.

**M1 fix — AutoPromoteCriteria Pydantic model** (validated at `PUT /api/orchestrator/bots/{id}/auto-promote/criteria`):
```python
class AutoPromoteCriteria(BaseModel):
    model_config = ConfigDict(extra='forbid')
    min_sharpe: float
    max_drawdown: float          # 0–1, e.g. 0.15 = 15% max drawdown
    min_win_rate: float          # 0–1
    min_comparison_days: int = 14
    auto_apply: bool = False
```
Unknown keys rejected with 422.

**H4 fix — fire-once-per-shadow guard:** Before evaluating, check `shadow_promotion_events` for any non-failed row with `(live_bot_id, shadow_bot_id)` — if found, skip (already promoted or in progress). The `promoted_via TEXT` column (values `'manual'` / `'auto'`) is added to `shadow_promotion_events` in Alembic 0069.

Criteria evaluation: all of `min_sharpe`, `max_drawdown`, `min_win_rate`, `min_comparison_days` must pass. `app_config[orchestrator/auto_promote_enabled]` (default `false`) is the master switch — evaluator no-ops if `false` regardless of per-bot `auto_apply`. On pass + `auto_apply: true` + master switch on: calls existing `ShadowPromoterService.promote()` + sends Telegram notification.

### 3.5 NightlyRetrainJob

APScheduler cron `"0 2 * * *"` (L1: explicit crontab string, not CronTrigger kwargs, for consistency with Phase 21b). `max_instances=1`, `coalesce=True`, `misfire_grace_time=600` (H3 fix — overlapping jobs collapse cleanly).

Steps:
1. Query all bots where `is_shadow=False AND status='running'`.
2. **H3 fix — parallel fan-out with bounded concurrency:**
   ```python
   sem = asyncio.Semaphore(app_config[orchestrator/retrain_max_parallel])  # default 2
   async with asyncio.TaskGroup() as tg:
       for bot in bots:
           tg.create_task(_retrain_one(bot, sem))
   ```
   Each `_retrain_one` acquires the semaphore, calls `ParamTunerService.trigger(bot_id)`, awaits `poll_backtest_results` with per-bot timeout (`app_config[orchestrator/retrain_timeout_seconds]`, default 3600).
3. Collect ranked candidates across all bots.
4. Post consolidated Telegram report: N bots × M candidates ranked by Sharpe.
5. If `auto_promote_enabled` and top candidate clears threshold: auto-apply via existing `ParamTunerService.approve()`.

**Worst-case bound:** `ceil(N_bots / retrain_max_parallel) × retrain_timeout_seconds`. With defaults (N=10, parallel=2, timeout=3600): ~18000s = 5h. Document in operator notes.

### 3.6 Prometheus Metrics (22a)

| Metric | Type | Labels |
|---|---|---|
| `orchestrator_exposure_checks_total` | Counter | `outcome` (allow/warn/block) |
| `orchestrator_exposure_gate_latency_seconds` | Histogram | — |
| `orchestrator_exposure_gate_pg_fallback_total` | Counter | `outcome` (used/block) |
| `orchestrator_correlation_matrix_age_seconds` | Gauge | `account_id` |
| `orchestrator_auto_promote_total` | Counter | `outcome` (promoted/skipped/error) |
| `orchestrator_retrain_bots_total` | Counter | — |
| `orchestrator_retrain_latency_seconds` | Histogram | — |

### 3.7 REST API (22a)

All auto-promote routes under `/api/orchestrator/` prefix for consistency (L3 fix):

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/orchestrator/exposure` | JWT | Portfolio exposure state per account |
| `GET` | `/api/orchestrator/exposure-limits` | JWT | List `portfolio_exposure_limits` |
| `POST` | `/api/orchestrator/exposure-limits` | admin JWT | Create limit |
| `PUT` | `/api/orchestrator/exposure-limits/{id}` | admin JWT | Update limit |
| `DELETE` | `/api/orchestrator/exposure-limits/{id}` | admin JWT | Delete limit |
| `POST` | `/api/orchestrator/bots/{id}/auto-promote/evaluate` | admin JWT | Trigger immediate evaluation |
| `PUT` | `/api/orchestrator/bots/{id}/auto-promote/criteria` | admin JWT + CSRF | Set `auto_promote_criteria` (validated via `AutoPromoteCriteria`) |
| `POST` | `/api/orchestrator/retrain` | admin JWT + CSRF | Trigger manual retrain run |

### 3.8 Implementation Chunks (22a)

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0069 (+ `shadow_promotion_events.promoted_via` column) + migration tests | Qwen | — |
| **B — ExposureGate + Lua** | `orchestrator/exposure_gate.py`, Redis Lua script, PG fallback, unit tests (incl. buy-then-partial-sell, fail-CLOSED) | Codex | after A |
| **C — CorrelationService** | `orchestrator/correlation.py`, marginal-variance calculation, tests (incl. negative ρ, NaN bars) | Qwen | after A |
| **D — AutoPromoteEvaluator** | `orchestrator/auto_promote.py`, `AutoPromoteCriteria` Pydantic model, fire-once guard, tests | Qwen | after A |
| **E — NightlyRetrain + metrics** | `orchestrator/retrain.py`, `orchestrator/metrics.py`, `main.py` APScheduler wiring, tests (parallel fan-out, timeout, overlap collapse) | Codex | after B/C/D |
| **F — REST API** | `api/orchestrator.py`, tests | Qwen | after B/C/D |
| **G — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.22.0 | Opus direct | after all |

---

## 4. Phase 22b — LLM Strategy Generator

### 4.1 Data Model (Alembic 0070)

**C1 fix:** `promoted_bot_id` and `bot_strategy_provenance.bot_id` use `UUID` (not BIGINT) to match `bots.id`.

```sql
CREATE TABLE generated_strategies (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    source_code         TEXT NOT NULL,
    source_hash         TEXT NOT NULL,          -- SHA-256 of source_code; re-validated on every load (M4)
    -- L4: generation_prompt is multi-KB; PG TOAST compresses automatically. prompt_hash for dedupe.
    generation_prompt   TEXT NOT NULL,
    prompt_hash         TEXT NOT NULL,          -- SHA-256 of generation_prompt; dedupe before re-generating
    llm_model           TEXT NOT NULL,
    sandbox_status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (sandbox_status IN ('pending','validated','rejected','promoted')),
    sandbox_error       TEXT,
    backtest_id         BIGINT REFERENCES backtests(id) ON DELETE SET NULL,
    promoted_bot_id     UUID REFERENCES bots(id) ON DELETE SET NULL,   -- C1 fix: UUID
    approved_by         TEXT,                   -- JWT subject
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX generated_strategies_sandbox_status_idx
    ON generated_strategies (sandbox_status, created_at DESC);
CREATE INDEX generated_strategies_prompt_hash_idx
    ON generated_strategies (prompt_hash);      -- L4: dedupe lookup

CREATE TABLE bot_strategy_provenance (
    bot_id          UUID REFERENCES bots(id) ON DELETE CASCADE PRIMARY KEY,  -- C1 fix: UUID
    strategy_id     BIGINT REFERENCES generated_strategies(id) ON DELETE SET NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.2 StrategyGenerator Service (`app/services/strategy_gen/`)

**Generation prompt structure:**
- System: `BaseStrategy` ABC interface contract, allowed imports allowlist, prohibited patterns (network, file I/O, subprocess, `__import__`, eval/exec)
- Context: last 30-day market commentary from `AdvisorService` + Phase 18 scanner signals
- **H5 fix:** Phase 21c attribution signal (underperforming strategies) is **excluded** from the generation prompt. Short-window attribution data (≤30d) is too noisy and biases new strategies toward inverting recent losers — a backtest-overfitting amplifier. Attribution context is only included if the attribution window is ≥90 days AND `app_config[strategy_gen/include_attribution_context]` is explicitly `true` (default `false`). When included, bot_id and strategy names are redacted from the prompt (replaced with `bot_A`, `bot_B`).
- User: `"Generate a new trading strategy for {asset_class} instruments. Recent market context: {context}. Interface contract: {contract}. Be creative but respect the contract."`

**C2 fix — Child-process sandbox (Option A):** Generated strategies run in a `multiprocessing.Process(start_method='spawn')` child with:
- `resource.setrlimit(RLIMIT_AS, soft=512MB)` + `resource.setrlimit(RLIMIT_CPU, soft=30s)` — resource caps.
- `seccomp` (via `pyseccomp` or `python-prctl`) — blocks all syscalls except a minimal allowlist (read, write, mmap, exit, futex, clock_gettime).
- **Stripped environment:** no DB engine, no Redis client, no BrokerRegistry, no advisor handles — child receives only a narrow IPC channel.
- **IPC protocol:** `multiprocessing.Queue` pair — `event_queue` (parent→child: `BarEvent | TickEvent`) and `intent_queue` (child→parent: `OrderIntent | None`). Parent's `BotSupervisor` orchestrates the child; child's strategy calls `self.emit_order(intent)` which writes to `intent_queue`.
- **Spawn latency:** ~50–100ms per bot start — acceptable for bot lifecycle (not on hot order path).

**Sandbox validation pipeline (at approval time):**
1. `ast.parse()` — syntax check.
2. `RestrictedPython.compile_restricted()` — restricted mode compile.
3. AST walk: reject any `Import`/`ImportFrom` not in `{numpy, pandas, ta, math, decimal, collections, itertools}`.
4. AST walk: reject any `Call` to `eval`, `exec`, `open`, `__import__`, `subprocess`, `socket`, `os`, `sys`.
5. Pass → write `source_hash = sha256(source_code)`, set `sandbox_status='validated'`, auto-submit to `BacktestRunner` (runs in backtest worker, not in strategy child process).
6. Fail → `sandbox_status='rejected'`, write `sandbox_error`.

**M4 fix — Re-validate on every load:** `strategy_loader.py` re-hashes `sha256(source_code)` and re-runs the AST allowlist walk before spawning the child process. On mismatch or AST rejection: log structured error, set `sandbox_status='rejected'` with reason `'tampered'`, refuse to start the bot.

**Human approval gate:** `POST /api/strategy-gen/{id}/approve` (admin JWT + CSRF nonce). Sets `sandbox_status='promoted'`, `approved_by`, `approved_at`. Creates new `bot` row with `strategy_class='generated:{id}'` + **`status='paper_pending'`** (M6 fix — holds in pending state during veto window).

**M6 fix — Veto window with `paper_pending` status:**
- On auto-approve: bot created with `status='paper_pending'`. `BotSupervisor` refuses to start `paper_pending` rows.
- Telegram notification posts with `/veto_{id}` command available for `strategy_gen/veto_window_minutes` (default 60).
- On window expiry (APScheduler one-shot job): flip `status='paper'` → supervisor starts the bot.
- On `/veto_{id}` within window: flip `status='vetoed'` — no DELETE; full audit trail preserved.
- Manual approve path: bot created directly as `status='paper'` (no veto window — human already reviewed).

**Auto-approve path (gated):** `app_config[strategy_gen/auto_approve_enabled]` (default `false`). When `true` + backtest Sharpe ≥ `app_config[strategy_gen/auto_approve_min_sharpe]`: creates `paper_pending` bot, starts veto window timer, posts Telegram.

### 4.3 Bot Worker Integration

`BotSupervisor` extension: when `strategy_class` starts with `'generated:'`:
1. Look up `generated_strategies` by ID; assert `sandbox_status='promoted'` (gate — refuses if not promoted).
2. **M4:** Re-hash `sha256(source_code)` vs stored `source_hash`; re-run AST allowlist walk. On mismatch: mark `rejected`, log, refuse start.
3. Spawn `multiprocessing.Process(target=_strategy_worker, start_method='spawn', args=(source_code, event_queue, intent_queue))` with resource limits + seccomp.
4. `_strategy_worker` entry point: compile restricted bytecode, exec in stripped globals, instantiate `BaseStrategy` subclass, enter event loop (read from `event_queue`, call `strategy.on_bar()`, write any returned `OrderIntent` to `intent_queue`).
5. Parent supervisor reads `intent_queue` → routes `OrderIntent` through the normal pre-trade gate pipeline (stations 1–5.75 — including the new `PortfolioExposureGate`).

Unknown or unvalidated ID → structured log error + bot fails to start; `sandbox_status != 'promoted'` is absolute gate.

### 4.4 Prometheus Metrics (22b)

| Metric | Type | Labels |
|---|---|---|
| `strategy_gen_generated_total` | Counter | `outcome` (validated/rejected) |
| `strategy_gen_sandbox_latency_seconds` | Histogram | — |
| `strategy_gen_auto_approved_total` | Counter | — |
| `strategy_gen_veto_window_cancellations_total` | Counter | — |
| `strategy_gen_load_hash_mismatch_total` | Counter | — |

### 4.5 REST API (22b)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/strategy-gen` | JWT | List generated strategies + sandbox status |
| `GET` | `/api/strategy-gen/{id}` | JWT | Detail: source, backtest result, error |
| `POST` | `/api/strategy-gen/generate` | admin JWT | Trigger LLM generation |
| `POST` | `/api/strategy-gen/{id}/approve` | admin JWT + CSRF | Promote to paper_pending bot |
| `POST` | `/api/strategy-gen/{id}/reject` | admin JWT + CSRF | Permanently reject |

### 4.6 `app_config` keys (22b)

| Key | Default | Description |
|---|---|---|
| `strategy_gen/enabled` | `true` | Kill switch for generation endpoint |
| `strategy_gen/auto_approve_enabled` | `false` | Enable auto-approve path |
| `strategy_gen/auto_approve_min_sharpe` | `0.5` | Sharpe threshold for auto-approve |
| `strategy_gen/veto_window_minutes` | `60` | Telegram veto window duration |
| `strategy_gen/allowed_imports` | `["numpy","pandas","ta","math","decimal","collections","itertools"]` | Sandbox allowlist |
| `strategy_gen/include_attribution_context` | `false` | Include 21c attribution in prompt (≥90d window required) |
| `strategy_gen/child_process_memory_mb` | `512` | RLIMIT_AS for strategy child process |
| `strategy_gen/child_process_cpu_seconds` | `30` | RLIMIT_CPU for strategy child process |

### 4.7 Implementation Chunks (22b)

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0070 + migration tests | Qwen | — |
| **B — StrategyGenerator + sandbox** | `strategy_gen/generator.py`, `strategy_gen/sandbox.py` (AST + RestrictedPython), unit tests (incl. H5 prompt test, reflection-chain rejection) | Codex | after A |
| **C — Bot worker integration** | `bot/supervisor.py` extension, `bot/strategy_loader.py` (child-process spawn + IPC + re-hash), `bot/strategy_worker.py` (child entry point), tests | Codex | after B |
| **D — REST API** | `api/strategy_gen.py`, tests (incl. CSRF gate, paper_pending flow) | Qwen | after B |
| **E — FE strategy-gen feed** | `OrchestrationPage` panel 4, `strategy_gen/api.ts` + `types.ts` | Codex | after D |
| **F — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.22.1 | Opus direct | after all |

---

## 5. Phase 22c — Health Digest + FE Orchestration Dashboard

### 5.1 Data Model (Alembic 0071)

**C1 fix:** `bot_health_snapshots.bot_id` uses `UUID` (not BIGINT) to match `bots.id`.

```sql
CREATE TABLE bot_health_snapshots (
    bot_id          UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,  -- C1 fix: UUID
    snapshot_at     TIMESTAMPTZ NOT NULL,
    sharpe_30d      NUMERIC(10,4),
    sharpe_7d       NUMERIC(10,4),
    max_drawdown    NUMERIC(10,4),
    win_rate        NUMERIC(10,4),
    total_pnl       NUMERIC(20,8),
    trade_count     INT,
    advisor_veto_accuracy_1h  NUMERIC(10,4),   -- from Phase 21c
    exposure_utilisation      NUMERIC(10,4),   -- current / limit (0–1)
    PRIMARY KEY (bot_id, snapshot_at)
);
SELECT create_hypertable('bot_health_snapshots', 'snapshot_at');
CREATE INDEX bot_health_snapshots_bot_id_idx
    ON bot_health_snapshots (bot_id, snapshot_at DESC);
-- M3: explicit retention policy (matches account_balance_snapshots pattern from Phase 10b.2)
SELECT add_retention_policy('bot_health_snapshots', INTERVAL '2 years');
```

### 5.2 HealthDigestService (`app/services/orchestrator/digest.py`)

APScheduler cron `"0 3 * * *"` (L1: crontab string). After retrain at `02:00`. `max_instances=1`, `coalesce=True`.

1. For each live bot: read `bot_runs` → compute rolling 30d Sharpe, max drawdown, win rate, total PnL, trade count.
2. Fetch 21c advisor attribution accuracy (veto accuracy at 1h window per bot from `bot_advisor_decisions`).
3. Rank by Sharpe (desc). Flag underperformers: Sharpe < `app_config[orchestrator/underperform_sharpe_threshold]` (default `0.0`).
4. Compute portfolio-level stats: aggregate NLV, total exposure utilisation (current / limit), max pairwise ρ.
5. Post Telegram digest: rank table with trend badge (▲ improving / ▼ degrading / — stable, based on 7d vs 30d Sharpe delta). Kill switch: `app_config[orchestrator/digest_telegram_enabled]` (default `true`).
6. Write `bot_health_snapshots` row per bot.

### 5.3 Frontend — `/orchestration` Dashboard Page

New route `frontend/src/pages/OrchestrationPage.tsx`. Four panels:

**Panel 1 — Cross-bot league table**
Table: Rank | Bot | Sharpe (30d) | Drawdown | Win Rate | Advisor Accuracy | Exposure % | Trend badge. Sortable. Row click → `BotDetailPage`.
Data: `GET /api/orchestrator/digest/latest`. Stale time: 300s.

**Panel 2 — Portfolio exposure heatmap**
Instrument × account matrix. Cell colour = exposure utilisation (0–100% of limit). Hover: current notional, limit, raw notional contribution.
Data: `GET /api/orchestrator/exposure`. Stale time: 60s.

**Panel 3 — Correlation matrix**
N×N heatmap of pairwise Pearson ρ. Colour: −1 (blue) → 0 (white) → +1 (red). |ρ| > 0.7 cells: border highlight.
Data: `GET /api/orchestrator/correlation`. Stale time: 3600s (updates nightly).

**Panel 4 — Strategy generation feed** (22b consumer)
List of recent `generated_strategies`: sandbox status badge, backtest Sharpe, `paper_pending` veto countdown, approve/reject buttons (admin only, CSRF).
Data: `GET /api/strategy-gen`. Stale time: 60s.

### 5.4 REST API (22c additions)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/orchestrator/digest/latest` | JWT | Latest health snapshot per bot |
| `GET` | `/api/orchestrator/digest/history/{bot_id}` | JWT | Historical snapshots for sparklines |
| `GET` | `/api/orchestrator/correlation` | JWT | Current correlation matrix from Redis |

### 5.5 `app_config` keys (22c)

| Key | Default | Description |
|---|---|---|
| `orchestrator/digest_cron` | `"0 3 * * *"` | Digest schedule (L1: crontab string) |
| `orchestrator/underperform_sharpe_threshold` | `0.0` | Flag threshold for digest |
| `orchestrator/digest_telegram_enabled` | `true` | Kill switch for Telegram digest post |

### 5.6 Implementation Chunks (22c)

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema + HealthDigestService** | Alembic 0071, `orchestrator/digest.py`, `main.py` APScheduler wiring, tests | Qwen | — |
| **B — Telegram digest** | `orchestrator/digest_telegram.py`, tests | Qwen | after A |
| **C — FE OrchestrationPage** | `OrchestrationPage.tsx` (panels 1–3), `orchestrator/api.ts` + `types.ts`, tests | Codex | after A |
| **D — REST API** | `api/orchestrator.py` digest + correlation endpoints, tests | Qwen | after A |
| **E — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.22.2 | Opus direct | after all |

---

## 6. Testing Strategy

### 22a Backend (~60 tests — L2: raised from 45 to reflect correlation math + Lua edge cases)

- `PortfolioExposureGate`: allow/warn/block for total_notional/per_sector/per_instrument; raw notional used (correlation adjustment deferred to 22a.1); Redis fail → PG fallback → correct totals; PG also down → fail-CLOSED + metric; kill switch; `risk_audit_log` row written
- Lua script atomicity: concurrent fill events don't race on exposure HASH; buy-then-partial-sell → correct net exposure; full close → zero balance
- `CorrelationService`: Pearson matrix correct from `bars_1d`; full symmetric form (both {i:{j}} and {j:{i}}); Redis TTL 86400s; NaN bars handled; stale fallback
- `AutoPromoteEvaluator`: all criteria pass → `promote()` called; any fail → skip; `auto_apply=false` → report only; master switch off → no-op; fire-once guard prevents double-promote of same shadow; `promoted_via='auto'` written; Telegram sent
- `NightlyRetrainJob`: asyncio.gather with semaphore N=2; skips paused/stopped bots; timeout per bot; overlapping schedule collapses (max_instances=1); posts Telegram; auto-applies if threshold + flag on
- REST: exposure CRUD, partial-unique-index blocks dupe limits (409), evaluate endpoint, retrain trigger
- `AutoPromoteCriteria`: unknown key → 422; missing required field → 422

### 22b Backend (~45 tests — raised from 35 for C2/M4/H5/M6)

- Sandbox: valid code → `validated`; syntax error → `rejected`; prohibited import → `rejected`; `eval`/`exec`/`open` → `rejected`; allowlist import passes; reflection chain `().__class__.__mro__[-1].__subclasses__()` → `rejected`
- M4 re-hash: tampered `source_code` (hash mismatch) → `rejected` + metric on load; clean code → starts correctly
- H5 prompt: attribution context absent by default; present only when window ≥90d + config `true`; bot_id/strategy names redacted when included
- M6 veto window: auto-approve creates `paper_pending`; supervisor refuses to start `paper_pending`; veto within window → `vetoed`; window expiry → `paper`; manual approve bypasses veto window
- Backtest auto-submission: validated strategy triggers `BacktestRunner`
- Child process: `strategy_class='generated:{id}'` spawns child, IPC queue delivers BarEvent, OrderIntent returned; unknown ID → clear error; resource limits applied
- Security: `POST /api/strategy-gen/{id}/approve` rejected without CSRF; sandbox rejects `__import__('os').system(...)` pattern; `paper_pending` bot not tradeable
- `prompt_hash` deduplication: same prompt → existing row found

### 22c Backend (~20 tests)

- `HealthDigestService`: Sharpe/drawdown/win-rate correct from `bot_runs`; attribution accuracy from 21c; underperformer flagging; `bot_health_snapshots` row written per bot; retention policy set
- Telegram digest: table rendered correctly; trend badge (▲/▼/—) logic; kill switch suppresses post
- Correlation endpoint: reads from Redis; 404 when no snapshot yet

### Frontend (~20 tests)

- `OrchestrationPage`: all four panels render; league table sortable by Sharpe; exposure heatmap colour scale; correlation |ρ| > 0.7 border highlight; strategy feed approve/reject hidden for non-admin; `paper_pending` veto countdown visible
- Stale time assertions: correlation = 3600s, digest = 300s, exposure = 60s, strategy-gen = 60s

---

## 7. Invariants Preserved

| Invariant | This phase |
|---|---|
| **No raw RL** | StrategyGenerator uses LLM + RestrictedPython; no RL training loop |
| **Fail-CLOSED for hard capacity constraints (C3)** | `PortfolioExposureGate`: Redis miss → PG fallback; PG miss → BLOCK + metric. Advisor gate's fail-OPEN posture is not reused here — exposure is a hard cap, not opinionated guidance |
| **Human approval gate** | Auto-promote and auto-approve both default `false`; operator opts in explicitly |
| **No new money-moving paths without CSRF** | All approve/promote endpoints require admin JWT + CSRF nonce |
| **Strategy isolation (C2)** | Generated strategies run in a spawned child process with stripped environment (no DB/Redis/broker handles), resource limits, and seccomp. Parent routes `OrderIntent` through full pre-trade gate pipeline |
| **Source integrity on every load (M4)** | `strategy_loader.py` rehashes `sha256(source_code)` and re-runs AST allowlist walk before spawning child. Tampered rows are rejected and marked |
| **Bot crash ≠ API crash** | Generated strategy child crash terminates only the child; `BotSupervisor` handles restart/pause per existing Phase 19 pattern |
| **Schema changes via Alembic only** | 0069 / 0070 / 0071 — no raw model edits |

---

## 8. Deferred

| Item | Target |
|---|---|
| FX conversion for exposure notional (multi-currency accounts) | Phase 24 infra hardening |
| Multi-worker Redis exposure HASH consistency (advisory lock) | Phase 24 multi-worker uvicorn |
| Raw RL | Post-v1.0 / out of scope (ROADMAP invariant) |
| LLM re-evaluation of failed strategies ("why did it fail?") | Beyond Phase 22 |
| Attribution for generated strategies (21c path) | Automatic — 21c's `AttributionService` covers all `bot_advisor_decisions` rows |
| Telegram veto window for auto-promote (not just auto-approve) | Phase 22a.1 patch if needed |
| Marginal-variance-adjusted notional in PortfolioExposureGate (raw notional used in 22a) | Phase 22a.1 — requires validated formula + backtest sanity check |

---

## 9. Known Issues (LOWs)

| # | Issue | Resolution |
|---|---|---|
| L1 | APScheduler cron expression style | All crons use `"0 H * * *"` crontab strings (not `CronTrigger` kwargs) for consistency with Phase 21b. Documented in §3.5 and §5.2. |
| L2 | Test count estimate was low | Raised 22a from ~45 to ~60; 22b from ~35 to ~45 to reflect C2/M4/H5/M6 additions. |
| L3 | Auto-promote routes under `/api/bots/` mixed with orchestrator routes | All orchestrator routes unified under `/api/orchestrator/` prefix (§3.7). |
| L4 | `generation_prompt TEXT` is multi-KB | PG TOAST compresses automatically — no action needed. `prompt_hash` column added for deduplication before re-generating (§4.1). |
