# Phase 22 — Bot Engine v3: Autonomous, Self-Refining (v0.22.x)

**Date:** 2026-05-19
**Status:** Brainstorm complete — ready for ARCHITECT-REVIEW
**Builds on:** Phase 19 (Bot engine v1) · Phase 20 (Backtesting) · Phase 21a/21a.1 (LLM Advisor) · Phase 21b (LLM-in-loop: ParamTuner + ShadowPromoter) · Phase 21c (Advisor attribution)
**Next phases:** Phase 23 (UK CGT)

**Sub-phases:**
- **22a** — BotOrchestrator + PortfolioExposureGate + CorrelationService + AutoPromoteEvaluator + NightlyRetrainJob → v0.22.0
- **22b** — StrategyGenerator + RestrictedPython sandbox + Bot worker integration → v0.22.1
- **22c** — HealthDigestService + FE OrchestrationPage → v0.22.2

---

## 1. Goal

Close the Phase 22 ROADMAP deliverable: **autonomous, self-refining bot engine**. Four pillars:

1. **Multi-bot orchestration** — portfolio-level exposure gate (station 5.75), correlation-adjusted notional, cross-bot kill switch.
2. **Auto-promotion rules** — replace the always-False Phase 21b stub with real configurable criteria (Sharpe/drawdown/win-rate); starts gated, config flag enables autonomy.
3. **Nightly retrain** — scheduled trigger of Phase 21b `ParamTunerService` across all active bots; consolidated Telegram report.
4. **LLM-driven strategy generation** — LLM generates new `BaseStrategy` Python subclasses; RestrictedPython sandbox validates; gated human approval (auto-approve config flag for future autonomy); loads dynamically in bot worker.

**No raw RL.** Self-refinement ceiling = parameter tuning + LLM-suggested refinements + shadow-mode promotion (ROADMAP invariant).

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  BotOrchestrator  (app/services/orchestrator/)           [22a]  │
│  ├── PortfolioExposureGate  — pre-trade station 5.75            │
│  │     Redis HASH portfolio:exposure:{account_id}               │
│  │     Checks: total_notional, per-sector, per-instrument       │
│  │     Correlation-adjusted notional (correlation matrix cache) │
│  ├── CorrelationService  — daily update via bars_1d             │
│  │     Pearson correlation matrix, N-day rolling window         │
│  │     Redis TTL 86400s; updated nightly                        │
│  └── AutoPromoteEvaluator  — replaces 21b always-False stub     │
│        Reads shadow_promotion_events + bot_runs metrics         │
│        Applies configurable criteria (Sharpe/drawdown/win-rate) │
│        Promotes when criteria met AND app_config flag enabled   │
│                                                                 │
│  NightlyRetrainJob  (APScheduler 02:00 local)            [22a]  │
│  ├── For each active live bot: trigger ParamTunerService        │
│  └── On retrain complete: post Telegram ranked report           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  StrategyGenerator  (app/services/strategy_gen/)         [22b]  │
│  LLM → RestrictedPython sandbox → BacktestRunner                │
│  → ranked report → gated (or auto) promotion                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  HealthDigestService  (app/services/orchestrator/digest.py)[22c]│
│  Nightly cross-bot Sharpe/drawdown/win-rate ranking             │
│  Telegram digest + /orchestration dashboard page                │
└─────────────────────────────────────────────────────────────────┘
```

**Order-path integration:** `PortfolioExposureGate` is station 5.75 — after `BotRiskCapService` (station 5, per-bot caps) and before the advisor gate (station 5.5 from Phase 21a). Every bot order passes through portfolio-level exposure checks with zero changes to broker adapters.

**Shared state:** Portfolio exposure totals live in Redis (`portfolio:exposure:{account_id}`) as a HASH updated atomically by Lua script on every fill event. Keys: `total`, `sector:{sector_name}`, `instr:{instrument_id}`. Values: notional in USD (FX-converted via last known rate). No DB round-trip on the hot path.

---

## 3. Phase 22a — BotOrchestrator + Auto-Promotion

### 3.1 Data Model (Alembic 0069)

```sql
-- Portfolio-level exposure limits (extends existing risk_limits pattern)
CREATE TABLE portfolio_exposure_limits (
    id              BIGSERIAL PRIMARY KEY,
    account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
    limit_type      TEXT NOT NULL
        CHECK (limit_type IN ('total_notional','per_sector','per_instrument')),
    sector          TEXT,          -- NULL for total_notional + per_instrument rows
    instrument_id   BIGINT REFERENCES instruments(id) ON DELETE CASCADE,
                                   -- NULL for total_notional + per_sector rows
    max_notional    NUMERIC(20,8) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Auto-promotion criteria per bot (replaces always-False stub in 21b)
ALTER TABLE bots
    ADD COLUMN auto_promote_criteria  JSONB,
    -- e.g. {"min_sharpe": 0.5, "max_drawdown": 0.15, "min_win_rate": 0.55,
    --        "min_comparison_days": 14, "auto_apply": false}
    ADD COLUMN last_auto_promote_check_at  TIMESTAMPTZ;

-- Correlation matrix snapshot (audit trail; live state in Redis)
CREATE TABLE portfolio_correlation_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    instrument_ids  BIGINT[] NOT NULL,
    matrix_json     JSONB NOT NULL,   -- {i: {j: pearson_r}} sparse upper-triangle
    window_days     INT NOT NULL DEFAULT 30
);
CREATE INDEX portfolio_correlation_snapshots_account_computed_idx
    ON portfolio_correlation_snapshots (account_id, computed_at DESC);
```

### 3.2 PortfolioExposureGate (station 5.75)

**Pre-trade check flow:**
1. `HGETALL portfolio:exposure:{account_id}` — single Redis read.
2. Compute order notional = `qty × price × multiplier × fx_rate`.
3. Compute correlation-adjusted contribution: `notional × sqrt(1 + 2ρ)` where ρ is the bot's existing position correlation to the portfolio (from cached matrix). Falls back to raw notional if matrix stale/missing.
4. Check against each enabled `portfolio_exposure_limits` row for this account.
5. ALLOW / WARN / BLOCK — same three-outcome model as Phase 10 risk gate. Writes to `risk_audit_log` with `gate='portfolio_exposure'`.

**Fill update:** Lua script atomically updates `portfolio:exposure:{account_id}` HASH on every fill event. Inverted on position close.

**Kill switch:** `app_config[orchestrator/exposure_gate_enabled]` (default `true`). Fail-OPEN: any Redis error → log + allow (same pattern as advisor gate).

### 3.3 CorrelationService

Daily job (APScheduler, configurable cron): reads `bars_1d` for all instruments held across active bots, computes Pearson correlation matrix over rolling `window_days` (default 30), stores to Redis (`portfolio:correlation:{account_id}`, TTL 86400s) and writes a `portfolio_correlation_snapshots` row for audit.

Stale matrix (>48h): `PortfolioExposureGate` falls back to raw notional, logs warning metric.

### 3.4 AutoPromoteEvaluator

Replaces `check_auto_promote_eligibility()` always-False stub in `ShadowPromoterService`. Criteria evaluated per `bots.auto_promote_criteria` JSONB:

| Criterion | Field | Default |
|---|---|---|
| Sharpe delta | `min_sharpe` | no default — must be set |
| Max drawdown | `max_drawdown` | no default — must be set |
| Win rate floor | `min_win_rate` | no default — must be set |
| Min comparison window | `min_comparison_days` | 14 |
| Auto-apply | `auto_apply` | `false` |

All criteria must pass. `app_config[orchestrator/auto_promote_enabled]` (default `false`) is the master switch — evaluator no-ops if `false` regardless of per-bot `auto_apply`. On pass + `auto_apply: true`: calls existing `ShadowPromoterService.promote()` + sends Telegram notification.

### 3.5 NightlyRetrainJob

APScheduler cron at `02:00` local (configurable via `app_config[orchestrator/retrain_cron]`). Steps:
1. Query all bots where `is_shadow=False AND status='running'`.
2. For each: call `ParamTunerService.trigger(bot_id)` (Phase 21b).
3. Await completion via existing `poll_backtest_results` loop (timeout: `app_config[orchestrator/retrain_timeout_seconds]`, default 3600).
4. Collect ranked candidates across all bots.
5. Post consolidated Telegram report: N bots × M candidates ranked by Sharpe.
6. If `auto_promote_enabled` and top candidate clears threshold: auto-apply via existing `ParamTunerService.approve()`.

### 3.6 Prometheus Metrics (22a)

| Metric | Type | Labels |
|---|---|---|
| `orchestrator_exposure_checks_total` | Counter | `outcome` (allow/warn/block) |
| `orchestrator_exposure_gate_latency_seconds` | Histogram | — |
| `orchestrator_correlation_matrix_age_seconds` | Gauge | `account_id` |
| `orchestrator_auto_promote_total` | Counter | `outcome` (promoted/skipped/error) |
| `orchestrator_retrain_bots_total` | Counter | — |
| `orchestrator_retrain_latency_seconds` | Histogram | — |

### 3.7 REST API (22a)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/orchestrator/exposure` | JWT | Portfolio exposure state per account |
| `GET` | `/api/orchestrator/exposure-limits` | JWT | List `portfolio_exposure_limits` |
| `POST` | `/api/orchestrator/exposure-limits` | admin JWT | Create limit |
| `PUT` | `/api/orchestrator/exposure-limits/{id}` | admin JWT | Update limit |
| `DELETE` | `/api/orchestrator/exposure-limits/{id}` | admin JWT | Delete limit |
| `POST` | `/api/bots/{id}/auto-promote/evaluate` | admin JWT | Trigger immediate evaluation |
| `PUT` | `/api/bots/{id}/auto-promote/criteria` | admin JWT + CSRF | Set `auto_promote_criteria` |
| `POST` | `/api/orchestrator/retrain` | admin JWT + CSRF | Trigger manual retrain run |

### 3.8 Implementation Chunks (22a)

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0069 + migration tests | Qwen | — |
| **B — ExposureGate + Lua** | `orchestrator/exposure_gate.py`, Redis Lua script, unit tests | Codex | after A |
| **C — CorrelationService** | `orchestrator/correlation.py`, tests | Qwen | after A |
| **D — AutoPromoteEvaluator** | `orchestrator/auto_promote.py`, tests | Qwen | after A |
| **E — NightlyRetrain + metrics** | `orchestrator/retrain.py`, `orchestrator/metrics.py`, `main.py` APScheduler wiring, tests | Codex | after B/C/D |
| **F — REST API** | `api/orchestrator.py`, tests | Qwen | after B/C/D |
| **G — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.22.0 | Opus direct | after all |

---

## 4. Phase 22b — LLM Strategy Generator

### 4.1 Data Model (Alembic 0070)

```sql
CREATE TABLE generated_strategies (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    source_code         TEXT NOT NULL,
    source_hash         TEXT NOT NULL,          -- SHA-256 of source_code
    restricted_bytecode BYTEA,                  -- compiled RestrictedPython output
    sandbox_status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (sandbox_status IN ('pending','validated','rejected','promoted')),
    sandbox_error       TEXT,
    generation_prompt   TEXT NOT NULL,          -- audit trail
    llm_model           TEXT NOT NULL,
    backtest_id         BIGINT REFERENCES backtests(id) ON DELETE SET NULL,
    promoted_bot_id     BIGINT REFERENCES bots(id) ON DELETE SET NULL,
    approved_by         TEXT,                   -- JWT subject
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX generated_strategies_sandbox_status_idx
    ON generated_strategies (sandbox_status, created_at DESC);

CREATE TABLE bot_strategy_provenance (
    bot_id          BIGINT REFERENCES bots(id) ON DELETE CASCADE PRIMARY KEY,
    strategy_id     BIGINT REFERENCES generated_strategies(id) ON DELETE SET NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.2 StrategyGenerator Service (`app/services/strategy_gen/`)

**Generation prompt structure:**
- System: `BaseStrategy` ABC interface contract, allowed imports allowlist, prohibited patterns (network, file I/O, subprocess, `__import__`, eval/exec)
- Context: last 30-day market commentary from `AdvisorService` + Phase 18 scanner signals + Phase 21c attribution signal (which strategies are underperforming and why)
- User: `"Generate a new trading strategy for {asset_class} instruments. Recent performance context: {context}. Existing strategies: {strategy_list}. Be creative but respect the interface contract."`

**RestrictedPython sandbox validation pipeline:**
1. `ast.parse()` — syntax check.
2. `RestrictedPython.compile_restricted()` — restricted mode compile.
3. AST walk: reject any `Import`/`ImportFrom` not in `{numpy, pandas, ta, math, decimal, collections, itertools}`.
4. AST walk: reject any `Call` to `eval`, `exec`, `open`, `__import__`, `subprocess`, `socket`, `os`, `sys`.
5. Pass → write `restricted_bytecode`, set `sandbox_status='validated'`, auto-submit to `BacktestRunner`.
6. Fail → `sandbox_status='rejected'`, write `sandbox_error`.

**Human approval gate:** `POST /api/strategy-gen/{id}/approve` (admin JWT + CSRF nonce). Sets `sandbox_status='promoted'`, `approved_by`, `approved_at`. Creates new `bot` row with `strategy_class='generated:{id}'` + `status='paper'` (always starts paper). Bot worker loads generated strategy by looking up `restricted_bytecode` when `strategy_class` starts with `'generated:'`.

**Auto-approve path (gated):** `app_config[strategy_gen/auto_approve_enabled]` (default `false`). When `true` + backtest Sharpe ≥ `app_config[strategy_gen/auto_approve_min_sharpe]`: skips human click, posts Telegram notification with 1-hour veto window (`/veto_{id}` within window cancels promotion). Metric `strategy_gen_veto_window_cancellations_total`.

### 4.3 Bot Worker Integration

`BotSupervisor` extension: when `strategy_class` starts with `'generated:'`, look up `generated_strategies` by ID, exec `restricted_bytecode` in a restricted globals dict, extract the `BaseStrategy` subclass, instantiate normally. No new Docker service — runs inside the existing bot worker.

Unknown or unvalidated ID → bot fails to start with a clear structured log error; `sandbox_status != 'promoted'` is an absolute gate.

### 4.4 Prometheus Metrics (22b)

| Metric | Type | Labels |
|---|---|---|
| `strategy_gen_generated_total` | Counter | `outcome` (validated/rejected) |
| `strategy_gen_sandbox_latency_seconds` | Histogram | — |
| `strategy_gen_auto_approved_total` | Counter | — |
| `strategy_gen_veto_window_cancellations_total` | Counter | — |

### 4.5 REST API (22b)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/strategy-gen` | JWT | List generated strategies + sandbox status |
| `GET` | `/api/strategy-gen/{id}` | JWT | Detail: source, backtest result, error |
| `POST` | `/api/strategy-gen/generate` | admin JWT | Trigger LLM generation |
| `POST` | `/api/strategy-gen/{id}/approve` | admin JWT + CSRF | Promote to paper bot |
| `POST` | `/api/strategy-gen/{id}/reject` | admin JWT + CSRF | Permanently reject |

### 4.6 `app_config` keys (22b)

| Key | Default | Description |
|---|---|---|
| `strategy_gen/enabled` | `true` | Kill switch for generation endpoint |
| `strategy_gen/auto_approve_enabled` | `false` | Enable auto-approve path |
| `strategy_gen/auto_approve_min_sharpe` | `0.5` | Sharpe threshold for auto-approve |
| `strategy_gen/veto_window_minutes` | `60` | Telegram veto window duration |
| `strategy_gen/allowed_imports` | `["numpy","pandas","ta","math","decimal","collections","itertools"]` | Sandbox allowlist |

### 4.7 Implementation Chunks (22b)

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0070 + migration tests | Qwen | — |
| **B — StrategyGenerator + sandbox** | `strategy_gen/generator.py`, `strategy_gen/sandbox.py`, unit tests | Codex | after A |
| **C — Bot worker integration** | `bot/supervisor.py` extension, `bot/strategy_loader.py`, tests | Codex | after B |
| **D — REST API** | `api/strategy_gen.py`, tests | Qwen | after B |
| **E — FE strategy-gen feed** | `OrchestrationPage` panel 4 (strategy feed), `strategy_gen/api.ts` + `types.ts` | Codex | after D |
| **F — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.22.1 | Opus direct | after all |

---

## 5. Phase 22c — Health Digest + FE Orchestration Dashboard

### 5.1 Data Model (Alembic 0071)

```sql
CREATE TABLE bot_health_snapshots (
    bot_id          BIGINT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
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
```

Retention: default TimescaleDB policy (one row/bot/night = negligible volume).

### 5.2 HealthDigestService (`app/services/orchestrator/digest.py`)

APScheduler cron at `03:00` local (after retrain at `02:00`; configurable via `app_config[orchestrator/digest_cron]`):
1. For each live bot: read `bot_runs` → compute rolling 30d Sharpe, max drawdown, win rate, total PnL, trade count.
2. Fetch 21c advisor attribution accuracy (veto accuracy at 1h window per bot).
3. Rank by Sharpe (desc). Flag underperformers: Sharpe < `app_config[orchestrator/underperform_sharpe_threshold]` (default `0.0`).
4. Compute portfolio-level stats: aggregate NLV, total exposure utilisation (current / limit), max pairwise correlation ρ.
5. Post Telegram digest: rank table with trend badge (▲ improving / ▼ degrading / — stable, based on 7d vs 30d Sharpe delta).
6. Write `bot_health_snapshots` row per bot.

### 5.3 Frontend — `/orchestration` Dashboard Page

New route `frontend/src/pages/OrchestrationPage.tsx`. Four panels:

**Panel 1 — Cross-bot league table**
Table: Rank | Bot | Sharpe (30d) | Drawdown | Win Rate | Advisor Accuracy | Exposure % | Trend badge. Sortable. Row click → `BotDetailPage`.
Data: `GET /api/orchestrator/digest/latest` (latest `bot_health_snapshots` per bot). Stale time: 300s.

**Panel 2 — Portfolio exposure heatmap**
Instrument × account matrix. Cell colour = exposure utilisation (0–100% of limit). Hover shows: current notional, limit, correlation-adjusted contribution.
Data: `GET /api/orchestrator/exposure`. Stale time: 60s.

**Panel 3 — Correlation matrix**
N×N heatmap of pairwise Pearson ρ for instruments held across all live bots. Colour: −1 (blue) → 0 (white) → +1 (red). |ρ| > 0.7 cells get border highlight.
Data: `GET /api/orchestrator/correlation`. Stale time: 3600s (updates nightly).

**Panel 4 — Strategy generation feed** (22b consumer)
List of recent `generated_strategies`: sandbox status badge, backtest Sharpe, approve/reject buttons (admin only, with CSRF).
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
| `orchestrator/digest_cron` | `"0 3 * * *"` | Digest schedule |
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

### 22a Backend (~45 tests)

- `PortfolioExposureGate`: allow/warn/block for total_notional/per_sector/per_instrument; correlation-adjusted notional with known ρ; Redis fail-open; kill switch; `risk_audit_log` row written
- `CorrelationService`: Pearson matrix correct from `bars_1d`; Redis TTL 86400s; stale fallback to raw notional
- `AutoPromoteEvaluator`: all criteria pass → `promote()` called; any fail → skip; `auto_apply=false` → report only; `auto_promote_enabled=false` → no-op; Telegram sent on auto-promote
- `NightlyRetrainJob`: triggers for each running non-shadow bot; skips paused/stopped; posts Telegram; auto-applies if threshold + flag on
- Lua script atomicity: concurrent fill events don't race on exposure HASH
- REST: exposure CRUD, evaluate, retrain trigger

### 22b Backend (~35 tests)

- Sandbox: valid code → `validated`; syntax error → `rejected`; prohibited import → `rejected`; `eval`/`exec`/`open` → `rejected`; allowlist import passes
- `StrategyGenerator` prompt: includes attribution signal, market context, interface contract
- Backtest auto-submission: validated strategy triggers `BacktestRunner`
- Approval gate: unapproved strategy cannot be loaded by bot worker; approved → paper bot created
- Auto-approve: disabled by default; enabled + threshold met → promotes; Telegram veto: `/veto_{id}` within window cancels
- Bot worker: `strategy_class='generated:{id}'` loads restricted bytecode correctly; unknown ID → clear error
- Security: `POST /api/strategy-gen/{id}/approve` rejected without CSRF nonce; sandbox rejects `__import__('os').system(...)` pattern

### 22c Backend (~20 tests)

- `HealthDigestService`: Sharpe/drawdown/win-rate correct from `bot_runs`; attribution accuracy from 21c; underperformer flagging; `bot_health_snapshots` row written per bot
- Telegram digest: table rendered correctly; trend badge (▲/▼/—) logic
- Correlation endpoint: reads from Redis; 404 when no snapshot yet

### Frontend (~20 tests)

- `OrchestrationPage`: all four panels render; league table sortable by Sharpe; exposure heatmap colour scale; correlation |ρ| > 0.7 border; strategy feed approve/reject hidden for non-admin
- Stale time assertions: correlation = 3600s, digest = 300s, exposure = 60s

---

## 7. Invariants Preserved

| Invariant | This phase |
|---|---|
| **No raw RL** | StrategyGenerator uses LLM + RestrictedPython; no RL training loop |
| **Fail-OPEN** | `PortfolioExposureGate` Redis error → allow + log; never blocks order flow |
| **Human approval gate** | Auto-promote and auto-approve both default `false`; operator opts in explicitly |
| **No new money-moving paths without CSRF** | All approve/promote endpoints require admin JWT + CSRF nonce |
| **Bot crash ≠ API crash** | Generated strategies load in existing bot worker Docker service; no new infra |
| **Schema changes via Alembic only** | 0069 / 0070 / 0071 — no raw model edits |

---

## 8. Deferred

| Item | Target |
|---|---|
| FX conversion for exposure notional (multi-currency accounts) | Phase 24 infra hardening |
| Multi-worker Redis exposure HASH consistency (advisory lock) | Phase 24 multi-worker uvicorn |
| Raw RL | Post-v1.0 / out of scope (ROADMAP invariant) |
| LLM re-evaluation of failed strategies ("why did it fail?") | Beyond Phase 22 |
| Attribution for generated strategies (21c path) | Automatic — 21c's `AttributionService` already covers all `bot_advisor_decisions` rows |
| Telegram veto window for auto-promote (not just auto-approve) | Phase 22a.1 patch if needed |
