# Phase 21b — LLM-in-Loop: Param-Tuning + Shadow-Promotion (v0.21.2)

**Date:** 2026-05-19  
**Status:** ARCHITECT-REVIEW Pass 1 + Pass 2 + Pass 3 applied — ready for /writing-plans  
**Builds on:** Phase 21a (LLM advisor, v0.21.0) · Phase 21a.1 (advisor polish, v0.21.1) · Phase 20 (backtesting harness, v0.20.0) · Phase 18 (scanner/filings/earnings, v0.18.0) · Phase 11c (Telegram bot, v0.11.2.0)  
**Next phases:** 21c (perf-attribution — "was the advisor right?")

**ARCHITECT-REVIEW applied:** Pass 1 (8 HIGH + 9 MED + 5 LOW) + Pass 2 (0 CRIT, 4 HIGH, 7 MED, 4 LOW) + Pass 3 (0 CRIT, 1 HIGH, 1 MED, 2 LOW). All HIGH + MED inline. LOWs noted.

---

## 1. Goal

Complete the Phase 21 ROADMAP deliverable: **LLM-in-loop bot lifecycle**. Three pillars:

1. **Param-tuning:** LLM reads recent run metrics → proposes N candidate param sets → each auto-backtested (Phase 20 harness) → ranked report → human approves one → bot restarted with new params.
2. **Shadow-promotion:** shadow bot runs paper-mode in parallel with live bot → comparison report after configurable window → human promotes → live bot adopts shadow's params; auto-promote is a wired stub (always-false) extensible to a future config flag.
3. **Advisor extensions:** advisor-in-backtest (stub, no real AI), Telegram VETO notifications + `/override_` command, news/filings injected into advisor context.

---

## 2. Scope

### In scope
- `app/services/param_tuner/` — new leaf module.
- `app/services/shadow_promoter/` — new leaf module.
- Alembic 0065: `bot_param_suggestions` table (with `jsonb_array_length` CHECK ≤5) + `bots.is_shadow / shadow_of / shadow_promoted_at / shadow_comparison_window_days / strategy_schema` columns + index confirmation/addition for `bot_runs` and `bot_orders`.
- Alembic 0066: `shadow_promotion_events` table (with `comparison_window_start` column).
- Alembic 0067: `backtests.advisor_config` JSONB column + `backtest_advisor_decisions` table.
- `app/services/advisor/context_builder.py` — filings/earnings injection.
- `app/services/telegram/advisor_notify.py` — VETO notifications + `/override_` command.
- `app/backtest/runner.py` + `AdvisorStub` — advisor-in-backtest wiring.
- `BaseStrategy` — `params_bounds_schema` classattr + `POST /api/bots` persists `strategy_schema`.
- `app_config[param_tuner/scheduled_enabled]`, `app_config[param_tuner/allow_cloud_reasoning]`, `app_config[param_tuner/cost_ceiling_usd_daily]` — kill switch + capability gate + cost ceiling.
- `app_config[shadow_promoter/comparison_notify_enabled]`, `app_config[shadow_promoter/auto_promote_check_enabled]` — APScheduler kill switches.
- `BotSupervisor.restart()` — new atomic stop→wait→start method.
- REST: param suggestion endpoints, shadow endpoints, backtest advisor decision endpoint.
- WS: `bot:tuner:{bot_id}` and `bot:shadow:{bot_id}` pubsub frames (global WS cap added).
- FE: `ParamTunerSection`, `ShadowComparisonPanel`, dual PnL curve on `BacktestPage`, Telegram config toggle on `/admin/ai`.
- 16 new Prometheus metrics across the two new services (3 added vs. brainstorm).

### Explicitly out of scope
- Real AI calls during backtest replay — stub only (deterministic, no cost).
- Auto-promote logic (`bots.auto_promote_config` column) — stub wired, always returns false.
- Fine-tuning, embeddings, RAG.
- Advisor perf-attribution ("was the advisor right?") — Phase 21c.
- Multi-bot orchestration — Phase 22.

---

## 3. Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  ParamTunerService                                          │
│  trigger() → AI router (REASONING) → N candidates          │
│           → fan-out N BacktestSubmitter.submit()            │
│  poll_backtest_results() [APScheduler 60s]                  │
│           → rank by Sharpe/MAR → status=RANKED             │
│  approve(candidate_index) → UPDATE bots.strategy_params     │
│                           → stop + restart BotSupervisor    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ShadowPromoterService                                      │
│  create_shadow() → clone bot (is_shadow=True, paper mode)   │
│  get_comparison() → read bot_runs metrics, compute delta    │
│  promote() → live bot adopts params, shadow soft-deleted    │
│  check_auto_promote_eligibility() → always False (stub)     │
│  [APScheduler daily] shadow_comparison_notify               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  AdvisorService (existing, Phase 21a)                       │
│  context_builder.py ← filings/earnings injection (NEW)      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  BacktestRunner (Phase 20) + AdvisorStub (NEW)              │
│  if backtests.advisor_config IS NOT NULL:                   │
│    AdvisorStub.review() per order intent                    │
│    → veto injection list support                            │
│    → dual PnL curve in report                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  AdvisorTelegramNotifier (NEW)                              │
│  psubscribe bot:advisor:* → filter verdict=veto             │
│  → Telegram VETO message + /override_{decision_id}          │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Data model

### Alembic 0065

```sql
-- Param suggestion table
CREATE TABLE bot_param_suggestions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id                      UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
    triggered_by                TEXT NOT NULL CHECK (triggered_by IN ('scheduled','manual')),
    status                      TEXT NOT NULL CHECK (status IN (
                                    'pending','backtesting','ranked',
                                    'approved','rejected','applied','failed')),
                                    -- M3-new-1: 'failed' added for terminal trigger failures
                                    -- (no_valid_candidates, queue_full). Distinct from 'pending'
                                    -- (in-progress) so trigger guard and FE treat them differently.
    strategy_params_current     JSONB NOT NULL,
    ai_reasoning                TEXT,
    candidates                  JSONB NOT NULL DEFAULT '[]'
                                    CHECK (candidates IS NOT NULL AND jsonb_array_length(candidates) <= 5),
    -- Hard cap of 5 candidates; enforced at DB level (belt-and-braces, M1).
    -- NOT NULL is also in the column definition above; the CHECK redundantly asserts it
    -- to prevent a NULL bypass of the array-length cap (L-new-2 paranoia-documentation).
    -- candidates array element shape:
    -- {
    --   "params": {...},
    --   "backtest_job_id": "uuid|null",
    --   "backtest_result": {
    --     "sharpe": 1.23, "mar": 0.87, "max_dd": -0.12,
    --     "win_rate": 0.54, "avg_trade_pnl": "12.50",
    --     "forced_close_pnl": "0.00", "total_trades": 47
    --   } | null,
    --   "rank": 1 | null,
    --   "delta_vs_current": {"sharpe": "+0.31", "max_dd": "+0.04"}
    -- }
    -- AI provenance (H3): link to ai_completions ledger
    ai_completion_id            BIGINT,          -- ai_completions.id; no FK (hypertable)
    -- M-new-1: Phase 11a cost ledger is fire-and-forget (batch insert every 1s/100 rows).
    -- The ai_completions row may not be visible when we write ai_completion_id.
    -- Provenance lookups from bot_param_suggestions must tolerate eventual consistency:
    -- JOIN may return null; callers should retry after 2s or accept "not yet committed".
    ai_model                    TEXT,            -- model that generated candidates
    ai_prompt_hash              TEXT,            -- sha256 of context payload (first 16 hex); CHAR(16) semantics but stored as TEXT (L-new-3)
    approved_candidate_index    INT,
    approved_by                 TEXT,
    applied_at                  TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX bot_param_suggestions_bot_id_status_idx
    ON bot_param_suggestions (bot_id, status);

-- Shadow bot columns on bots table
ALTER TABLE bots
    ADD COLUMN is_shadow                     BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN shadow_of                     UUID REFERENCES bots(id) ON DELETE SET NULL,
    ADD COLUMN shadow_promoted_at            TIMESTAMPTZ,
    ADD COLUMN shadow_comparison_window_days INT;
-- shadow_comparison_window_days: set at create_shadow() time; NULL on non-shadow bots.

-- strategy_schema column: stores BaseStrategy.params_schema at bot-create time.
-- Required by ParamTunerService to validate LLM-proposed candidate params (H2).
-- POST /api/bots must be updated to persist strategy_schema alongside strategy_params.
-- params_bounds_schema: per-field min/max/safe-default (H2) — same column, nested key.
--   Shape: {"param_name": {"min": ..., "max": ..., "safe_default": ...}}
ALTER TABLE bots
    ADD COLUMN strategy_schema JSONB;
-- NULL for bots created before 0065; tuner skips bots with strategy_schema IS NULL.
-- Operator backfill: POST /api/admin/bots/{id}/backfill-schema introspects the strategy
-- class and stores its params_schema + params_bounds_schema (M9 / M-new-2 operator runbook).
-- IMPORTANT (M-new-2): this endpoint MUST use the same DenylistFinder + RLIMIT_AS 256MB +
-- RLIMIT_CPU 3s + 5s timeout subprocess sandbox as Phase 19 params_schema extraction
-- (app/bot/sandbox.py). Importing strategy in-process allows a malicious strategy file to
-- crash the backend. Sandbox is mandatory — not optional.

-- Index for shadow queries
CREATE INDEX bots_shadow_of_idx ON bots (shadow_of) WHERE shadow_of IS NOT NULL;

-- Confirm indices needed by tuner context-builder exist (M2).
-- If missing, create here; if already present, no-op.
CREATE INDEX IF NOT EXISTS bot_runs_bot_id_started_at_idx
    ON bot_runs (bot_id, started_at DESC);
CREATE INDEX IF NOT EXISTS bot_orders_bot_id_created_at_idx
    ON bot_orders (bot_id, created_at DESC);

-- updated_at trigger for bot_param_suggestions
CREATE TRIGGER bot_param_suggestions_updated_at
    BEFORE UPDATE ON bot_param_suggestions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- H-new-1: Widen risk_decisions.attempt_kind CHECK to include 'shadow_place_order'
-- (Referenced in §6.2 and §15 M6 but was missing from the DDL block — shipped broken
-- without this, RiskService INSERT fails with CHECK violation on shadow bots.)
-- Drop existing constraint (name from alembic 0061 — check with \d risk_decisions):
ALTER TABLE risk_decisions
    DROP CONSTRAINT risk_decisions_attempt_kind_check;
ALTER TABLE risk_decisions
    ADD CONSTRAINT risk_decisions_attempt_kind_check
        CHECK (attempt_kind IN (
            'preview', 'place_order', 'modify_order',
            'bot_place_order', 'shadow_place_order'
        ));
```

### Alembic 0066

```sql
CREATE TABLE shadow_promotion_events (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shadow_bot_id            UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
    live_bot_id              UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
    promoted_by              TEXT NOT NULL,
    comparison_window_days   INT NOT NULL,
    comparison_window_start  TIMESTAMPTZ NOT NULL,  -- M4: unambiguous window start anchor
    shadow_metrics           JSONB NOT NULL,
    -- {sharpe, mar, max_dd, win_rate, avg_trade_pnl, total_trades}
    live_metrics             JSONB NOT NULL,
    promoted_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX shadow_promotion_events_live_bot_id_idx
    ON shadow_promotion_events (live_bot_id, promoted_at DESC);
```

### Alembic 0067

```sql
-- Backtest advisor config column
ALTER TABLE backtests
    ADD COLUMN advisor_config JSONB;
-- NULL = advisor disabled for this backtest run (existing rows unaffected)

-- Backtest advisor decisions table
CREATE TABLE backtest_advisor_decisions (
    id              BIGSERIAL PRIMARY KEY,
    backtest_id     UUID NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    bar_index       INT NOT NULL,
    canonical_id    TEXT NOT NULL,
    intent          JSONB NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('approve','veto','fail_open')),
    reasoning       TEXT NOT NULL,
    latency_ms      INT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX backtest_advisor_decisions_backtest_id_idx
    ON backtest_advisor_decisions (backtest_id);
```

---

## 5. Param-Tuner Service

### 5.1 `app/services/param_tuner/types.py`

```python
class TunerTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"

class SuggestionStatus(StrEnum):
    PENDING = "pending"
    BACKTESTING = "backtesting"
    RANKED = "ranked"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"   # M3-new-1: terminal trigger failure (no_valid_candidates, queue_full)

class BacktestResultSnapshot(BaseModel):
    sharpe: float | None
    mar: float | None
    max_dd: float | None
    win_rate: float | None
    avg_trade_pnl: Decimal
    forced_close_pnl: Decimal
    total_trades: int

class ParamCandidate(BaseModel):
    params: dict
    backtest_job_id: UUID | None = None
    backtest_result: BacktestResultSnapshot | None = None
    rank: int | None = None
    delta_vs_current: dict[str, str] = {}  # {"sharpe": "+0.31"}

class ParamSuggestion(BaseModel):
    id: UUID
    bot_id: UUID
    triggered_by: TunerTrigger
    status: SuggestionStatus
    strategy_params_current: dict
    ai_reasoning: str | None
    candidates: list[ParamCandidate]
    approved_candidate_index: int | None
    approved_by: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime

# LLM response schema for candidate generation
class CandidateListResponse(BaseModel):
    candidates: list[dict]   # validated against strategy params_schema after parse
    reasoning: str
```

### 5.2 `app/services/param_tuner/context_builder.py`

Pure function. Builds LLM prompt payload for param suggestion. Reads strategy-level aggregates, not per-order bars.

**Inputs (single read transaction):**
- `bots` row: `strategy_params`, `strategy_schema` JSONB (stored at bot-create time from `BaseStrategy.params_schema`)
- `bot_runs` last 10 completed runs: all KPI fields + `started_at`, `stopped_at`
- `bot_orders` last 100 orders: `side`, `qty`, `fill_price`, PnL distribution summary
- `bot_advisor_decisions` last 50: verdict breakdown counts + top-5 `advice_tags` by frequency
- `backtest_bars` available timeframes for this bot's `canonical_id` instruments

**Output:** structured JSON wrapped in `<<BEGIN_TUNER_CONTEXT>>` / `<<END_TUNER_CONTEXT>>` fences — distinct tokens from advisor's `<<BEGIN_CONTEXT>>` / `<<END_CONTEXT>>` to prevent cross-contamination.

**Token budget:** ~4000 tokens max. `bot_runs` truncated oldest-first if over budget.

**PII strip:** `account_number` never included. `instruments.meta` excluded.

**Sanitisation:** same rules as advisor `context_builder.py` (200-char cap per free-text field, role-tag regex, code-fence strip).

### 5.3 `app/services/param_tuner/service.py` — `ParamTunerService`

```python
class ParamTunerService:
    def __init__(
        self,
        ai_client: AICompletionClient,
        redis: Any,
        db_factory: async_sessionmaker[AsyncSession],
        backtest_submitter: BacktestSubmitter,
    ) -> None: ...
```

**`BacktestSubmitter`** calls the internal backtest submission service method directly (not via HTTP) — same DB session family, avoids round-trip overhead. Extracted to allow injection in tests without standing up a full backtest worker.

**`BacktestSubmitter.submit(bot_id, params)` full signature (M-new-4):**
```python
async def submit(self, bot_id: UUID, params: dict) -> UUID:
    """Submit one candidate backtest. Returns backtest_job_id."""
```
In production it wraps `BacktestService.submit()` with the following sourced parameters:
- **`bars_source`**: `"db"` (reads from `backtest_bars` / `bars_1m` CAGG for the bot's canonical instruments). No CSV upload — tuner-submitted backtests always use the DB bar feed.
- **`start_ts` / `end_ts`**: rolling window of last 90 days (`now() - interval '90 days'` to `now()`). Operator cannot override per-suggestion; the 90-day window is fixed to ensure comparability across candidates within one suggestion run. Configurable via `app_config[param_tuner/backtest_window_days]` (default `90`).
- **`slippage_config`**: copied from the bot's most recent completed backtest (`backtests WHERE bot_id=... ORDER BY created_at DESC LIMIT 1`). If no prior backtest exists: `{"type": "bps", "value": 5}` (5 bps default). This ensures tuner backtests use the same slippage assumption as the operator's manual backtests.
- **`commission_schedule`**: same source as slippage — from the bot's most recent completed backtest; default `{"type": "zero"}` if none.
- **`advisor_config`**: `null` (no advisor stub during param-tuner backtests — would create recursive dependency).

#### `trigger(bot_id, triggered_by, db) → UUID`

**Kill switch (H5):** Read `app_config[param_tuner/scheduled_enabled]`. If `False` and `triggered_by='scheduled'` → no-op, return early. Manual triggers are NOT gated by this flag (operator explicitly requested).

1. Read bot row. Assert `is_shadow=False`, `status != 'deleted'`, `strategy_schema IS NOT NULL` (strategy has `params_schema`; pre-0065 bots without `strategy_schema` → 422 with hint to run backfill-schema endpoint).
2. Assert no suggestion in `status IN ('pending','backtesting','ranked')` already exists for this bot — one active suggestion per bot at a time. (`failed`, `rejected`, `approved`, `applied` rows are terminal and do not block.) If exists → raise `TunerAlreadyActiveError` (409 from REST handler). (M3-new-1: explicit status list ensures `failed` rows do not block re-triggering.)
3. **Daily cost ceiling check (H1, H-new-3 — TOCTOU-safe):** Two concurrent `trigger()` calls (manual + scheduled, or two manuals) both reading the `ai_completions` SUM before either AI call completes can both pass the check and together spend ≈2× the ceiling. Fix: use a Redis reservation counter as a soft atomic guard.
   - Read `ai_completions WHERE caller LIKE 'param_tuner:bot:%' AND ts >= now() - interval '1 day'`, sum `cost_usd`. Call this `committed_cost`.
   - **Reserve estimated cost atomically:** `INCRBYFLOAT param_tuner:cost_pending:{utc_date} {estimated_cost_usd} EX 86400`. `estimated_cost_usd` = `0.10` (conservative estimate per trigger; actual cost recorded after AI call). The key expires at UTC midnight + 24h. Read the post-increment value back.
   - If `committed_cost + post_increment_value > ceiling` → `DECRBY` the reservation (clean up), raise `TunerCostCeilingError` (429); metric `param_tuner_trigger_failures_total{reason="cost_ceiling"}`.
   - After the AI call returns and actual cost is known: `DECRBY param_tuner:cost_pending:{utc_date} {estimated_cost_usd}` (removes the reservation; actual cost is recorded in `ai_completions` ledger by Phase 11a machinery).
   - This makes the ceiling "soft with bounded overrun ≤ estimated_cost_usd × N_concurrent" rather than hard-exact. At `N_concurrent ≤ 5` bots and `estimated_cost_usd = 0.10`, maximum overrun is `$0.50` above ceiling — acceptable and explicitly documented.
   - If Redis is unavailable during reservation: fail-OPEN on the reservation (proceed without reservation), log `structlog.warning("param_tuner.cost_reservation_failed", ...)`, metric `param_tuner_cost_reservation_failures_total`. The DB ceiling check still applies as a backstop.
4. Build context payload via `TunerContextBuilder.build(bot_id, db)`. Record `ai_prompt_hash = sha256(payload)[:16]`.
5. **Capability routing (H4):** default `capability = AICapability.LOCAL_ONLY`. If `app_config[param_tuner/allow_cloud_reasoning]` is `true`, use `capability = AICapability.REASONING`. This makes LOCAL_ONLY the safe default — operator must explicitly opt cloud in.
6. Call AI router:
   - `capability = (as resolved above)`
   - `caller = f"param_tuner:bot:{bot_id}"`
   - `jwt_subject = f"system:bot:{bot_id}"`
   - `response_format = CandidateListResponse.model_json_schema()`
   - `timeout = 30s`
7. Parse `CandidateListResponse`. Hard-cap: `len(candidates) = min(len(candidates), MAX_CANDIDATES_PER_SUGGESTION)` where `MAX_CANDIDATES_PER_SUGGESTION = 5` (H1). Validate each remaining candidate dict against `strategy_schema` (type check via Pydantic). Then validate against `params_bounds_schema` (semantic bounds — per-field min/max; H2). Drop invalid candidates; metric `param_tuner_invalid_candidates_total{reason}` per drop (reasons: `schema_type`, `out_of_bounds`).
8. If `len(valid_candidates) < 1` → persist row with `status='failed'`, `candidates=[]`, publish failure frame `{type:"failed", reason:"no_valid_candidates"}`, metric `param_tuner_trigger_failures_total{reason="no_valid_candidates"}`. Return suggestion_id (202). (M3-new-1: `failed` is a distinct terminal state — trigger guard in step 2 counts only `pending|backtesting|ranked` as "active"; a `failed` row does not block re-triggering. FE `ParamTunerSection` shows a "Dismiss" affordance on `failed` rows that calls `DELETE /reject`.)
9. **Queue depth check (H7):** call `backtest_submitter.queue_depth()`. If depth ≥ `app_config[param_tuner/max_backtest_queue_depth]` (default `20`; moved from magic constant to config for parity with other tuner knobs — L-new-1) → persist row with `status='failed'`, publish `{type:"failed", reason:"queue_full"}`, metric `param_tuner_trigger_failures_total{reason="queue_full"}`. Return suggestion_id (202 — operator can retry once queue drains). Metric `param_tuner_backtest_queue_depth` (gauge, updated here). (M3-new-1: same `failed` terminal state; does not block re-triggering.)
10. Persist `bot_param_suggestions` row: `status='backtesting'`, `candidates` array with `params` only, `ai_reasoning`, `ai_completion_id`, `ai_model`, `ai_prompt_hash` (H3).
11. Fan-out: for each valid candidate, call `backtest_submitter.submit(bot_id, candidate.params)` → store `backtest_job_id` per candidate. Update `candidates` JSONB in-place. Metric `param_tuner_backtest_fan_out_total`.
12. Publish `bot:tuner:{bot_id}` frame `{v:1, type:"backtesting", suggestion_id, candidate_count: N}`.
13. Return `suggestion_id`.

**Fail-OPEN:** any exception after the DB row is created → row stays in current status. Next `trigger()` call will reject with 409 if status is still active. Operator must call `reject()` to clear it.

#### `poll_backtest_results(db)` — called by APScheduler every 60s

1. Query all `bot_param_suggestions WHERE status='backtesting'`.
2. For each suggestion, for each candidate with `backtest_job_id` and no `backtest_result`:
   - Query `backtests` table by `backtest_job_id`.
   - If `status='done'`: copy KPI fields into `candidate.backtest_result`. (`delta_vs_current` is computed in step 3 once all candidates resolve — do not compute here against a single run.)
   - If `status='failed'`: set `candidate.backtest_result=null`, `candidate.rank=null`.
   - If `status` still running/queued: skip.
3. When all candidates have `backtest_result IS NOT NULL` or `backtest_job_id` job is failed:
   - **Delta computation (M8, M-new-3):** compute `delta_vs_current` using a rolling window aggregate — mean Sharpe/MAR/max_dd over last 5 completed `bot_runs` rows (same window the context-builder fed the LLM). A single anomalous recent run no longer distorts the delta. If < 5 completed runs exist, use all available. The `delta_vs_current` dict is then applied to each candidate that has a non-null `backtest_result`.
   - Rank remaining candidates by `sharpe DESC` (MAR as tiebreaker; NaN/null ranked last).
   - Set `status='ranked'`.
   - Publish `bot:tuner:{bot_id}` frame `{v:1, type:"ranked", suggestion_id, candidate_count: N}`.
   - Metric `param_tuner_ranked_total`.

#### `approve(suggestion_id, candidate_index, approved_by, db)`

1. Load suggestion, assert `status='ranked'`.
2. Assert `candidate_index` in `[0, len(candidates))` and `candidates[candidate_index].backtest_result IS NOT NULL`.
3. DB transaction:
   - `UPDATE bots SET strategy_params = candidates[index].params WHERE id = bot_id`
   - `UPDATE bot_param_suggestions SET status='applied', approved_candidate_index=index, approved_by=approved_by, applied_at=now()`
4. If bot `status='running'`:
   - **Use `BotSupervisor.restart(bot_id)` (H8)** — new atomic method that owns the stop→wait→start serialization internally. `restart()` publishes `STOP`, waits up to `stop_drain_seconds + 3s` (configurable, default 8s), then calls `start()`. If stop times out: `restart()` raises `SupervisorRestartError`; `approve()` re-raises as 500; suggestion stays `status='ranked'` (operator retries approve after bot settles).
   - Using `restart()` (not separate stop+start) eliminates the double-process race window.
5. Publish `bot:tuner:{bot_id}` frame `{v:1, type:"applied", suggestion_id, candidate_index}`.
6. Metric `param_tuner_applied_total{triggered_by}`.

#### `reject(suggestion_id, rejected_by, db)`

Sets `status='rejected'`. No bot changes. No pubsub.

### 5.3a `BotSupervisor.restart()` — Full Specification (H-new-2)

**New method on `app/bot/supervisor.py::BotSupervisor`.** Phase 19's module is extended; no new file.

```python
async def restart(self, bot_id: UUID, stop_drain_seconds: float = 5.0) -> None:
    """Atomically stop a running bot and restart it with its current DB configuration.
    
    Valid source states: running, paused, error.
    Raises SupervisorRestartError on state-machine violations or stop timeout.
    """
```

**Valid source states:**
- `running` → normal path: SIGTERM → drain → start.
- `paused` → treated as running for restart purposes: sends SIGTERM; waits for process exit (no drain timeout issue since paused bots have already drained the bar queue).
- `error` → the respawn backoff counter is **reset to zero** on `restart()` call. This is the intended semantics: a human-triggered restart (via approve() or Telegram) signals that the operator has acknowledged the error. The `[10, 30, 60]s` backoff sequence restarts from `10s` on the next failure.

**Rejection states (raise `SupervisorRestartError` immediately):**
- `stopped` → no process to stop; caller must call `start()` directly.
- `starting` → mid-startup; restart would race with the startup sequence.
- `deleted` → bot is soft-deleted; cannot restart.

**Mid-respawn behaviour:** if the supervisor is mid-respawn (state = `starting`, process is being launched after a crash):
- `restart()` raises `SupervisorRestartError(reason="mid_respawn")`.
- Caller (`approve()`) re-raises as HTTP 409 with `retry_after_seconds=10`.
- The operator can poll `GET /api/bots/{id}` for `status != 'starting'` then retry.

**Stop sequence (mirrors Phase 19 graceful-stop):**
1. Publish `STOP` to bot control queue.
2. Send `SIGTERM` to child process.
3. Poll `bot:status:{bot_id}` Redis pubsub channel for `status` transition to `stopped` or `paused` (same channel Phase 19 `BotSupervisor` publishes on every transition — **poll pubsub, not process state or DB**). Timeout = `stop_drain_seconds + 3s` (default 8s).
4. If timeout: raise `SupervisorRestartError(reason="stop_timeout")`. Child process is left running; caller must recover.
5. On success: call `start(bot_id)` (existing Phase 19 method). Publishes `bot:status:{bot_id} → starting` then `running` on success.

**Backoff interaction:** `start()` called from `restart()` resets `_respawn_count[bot_id] = 0` before returning, so the next crash begins the backoff ladder from `10s` again. This is the same semantics as a fresh bot start.

### 5.4 APScheduler jobs

**`param_tuner_scheduled`** — cron, `app_config[param_tuner/schedule]` (default `0 2 * * 1`, Monday 02:00 local).

Kill switch (H5): reads `app_config[param_tuner/scheduled_enabled]` (default `false` — opt-in). If `False` → job exits immediately, logs `structlog.info("param_tuner.scheduled.skipped", reason="disabled")`.

For every bot satisfying:
- `is_shadow = false`
- `status = 'running'`
- `advisor_config->>'mode' != 'OFF'` (tuner only runs on advisor-enabled bots)
- No suggestion in `status IN ('pending','backtesting','ranked')` exists
- `strategy_schema IS NOT NULL`

**Fleet concurrency limit (H1):** process bots sequentially (not fan-out). If `trigger()` raises `TunerCostCeilingError` on any bot → stop processing remaining bots for this cron tick; metric `param_tuner_fleet_cost_ceiling_total`.

**`param_tuner_poll`** — runs every 60s regardless of `scheduled_enabled`. Calls `poll_backtest_results()`.

### 5.5 Prometheus metrics (8)

| Metric | Type | Labels |
|---|---|---|
| `param_tuner_trigger_total` | Counter | `triggered_by` |
| `param_tuner_trigger_failures_total` | Counter | `reason` (`no_valid_candidates\|cost_ceiling\|queue_full\|ai_error`) |
| `param_tuner_candidates_generated_total` | Counter | — |
| `param_tuner_invalid_candidates_total` | Counter | `reason` (`schema_type\|out_of_bounds`) |
| `param_tuner_backtest_fan_out_total` | Counter | — |
| `param_tuner_backtest_queue_depth` | Gauge | — |
| `param_tuner_ranked_total` | Counter | — |
| `param_tuner_applied_total` | Counter | `triggered_by` |
| `param_tuner_ai_latency_seconds` | Histogram | — |
| `param_tuner_fleet_cost_ceiling_total` | Counter | — |
| `param_tuner_cost_reservation_failures_total` | Counter | — | Redis reservation unavailable; ceiling check fell back to DB-only (H-new-3) |

---

## 6. Shadow-Promoter Service

### 6.1 `app/services/shadow_promoter/types.py`

```python
class ShadowMetrics(BaseModel):
    sharpe: float | None
    mar: float | None
    max_dd: float | None
    win_rate: float | None
    avg_trade_pnl: Decimal
    total_trades: int
    window_days: int

class ShadowVsLive(BaseModel):
    shadow_bot_id: UUID
    shadow_bot_name: str
    shadow_metrics: ShadowMetrics
    live_metrics: ShadowMetrics
    delta: dict[str, str]    # {"sharpe": "+0.31", "max_dd": "+0.04"}
    running_since: datetime
    comparison_window_days: int
    comparison_ready: bool   # True if oldest completed bot_run started >= window_days ago (M-new-7)

class ShadowComparisonReport(BaseModel):
    live_bot_id: UUID
    shadows: list[ShadowVsLive]
    generated_at: datetime

class ShadowPromotionEvent(BaseModel):
    id: UUID
    shadow_bot_id: UUID
    live_bot_id: UUID
    promoted_by: str
    comparison_window_days: int
    shadow_metrics: ShadowMetrics
    live_metrics: ShadowMetrics
    promoted_at: datetime
```

### 6.2 `app/services/shadow_promoter/service.py` — `ShadowPromoterService`

```python
class ShadowPromoterService:
    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        supervisor: BotSupervisor,
        redis: Any,
    ) -> None: ...
```

#### `create_shadow(live_bot_id, override_params, comparison_window_days, created_by, db) → UUID`

1. Read live bot. Assert `is_shadow=False`, `status != 'deleted'`.
2. `INSERT INTO bots`:
   - All strategy fields copied from live bot.
   - `strategy_params = {**live.strategy_params, **override_params}`.
   - `is_shadow = True`, `shadow_of = live_bot_id`.
   - **`mode = 'paper'`** — explicitly set regardless of live bot's mode. This is the authoritative paper-mode field. (H3-new-1: `bot_accounts` has no `mode` column — `bots.mode` is the single source of truth per Alembic 0061. A shadow must NEVER be created in `mode='live'` even if the live bot is live.)
   - `name = f"{live_bot.name} [shadow]"`.
   - `status = 'stopped'` (not auto-started — operator starts via existing `POST /api/bots/{id}/start`).
   - `advisor_config` copied from live bot (shadow participates in advisor if live bot does).
3. Copy `bot_risk_caps` row to shadow bot (identical caps — shadow must respect same risk limits).
4. Copy `bot_accounts` rows — same account associations as live bot. No per-account mode field exists on `bot_accounts`; paper-mode enforcement is entirely via `bots.mode='paper'` set in step 2. (H3-new-1: the previous "set `bot_accounts.mode='paper'`" wording was a schema-vs-spec error; that column does not exist.)
5. Return shadow `bot_id`.
6. Metric `shadow_promoter_created_total`.

**Three-layer paper-mode enforcement (H6 — mirrors Phase 11a LOCAL_ONLY defence-in-depth):**
1. **`BotContext.place_order`**: asserts `self.bot.is_shadow == False` before calling facade; if True → raises `ShadowBotLiveTradeAttempt` (never reaches broker).
2. **`place_order_for_bot()` in `app/api/bots.py`**: re-reads `bots.is_shadow` from DB and forces `mode='paper'` on the order request before entering the risk gate.
3. **`RiskService.evaluate()`**: reads `ctx.is_shadow` and returns `BLOCK` with `check_name="shadow_bot_live_mode"` if the evaluation context somehow has `mode='live'` on an `is_shadow=True` bot. Widens `risk_decisions.attempt_kind` CHECK to include `shadow_place_order` (Alembic 0065, H-new-1, M6).

Single-layer guards on money-moving paths have caused real bugs in prior phases. Three layers are required.

**Fill routing isolation (H-new-4):** `BotFillRouter` (Phase 19) subscribes `fills:*` Redis pubsub and routes fills to the matching running child. Shadow bots use the same `bot_accounts` as live bots (with `mode='paper'`). Without explicit isolation, paper fills published by the broker paper gateway could be routed to both the live child and the shadow child if both are subscribed.

Isolation design:
- Paper-mode broker fills are published to channel namespace `fills:paper:{account_id}:{order_id}` (distinct from live fills at `fills:live:{account_id}:{order_id}`). The paper broker adapter already uses this naming (confirm against Phase 19 `BrokerFillListener` implementation before chunk D).
- `BotFillRouter` matching predicate is `(bot_id, is_shadow)` **not** `(account_id)` alone. When a fill event arrives, the router checks: is this a paper fill channel (`fills:paper:*`)? → only route to children where `bot.is_shadow=True` OR `bot.mode=paper` for that account. Is this a live fill channel? → only route to children where `bot.is_shadow=False`.
- If Phase 19 does **not** already split channels by live/paper: Alembic 0065 does not fix this (it's runtime code), but chunk D of this phase must add the channel split to `BotFillRouter` and document it in the Phase 19 section of CLAUDE.md.
- Test: `test_shadow_bot_fill_does_not_leak_to_live_child` — live bot child receives a live fill; shadow child does not receive it and vice versa.

#### `get_comparison(live_bot_id, db) → ShadowComparisonReport`

Pure read. For each `is_shadow=True, shadow_of=live_bot_id, status != 'deleted'` bot:
- Aggregate `bot_runs` metrics for shadow bot and live bot over `comparison_window_days`.
- Compute delta fields.
- Set `comparison_ready = True` if shadow has at least one completed `bot_runs` row with `started_at <= now() - comparison_window_days * interval '1 day'` — i.e., the shadow has been running continuously since *before* the comparison window boundary. A shadow started yesterday with `window=14` is **not** comparison-ready; it must have its oldest completed run starting at least 14 days ago. (M-new-7 — previous `>=` was a semantics inversion.)

Returns `ShadowComparisonReport`. No DB write.

#### `promote(live_bot_id, shadow_bot_id, promoted_by, comparison_window_days, db)`

Single DB transaction:

1. Assert `shadow.shadow_of == live_bot_id`, `shadow.is_shadow == True`, `shadow.status != 'deleted'`.
2. Stop live bot if running: publish STOP to control queue, wait up to 5s on pubsub. On timeout: log warning, proceed.
3. Stop shadow bot if running: same.
4. `UPDATE bots SET strategy_params = shadow.strategy_params, shadow_promoted_at = now() WHERE id = live_bot_id`.
5. Read current metrics snapshot for both bots (same query as `get_comparison`, window = `shadow.shadow_comparison_window_days`).
6. `INSERT INTO shadow_promotion_events` with both metric snapshots, `comparison_window_start = now() - shadow.shadow_comparison_window_days * interval '1 day'` (M4 — unambiguous window anchor).
7. `UPDATE bots SET status = 'deleted', is_shadow = false WHERE id = shadow_bot_id` — soft-delete; `bot_runs`/`bot_orders` rows survive for audit.
8. Restart live bot: `BotSupervisor.start(live_bot_id)`.
9. Publish `bot:shadow:{live_bot_id}` frame `{type:"promoted", shadow_bot_id, promoted_by}`.
10. Metrics: `shadow_promoter_promoted_total`.

**On transaction failure:** `shadow_promoter_promote_failures_total` incremented. Live bot left in stopped state (operator must restart manually). Shadow bot left in its prior state (not deleted). Both failures are surfaced in the 500 response body.

#### `check_auto_promote_eligibility(live_bot_id, db) → bool`

**Stub — always returns `False`.** Wiring is in place for a future phase to read `bots.auto_promote_config JSONB` and evaluate thresholds. The APScheduler job calls this daily.

### 6.3 APScheduler jobs

**`shadow_comparison_notify`** — daily at 08:00. Kill switch: `app_config[shadow_promoter/comparison_notify_enabled]` (default `true`). For every live bot with `is_shadow=False` that has at least one active shadow bot (`is_shadow=True, shadow_of=live_bot_id, status='running'`) running for `>= shadow_comparison_window_days` days: publish `bot:shadow:{live_bot_id}` frame `{v:1, type:"comparison_ready"}`. FE surfaces a "Review shadow performance" banner.

**`shadow_auto_promote_check`** — daily at 08:05. Kill switch: `app_config[shadow_promoter/auto_promote_check_enabled]` (default `false` — opt-in, since stub is always-False). For every live bot with active shadow bots: calls `check_auto_promote_eligibility()`. Always a no-op in 21b. (H5)

### 6.4 Prometheus metrics (5)

| Metric | Type | Labels |
|---|---|---|
| `shadow_promoter_created_total` | Counter | — |
| `shadow_promoter_promoted_total` | Counter | — |
| `shadow_promoter_promote_failures_total` | Counter | — |
| `shadow_promoter_comparison_notify_total` | Counter | — |
| `shadow_promoter_active_shadows` | Gauge | — |

`shadow_promoter_active_shadows` updated on every `shadow_comparison_notify` run: `COUNT(*) WHERE is_shadow=True AND status='running'`.

---

## 7. Advisor Extensions

### 7.1 News/Filings in Advisor Context

**Location:** `app/services/advisor/context_builder.py` — additive change only.

For the `canonical_id` in the order intent, the context builder queries two additional data sources in the same read transaction:

**Filings (`filings` table, Phase 18):**
- Last 3 filings for this instrument filed within 30 days.
- Fields included: `filing_type`, `filed_at`, `llm_summary` (already stored by Phase 18 poller).
- If `llm_summary IS NULL`: filing omitted (no on-demand LLM call from advisor context builder).
- Sanitisation: `llm_summary` truncated to 300 chars (longer than other fields; information-dense). Same role-tag regex and code-fence strip applied.

**Earnings events (`earnings_events` table, Phase 18):**
- Next upcoming earnings event within 14 days: `expected_date`, `estimate_eps`, `consensus_eps`.
- Past earnings within 7 days: same fields plus actuals if available.
- No LLM call; raw structured data only.

**Token budget impact:** filings add ~300 tokens worst-case; earnings ~50 tokens. Total context remains under 5000-token cap. `ContextSummary.payload_token_estimate` updated to account for these fields.

**Config gate:** `app_config[advisor/filings_context_enabled]` boolean (default `true`). When `false`, both queries are skipped.

**No new tables, no new migrations, no new metrics.** `advisor_context_build_latency_seconds` histogram (Phase 21a) captures the overhead; the queries add one JOIN to the existing read transaction.

### 7.2 Telegram VETO Notifications

**Location:** `app/services/telegram/advisor_notify.py` — new file.

**`AdvisorTelegramNotifier`** — lifespan singleton. `psubscribe bot:advisor:*` on Redis. Filters frames where `verdict == 'veto'`.

**Message format (HTML):**
```
🚫 <b>Advisor VETO</b> — {bot_name}
Symbol: {canonical_id}
Side: {side} {qty}
Reason: {reasoning[:200]}
Tags: {", ".join(advice_tags) or "none"}
Confidence: {confidence or "n/a"}

Run it anyway? /override_{decision_id}
```

HTML-escaped via existing `html.escape()` pattern from Phase 11c/11d. `bot_name` read from `bots` table (cached in Redis `bot:name:{bot_id}`, TTL 300s, populated lazily on first notify).

**Veto injection validation (M5):** `veto_injections` from `BacktestConfigForm` textarea are validated before backtest start: each `canonical_id` must exist in `instruments` table. Unknown `canonical_id` → 422 with list of invalid values. Prevents silent zero-veto behavior from typos.

**`/override_{decision_id}` Telegram command:**
- Registered as a dynamic command handler (same pattern as Phase 11d `/confirm`).
- `decision_id` is a BIGINT; validated as numeric before any DB call (injection guard).
- Calls `PATCH /api/bots/{bot_id}/advisor-decisions/{decision_id}` override endpoint internally (Phase 21a.1) with `override_action='approve'` and `override_reason=f"telegram_override:{from_user_id}"`.
- Requires `from_user_id` in `app_config[telegram/allowlist]`.
- **jwt-subject scoping (M-new-5):** the PATCH call is scoped to bots whose `created_by` matches the Telegram user's mapped jwt_subject. The mapping `telegram_user_id → jwt_subject` is stored in `app_config[telegram/user_jwt_map]` (JSONB, operator-configured). If no mapping exists for this `from_user_id`, the override is rejected with a Telegram reply: `⛔ Your Telegram user is not mapped to a JWT subject — contact admin.` This prevents any allowlisted Telegram user from overriding any bot's decisions regardless of ownership.
- Uses `check_trade` rate-limit bucket (fail-CLOSED on Redis error — same money-moving bucket as Phase 11d).
- Replies with confirmation: `✅ Override recorded for decision {decision_id}. The original order was not re-submitted.`
- **Override is audit-only** — same invariant as Phase 21a.1 §3.3.

**Config gates:**
- Global: `app_config[telegram/advisor_veto_notify]` boolean (default `false` — opt-in).
- Per-bot: `AdvisorConfig.notify_telegram: bool = True` — new field added to `AdvisorConfig` in `types.py`; backward-compatible (Pydantic default `True`; lazy backfill via existing HIGH-8 pattern from Phase 21a).

**Prometheus metrics (3):**

| Metric | Type | Labels |
|---|---|---|
| `telegram_advisor_veto_notify_total` | Counter | — |
| `telegram_advisor_override_total` | Counter | `outcome` (`applied\|rejected\|rate_limited`) |
| `telegram_advisor_notify_failures_total` | Counter | — |

---

## 8. Advisor-in-Backtest

### 8.1 `AdvisorStub`

New class `app/backtest/advisor_stub.py`. Plain sync class — no async, no Redis, no DB sessions.

```python
class AdvisorStub:
    def __init__(
        self,
        mode: AdvisorMode,
        veto_injections: list[tuple[int, str]] = [],
        # veto_injections: [(bar_index, canonical_id), ...]
    ) -> None: ...

    def review(
        self,
        bar_index: int,
        canonical_id: str,
        intent: OrderIntent,
        bar_buffer: list[dict],   # in-memory bars up to current bar
    ) -> AdvisorVerdict:
        if (bar_index, canonical_id) in self._veto_set:
            return AdvisorVerdict(action="veto", reasoning="veto_injection")
        return AdvisorVerdict(action="approve", reasoning="backtest_stub")
```

Context build in `AdvisorStub` is a simplified version reading from `bar_buffer` (in-memory) instead of DB queries. No prompt assembly, no AI call.

### 8.2 `BacktestRunner` changes

In `app/backtest/runner.py`:

1. If `backtest.advisor_config IS NOT NULL`: instantiate `AdvisorStub(mode, veto_injections)` before bar loop.
2. Per order intent (after `FillSimulator` generates it, before pushing to fill queue):
   - Call `stub.review(bar_index, canonical_id, intent, bar_buffer)`.
   - Record `backtest_advisor_decisions` row (batch insert, same pattern as `backtest_bars` batch insert in Phase 20).
   - If `verdict='veto'`: skip order (do not push to fill queue); add `|fill_price * qty|` to `advisor_vetoed_pnl` accumulator.
   - If `verdict='approve'`: proceed normally.
3. **Flush on status transition (M-new-6):** the batch-insert buffer for `backtest_advisor_decisions` is flushed whenever the backtest transitions to `done`, `failed`, or `cancelled` — regardless of buffer size. This prevents unflushed advisor-decision rows from being lost on early termination. The flush hook is added to `BacktestRunner._on_status_change()` (same hook point used by `ProgressPublisher`).
4. Latency measured via `time.monotonic()` around `stub.review()` — context-build time only.

### 8.3 `BacktestConfigForm` changes

New `advisor_enabled: bool` toggle (default off). When enabled, reveals:
- `advisor_mode` selector: OBSERVE | VETO.
- `veto_injections` textarea: newline-separated `bar_index,canonical_id` pairs (optional; only meaningful for VETO mode).

### 8.4 `BacktestReportKpis` extensions

New fields on existing Pydantic model:
```python
advisor_approve_count: int = 0
advisor_veto_count: int = 0
advisor_vetoed_pnl: Decimal = Decimal("0")
advisor_veto_rate: float = 0.0
```

### 8.5 Dual PnL curve on `BacktestPage`

When `advisor_veto_count > 0`, `PnlChart` renders two lines:
- **Base**: full PnL curve (all orders executed, same as non-advisor run).
- **With Advisor**: PnL curve excluding vetoed orders (computed from `pnl_curve` minus vetoed-order PnL at their bar indices).

Toggle between single/dual view via a checkbox above the chart. Default: dual view when advisor was enabled.

---

## 9. REST API

### Param-tuner endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/bots/{id}/param-suggestions` | admin JWT + CSRF | Trigger manual param suggestion (202) |
| `GET` | `/api/bots/{id}/param-suggestions` | JWT | List suggestions (cursor-paginated) |
| `GET` | `/api/bots/{id}/param-suggestions/{suggestion_id}` | JWT | Suggestion detail with candidates |
| `POST` | `/api/bots/{id}/param-suggestions/{suggestion_id}/approve` | admin JWT + CSRF | Approve candidate (`{candidate_index}` in body) |
| `POST` | `/api/bots/{id}/param-suggestions/{suggestion_id}/reject` | admin JWT + CSRF | Reject suggestion |

### Shadow-promoter endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/bots/{id}/shadows` | admin JWT + CSRF | Create shadow bot |
| `GET` | `/api/bots/{id}/shadows/comparison` | JWT | Shadow comparison report |
| `POST` | `/api/bots/{id}/shadows/{shadow_id}/promote` | admin JWT + CSRF | Promote shadow to live |
| `GET` | `/api/bots/{id}/shadow-promotions` | JWT | List `shadow_promotion_events` |

### Backtest advisor endpoint

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/bots/{id}/backtests/{backtest_id}/advisor-decisions` | JWT | List `backtest_advisor_decisions` (cursor-paginated) |

---

## 10. WebSocket frames

### `bot:tuner:{bot_id}` pubsub channel

| Frame type | Fields | When |
|---|---|---|
| `backtesting` | `suggestion_id, candidate_count` | After fan-out to backtests |
| `ranked` | `suggestion_id, candidate_count` | When all backtests complete |
| `applied` | `suggestion_id, candidate_index` | After params applied + bot restarted |
| `failed` | `suggestion_id, reason` | Trigger failure (< 1 valid candidate) |

All frames include `v: 1` version field. FE drops `v !== 1` (same guard as Phase 21a advisor WS).

**Versioned frame shapes (L-new-4 — for parser symmetry):**
```jsonc
// backtesting
{"v": 1, "type": "backtesting", "suggestion_id": "uuid", "candidate_count": 3}
// ranked
{"v": 1, "type": "ranked",      "suggestion_id": "uuid", "candidate_count": 3}
// applied
{"v": 1, "type": "applied",     "suggestion_id": "uuid", "candidate_index": 1}
// failed — explicit v:1 shape (was missing from Pass 1)
{"v": 1, "type": "failed",      "suggestion_id": "uuid", "reason": "no_valid_candidates|cost_ceiling|queue_full|ai_error"}
```

**WS endpoint:** `GET /ws/bots/{id}/tuner` — per-bot, 50-conn cap, **global cap 100** (same pattern as Phase 19 `/ws/bots/status`; prevents connection exhaustion across many bots, M7), pubsub `bot:tuner:{bot_id}`, 500ms conflation.

### `bot:shadow:{live_bot_id}` pubsub channel

| Frame type | Fields | When |
|---|---|---|
| `comparison_ready` | `shadow_bot_ids: [uuid]` | APScheduler daily notify |
| `promoted` | `shadow_bot_id, promoted_by` | On promotion |

**WS endpoint:** `GET /ws/bots/{id}/shadow` — per-bot, 50-conn cap, **global cap 100**, pubsub `bot:shadow:{bot_id}`.

---

## 11. Frontend components

| Component | Description |
|---|---|
| `ParamTunerSection` | On `BotDetailPage` — trigger button (manual), suggestion list, candidate cards with backtest KPIs + delta badges, approve/reject buttons |
| `ParamCandidateCard` | Shows params diff vs current, backtest KPIs, rank badge, "Approve this" button |
| `ShadowComparisonPanel` | On `BotDetailPage` — create shadow form, list of active shadows, comparison table (shadow vs live metrics), promote button |
| `ShadowMetricsTable` | Comparison table rows: Sharpe, MAR, max_dd, win_rate, avg_trade_pnl — delta column coloured green/red |
| `useParamTunerStream` | WS hook for `bot:tuner:{id}`; invalidates TanStack Query on `ranked`/`applied` frames |
| `useShadowStream` | WS hook for `bot:shadow:{id}`; shows `comparison_ready` toast |
| `BacktestConfigForm` | Gains `advisor_enabled` toggle + `veto_injections` textarea (Phase 20 component, additive change) |
| `BacktestReportKpis` | Gains advisor veto count/rate/vetoed PnL row |
| `PnlChart` | Dual-line mode when advisor vetoes > 0; toggle checkbox above chart |
| `BotDetailPage` | Gains `ParamTunerSection` below advisor tab; `ShadowComparisonPanel` in new "Shadows" sub-tab |

---

## 12. Tests

### Backend (~60 new tests)

**Param-tuner:**
- `test_trigger_creates_suggestion_and_fans_out`: trigger → `bot_param_suggestions` row; N backtest jobs submitted.
- `test_trigger_blocks_second_active_suggestion`: second trigger while `status='backtesting'` → 409.
- `test_trigger_invalid_candidates_dropped`: LLM returns 1 valid + 1 invalid candidate → only valid one fanned out.
- `test_trigger_all_invalid_candidates`: 0 valid → row persisted with empty candidates; frame `type=failed`.
- `test_poll_marks_ranked_when_all_done`: all backtests complete → `status='ranked'`, rank order correct.
- `test_poll_handles_failed_backtest`: one backtest fails → that candidate has `backtest_result=null`; others still ranked.
- `test_poll_skips_in_flight_backtests`: backtests still running → suggestion stays `backtesting`.
- `test_approve_updates_params_and_restarts_bot`: approve → `bots.strategy_params` updated; bot stop+start fired.
- `test_approve_rejects_out_of_bounds_index`: candidate_index=99 → 422.
- `test_approve_rejects_null_result_candidate`: candidate with failed backtest → 422.
- `test_reject_sets_rejected_status`: reject → `status='rejected'`; bot unchanged.
- `test_scheduled_job_skips_non_advisor_bots`: bot with `advisor_config->>'mode'='OFF'` → not triggered.
- `test_scheduled_job_skips_shadow_bots`: `is_shadow=True` bot → not triggered.

**Shadow-promoter:**
- `test_create_shadow_clones_bot`: shadow bot has `is_shadow=True`, `shadow_of=live_id`, `status='stopped'`.
- `test_create_shadow_forces_paper_mode`: all `bot_accounts` rows have `mode='paper'`.
- `test_create_shadow_copies_risk_caps`: shadow `bot_risk_caps` identical to live.
- `test_create_shadow_live_bot_is_shadow_rejected`: `is_shadow=True` bot → 400 (cannot shadow a shadow).
- `test_get_comparison_returns_metrics`: comparison report includes delta fields.
- `test_get_comparison_ready_flag`: shadow running ≥ window → `comparison_ready=True`.
- `test_promote_updates_live_params`: `bots.strategy_params` set to shadow's params; `shadow_promoted_at` set.
- `test_promote_soft_deletes_shadow`: shadow `status='deleted'`; `bot_runs` rows survive.
- `test_promote_inserts_audit_row`: `shadow_promotion_events` row with metric snapshots.
- `test_promote_wrong_shadow_of`: shadow not owned by live bot → 400.
- `test_promote_already_deleted_shadow`: → 404.
- `test_shadow_bot_cannot_trade_live`: `BotContext.place_order` with `is_shadow=True` → asserts paper mode on facade call (integration test).
- `test_shadow_bot_fill_does_not_leak_to_live_child`: live bot child receives live fill event (`fills:live:*`); shadow child does not. Shadow child receives paper fill event (`fills:paper:*`); live child does not. (H-new-4)

**Advisor extensions:**
- `test_filings_injected_into_context`: `filings` row exists → appears in context payload.
- `test_filings_skipped_when_no_summary`: `llm_summary IS NULL` → filing omitted.
- `test_earnings_injected_upcoming`: upcoming earnings within 14 days → appears in context.
- `test_filings_context_gate_disabled`: `app_config[advisor/filings_context_enabled]=false` → no filings query.
- `test_telegram_veto_notify_fires_on_veto`: advisor veto pubsub → Telegram message sent.
- `test_telegram_veto_notify_skipped_on_approve`: approve verdict → no Telegram message.
- `test_telegram_veto_notify_global_gate_off`: `advisor_veto_notify=false` → no message.
- `test_telegram_veto_notify_per_bot_gate_off`: `notify_telegram=false` in advisor_config → no message.
- `test_telegram_override_command`: `/override_{id}` → calls PATCH override endpoint; reply sent.
- `test_telegram_override_rate_limited`: rate-limit bucket exhausted → `outcome=rate_limited`; fail-CLOSED.
- `test_telegram_override_invalid_decision_id`: non-numeric → 400; no DB call.

**Advisor-in-backtest:**
- `test_advisor_stub_approve_by_default`: no veto_injections → all verdicts `approve`.
- `test_advisor_stub_veto_injection`: `(bar_index=5, canonical_id='AAPL')` → verdict `veto` at that bar.
- `test_backtest_runner_with_advisor_skips_vetoed_orders`: vetoed order absent from `fill_simulator` queue; `advisor_vetoed_pnl` accumulated.
- `test_backtest_runner_persists_advisor_decisions`: `backtest_advisor_decisions` rows created per order.
- `test_backtest_kpis_include_advisor_fields`: report KPIs include `advisor_veto_count`, `advisor_veto_rate`.
- `test_backtest_without_advisor_config_unaffected`: `advisor_config IS NULL` → no stub instantiated; no advisor rows.

### Frontend (~15 new tests)

- `ParamTunerSection`: trigger button calls POST; shows "backtesting" state; shows ranked candidates.
- `ParamCandidateCard`: delta badges coloured correctly (green = improvement, red = regression for max_dd).
- `ParamCandidateCard`: approve button calls POST approve; reject calls POST reject.
- `ShadowComparisonPanel`: create form submits POST; comparison table renders delta column.
- `ShadowComparisonPanel`: promote button calls POST promote; confirmation dialog before submit.
- `useParamTunerStream`: `ranked` frame invalidates query; `applied` frame shows toast.
- `PnlChart`: dual-line mode renders two datasets; single-line toggle hides advisor line.
- `BacktestConfigForm`: advisor toggle shows/hides veto_injections textarea.
- `BacktestReportKpis`: advisor fields render when `advisor_veto_count > 0`.

---

## 13. Implementation chunks

| Chunk | Files | Routing | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0065 + 0066 + 0067, migration tests | Qwen | — |
| **B — Param-tuner types + context builder** | `param_tuner/types.py`, `param_tuner/context_builder.py`, tests | Qwen | after A |
| **C — Param-tuner service + APScheduler** | `param_tuner/service.py`, `param_tuner/metrics.py`, APScheduler job wiring in `main.py`, tests | Codex | after B |
| **D — Shadow-promoter service + APScheduler** | `shadow_promoter/types.py`, `shadow_promoter/service.py`, `shadow_promoter/metrics.py`, APScheduler wiring, `BotContext` `is_shadow` guard, `BotFillRouter` fill-channel isolation (H-new-4), `BotSupervisor.restart()` (H-new-2), tests | Codex | after A |
| **E — Advisor extensions** | `advisor/context_builder.py` (filings/earnings), `AdvisorConfig.notify_telegram`, `telegram/advisor_notify.py`, tests | Codex | after A |
| **F — Advisor-in-backtest** | `backtest/advisor_stub.py`, `backtest/runner.py`, `BacktestReportKpis` extension, tests | Qwen | after A |
| **G — REST + WS API** | `api/bots.py` (param-tuner + shadow endpoints), `api/ws_bots.py` (tuner + shadow WS), tests | Codex | after C + D |
| **H — Frontend** | All FE components + hooks + services, `BacktestPage` dual curve, `BacktestConfigForm` | Codex | after G + F |
| **I — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.21.2 | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku) on all. Chunk A: + database-reviewer (sonnet). Chunks C + D + E + G: + security-reviewer (sonnet). Chunk H: + typescript-reviewer (haiku). Phase end: ARCHITECT-REVIEW (opus).

---

## 13a. Cross-cutting concerns (Pass 2)

**1. Phase 19 interfaces this phase modifies or depends on.** Implementers must read Phase 19 CLAUDE.md section before touching these surfaces:
- `BotSupervisor.restart()` — new method, `supervisor.py` modified (§5.3a).
- `BotFillRouter` shadow routing — fill channel namespace and matching predicate (§6.2, H-new-4).
- `BotContext.place_order` shadow guard — `is_shadow` assertion (§6.2, H6).
- `params_schema` subprocess sandbox — `app/bot/sandbox.py` reused by backfill-schema endpoint (§4, M-new-2).

**2. Concurrency assumptions.** Every "read-aggregate then act" path in this spec is racy under concurrent triggers unless explicitly serialised:
- `trigger()` cost ceiling: guarded by Redis `INCRBYFLOAT` reservation (H-new-3).
- `trigger()` active-suggestion check: guarded by `SELECT … FOR UPDATE` on the `bot_param_suggestions` row (step 2 in §5.3 — implementer must use `SELECT … FOR UPDATE SKIP LOCKED` or equivalent to prevent double-trigger).
- Scheduled fleet loop: serialised by single-replica today; multi-worker locking deferred to Phase 24.

**3. Audit completeness.** The `applied` event — the most consequential write — currently has no immutable audit row independent of `bot_param_suggestions.status='applied'`. If the suggestion row is later mutated, the apply event history is lost. `shadow_promotion_events` handles this correctly for promotions. A future `bot_param_audit` append-only log keyed off `suggestion_id + timestamp` is recommended for Phase 23+; deferred intentionally here.

**4. Param-approve risk gate note.** The LLM-proposes → human-approves param flow does not invoke `RiskService.evaluate()` directly. The documented assumption: bot risk caps (`bot_risk_caps`) + on-next-bar gate evaluation will catch any bad params at order time. This is acceptable for v0.21.2 but should be explicitly re-evaluated if param tuner ever becomes fully automated (auto-approve stub becomes real).

---

## 14. Phase 11a invariants this phase preserves

Every Phase 11a AI router consumer must re-apply these conventions — they are not inherited by reference.

| Invariant | This phase's implementation |
|---|---|
| **Capability choice + rationale** | Param-tuner defaults `LOCAL_ONLY`; `REASONING` (cloud) opt-in via `app_config[param_tuner/allow_cloud_reasoning]`. Rationale: param proposals are decision-shaping for live trading; cloud is not the safe default. |
| **AI call provenance → `ai_completions`** | `bot_param_suggestions.ai_completion_id`, `ai_model`, `ai_prompt_hash` link every suggestion to its `ai_completions` ledger row (H3). |
| **Kill switch** | `app_config[param_tuner/scheduled_enabled]` (default `false`); `app_config[shadow_promoter/auto_promote_check_enabled]` (default `false`). Admin can disable without code deploy. |
| **Cost ceiling** | `app_config[param_tuner/cost_ceiling_usd_daily]` checked before each AI call; fleet stops on ceiling hit. |
| **Fail-OPEN / Fail-CLOSED policy** | Param-tuner: AI errors → `status='pending'`, no backtest fan-out (fail-OPEN for non-money-moving trigger). Telegram override (`check_trade` bucket): fail-CLOSED on Redis error — same as Phase 11d. Shadow paper-mode enforcement: fails CLOSED (order blocked) if any layer fires. |
| **Multi-layer enforcement on money-moving paths** | Shadow paper-mode: 3 layers (BotContext, place_order_for_bot, RiskService). Param approve: human approval required + BotSupervisor.restart() serialises the stop/start. |
| **LOCAL_ONLY on decision-making paths** | Param-tuner defaults LOCAL_ONLY. Advisor context builder (filings injection) makes no AI calls itself — reads pre-computed `llm_summary` from DB. |

---

## 15. Resolved findings

| Finding | Resolution |
|---|---|
| H1: No cost ceiling / circuit breaker | Daily cost ceiling check in `trigger()`; `MAX_CANDIDATES_PER_SUGGESTION=5` hard cap; fleet serial processing in scheduled job; `param_tuner_fleet_cost_ceiling_total` metric |
| H2: Params not semantically bounded | `BaseStrategy.params_bounds_schema` classattr; `strategy_schema` JSONB includes bounds; `trigger()` validates both type and bounds before fan-out; `param_tuner_invalid_candidates_total{reason=out_of_bounds}` |
| H3: No AI provenance trail | `bot_param_suggestions.ai_completion_id / ai_model / ai_prompt_hash` columns in Alembic 0065 |
| H4: LOCAL_ONLY default not specified | Param-tuner defaults `LOCAL_ONLY`; `REASONING` opt-in via config; documented in §14 invariants |
| H5: No kill switch on APScheduler jobs | `app_config[param_tuner/scheduled_enabled]` (default `false`) + `app_config[shadow_promoter/comparison_notify_enabled]` (default `true`) + `app_config[shadow_promoter/auto_promote_check_enabled]` (default `false`) |
| H6: Shadow paper-mode single-layer | Three-layer enforcement: BotContext + place_order_for_bot + RiskService; `shadow_bot_live_mode` check name; `attempt_kind="shadow_place_order"` in 0065 |
| H7: BacktestSubmitter bypasses queue caps | Queue depth check via `backtest_submitter.queue_depth()` before fan-out; `MAX_BACKTEST_QUEUE_DEPTH=20`; `param_tuner_backtest_queue_depth` gauge |
| H8: approve() stop+restart race | `BotSupervisor.restart()` new atomic method; `SupervisorRestartError` on timeout → 500; suggestion stays `ranked` for retry |
| M1: `candidates` JSONB unbounded | `CHECK (jsonb_array_length(candidates) <= 5)` in Alembic 0065 |
| M2: Missing indices for tuner context reads | `CREATE INDEX IF NOT EXISTS` for `bot_runs(bot_id, started_at DESC)` and `bot_orders(bot_id, created_at DESC)` in Alembic 0065 |
| M3: Telegram override re-order not server-enforced | PATCH endpoint has no `place_order` code path; explicit note in §7.2 (server is real enforcer) |
| M4: `shadow_promotion_events` missing window anchor | `comparison_window_start TIMESTAMPTZ NOT NULL` added to Alembic 0066 |
| M5: Veto injections unsanitised | `canonical_id` validated against `instruments` table before backtest start; unknown → 422 |
| M6: `attempt_kind` not widened for shadow | Alembic 0065 widens `risk_decisions.attempt_kind` CHECK to include `shadow_place_order` |
| M7: No global WS cap | Global cap 100 on both `/ws/bots/{id}/tuner` and `/ws/bots/{id}/shadow` |
| M8: Delta vs single recent run | Rolling window mean over last 5 `bot_runs` for delta computation in `poll_backtest_results` |
| M9: `strategy_schema` backfill | `POST /api/admin/bots/{id}/backfill-schema` operator endpoint; NULL bots skipped by tuner with informative metric |
| L1: `ParamCandidate.params` untyped | Documented: runtime-validated via `strategy_schema` only |
| L2: FE zero-candidates UX | `ParamTunerSection` shows "LLM returned no valid candidates" affordance when `candidates=[]` |
| L3: Default mutable arg in `AdvisorStub` | `veto_injections: list[...] | None = None` pattern; `or []` in body; per Codex defaults |
| L4: Single-replica cron caveat | Documented in §14 invariants and CLAUDE.md update (Phase 24 will add cron lease) |
| L5: LLM verdict feedback loop | Documented as known characteristic; future test "veto-heavy context → bias check" noted |
| H-new-1 (Pass 2): `risk_decisions.attempt_kind` CHECK DDL missing | `ALTER TABLE risk_decisions DROP CONSTRAINT ... ADD CONSTRAINT CHECK (... 'shadow_place_order')` added to Alembic 0065 §4 |
| H-new-2 (Pass 2): `BotSupervisor.restart()` unspecified | Full specification added as §5.3a: signature, valid source states (`running/paused/error`), respawn backoff reset, mid-respawn rejection, stop-polling-via-pubsub, 8s timeout |
| H-new-3 (Pass 2): Cost ceiling TOCTOU race | Redis `INCRBYFLOAT param_tuner:cost_pending:{utc_date}` reservation before AI call; `DECRBY` after; bounded overrun ≤ `$0.50` at N=5; `param_tuner_cost_reservation_failures_total` metric; documented in `trigger()` step 3 |
| H-new-4 (Pass 2): Shadow fill isolation unspecified | `BotFillRouter` matching predicate is `(bot_id, is_shadow)` not `account_id` alone; paper fills on `fills:paper:*` channel; live fills on `fills:live:*`; isolation specified in §6.2; `test_shadow_bot_fill_does_not_leak_to_live_child` added |
| M-new-1 (Pass 2): `ai_completion_id` eventual consistency | Comment in §4 DDL: provenance JOIN may return null; retry after 2s or accept eventual consistency |
| M-new-2 (Pass 2): Backfill-schema endpoint subprocess sandbox | `POST /api/admin/bots/{id}/backfill-schema` mandated to use `DenylistFinder + RLIMIT_*` subprocess; documented in §4 DDL comment |
| M-new-3 (Pass 2): "Most recent run" vs "rolling 5" contradiction | Stale "most recent bot_runs row" phrasing removed from `poll_backtest_results` step 2; delta deferred to step 3 where rolling-5 is computed |
| M-new-4 (Pass 2): `BacktestSubmitter.submit()` parameters unspecified | Full parameter list added in §5.3: 90-day rolling window (`app_config[param_tuner/backtest_window_days]`), slippage/commission copied from bot's most recent backtest |
| M-new-5 (Pass 2): Telegram override not jwt-subject-scoped | `/override_{decision_id}` now requires `app_config[telegram/user_jwt_map]` mapping; PATCH scoped to bots `created_by` matching operator's jwt_subject |
| M-new-6 (Pass 2): Advisor decisions lost on backtest failure | Batch buffer flushed on `done/failed/cancelled` status transition via `BacktestRunner._on_status_change()` |
| M-new-7 (Pass 2): `comparison_ready` semantics inverted | Fixed: `started_at <= now() - window_days * interval '1 day'` (oldest run is older than window); `>=` was wrong (any run started within window) |
| L-new-1 (Pass 2): `MAX_BACKTEST_QUEUE_DEPTH` magic constant | Moved to `app_config[param_tuner/max_backtest_queue_depth]` (default 20) |
| L-new-2 (Pass 2): `candidates` CHECK missing NOT NULL | `CHECK (candidates IS NOT NULL AND jsonb_array_length(candidates) <= 5)` — paranoia-documented |
| L-new-3 (Pass 2): `ai_prompt_hash` unbounded TEXT | Documented in §4 DDL comment: CHAR(16) semantics, stored as unbounded TEXT |
| L-new-4 (Pass 2): `failed` frame missing v:1 example | Full v:1-versioned frame shapes for all 4 frame types added to §10 |
| H3-new-1 (Pass 3): `bot_accounts.mode` does not exist; shadow `bots.mode` not set explicitly | §6.2 `create_shadow` step 2 now explicitly sets `bots.mode='paper'`; step 4 reworded to remove erroneous `bot_accounts.mode` reference. `bots.mode` is the single source of truth per Alembic 0061. |
| M3-new-1 (Pass 3): `status='pending'` overloaded for in-progress and terminal-failure | `failed` added to `bot_param_suggestions.status` CHECK in Alembic 0065; `SuggestionStatus.FAILED` added to types; steps 8/9 set `status='failed'`; step 2 trigger guard counts only `pending\|backtesting\|ranked` as active; FE `ParamTunerSection` shows "Dismiss" on `failed` rows |

---

## 16. Deferred

| Item | Target |
|---|---|
| Auto-promote logic (`bots.auto_promote_config` column + threshold evaluation) | Beyond 21b |
| Staged allocation (10% → 50% → 100%) | Beyond 21b |
| Real AI calls during backtest replay | Not planned (deterministic stub is intentional) |
| Advisor perf-attribution ("was the advisor right?") | Phase 21c |
| Multi-bot orchestration | Phase 22 |
| `bot_param_suggestions` → hypertable | Phase 24 |
| Param-tuner for shadow bots (tune a shadow independently) | Beyond 21b |
| Cron lease / multi-worker cron dedup | Phase 24 |
