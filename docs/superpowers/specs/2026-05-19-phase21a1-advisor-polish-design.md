# Phase 21a.1 — Advisor Polish (v0.21.1)

**Date:** 2026-05-19  
**Status:** Brainstorm approved — ready for /writing-plans  
**Builds on:** Phase 21a (LLM advisor, v0.21.0)  
**Next phases:** 21b (param-tuning + shadow-promotion + full LLM-in-loop)

---

## 1. Goal

Close the four items explicitly deferred from Phase 21a:

1. **SHADOW mode** — full advisor pipeline without AI cost; wire-test and overhead measurement.
2. **Async-parallel advisor** — lift the 1-at-a-time in-flight lock to a configurable semaphore.
3. **Live human veto override** — operator can override a veto decision post-hoc via REST + Telegram.
4. **Per-account advisor config UI** — FE form for `bot_accounts.advisor_config_override` (DB column already ships in Alembic 0063).

No new AI capabilities, no new tables beyond Alembic 0064. All changes are additive to `app/services/advisor/`.

---

## 2. Scope

### In scope
- `AdvisorMode.SHADOW` enum value + `service.py` SHADOW path.
- `AdvisorConfig.max_concurrent: int` field + semaphore replacement in `service.py`.
- Alembic 0064: override columns on `bot_advisor_decisions`.
- `PATCH /api/bots/{id}/advisor-decisions/{decision_id}` REST endpoint.
- `PUT /api/bots/{id}/accounts/{account_id}/advisor-config` REST endpoint.
- FE: override button in `AdvisorDecisionDrawer`; per-account config form in `BotDetailPage` advisor tab.
- 3 new Prometheus metrics.

### Explicitly out of scope
- Advisor in backtest replay — Phase 21b.
- Param-tuning, shadow-promotion — Phase 21b.
- Telegram VETO notifications — Phase 21b.
- News/filings in advisor context — Phase 21b.
- Auto-promote config — deferred beyond 21b.

---

## 3. Architecture

### 3.1 SHADOW mode

`AdvisorMode.SHADOW = "SHADOW"` added to the existing StrEnum in `types.py`.

In `service.py`, SHADOW takes the **full path** up to (but not including) the AI client call:
- Context build via `ContextBuilder.build()` — full DB reads, token estimation.
- Prompt assembly — `SYSTEM_PROMPT` + user message constructed.
- `asyncio.wait_for` block skipped.
- Returns synthetic `AdvisorVerdict(action="approve", reasoning="shadow_mode", confidence=None)`.
- `latency_ms` reflects context-build time only (not AI call time).
- Audit row persisted normally with `provider=None`, `model=None`, `fallback_chain=[]`.

**Purpose:** integration testing without AI cost; context-build latency measurement via `advisor_latency_seconds` histogram (bucket `shadow` label); validating that context builder reads are correct before enabling OBSERVE/VETO.

**CHECK constraint update:** Alembic 0064 widens the `bots.advisor_config` JSONB mode CHECK to include `"SHADOW"`. The `bot_advisor_decisions.verdict` CHECK is unchanged — verdict values are always `approve|veto|fail_open`; SHADOW mode always emits `action="approve"`.

### 3.2 Async-parallel advisor

**Current:** per-bot `asyncio.Lock` in `_in_flight: dict[str, asyncio.Lock]`. Any second concurrent call → `fail_open(reason="advisor_in_flight")`.

**New:** replace `asyncio.Lock` with `asyncio.Semaphore(config.max_concurrent)`.

```python
class AdvisorConfig(BaseModel):
    # ... existing fields ...
    max_concurrent: int = Field(1, ge=1, le=4)
    # max_concurrent=1 preserves existing behaviour by default
```

`_in_flight: dict[str, asyncio.Semaphore]` — semaphore created lazily keyed by `str(bot_id)`.

When semaphore is fully acquired (all `max_concurrent` slots in use): `fail_open(reason="advisor_in_flight")` — same metric, same behaviour as before. The `advisor_in_flight_skips_total` metric is retained (not renamed) for backward compatibility with existing dashboards.

New gauge: `advisor_concurrent_calls{bot_id}` — tracks live concurrent calls per bot. Incremented on semaphore acquire, decremented on release (via `finally` block).

**Backward compatibility:** existing `bot_advisor_decisions` rows and `bots.advisor_config` JSONB with no `max_concurrent` key → Pydantic default of `1` applies (lazy backfill already wired in Phase 21a HIGH-8 pattern).

### 3.3 Human veto override

**Alembic 0064 adds four columns to `bot_advisor_decisions`:**

```sql
overridden_by       TEXT,           -- jwt_subject of operator
override_action     TEXT CHECK (override_action IN ('approve', 'veto')),
override_reason     TEXT,
overridden_at       TIMESTAMPTZ
```

All nullable. A row with `overridden_at IS NOT NULL` has been overridden.

**`PATCH /api/bots/{id}/advisor-decisions/{decision_id}`:**

```python
class AdvisorDecisionOverride(BaseModel):
    override_action: Literal["approve", "veto"]
    override_reason: str = Field(..., min_length=1, max_length=500)
```

- Requires `require_admin_jwt` + CSRF nonce (same pattern as `PUT /api/bots/{id}/advisor-config`).
- 404 if `decision_id` not found or `bot_id` mismatch (existence-oracle defence — same pattern as Phase 11a job endpoints).
- 409 if already overridden (`overridden_at IS NOT NULL`).
- **Override is audit-only.** A vetoed order that is overridden-to-approve does NOT re-run the order. The override records the operator's post-hoc judgement only. UI copy makes this explicit: "This records your override for audit purposes. The original order was not re-submitted."
- Publishes `bot:advisor:{bot_id}` pubsub frame `{type:"decision_overridden", decision_id, override_action}`.
- Metric: `advisor_overrides_total{override_action}`.

**`AdvisorDecision` response model** gains `overridden_by`, `override_action`, `override_reason`, `overridden_at` fields (nullable). `AdvisorDecisionsTable` FE component shows an "Overridden" badge on affected rows. `AdvisorDecisionDrawer` shows override metadata when present, and an "Override" button when absent (admin-only, guarded by identity check on FE).

### 3.4 Per-account advisor config UI

**New REST endpoint: `PUT /api/bots/{id}/accounts/{account_id}/advisor-config`**

```python
class AccountAdvisorConfigUpdate(BaseModel):
    advisor_config_override: AdvisorConfig | None
    # None = clear override (revert to bot-level default)
```

- Requires `require_admin_jwt` + CSRF nonce.
- 404 if `bot_id` or `account_id` not found, or `bot_accounts` row absent.
- Writes `bot_accounts.advisor_config_override` JSONB (the column already exists from 0063).
- Publishes `bot:advisor:{bot_id}` pubsub frame `{type:"account_config_updated", account_id}` so running child picks up new effective config on next `place_order` call (no restart required — `BotContext` re-resolves effective config per-call from the DB row).
- No metric (low-frequency admin write; covered by existing `advisor_config_reloads_total`).

**FE:** new `AccountAdvisorConfigForm` component in `BotDetailPage` advisor tab. Rendered per `bot_accounts` row. Shows:
- Current `bot_accounts.advisor_config_override` (or "Using bot default" if null).
- Form fields: mode selector, capability selector, local_only toggle, timeout_ms, daily_budget_usd, max_concurrent.
- "Effective config" read-only preview (merge of bot default + override, same merge logic as Phase 21a §3.2 invariant #9).
- "Clear override" button sets override to null.
- Calls the new `PUT /api/bots/{id}/accounts/{account_id}/advisor-config` endpoint.

---

## 4. Data model

### Alembic 0064

```sql
-- Override columns on bot_advisor_decisions
ALTER TABLE bot_advisor_decisions
  ADD COLUMN overridden_by      TEXT,
  ADD COLUMN override_action    TEXT CHECK (override_action IN ('approve', 'veto')),
  ADD COLUMN override_reason    TEXT,
  ADD COLUMN overridden_at      TIMESTAMPTZ;

-- Widen advisor_config mode CHECK to include SHADOW
-- (if stored as CHECK on bots.advisor_config JSONB)
-- Use the same ?-operator pattern as HIGH-8 in Phase 21a:
-- CHECK (advisor_config ? 'mode' AND advisor_config->>'mode' IN ('OFF','OBSERVE','VETO','SHADOW'))
-- Replace existing constraint with updated one.

-- Index for override queries
CREATE INDEX bot_advisor_decisions_overridden_at_idx
  ON bot_advisor_decisions (overridden_at)
  WHERE overridden_at IS NOT NULL;
```

---

## 5. API surface

| Method | Path | Auth | Description |
|---|---|---|---|
| `PATCH` | `/api/bots/{id}/advisor-decisions/{decision_id}` | admin JWT + CSRF | Override a veto decision (audit-only) |
| `PUT` | `/api/bots/{id}/accounts/{account_id}/advisor-config` | admin JWT + CSRF | Set per-account advisor config override |

Existing endpoints unchanged.

---

## 6. Frontend components

| Component | Change |
|---|---|
| `AdvisorDecision` type | Add `overridden_by`, `override_action`, `override_reason`, `overridden_at` fields |
| `AdvisorDecisionsTable` | "Overridden" badge on rows with `overridden_at` |
| `AdvisorDecisionDrawer` | Override metadata section; "Override" button (admin-only) |
| `AccountAdvisorConfigForm` | New component; per-account override form with effective-config preview |
| `BotDetailPage` advisor tab | Renders `AccountAdvisorConfigForm` per bot_accounts row below decisions table |
| `services/advisor/api.ts` | `patchAdvisorDecisionOverride()`, `putAccountAdvisorConfig()` |
| `services/advisor/types.ts` | Extended `AdvisorDecision`, new `AccountAdvisorConfigUpdate` type |

---

## 7. Prometheus metrics (3 new)

| Metric | Type | Labels | Description |
|---|---|---|---|
| `advisor_overrides_total` | Counter | `override_action` | Human veto overrides applied |
| `advisor_concurrent_calls` | Gauge | `bot_id` | Live concurrent advisor calls per bot |
| `advisor_shadow_context_build_seconds` | Histogram | — | Context-build latency in SHADOW mode (no AI call) |

Existing metrics unchanged. `advisor_in_flight_skips_total` retained (semaphore exhaustion still fires it).

---

## 8. Tests

### Backend (~25 new tests)

- `test_shadow_mode_no_ai_call`: SHADOW mode → AI client never called; audit row persisted; `provider=None`.
- `test_shadow_mode_latency_metric`: `advisor_shadow_context_build_seconds` histogram has one observation after SHADOW call.
- `test_max_concurrent_semaphore`: `max_concurrent=2` → 2 simultaneous calls proceed; 3rd → `fail_open(advisor_in_flight)`.
- `test_max_concurrent_default_one`: default `max_concurrent=1` → second simultaneous call → `fail_open`.
- `test_override_veto_decision`: PATCH endpoint → override columns set; pubsub frame published.
- `test_override_already_overridden`: second PATCH on same decision → 409.
- `test_override_wrong_bot_id`: decision exists but wrong `bot_id` → 404.
- `test_override_does_not_resubmit_order`: no new `orders` row after override-to-approve.
- `test_account_advisor_config_put`: PUT endpoint → `bot_accounts.advisor_config_override` updated; pubsub frame.
- `test_account_advisor_config_clear`: PUT with `null` → override cleared; effective config reverts to bot default.
- `test_account_advisor_config_missing_account`: 404 when `account_id` not in `bot_accounts`.
- `test_effective_config_merge_after_account_override`: next `place_order` uses per-account override (integration test with real BotContext).
- `test_shadow_mode_check_constraint`: `advisor_config` with `mode=SHADOW` accepted by 0064 CHECK.
- `test_override_check_constraint`: invalid `override_action` value → DB CHECK violation.

### Frontend (~8 new tests)

- `AdvisorDecisionsTable`: overridden row shows "Overridden" badge.
- `AdvisorDecisionDrawer`: shows override metadata when `overridden_at` set; hides "Override" button.
- `AdvisorDecisionDrawer`: "Override" button visible when no override; submits PATCH; shows confirmation.
- `AccountAdvisorConfigForm`: renders per-account override fields; "Using bot default" when override null.
- `AccountAdvisorConfigForm`: effective config preview updates on field change.
- `AccountAdvisorConfigForm`: "Clear override" sets override to null; calls PUT with null body.

---

## 9. Implementation chunks

| Chunk | Files | Routing | Gate |
|---|---|---|---|
| **A — Schema + types** | Alembic 0064, `types.py` (SHADOW mode + `max_concurrent`), migration tests | Qwen | — |
| **B — Service changes** | `service.py` (semaphore, SHADOW path), `metrics.py` (new metrics), tests | Qwen | after A |
| **C — REST endpoints** | `api/bots.py` (PATCH override + PUT account config), tests | Codex | after A + B |
| **D — Frontend** | `services/advisor/types.ts`, `api.ts`, `AdvisorDecisionDrawer` (override), `AccountAdvisorConfigForm`, `BotDetailPage` advisor tab | Codex | after C |
| **E — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.21.1 | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku). Chunk A: + database-reviewer (sonnet). Chunk C: + security-reviewer (sonnet). Chunk D: + typescript-reviewer (haiku).

---

## 10. Deferred

| Item | Target |
|---|---|
| Advisor in backtest replay | Phase 21b |
| Telegram VETO notifications | Phase 21b |
| News/filings in advisor context | Phase 21b |
| Param-tuning | Phase 21b |
| Shadow-promotion | Phase 21b |
| Auto-promote config (`bots.auto_promote_config`) | Beyond 21b |
| One-retry on schema violation | 21b or later |
| `bot_advisor_decisions` → hypertable | Phase 24 |
