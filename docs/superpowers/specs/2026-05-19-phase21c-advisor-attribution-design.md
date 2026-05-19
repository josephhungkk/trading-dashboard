# Phase 21c — Advisor Perf-Attribution: "Was the Advisor Right?" (v0.21.3)

**Date:** 2026-05-19
**Status:** Brainstorm approved — ready for /writing-plans
**Builds on:** Phase 21a (LLM advisor, v0.21.0) · Phase 21a.1 (advisor polish, v0.21.1) · Phase 21b (LLM-in-loop, v0.21.2) · Phase 9 (bar aggregator + historical store)
**Next phases:** Phase 22 (multi-bot orchestration)

---

## 1. Goal

Answer "was the advisor right?" for every `bot_advisor_decisions` row. For **veto** decisions: simulate what the order outcome would have been using `bars_1m` price data. For **approve** decisions: use the actual fill P&L via the `bot_orders` FK. Compute outcomes across multiple time windows (15m/1h/4h/EOD). Surface rolling accuracy stats in a new `AdvisorScoreCard` component on `BotDetailPage`.

---

## 2. Scope

### In scope
- Alembic 0068: outcome columns + `attribution_status` on `bot_advisor_decisions`; `advisor_decision_id` FK on `bot_orders`.
- `app/services/advisor/attribution.py` — `AttributionService` (poll + summary).
- APScheduler job wired in `main.py` with configurable poll interval.
- 3 new Prometheus metrics.
- `BotContext.place_order` populates `bot_orders.advisor_decision_id` on approve.
- REST: `GET /api/bots/{id}/advisor-attribution` + widen `AdvisorDecisionResponse` with outcome fields.
- FE: `AdvisorScoreCard` on overview tab; `AdvisorDecisionsTable` outcome columns; single outcome line in `AdvisorDecisionDrawer`.

### Explicitly out of scope
- LLM re-evaluation of attribution ("why was the advisor wrong?") — beyond 21c.
- Attribution for `fail_open` verdicts — excluded (no clear ground truth).
- Real-time outcome streaming — poller is sufficient; no new WS channel.
- Attribution for `backtest_advisor_decisions` (Phase 21b) — deferred; no `bars_1m` linkage in backtest context.

---

## 3. Architecture overview

```
APScheduler (configurable interval, default 900s)
  └── AttributionService.poll(db)
        ├── Query bot_advisor_decisions WHERE attribution_status IN ('pending','partial')
        │     AND created_at >= now() - max_lookback_days
        ├── For each matured window per decision:
        │   ├── VETO: look up bars_1m → simulate fill at next-bar open → compute pnl
        │   └── APPROVE: join bot_orders via advisor_decision_id → use fill_price
        └── UPDATE outcome columns + attribution_status

GET /api/bots/{id}/advisor-attribution
  └── AttributionService.get_summary(bot_id, window, db)
        └── Aggregate complete decisions → AttributionSummary

FE: AdvisorScoreCard (overview tab)
  └── TanStack Query GET /advisor-attribution, 60s stale
AdvisorDecisionsTable
  └── outcome_*_correct columns → ✓/✗ badges
AdvisorDecisionDrawer
  └── Single outcome line when attribution_status='complete'
```

---

## 4. Data model

### Alembic 0068

```sql
-- Outcome columns + status on bot_advisor_decisions (hypertable from Phase 21a)
ALTER TABLE bot_advisor_decisions
    ADD COLUMN attribution_status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (attribution_status IN ('pending','partial','complete','bars_unavailable')),
    ADD COLUMN outcome_15m_correct      BOOL,
    ADD COLUMN outcome_15m_pnl          NUMERIC(20,8),
    ADD COLUMN outcome_1h_correct       BOOL,
    ADD COLUMN outcome_1h_pnl           NUMERIC(20,8),
    ADD COLUMN outcome_4h_correct       BOOL,
    ADD COLUMN outcome_4h_pnl           NUMERIC(20,8),
    ADD COLUMN outcome_eod_correct      BOOL,
    ADD COLUMN outcome_eod_pnl          NUMERIC(20,8),
    ADD COLUMN attribution_computed_at  TIMESTAMPTZ;

-- Index for poll query
CREATE INDEX bot_advisor_decisions_attribution_status_created_at_idx
    ON bot_advisor_decisions (attribution_status, created_at DESC)
    WHERE attribution_status IN ('pending', 'partial');

-- FK from bot_orders to bot_advisor_decisions
-- ON DELETE SET NULL: decision row deletion (rare) does not orphan orders
ALTER TABLE bot_orders
    ADD COLUMN advisor_decision_id BIGINT
        REFERENCES bot_advisor_decisions(id) ON DELETE SET NULL;

CREATE INDEX bot_orders_advisor_decision_id_idx
    ON bot_orders (advisor_decision_id)
    WHERE advisor_decision_id IS NOT NULL;
```

**Attribution status transitions:**
- `pending` → `partial`: at least one window outcome filled in
- `partial` → `complete`: all configured windows have non-NULL outcome columns
- `pending` → `bars_unavailable`: `bars_1m` has no rows for this `canonical_id` at the decision timestamp

**Configurable windows:** `app_config[advisor_attribution/windows]` (default `["15m","1h","4h","eod"]`). Poller reads this and only computes enabled windows; unused columns stay NULL and do not block `complete` status.

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

1. Read enabled windows from `app_config[advisor_attribution/windows]` (default `["15m","1h","4h","eod"]`). Parse to `timedelta` (EOD = seconds until market close, computed via `MarketCalendar` for the instrument's exchange; falls back to 18:00 UTC if exchange unknown).
2. Query `bot_advisor_decisions WHERE attribution_status IN ('pending','partial') AND verdict IN ('approve','veto') AND created_at >= now() - interval '{max_lookback_days} days'`. Batch size: 500 rows per poll tick (prevents long-running transactions on the hypertable).
3. For each decision:
   a. Determine which windows have matured: `window_matured = (now() - created_at) >= window_duration`.
   b. For each matured window where the outcome column is NULL:
      - Fetch `bars_1m` rows for `canonical_id` in `[created_at, created_at + window_duration + 5min]` (5min buffer for late bar writes). If zero rows found and `created_at < now() - 24h` → skip this window (leave column NULL, set no outcome). If ALL enabled windows have no bars and all are old enough → mark `attribution_status='bars_unavailable'` for the whole decision. Per-window bar absence does not block other windows from computing.
      - **Entry price:** first bar's `open` at or after `created_at` (next-bar open). For approve decisions with a matching `bot_orders` fill, use `fill_price` instead.
      - **Exit price:** last bar's `close` at or before `created_at + window_duration`.
      - `side_sign = 1` if `intent['side'] in ('buy','BUY')` else `-1`.
      - `qty = Decimal(intent['qty'])`.
      - `simulated_pnl = (exit_price - entry_price) * qty * side_sign`.
      - **Veto:** `correct = simulated_pnl < 0` (veto right if trade would have lost money).
      - **Approve:** `correct = simulated_pnl > 0` (approve right if trade made money).
      - Write outcome column pair for this window.
   c. Recompute `attribution_status`:
      - All enabled window columns non-NULL → `complete`.
      - At least one non-NULL → `partial`.
      - All still NULL (no windows matured yet) → `pending`.
   d. Set `attribution_computed_at = now()`.
4. Bulk UPDATE in a single transaction per batch of 500.
5. Metrics: `advisor_attribution_decisions_processed_total` (counter), `advisor_attribution_bars_unavailable_total` (counter), `advisor_attribution_poll_latency_seconds` (histogram).

**Fail-OPEN:** any exception during a single decision's computation is logged and skipped; the decision stays in its current status. Next poll will retry.

### 5.2 `get_summary(bot_id, window, db) → AttributionSummary`

Pure read. Aggregates `bot_advisor_decisions WHERE bot_id = bot_id AND attribution_status = 'complete' AND verdict IN ('veto','approve')` over the specified window column:

```python
class AttributionSummary(BaseModel):
    bot_id: UUID
    window: str                        # "15m" | "1h" | "4h" | "eod"
    veto_accuracy: float | None        # None if no complete veto decisions
    approve_accuracy: float | None     # None if no complete approve decisions
    avg_avoided_loss_usd: Decimal | None   # mean pnl of correct vetoes (positive = money saved)
    avg_missed_gain_usd: Decimal | None    # mean |pnl| of incorrect vetoes (positive = opportunity cost)
    total_decisions: int
    complete_count: int
    pending_count: int
    bars_unavailable_count: int
    generated_at: datetime
```

`veto_accuracy = COUNT(verdict='veto' AND outcome_{window}_correct=True) / COUNT(verdict='veto' AND outcome_{window}_correct IS NOT NULL)`.

`avg_avoided_loss_usd`: mean of `ABS(outcome_{window}_pnl)` where `verdict='veto' AND outcome_{window}_correct=True`. Positive value = money the advisor saved.
`avg_missed_gain_usd`: mean of `ABS(outcome_{window}_pnl)` where `verdict='veto' AND outcome_{window}_correct=False`. Positive value = opportunity cost of wrong vetoes.

### 5.3 `app_config` keys

| Key | Default | Description |
|---|---|---|
| `advisor_attribution/enabled` | `true` | Kill switch for APScheduler job |
| `advisor_attribution/poll_interval_seconds` | `900` | APScheduler interval |
| `advisor_attribution/windows` | `["15m","1h","4h","eod"]` | Which windows to compute |
| `advisor_attribution/max_lookback_days` | `7` | How far back the poll scans |

### 5.4 Prometheus metrics (3)

| Metric | Type | Labels |
|---|---|---|
| `advisor_attribution_decisions_processed_total` | Counter | `verdict` (`veto\|approve`) |
| `advisor_attribution_bars_unavailable_total` | Counter | — |
| `advisor_attribution_poll_latency_seconds` | Histogram | — |

---

## 6. BotContext.place_order change

In `app/bot/context.py`, after the advisor approves an order and `place_order_for_bot()` returns the `bot_orders` row ID:

```python
# Populate FK for attribution tracking
if advisor_decision_id is not None:
    await db.execute(
        update(BotOrder)
        .where(BotOrder.id == bot_order_id)
        .values(advisor_decision_id=advisor_decision_id)
    )
```

`advisor_decision_id` is the `AdvisorDecision.id` returned by `AdvisorService.review()` when `verdict='approve'`. For `verdict='veto'` and `verdict='fail_open'`, no order is placed so no FK write occurs.

---

## 7. REST API

### New endpoint

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/bots/{id}/advisor-attribution` | JWT | `AttributionSummary` for a bot. Query param `?window=1h` (default `1h`). |

### Widened `AdvisorDecisionResponse` (additive, no breaking change)

```python
# New fields — all default to None/"pending" for backward compatibility
attribution_status: str = "pending"
outcome_15m_correct: bool | None = None
outcome_15m_pnl: Decimal | None = None
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

- Reads `GET /api/bots/{id}/advisor-attribution` via TanStack Query, 60s stale time.
- Window selector dropdown (15m / 1h / 4h / EOD); state local to component, default `1h`.
- Shows: veto accuracy %, approve accuracy % (both as progress bars), avg avoided loss (green), avg missed gain (red), complete/pending counts, last updated time.
- When `complete_count = 0`: renders "No attribution data yet — outcomes computed after window elapses."
- Hidden entirely when bot has `advisor_config.mode = 'OFF'`.

### 8.2 `AdvisorDecisionsTable` outcome columns

- Adds ✓ (green) / ✗ (red) / `—` (pending/unavailable) column for each enabled window.
- Columns hidden (not rendered) when all visible decisions have `attribution_status = 'pending'` — avoids a wall of `—` on fresh bots.
- Column header shows window label ("1h").

### 8.3 `AdvisorDecisionDrawer` outcome line

Single line at the bottom of the drawer, rendered only when `attribution_status = 'complete'`:

- Veto + correct: `"Outcome (1h): ✓ Avoided $31.20"`
- Veto + incorrect: `"Outcome (1h): ✗ Missed gain $8.60"`
- Approve + correct: `"Outcome (1h): ✓ Fill P&L +$24.10"`
- Approve + incorrect: `"Outcome (1h): ✗ Fill P&L -$9.30"`

Default window = `1h`. No window selector in drawer (full breakdown is in `AdvisorScoreCard`).

### 8.4 Service layer additions

**`frontend/src/services/advisor/types.ts`** — adds:
```typescript
export interface AttributionSummary {
  bot_id: string;
  window: string;
  veto_accuracy: number | null;
  approve_accuracy: number | null;
  avg_avoided_loss_usd: string | null;
  avg_missed_gain_usd: string | null;
  total_decisions: number;
  complete_count: number;
  pending_count: number;
  bars_unavailable_count: number;
  generated_at: string;
}

// Added to AdvisorDecision
attribution_status: 'pending' | 'partial' | 'complete' | 'bars_unavailable';
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

**`frontend/src/services/advisor/api.ts`** — adds `getAdvisorAttribution(botId: string, window: string): Promise<AttributionSummary>`.

---

## 9. Tests

### Backend (~25 new tests)

**Attribution service:**
- `test_attribution_poll_computes_veto_outcome`: veto decision + bars present → `outcome_1h_correct` set, `attribution_status='partial'`
- `test_attribution_poll_marks_complete_when_all_windows_done`: all 4 windows matured → `status='complete'`
- `test_attribution_poll_bars_unavailable`: no `bars_1m` rows for `canonical_id`, decision >24h old → `status='bars_unavailable'`
- `test_attribution_poll_skips_unmatured_windows`: decision 30min old → 1h/4h/EOD columns untouched
- `test_attribution_veto_correct_when_price_falls`: BUY veto, price fell → `correct=True`, `pnl < 0`
- `test_attribution_veto_incorrect_when_price_rises`: BUY veto, price rose → `correct=False`, `pnl > 0`
- `test_attribution_sell_veto_correct_when_price_rises`: SELL veto, price rose → `correct=True`
- `test_attribution_approve_uses_fill_price`: approve + `bot_orders` FK match → `entry = fill_price`
- `test_attribution_approve_fallback_no_fill`: approve + no fill row → falls back to next-bar-open price
- `test_attribution_summary_veto_accuracy`: 10 complete decisions, 7 correct vetoes → `veto_accuracy=0.7`
- `test_attribution_summary_pending_excluded`: pending decisions not counted in accuracy
- `test_attribution_summary_bars_unavailable_excluded`: unavailable decisions not counted
- `test_attribution_kill_switch_disabled`: `advisor_attribution/enabled=false` → poll exits immediately
- `test_attribution_7day_lookback_cap`: decision older than 7 days → excluded from poll batch
- `test_attribution_batch_size_500`: 600 pending decisions → first tick processes 500, second tick processes 100
- `test_attribution_fail_open_bad_bars`: malformed bar data for one decision → that decision skipped; others processed
- `test_bot_orders_fk_populated_on_approve`: `BotContext.place_order` after approve → `advisor_decision_id` set on `bot_orders` row
- `test_bot_orders_fk_null_on_veto`: veto → no `bot_orders` row created, no FK write

**REST API:**
- `test_advisor_attribution_endpoint_returns_summary`: `GET /api/bots/{id}/advisor-attribution` → 200 with `AttributionSummary`
- `test_advisor_attribution_window_param`: `?window=4h` → summary uses 4h columns
- `test_advisor_decision_response_includes_outcome_fields`: existing decisions endpoint includes new fields with defaults
- `test_advisor_attribution_no_complete_decisions`: `complete_count=0` → accuracies are None

### Frontend (~8 new tests)

- `AdvisorScoreCard`: renders accuracy bars and correct % values
- `AdvisorScoreCard`: window selector changes displayed window
- `AdvisorScoreCard`: shows "no attribution data" state when `complete_count = 0`
- `AdvisorScoreCard`: hidden when `advisor_config.mode = 'OFF'`
- `AdvisorDecisionsTable`: outcome columns hidden when all `pending`
- `AdvisorDecisionsTable`: ✓ renders green, ✗ renders red
- `AdvisorDecisionDrawer`: single outcome line renders when `attribution_status='complete'`
- `AdvisorDecisionDrawer`: no outcome line when `attribution_status='pending'`

---

## 10. Implementation chunks

| Chunk | Files | Route | Gate |
|---|---|---|---|
| **A — Schema** | Alembic 0068 (`bot_advisor_decisions` outcome cols + status; `bot_orders.advisor_decision_id` FK), migration tests | Qwen | — |
| **B — Attribution service** | `advisor/attribution.py`, `advisor/types.py` (new types), unit tests | Qwen | after A |
| **C — APScheduler + metrics + FK write** | `main.py` APScheduler job wiring, `advisor/metrics.py` (3 new metrics), `bot/context.py` FK write, integration tests | Codex | after B |
| **D — REST API** | `api/bots.py` (attribution endpoint + widen `AdvisorDecisionResponse`), tests | Qwen | after B |
| **E — Frontend** | `AdvisorScoreCard`, `AdvisorDecisionsTable` outcome columns, drawer outcome line, `services/advisor/types.ts` + `api.ts` additions | Codex | after D |
| **F — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.21.3 | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku). Chunk A: + database-reviewer (sonnet). Chunk C: + security-reviewer (sonnet). Chunk E: + typescript-reviewer (haiku). Phase end: ARCHITECT-REVIEW (opus).

---

## 11. Phase 21a invariants preserved

| Invariant | This phase's implementation |
|---|---|
| **Fail-OPEN** | Attribution poll errors skip the offending decision and continue; never blocks order flow |
| **No new money-moving paths** | Attribution is read-only on `bars_1m` + `bot_orders`; no orders placed or modified |
| **Kill switch** | `app_config[advisor_attribution/enabled]` (default `true`); admin can disable without deploy |
| **Hypertable discipline** | `bot_advisor_decisions` is a hypertable — ALTER TABLE adds columns to all chunks; migration must use `ALTER TABLE` not `CREATE TABLE AS` |

---

## 12. Deferred

| Item | Target |
|---|---|
| LLM re-evaluation of attribution ("why was the advisor wrong?") | Beyond 21c |
| Attribution for `fail_open` verdicts | Beyond 21c |
| Attribution for `backtest_advisor_decisions` (Phase 21b) | Beyond 21c |
| Real-time WS push of attribution updates | Beyond 21c |
| Attribution for shadow bots (Phase 21b) | Beyond 21c |
| Auto-advisor-tuning based on attribution signal | Phase 22+ |
