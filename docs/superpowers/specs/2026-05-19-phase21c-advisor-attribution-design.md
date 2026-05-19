# Phase 21c — Advisor Perf-Attribution: "Was the Advisor Right?" (v0.21.3)

**Date:** 2026-05-19
**Status:** ARCHITECT-REVIEW applied (2 CRIT + 4 HIGH + 6 MED inline) — ready for /writing-plans
**Builds on:** Phase 21a (LLM advisor, v0.21.0) · Phase 21a.1 (advisor polish, v0.21.1) · Phase 21b (LLM-in-loop, v0.21.2) · Phase 9 (bar aggregator + historical store)
**Next phases:** Phase 22 (multi-bot orchestration)

**ARCHITECT-REVIEW applied:** 2 CRIT + 4 HIGH + 6 MED inline. 3 LOWs noted below.

---

## 1. Goal

Answer "was the advisor right?" for every `bot_advisor_decisions` row. For **veto** decisions: simulate what the order outcome would have been using `bars_1m` price data (keyed on `instrument_id`, resolved from `canonical_id`). For **approve** decisions: use the same next-bar-open price lookback (FK to `bot_orders` is provenance-only, not pricing — HIGH-1). Compute outcomes across multiple time windows (15m/1h/4h/EOD). Surface rolling accuracy stats in a new `AdvisorScoreCard` component on `BotDetailPage`.

---

## 2. Scope

### In scope
- Alembic 0068: outcome columns + `attribution_status` on `bot_advisor_decisions`; `advisor_decision_id` FK on `bot_orders`.
- `app/services/advisor/attribution.py` — `AttributionService` (poll + summary + recompute).
- APScheduler job wired in `main.py` with configurable poll interval.
- 4 new Prometheus metrics (1 added vs brainstorm for instrument-unresolvable case).
- `BotContext.place_order` populates `bot_orders.advisor_decision_id` inline in the existing INSERT (MED-1).
- REST: `GET /api/bots/{id}/advisor-attribution` + `POST /api/bots/{id}/advisor-attribution/recompute` + widen `AdvisorDecisionResponse` with outcome fields.
- FE: `AdvisorScoreCard` on overview tab; `AdvisorDecisionsTable` outcome columns; single outcome line in `AdvisorDecisionDrawer`.

### Explicitly out of scope
- LLM re-evaluation of attribution ("why was the advisor wrong?") — beyond 21c.
- Attribution for `fail_open` verdicts — excluded (no clear ground truth).
- Real-time outcome streaming — poller is sufficient; no new WS channel.
- Attribution for `backtest_advisor_decisions` (Phase 21b) — deferred; no `bars_1m` linkage in backtest context.
- FX conversion to USD for `avg_avoided_loss` / `avg_missed_gain` — deferred to 21c.1 patch; values reported in instrument-native quote currency (HIGH-3).

---

## 3. Architecture overview

```
APScheduler (configurable interval, default 900s)
  └── AttributionService.poll(db)
        ├── SELECT FOR UPDATE SKIP LOCKED LIMIT 500 — concurrency-safe claim
        ├── Resolve canonical_id → instrument_id via InstrumentResolver (Redis TTL cache)
        ├── For each matured window per decision:
        │   ├── VETO:    bars_1m WHERE instrument_id → next-bar-open entry, window close exit
        │   └── APPROVE: same price-lookback (FK is provenance only, not pricing)
        │       simulated_pnl = (exit - entry) * qty * multiplier * side_sign
        └── UPDATE outcome columns + attribution_status

GET  /api/bots/{id}/advisor-attribution       → AttributionSummary
POST /api/bots/{id}/advisor-attribution/recompute → reset attribution_status to 'pending'

FE: AdvisorScoreCard (overview tab, 300s stale time)
AdvisorDecisionsTable → outcome_*_correct ✓/✗ columns
AdvisorDecisionDrawer → single outcome line when complete
```

---

## 4. Data model

### Alembic 0068

**CRIT-2 note:** `bot_advisor_decisions` is a **plain table** (not a hypertable — verified: no `create_hypertable` call in alembic 0063 or 0064). `ALTER TABLE` adds columns directly with no chunk considerations.

```sql
-- Outcome columns + status on bot_advisor_decisions (plain table)
ALTER TABLE bot_advisor_decisions
    ADD COLUMN attribution_status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (attribution_status IN ('pending','partial','complete','bars_unavailable','unresolvable')),
    -- 'unresolvable': canonical_id has no matching instrument_id in instruments table (permanent skip)
    -- 'bars_unavailable': instrument resolves but bars_1m has no data for the window (illiquid/delisted)
    ADD COLUMN outcome_15m_correct      BOOL,
    ADD COLUMN outcome_15m_pnl          NUMERIC(20,8),
    ADD COLUMN outcome_1h_correct       BOOL,
    ADD COLUMN outcome_1h_pnl           NUMERIC(20,8),
    ADD COLUMN outcome_4h_correct       BOOL,
    ADD COLUMN outcome_4h_pnl           NUMERIC(20,8),
    ADD COLUMN outcome_eod_correct      BOOL,
    ADD COLUMN outcome_eod_pnl          NUMERIC(20,8),
    -- pnl columns are in instrument-native quote currency (not USD); FX conversion deferred to 21c.1
    ADD COLUMN attribution_computed_at  TIMESTAMPTZ;

-- Index for poll query (partial index keeps it cheap on the large table)
CREATE INDEX bot_advisor_decisions_attribution_status_created_at_idx
    ON bot_advisor_decisions (attribution_status, created_at DESC)
    WHERE attribution_status IN ('pending', 'partial');

-- FK from bot_orders to bot_advisor_decisions (provenance only — not used for pricing)
-- ON DELETE SET NULL: decision row deletion does not orphan orders
ALTER TABLE bot_orders
    ADD COLUMN advisor_decision_id BIGINT
        REFERENCES bot_advisor_decisions(id) ON DELETE SET NULL;

CREATE INDEX bot_orders_advisor_decision_id_idx
    ON bot_orders (advisor_decision_id)
    WHERE advisor_decision_id IS NOT NULL;
```

**Attribution status transitions:**
- `pending` → `partial`: at least one window outcome filled in
- `partial` → `complete`: all currently-enabled windows have non-NULL outcome columns
- `pending`/`partial` → `bars_unavailable`: instrument resolves but ALL enabled windows lack bar data and all are old enough (> 24h past window maturity)
- `pending` → `unresolvable`: `canonical_id` has no `instrument_id` in `instruments` table (permanent; no retry)
- `partial` at lookback expiry → forced to `complete` with whatever windows filled in (MED-5: decisions in `partial` state when they age past `max_lookback_days` are promoted to `complete` so they don't bias `pending_count` forever)

**Window completeness rule (MED-2):** `complete` status is evaluated against the windows that were enabled **at the time the first window was computed** (stored in `attribution_windows TEXT[]` column below). Adding a new window to `app_config` after a row is already `complete` does not reopen it — new decisions pick up the new window; old decisions require the recompute endpoint.

```sql
ALTER TABLE bot_advisor_decisions
    ADD COLUMN attribution_windows TEXT[];
-- Populated at first poll tick for this decision (snapshot of app_config windows at compute time).
-- NULL on rows created before 0068 — treated as ["15m","1h","4h","eod"] for backcompat.
```

**Configurable windows:** `app_config[advisor_attribution/windows]` (default `["15m","1h","4h","eod"]`). Poller reads this; unused columns stay NULL.

---

## 5. Attribution Service

### `app/services/advisor/attribution.py`

```python
class AttributionService:
    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        redis: Any,
    ) -> None: ...
```

### 5.1 `poll(db)` — APScheduler entrypoint

**Kill switch:** read `app_config[advisor_attribution/enabled]`. If `False` → exit immediately.

**Window parsing:**

| Config value | `timedelta` | EOD rule |
|---|---|---|
| `"15m"` | 15 minutes | — |
| `"1h"` | 1 hour | — |
| `"4h"` | 4 hours | — |
| `"eod"` | computed (see below) | Decision day's session close |

**EOD window definition (HIGH-4):**
- EOD = the close of the *trading session on the day the decision was made* (`created_at` date in the instrument's exchange timezone).
- Computed via `MarketCalendar.session_close(exchange, date)`. Exchange read from `instruments.exchange` (resolved alongside `instrument_id`).
- If `created_at` is **after the session close** (after-hours decision): EOD = the *next* session's close.
- **`min_eod_buffer`:** if `session_close - created_at < app_config[advisor_attribution/min_eod_buffer_minutes]` (default 30 min), the EOD window is **skipped** for this decision — a 5-minute window carries no meaningful signal.
- If exchange is unknown: `attribution_status='unresolvable'` (same as no instrument_id) — **no UTC fallback** (HIGH-4). Metric `advisor_attribution_unresolvable_total{reason="unknown_exchange"}`.

**Poll steps:**

1. Read enabled windows from `app_config[advisor_attribution/windows]`. Parse per table above.
2. `SELECT ... FROM bot_advisor_decisions WHERE attribution_status IN ('pending','partial') AND verdict IN ('approve','veto') AND created_at >= now() - interval '{max_lookback_days} days' FOR UPDATE SKIP LOCKED LIMIT 500` (HIGH-2 — prevents double-processing under concurrent pollers or restarts).
3. For each decision:
   a. **Resolve `canonical_id` → `instrument_id`** (CRIT-1): call `InstrumentResolver.find_by_canonical_id(canonical_id)` (existing service, Redis TTL 3600s — canonical_id→instrument_id mapping is effectively immutable). If no match → set `attribution_status='unresolvable'`, `attribution_computed_at=now()`, skip. Metric `advisor_attribution_unresolvable_total{reason="no_instrument"}`.
   b. Look up `instruments.multiplier` (default `Decimal("1")` if NULL — handles stocks; options/futures will have real multiplier from Phase 12/14).
   c. Snapshot `attribution_windows` = current enabled windows (stored if this is the first compute tick for this row, i.e., `attribution_windows IS NULL`).
   d. Determine which windows have matured: `window_matured = (now() - created_at) >= window_duration`.
   e. For each matured window where the outcome column is NULL:
      - Fetch `bars_1m WHERE instrument_id = :iid AND bucket_start BETWEEN :t0 AND :t1` where `t0 = created_at`, `t1 = created_at + window_duration + interval '5 minutes'` (5-min buffer for late bar writes).
      - If zero rows and `created_at + window_duration < now() - interval '24 hours'`: skip this window (leave NULL). If ALL enabled windows across all maturity checks have no bars → mark `attribution_status='bars_unavailable'`. Per-window bar absence does not block other windows from computing (CRIT-1 fix: per-window, not per-decision blanket).
      - **Entry price (both veto and approve — HIGH-1):** first bar's `open` at or after `created_at` (next-bar open). The `bot_orders.advisor_decision_id` FK is provenance-only; pricing always uses bars.
      - **Exit price:** last bar's `close` at or before `created_at + window_duration`.
      - **Side and position effect (MED-6):** `side` from `intent['side']` is already lowercase per `OrderIntent`. `side_sign = 1` for `'buy'`, `-1` for `'sell'`. `position_effect = intent.get('position_effect', 'OPEN')`. If `position_effect == 'CLOSE'`, skip attribution for this decision (CLOSE orders flip economic direction; correctness depends on the original entry price which is outside this spec's scope). Metric `advisor_attribution_skipped_total{reason="close_position"}`.
      - `qty = Decimal(intent['qty'])`.
      - `multiplier = instruments.multiplier` (from step b).
      - `simulated_pnl = (exit_price - entry_price) * qty * multiplier * side_sign`.
      - **Veto:** `correct = simulated_pnl < 0` (veto right if trade would have lost money).
      - **Approve:** `correct = simulated_pnl > 0` (approve right if trade made money).
      - Write outcome column pair for this window. `outcome_{window}_pnl` is in instrument-native quote currency.
   f. Recompute `attribution_status` against `attribution_windows` snapshot:
      - `partial`-at-lookback-expiry check: if `created_at < now() - interval '{max_lookback_days} days'` and at least one window is non-NULL → force `complete` (MED-5).
      - All snapshotted window columns non-NULL → `complete`.
      - At least one non-NULL → `partial`.
      - All still NULL (no windows matured yet) → `pending`.
   g. Set `attribution_computed_at = now()`.
4. Bulk UPDATE in a single transaction per batch of 500. (The `FOR UPDATE SKIP LOCKED` lock is held for the duration of this transaction — keep the batch small to avoid lock contention.)
5. Metrics.

**Fail-OPEN:** any exception during a single decision's computation is logged (`structlog.exception`) and skipped; the decision stays in its current status. Next poll will retry.

### 5.2 `get_summary(bot_id, window, db) → AttributionSummary`

Pure read. `window` parameter is validated against `{"15m","1h","4h","eod"}` allowlist **before any SQL is constructed** (MED-3). Implementation uses a `match`/`case` dispatch to four separate parameterized queries — no f-string column name interpolation.

```python
class AttributionSummary(BaseModel):
    bot_id: UUID
    window: str                              # "15m" | "1h" | "4h" | "eod"
    veto_accuracy: float | None              # None if no complete veto decisions
    approve_accuracy: float | None           # None if no complete approve decisions
    avg_avoided_loss_quote: Decimal | None   # mean |pnl| of correct vetoes (instrument-native currency)
    avg_missed_gain_quote: Decimal | None    # mean |pnl| of incorrect vetoes (instrument-native currency)
    total_decisions: int
    complete_count: int
    pending_count: int
    bars_unavailable_count: int
    unresolvable_count: int
    generated_at: datetime
```

`veto_accuracy = COUNT(verdict='veto' AND outcome_{window}_correct=True) / COUNT(verdict='veto' AND outcome_{window}_correct IS NOT NULL)`.

`avg_avoided_loss_quote`: mean of `ABS(outcome_{window}_pnl)` where `verdict='veto' AND outcome_{window}_correct=True`. Positive = money the advisor saved (in quote currency, not USD).
`avg_missed_gain_quote`: mean of `ABS(outcome_{window}_pnl)` where `verdict='veto' AND outcome_{window}_correct=False`.

**Note on mixed-currency summary (HIGH-3):** when a bot trades instruments with different quote currencies (e.g., AAPL in USD and 0700.HK in HKD), the averages mix currencies and are only meaningful for single-currency bots. The FE `AdvisorScoreCard` renders the label "avg avoided loss (quote currency)" and omits the currency symbol. FX conversion deferred to 21c.1.

### 5.3 `recompute(bot_id, since, db)` — admin path (MED-4)

Resets `attribution_status='pending'` and NULLs all outcome columns and `attribution_windows` for decisions on this bot created since `since` (TIMESTAMPTZ). This allows operators to re-run attribution after a multiplier fix, a window config change, or a bug patch.

Exposed as `POST /api/bots/{id}/advisor-attribution/recompute` (admin JWT + CSRF nonce). Body: `{"since": "2026-05-01T00:00:00Z"}`. Max rows per call: 10,000 (prevents accidental full-table resets).

### 5.4 `app_config` keys

| Key | Default | Description |
|---|---|---|
| `advisor_attribution/enabled` | `true` | Kill switch for APScheduler job |
| `advisor_attribution/poll_interval_seconds` | `900` | APScheduler interval |
| `advisor_attribution/windows` | `["15m","1h","4h","eod"]` | Which windows to compute |
| `advisor_attribution/max_lookback_days` | `7` | How far back the poll scans |
| `advisor_attribution/min_eod_buffer_minutes` | `30` | Min minutes before close for EOD window to be meaningful |

### 5.5 Prometheus metrics (4)

| Metric | Type | Labels |
|---|---|---|
| `advisor_attribution_decisions_processed_total` | Counter | `verdict` (`veto\|approve`) |
| `advisor_attribution_bars_unavailable_total` | Counter | — |
| `advisor_attribution_unresolvable_total` | Counter | `reason` (`no_instrument\|unknown_exchange`) |
| `advisor_attribution_poll_latency_seconds` | Histogram | — |
| `advisor_attribution_skipped_total` | Counter | `reason` (`close_position\|eod_buffer`) |

---

## 6. BotContext.place_order change (MED-1)

In `app/bot/context.py`, extend the existing `bot_orders` INSERT to include `advisor_decision_id` directly — no separate UPDATE round-trip:

```python
# Existing INSERT extended with new column (verdict=='approve' only)
await db.execute(
    text(
        "INSERT INTO bot_orders "
        "(order_id, bot_id, account_id, placed_at, advisor_decision_id) "
        "VALUES (:oid, :bid, :aid, now(), :adv_id)"
    ),
    {
        "oid": order_id,
        "bid": bot_id,
        "aid": account_id,
        "adv_id": advisor_decision_id,  # None when verdict != 'approve'
    },
)
```

`advisor_decision_id` is `AdvisorDecision.id` (BIGINT) returned by `AdvisorService.review()` when `verdict='approve'`. It is `None` for `veto` and `fail_open` verdicts. The FK is `ON DELETE SET NULL` so rows survive advisor decision cleanup.

---

## 7. REST API

### Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/bots/{id}/advisor-attribution` | JWT | `AttributionSummary`. Query param `?window=1h` (default `1h`, validated against allowlist). |
| `POST` | `/api/bots/{id}/advisor-attribution/recompute` | admin JWT + CSRF | Reset attribution for decisions since `since` timestamp. Body: `{"since": "<iso8601>"}`. |

### Widened `AdvisorDecisionResponse` (additive, no breaking change)

```python
# New fields — all default to None/"pending" for backward compatibility
attribution_status: str = "pending"
outcome_15m_correct: bool | None = None
outcome_15m_pnl: Decimal | None = None    # quote currency
outcome_1h_correct: bool | None = None
outcome_1h_pnl: Decimal | None = None
outcome_4h_correct: bool | None = None
outcome_4h_pnl: Decimal | None = None
outcome_eod_correct: bool | None = None
outcome_eod_pnl: Decimal | None = None
attribution_computed_at: datetime | None = None
```

---

## 8. Frontend

### 8.1 `AdvisorScoreCard`

New component at `frontend/src/features/bots/components/AdvisorScoreCard.tsx`.

Placed on `BotDetailPage` overview tab, below the existing bot status section.

- Reads `GET /api/bots/{id}/advisor-attribution` via TanStack Query, **300s stale time** (LOW-3: poll runs every 900s; 60s was unnecessarily frequent).
- Window selector dropdown (15m / 1h / 4h / EOD); state local to component, default `1h`.
- Shows: veto accuracy %, approve accuracy % (both as progress bars), avg avoided loss (green, labelled "quote currency"), avg missed gain (red), complete/pending/unavailable counts, last updated time.
- When `complete_count = 0`: renders "No attribution data yet — outcomes computed after window elapses."
- Hidden entirely when bot has `advisor_config.mode = 'OFF'`.
- **Naming (LOW-2):** component named `AdvisorScoreCard` (user-approved); alias `AdvisorAttributionCard` may be added as a re-export for consistency with corpus.

### 8.2 `AdvisorDecisionsTable` outcome columns

- Adds ✓ (green) / ✗ (red) / `—` (pending/unavailable) column for the **default window only** (1h) to keep the table narrow. Full window breakdown is in `AdvisorScoreCard`.
- Column hidden (not rendered) when all visible decisions have `attribution_status IN ('pending','unresolvable')`.
- Column header: "Outcome (1h)".

### 8.3 `AdvisorDecisionDrawer` outcome line

Single line at the bottom of the drawer, rendered only when `attribution_status = 'complete'`:

- Veto + correct: `"Outcome (1h): ✓ Avoided 31.20 (quote)"`
- Veto + incorrect: `"Outcome (1h): ✗ Missed gain 8.60 (quote)"`
- Approve + correct: `"Outcome (1h): ✓ +24.10 (quote)"`
- Approve + incorrect: `"Outcome (1h): ✗ -9.30 (quote)"`

"(quote)" renders as a tooltip: "Amount in instrument's quote currency. USD conversion coming in v0.21.3.1."

Default window = `1h`. No window selector in drawer.

### 8.4 Service layer additions

**`frontend/src/services/advisor/types.ts`** — adds:
```typescript
export interface AttributionSummary {
  bot_id: string;
  window: string;
  veto_accuracy: number | null;
  approve_accuracy: number | null;
  avg_avoided_loss_quote: string | null;  // renamed from _usd (HIGH-3)
  avg_missed_gain_quote: string | null;
  total_decisions: number;
  complete_count: number;
  pending_count: number;
  bars_unavailable_count: number;
  unresolvable_count: number;
  generated_at: string;
}

// Added to AdvisorDecision interface
attribution_status: 'pending' | 'partial' | 'complete' | 'bars_unavailable' | 'unresolvable';
outcome_15m_correct: boolean | null;
outcome_15m_pnl: string | null;
outcome_1h_correct: boolean | null;
outcome_1h_pnl: string | null;
outcome_4h_correct: boolean | null;
outcome_4h_pnl: string | null;
outcome_eod_correct: boolean | null;
outcome_eod_pnl: string | null;
attribution_computed_at: string | null;
```

**`frontend/src/services/advisor/api.ts`** — adds:
- `getAdvisorAttribution(botId: string, window: string): Promise<AttributionSummary>`
- `recomputeAttribution(botId: string, since: string): Promise<void>`

---

## 9. Tests

### Backend (~40 new tests — LOW-1: expanded from 25 to cover matrix)

**Attribution service — core:**
- `test_attribution_poll_computes_veto_outcome_buy`: BUY veto + bars present → `outcome_1h_correct=True`, pnl < 0, `attribution_status='partial'`
- `test_attribution_poll_computes_veto_outcome_sell`: SELL veto + price falls → `outcome_1h_correct=False`
- `test_attribution_poll_computes_approve_outcome`: approve + bars → `outcome_1h_correct=True`, `attribution_status='partial'`
- `test_attribution_poll_marks_complete_when_all_windows_done`: all 4 windows matured → `status='complete'`
- `test_attribution_poll_skips_unmatured_windows`: decision 30min old → 1h/4h/EOD columns untouched
- `test_attribution_partial_at_expiry_forced_complete`: `partial` decision past `max_lookback_days` → forced `complete`

**Attribution service — instrument resolution (CRIT-1):**
- `test_attribution_unresolvable_no_instrument`: canonical_id not in instruments → `status='unresolvable'`; metric incremented
- `test_attribution_instrument_resolved_via_cache`: second poll tick uses Redis cache (no DB query)

**Attribution service — bars:**
- `test_attribution_bars_unavailable_all_windows`: no bars_1m rows for instrument, all windows old → `status='bars_unavailable'`
- `test_attribution_per_window_bar_absence_does_not_block_others`: 1h has no bars but 4h does → 1h stays NULL, 4h computes

**Attribution service — pnl formula (HIGH-3):**
- `test_attribution_pnl_applies_multiplier_options`: multiplier=100 → pnl = (exit-entry)*qty*100
- `test_attribution_pnl_applies_multiplier_futures`: multiplier=50 → pnl correct
- `test_attribution_pnl_multiplier_default_one_for_stocks`: instruments.multiplier=NULL → treated as 1

**Attribution service — EOD window (HIGH-4):**
- `test_attribution_eod_decision_before_close`: normal intraday decision → EOD = same day close
- `test_attribution_eod_decision_after_close`: after-hours decision → EOD = next session close
- `test_attribution_eod_skipped_within_buffer`: decision 10min before close → EOD skipped; metric `eod_buffer`
- `test_attribution_eod_unknown_exchange_unresolvable`: exchange unknown → `status='unresolvable'`; no UTC fallback

**Attribution service — CLOSE position (MED-6):**
- `test_attribution_close_position_skipped`: `position_effect='CLOSE'` → skipped; metric `close_position`

**Attribution service — concurrency (HIGH-2):**
- `test_attribution_for_update_skip_locked`: two concurrent pollers don't double-process same rows

**Attribution service — window snapshot (MED-2):**
- `test_attribution_windows_snapshot_stored_on_first_compute`: `attribution_windows` set at first tick
- `test_attribution_complete_uses_snapshot_not_current_config`: config changes mid-flight don't reopen `complete` rows

**Attribution service — recompute (MED-4):**
- `test_attribution_recompute_resets_status`: recompute → `pending` + NULLed outcome columns
- `test_attribution_recompute_max_rows_cap`: >10000 rows → 422

**Summary:**
- `test_attribution_summary_veto_accuracy`: 10 complete decisions, 7 correct vetoes → `veto_accuracy=0.7`
- `test_attribution_summary_pending_excluded`: pending decisions not counted
- `test_attribution_summary_window_param_validated`: `?window=invalid` → 422 (no SQL)
- `test_attribution_summary_no_complete_decisions`: `complete_count=0` → accuracies are None

**Kill switch / lookback:**
- `test_attribution_kill_switch_disabled`: `enabled=false` → poll exits immediately
- `test_attribution_7day_lookback_cap`: decision older than 7 days → excluded from poll
- `test_attribution_batch_size_500`: 600 pending decisions → first tick processes 500

**BotContext FK write (MED-1):**
- `test_bot_orders_fk_populated_on_approve_in_insert`: single INSERT sets `advisor_decision_id` — no UPDATE roundtrip
- `test_bot_orders_fk_null_on_veto`: veto → `advisor_decision_id=NULL`

**REST API:**
- `test_advisor_attribution_endpoint_returns_summary`
- `test_advisor_attribution_recompute_endpoint`: POST recompute → 200; decisions reset
- `test_advisor_decision_response_includes_outcome_fields`: existing decisions endpoint backward-compatible

### Frontend (~9 new tests)

- `AdvisorScoreCard`: renders accuracy bars and correct % values
- `AdvisorScoreCard`: window selector changes displayed window
- `AdvisorScoreCard`: shows "no attribution data" state when `complete_count = 0`
- `AdvisorScoreCard`: hidden when `advisor_config.mode = 'OFF'`
- `AdvisorScoreCard`: stale time is 300_000ms
- `AdvisorDecisionsTable`: outcome column hidden when all `pending`/`unresolvable`
- `AdvisorDecisionsTable`: ✓ renders green, ✗ renders red
- `AdvisorDecisionDrawer`: single outcome line renders when `attribution_status='complete'`
- `AdvisorDecisionDrawer`: no outcome line when `attribution_status='pending'`

---

## 10. Implementation chunks

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0068 (`bot_advisor_decisions` outcome cols + status + `attribution_windows`; `bot_orders.advisor_decision_id` FK), migration tests | Qwen | — |
| **B — Attribution service** | `advisor/attribution.py`, `advisor/types.py` (new types), unit tests (instrument resolution, pnl formula, EOD, CLOSE skip) | Qwen | after A |
| **C — APScheduler + metrics + FK write** | `main.py` APScheduler job wiring, `advisor/metrics.py` (4 new metrics + skipped counter), `bot/context.py` INSERT extension, integration tests | Codex | after B |
| **D — REST API** | `api/bots.py` (attribution + recompute endpoints, widen `AdvisorDecisionResponse`), tests | Qwen | after B |
| **E — Frontend** | `AdvisorScoreCard`, `AdvisorDecisionsTable` outcome column, drawer outcome line, `services/advisor/types.ts` + `api.ts` additions | Codex | after D |
| **F — Close-out** | CLAUDE.md (fix `bot_advisor_decisions` plain-table note), CHANGELOG.md, TASKS.md, tag v0.21.3 | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku). Chunk A: + database-reviewer (sonnet). Chunk C: + security-reviewer (sonnet). Chunk E: + typescript-reviewer (haiku). Phase end: ARCHITECT-REVIEW (opus).

---

## 11. Invariants preserved

| Invariant | This phase's implementation |
|---|---|
| **Fail-OPEN** | Attribution poll errors skip the offending decision; never blocks order flow |
| **No new money-moving paths** | Attribution is read-only on `bars_1m` + `instruments` + `bot_orders`; no orders placed or modified |
| **Kill switch** | `app_config[advisor_attribution/enabled]` (default `true`); admin can disable without deploy |
| **Plain-table discipline** | `bot_advisor_decisions` is a plain table (not hypertable — CRIT-2). `ALTER TABLE` applies directly. Close-out chunk F updates CLAUDE.md to correct the erroneous memory entry. |
| **No SQL injection** | `window` param validated against enum before any SQL; `match`/`case` dispatch to parameterized queries (MED-3) |

---

## 12. Resolved findings

| Finding | Resolution |
|---|---|
| CRIT-1: bars_1m keyed on instrument_id not canonical_id | §5.1 step 3a: resolve canonical_id → instrument_id via InstrumentResolver (Redis cache). Per-window bar queries use instrument_id. New `unresolvable` status + metric. |
| CRIT-2: bot_advisor_decisions is not a hypertable | §4 DDL comment + §11 invariant corrected. Close-out chunk F fixes CLAUDE.md. `unresolvable` added to status CHECK. |
| HIGH-1: approve pricing via fill_price broken for multi-fill/algo | Both approve and veto use next-bar-open price-lookback. FK is provenance-only. §1 goal + §5.1 step 3e updated. |
| HIGH-2: no UPDATE concurrency control | `SELECT … FOR UPDATE SKIP LOCKED LIMIT 500` in §5.1 step 2. |
| HIGH-3: pnl missing multiplier, wrong _usd naming | `simulated_pnl *= instruments.multiplier`. Fields renamed `*_quote`. FX conversion deferred to 21c.1. §5.2 model + §8.4 TS types updated. |
| HIGH-4: EOD definition ambiguous, UTC fallback | After-hours → next session close. `min_eod_buffer` skips near-close decisions. No UTC fallback → `unresolvable` when exchange unknown. §5.1 EOD table added. |
| MED-1: FK write as UPDATE, not INSERT | §6 extended existing INSERT with `advisor_decision_id` column. No second round-trip. |
| MED-2: complete status biased by config changes | `attribution_windows TEXT[]` column snapshots enabled windows at first compute. `complete` evaluated against snapshot. §4 + §5.1 step 3c updated. |
| MED-3: window param SQL injection | `window` validated against `{"15m","1h","4h","eod"}` allowlist before any SQL. `match`/`case` dispatch. §5.2 updated. |
| MED-4: no recompute path | `recompute()` method + `POST /api/bots/{id}/advisor-attribution/recompute` endpoint. §5.3 + §7 updated. |
| MED-5: partial-at-expiry stuck forever | Partial decisions past `max_lookback_days` forced to `complete`. §5.1 step 3f updated. |
| MED-6: CLOSE position sign ambiguity | `position_effect='CLOSE'` decisions skipped with metric `close_position`. Side already lowercase per OrderIntent. §5.1 step 3e updated. |
| LOW-1: test count low | Expanded to ~40 BE tests with parametrized matrix coverage. |
| LOW-2: naming consistency | `AdvisorScoreCard` kept (user-approved); alias noted in §8.1. |
| LOW-3: 60s stale time too tight | Changed to 300s (5 min) in §8.1. |

---

## 13. Deferred

| Item | Target |
|---|---|
| FX conversion to USD for avg_avoided_loss / avg_missed_gain | v0.21.3.1 patch |
| LLM re-evaluation of attribution ("why was the advisor wrong?") | Beyond 21c |
| Attribution for `fail_open` verdicts | Beyond 21c |
| Attribution for `backtest_advisor_decisions` (Phase 21b) | Beyond 21c |
| Real-time WS push of attribution updates | Beyond 21c |
| Attribution for shadow bots (Phase 21b) | Beyond 21c |
| Auto-advisor-tuning based on attribution signal | Phase 22+ |
| CLOSE-position attribution (sign-flip accounting) | Beyond 21c |
| bot_advisor_decisions → hypertable migration with retention | Phase 24 |
