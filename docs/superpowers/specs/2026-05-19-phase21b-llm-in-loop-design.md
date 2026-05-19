# Phase 21b — LLM-in-Loop: Param-Tuning + Shadow-Promotion (v0.21.2)

**Date:** 2026-05-19  
**Status:** Brainstorm approved — ready for /writing-plans  
**Builds on:** Phase 21a (LLM advisor, v0.21.0) · Phase 21a.1 (advisor polish, v0.21.1) · Phase 20 (backtesting harness, v0.20.0) · Phase 18 (scanner/filings/earnings, v0.18.0) · Phase 11c (Telegram bot, v0.11.2.0)  
**Next phases:** 21c (perf-attribution — "was the advisor right?")

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
- Alembic 0065: `bot_param_suggestions` table + `bots.is_shadow / shadow_of / shadow_promoted_at` columns.
- Alembic 0066: `shadow_promotion_events` table.
- Alembic 0067: `backtests.advisor_config` JSONB column + `backtest_advisor_decisions` table.
- `app/services/advisor/context_builder.py` — filings/earnings injection.
- `app/services/telegram/advisor_notify.py` — VETO notifications + `/override_` command.
- `app/backtest/runner.py` + `AdvisorStub` — advisor-in-backtest wiring.
- REST: param suggestion endpoints, shadow endpoints, backtest advisor decision endpoint.
- WS: `bot:tuner:{bot_id}` and `bot:shadow:{bot_id}` pubsub frames.
- FE: `ParamTunerSection`, `ShadowComparisonPanel`, dual PnL curve on `BacktestPage`, Telegram config toggle on `/admin/ai`.
- 13 new Prometheus metrics across the two new services.

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
                                    'approved','rejected','applied')),
    strategy_params_current     JSONB NOT NULL,
    ai_reasoning                TEXT,
    candidates                  JSONB NOT NULL DEFAULT '[]',
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
    ADD COLUMN is_shadow                    BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN shadow_of                    UUID REFERENCES bots(id) ON DELETE SET NULL,
    ADD COLUMN shadow_promoted_at           TIMESTAMPTZ,
    ADD COLUMN shadow_comparison_window_days INT;
-- shadow_comparison_window_days: set at create_shadow() time; NULL on non-shadow bots.

-- strategy_schema column: stores BaseStrategy.params_schema at bot-create time.
-- Required by ParamTunerService to validate LLM-proposed candidate params.
-- POST /api/bots must be updated to persist strategy_schema alongside strategy_params.
ALTER TABLE bots
    ADD COLUMN strategy_schema JSONB;
-- NULL for bots created before 0065; tuner skips bots with strategy_schema IS NULL.

-- Index for shadow queries
CREATE INDEX bots_shadow_of_idx ON bots (shadow_of) WHERE shadow_of IS NOT NULL;

-- updated_at trigger for bot_param_suggestions
CREATE TRIGGER bot_param_suggestions_updated_at
    BEFORE UPDATE ON bot_param_suggestions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

### Alembic 0066

```sql
CREATE TABLE shadow_promotion_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shadow_bot_id           UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
    live_bot_id             UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
    promoted_by             TEXT NOT NULL,
    comparison_window_days  INT NOT NULL,
    shadow_metrics          JSONB NOT NULL,
    -- {sharpe, mar, max_dd, win_rate, avg_trade_pnl, total_trades}
    live_metrics            JSONB NOT NULL,
    promoted_at             TIMESTAMPTZ NOT NULL DEFAULT now()
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

**`BacktestSubmitter`** calls the internal backtest submission service method directly (not via HTTP) — same DB session family, avoids round-trip overhead. Extracted to allow injection in tests without standing up a full backtest worker. In production it wraps `BacktestService.submit(bot_id, params, bars_source)` from `app/backtest/`.

#### `trigger(bot_id, triggered_by, db) → UUID`

1. Read bot row. Assert `is_shadow=False`, `status != 'deleted'`, `strategy_schema` is not null (strategy has `params_schema`).
2. Assert no suggestion in `status IN ('pending','backtesting','ranked')` already exists for this bot — one active suggestion per bot at a time. If exists → raise `TunerAlreadyActiveError` (409 from REST handler).
3. Build context payload via `TunerContextBuilder.build(bot_id, db)`.
4. Call AI router:
   - `capability = AICapability.REASONING`
   - `caller = f"param_tuner:bot:{bot_id}"`
   - `jwt_subject = f"system:bot:{bot_id}"`
   - `response_format = CandidateListResponse.model_json_schema()`
   - `timeout = 30s` (longer than advisor — reasoning over run history)
5. Parse `CandidateListResponse`. Validate each candidate dict against `strategy_schema` via Pydantic. Drop invalid candidates; metric `param_tuner_invalid_candidates_total` per drop.
6. If `len(valid_candidates) < 1` → persist row with `status='pending'`, `candidates=[]`, publish failure frame, metric `param_tuner_trigger_failures_total`. Return suggestion_id (caller returns 202 — operator can see the empty suggestion).
7. Persist `bot_param_suggestions` row: `status='backtesting'`, `candidates` array with `params` only (no `backtest_job_id` yet), `ai_reasoning`.
8. Fan-out: for each valid candidate, call `backtest_submitter.submit(bot_id, candidate.params)` → store `backtest_job_id` per candidate. Update `candidates` JSONB in-place. Metric `param_tuner_backtest_fan_out_total`.
9. Publish `bot:tuner:{bot_id}` frame `{type:"backtesting", suggestion_id, candidate_count: N}`.
10. Return `suggestion_id`.

**Fail-OPEN:** any exception after the DB row is created → row stays in current status. Next `trigger()` call will reject with 409 if status is still active. Operator must call `reject()` to clear it.

#### `poll_backtest_results(db)` — called by APScheduler every 60s

1. Query all `bot_param_suggestions WHERE status='backtesting'`.
2. For each suggestion, for each candidate with `backtest_job_id` and no `backtest_result`:
   - Query `backtests` table by `backtest_job_id`.
   - If `status='done'`: copy KPI fields into `candidate.backtest_result`; compute `delta_vs_current` (difference vs `strategy_params_current` run metrics from most recent `bot_runs` row).
   - If `status='failed'`: set `candidate.backtest_result=null`, `candidate.rank=null`.
   - If `status` still running/queued: skip.
3. When all candidates have `backtest_result IS NOT NULL` or `backtest_job_id` job is failed:
   - Rank remaining candidates by `sharpe DESC` (MAR as tiebreaker; NaN/null ranked last).
   - Set `status='ranked'`.
   - Publish `bot:tuner:{bot_id}` frame `{type:"ranked", suggestion_id, candidate_count: N}`.
   - Metric `param_tuner_ranked_total`.

#### `approve(suggestion_id, candidate_index, approved_by, db)`

1. Load suggestion, assert `status='ranked'`.
2. Assert `candidate_index` in `[0, len(candidates))` and `candidates[candidate_index].backtest_result IS NOT NULL`.
3. DB transaction:
   - `UPDATE bots SET strategy_params = candidates[index].params WHERE id = bot_id`
   - `UPDATE bot_param_suggestions SET status='applied', approved_candidate_index=index, approved_by=approved_by, applied_at=now()`
4. If bot `status='running'`:
   - Stop: publish `STOP` to bot control queue, `asyncio.wait_for` on `bot:status:{bot_id}` pubsub for `status=stopped`, 5s timeout.
   - On timeout: log warning, proceed with restart anyway (supervisor handles the race).
   - Restart: `BotSupervisor.start(bot_id)`.
5. Publish `bot:tuner:{bot_id}` frame `{type:"applied", suggestion_id, candidate_index}`.
6. Metric `param_tuner_applied_total{triggered_by}`.

#### `reject(suggestion_id, rejected_by, db)`

Sets `status='rejected'`. No bot changes. No pubsub.

### 5.4 APScheduler jobs

**`param_tuner_scheduled`** — cron, `app_config[param_tuner/schedule]` (default `0 2 * * 1`, Monday 02:00 local). For every bot satisfying:
- `is_shadow = false`
- `status = 'running'`
- `advisor_config->>'mode' != 'OFF'` (tuner only runs on advisor-enabled bots)
- No suggestion in `status IN ('pending','backtesting','ranked')` exists

calls `trigger(bot_id, triggered_by='scheduled')`.

**`param_tuner_poll`** — runs every 60s. Calls `poll_backtest_results()`.

### 5.5 Prometheus metrics (8)

| Metric | Type | Labels |
|---|---|---|
| `param_tuner_trigger_total` | Counter | `triggered_by` |
| `param_tuner_trigger_failures_total` | Counter | — |
| `param_tuner_candidates_generated_total` | Counter | — |
| `param_tuner_invalid_candidates_total` | Counter | — |
| `param_tuner_backtest_fan_out_total` | Counter | — |
| `param_tuner_ranked_total` | Counter | — |
| `param_tuner_applied_total` | Counter | `triggered_by` |
| `param_tuner_ai_latency_seconds` | Histogram | — |

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
    comparison_ready: bool   # True if shadow has run >= comparison_window_days

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
   - `name = f"{live_bot.name} [shadow]"`.
   - `status = 'stopped'` (not auto-started — operator starts via existing `POST /api/bots/{id}/start`).
   - `advisor_config` copied from live bot (shadow participates in advisor if live bot does).
3. Copy `bot_risk_caps` row to shadow bot (identical caps — shadow must respect same risk limits).
4. Copy `bot_accounts` rows — shadow trades **same accounts in paper mode** regardless of live bot mode. `bot_accounts.mode` set to `'paper'` on all shadow rows.
5. Return shadow `bot_id`.
6. Metric `shadow_promoter_created_total`.

**Invariant:** a shadow bot is always paper-mode on all accounts. The `BotContext` enforces this via `is_shadow` flag check before any `facade.place_order()` call — shadow bots cannot trade live even if `bot_accounts.mode` is later mutated.

#### `get_comparison(live_bot_id, db) → ShadowComparisonReport`

Pure read. For each `is_shadow=True, shadow_of=live_bot_id, status != 'deleted'` bot:
- Aggregate `bot_runs` metrics for shadow bot and live bot over `comparison_window_days`.
- Compute delta fields.
- Set `comparison_ready = True` if shadow has at least one completed `bot_runs` row with `started_at >= now() - comparison_window_days * interval '1 day'`.

Returns `ShadowComparisonReport`. No DB write.

#### `promote(live_bot_id, shadow_bot_id, promoted_by, comparison_window_days, db)`

Single DB transaction:

1. Assert `shadow.shadow_of == live_bot_id`, `shadow.is_shadow == True`, `shadow.status != 'deleted'`.
2. Stop live bot if running: publish STOP to control queue, wait up to 5s on pubsub. On timeout: log warning, proceed.
3. Stop shadow bot if running: same.
4. `UPDATE bots SET strategy_params = shadow.strategy_params, shadow_promoted_at = now() WHERE id = live_bot_id`.
5. Read current metrics snapshot for both bots (same query as `get_comparison`, window = `comparison_window_days`).
6. `INSERT INTO shadow_promotion_events` with both metric snapshots.
7. `UPDATE bots SET status = 'deleted', is_shadow = false WHERE id = shadow_bot_id` — soft-delete; `bot_runs`/`bot_orders` rows survive for audit.
8. Restart live bot: `BotSupervisor.start(live_bot_id)`.
9. Publish `bot:shadow:{live_bot_id}` frame `{type:"promoted", shadow_bot_id, promoted_by}`.
10. Metrics: `shadow_promoter_promoted_total`.

**On transaction failure:** `shadow_promoter_promote_failures_total` incremented. Live bot left in stopped state (operator must restart manually). Shadow bot left in its prior state (not deleted). Both failures are surfaced in the 500 response body.

#### `check_auto_promote_eligibility(live_bot_id, db) → bool`

**Stub — always returns `False`.** Wiring is in place for a future phase to read `bots.auto_promote_config JSONB` and evaluate thresholds. The APScheduler job calls this daily.

### 6.3 APScheduler jobs

**`shadow_comparison_notify`** — daily at 08:00. For every live bot with `is_shadow=False` that has at least one active shadow bot (`is_shadow=True, shadow_of=live_bot_id, status='running'`) running for `>= comparison_window_days` days: publish `bot:shadow:{live_bot_id}` frame `{type:"comparison_ready"}`. FE surfaces a "Review shadow performance" banner.

**`shadow_auto_promote_check`** — daily at 08:05. For every live bot with active shadow bots: calls `check_auto_promote_eligibility()`. Always a no-op in 21b.

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

**`/override_{decision_id}` Telegram command:**
- Registered as a dynamic command handler (same pattern as Phase 11d `/confirm`).
- `decision_id` is a BIGINT; validated as numeric before any DB call (injection guard).
- Calls `PATCH /api/bots/{bot_id}/advisor-decisions/{decision_id}` override endpoint internally (Phase 21a.1) with `override_action='approve'` and `override_reason=f"telegram_override:{from_user_id}"`.
- Requires `from_user_id` in `app_config[telegram/allowlist]`.
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
3. Latency measured via `time.monotonic()` around `stub.review()` — context-build time only.

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

**WS endpoint:** `GET /ws/bots/{id}/tuner` — per-bot, 50-conn cap, pubsub `bot:tuner:{bot_id}`, 500ms conflation.

### `bot:shadow:{live_bot_id}` pubsub channel

| Frame type | Fields | When |
|---|---|---|
| `comparison_ready` | `shadow_bot_ids: [uuid]` | APScheduler daily notify |
| `promoted` | `shadow_bot_id, promoted_by` | On promotion |

**WS endpoint:** `GET /ws/bots/{id}/shadow` — per-bot, 50-conn cap, pubsub `bot:shadow:{bot_id}`.

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
| **D — Shadow-promoter service + APScheduler** | `shadow_promoter/types.py`, `shadow_promoter/service.py`, `shadow_promoter/metrics.py`, APScheduler wiring, `BotContext` `is_shadow` guard, tests | Codex | after A |
| **E — Advisor extensions** | `advisor/context_builder.py` (filings/earnings), `AdvisorConfig.notify_telegram`, `telegram/advisor_notify.py`, tests | Codex | after A |
| **F — Advisor-in-backtest** | `backtest/advisor_stub.py`, `backtest/runner.py`, `BacktestReportKpis` extension, tests | Qwen | after A |
| **G — REST + WS API** | `api/bots.py` (param-tuner + shadow endpoints), `api/ws_bots.py` (tuner + shadow WS), tests | Codex | after C + D |
| **H — Frontend** | All FE components + hooks + services, `BacktestPage` dual curve, `BacktestConfigForm` | Codex | after G + F |
| **I — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.21.2 | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku) on all. Chunk A: + database-reviewer (sonnet). Chunks C + D + E + G: + security-reviewer (sonnet). Chunk H: + typescript-reviewer (haiku). Phase end: ARCHITECT-REVIEW (opus).

---

## 14. Deferred

| Item | Target |
|---|---|
| Auto-promote logic (`bots.auto_promote_config` column + threshold evaluation) | Beyond 21b |
| Staged allocation (10% → 50% → 100%) | Beyond 21b |
| Real AI calls during backtest replay | Not planned (deterministic stub is intentional) |
| Advisor perf-attribution ("was the advisor right?") | Phase 21c |
| Multi-bot orchestration | Phase 22 |
| `bot_param_suggestions` → hypertable | Phase 24 |
| Fleet-wide param-tuner cost ceiling | Phase 22 |
| Param-tuner for shadow bots (tune a shadow independently) | Beyond 21b |
