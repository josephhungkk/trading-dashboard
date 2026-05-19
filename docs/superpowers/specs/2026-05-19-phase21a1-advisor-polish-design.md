# Phase 21a.1 — Advisor Polish (v0.21.1)

**Date:** 2026-05-19  
**Status:** ARCHITECT-REVIEW Pass 1 applied — ready for /writing-plans  
**Builds on:** Phase 21a (LLM advisor, v0.21.0)  
**Next phases:** 21b (param-tuning + shadow-promotion + full LLM-in-loop)

**ARCHITECT-REVIEW applied:** Pass 1 (4 HIGH + 7 MED + 4 LOW). All HIGH + MED inline. LOWs noted.

---

## 1. Goal

Close the four items explicitly deferred from Phase 21a:

1. **SHADOW mode** — full advisor pipeline without AI cost; wire-test and overhead measurement.
2. **Async-parallel advisor** — lift the 1-at-a-time in-flight lock to a configurable semaphore.
3. **Live human veto override** — operator can override a veto decision post-hoc via REST.
4. **Per-account advisor config UI** — FE form for `bot_accounts.advisor_config_override` (DB column already ships in Alembic 0063).

No new AI capabilities, no new tables beyond Alembic 0064. All changes are additive to `app/services/advisor/`.

---

## 2. Scope

### In scope
- `AdvisorMode.SHADOW` enum value + `service.py` SHADOW path.
- `AdvisorConfig.max_concurrent: int` field + semaphore replacement in `service.py`.
- Alembic 0064: override columns on `bot_advisor_decisions` + mode CHECK widen + `CONCURRENTLY` index.
- `PATCH /api/bots/{id}/advisor-decisions/{decision_id}` REST endpoint.
- `PUT /api/bots/{id}/accounts/{account_id}/advisor-config` REST endpoint.
- FE: override button in `AdvisorDecisionDrawer` (server-enforced; FE hides for non-admins only); per-account config form in `BotDetailPage` advisor tab.
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
- Semaphore slot is acquired and held during context-build (same as OBSERVE/VETO). `advisor_in_flight_skips_total` can fire in SHADOW mode when all `max_concurrent` slots are occupied — this is correct and expected.

**Purpose:** integration testing without AI cost; context-build latency measurement via `advisor_shadow_context_build_seconds` histogram (separate metric — keeps `advisor_latency_seconds` semantics clean for AI-call paths only); validating that context builder reads are correct before enabling OBSERVE/VETO.

**CHECK constraint update (H1):** Alembic 0064 widens the `bots.advisor_config` JSONB mode CHECK to include `"SHADOW"`. The `bot_advisor_decisions.verdict` CHECK is unchanged — verdict values are always `approve|veto|fail_open`; SHADOW mode always emits `action="approve"`.

Migration sequence for the mode CHECK (H1 — exact constraint name required):
```python
# In alembic 0064 upgrade():
# 1. Pre-flight assertion before dropping constraint:
op.execute("""
    DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM bots
            WHERE advisor_config IS NOT NULL
              AND advisor_config->>'mode' NOT IN ('OFF','OBSERVE','VETO')
        ) THEN
            RAISE EXCEPTION 'bots.advisor_config has unknown mode values — cannot widen CHECK';
        END IF;
    END $$;
""")
# 2. Drop existing constraint (name from alembic 0063):
op.drop_constraint("bots_advisor_config_mode_check", "bots")
# 3. Create widened constraint:
op.create_check_constraint(
    "bots_advisor_config_mode_check",
    "bots",
    "advisor_config ? 'mode' AND advisor_config->>'mode' IN ('OFF','OBSERVE','VETO','SHADOW')",
)
```

### 3.2 Async-parallel advisor

**Current:** per-bot `asyncio.Lock` in `_in_flight: dict[str, asyncio.Lock]`. Any second concurrent call → `fail_open(reason="advisor_in_flight")`.

**New:** replace `asyncio.Lock` with `asyncio.Semaphore(config.max_concurrent)`.

```python
class AdvisorConfig(BaseModel):
    # ... existing fields ...
    max_concurrent: int = Field(1, ge=1, le=4)
    # max_concurrent=1 preserves existing behaviour by default.
    # Upper bound of 4 reflects heavy-box GPU saturation risk and AIRouterRateLimiter
    # semaphore interaction; the Phase 11a per-capability semaphores (e.g. REASONING: 2)
    # provide the real ceiling — this is a per-bot local cap. (LOW-1)
```

**Semaphore creation (H2 — race-free):** Semaphores are pre-created (not lazily) when `AdvisorService` receives an `UPDATE_ADVISOR_CONFIG` pubsub frame or at bot startup. The `_in_flight` dict is populated at config-load time, not on first call. If a `bot_id` is not in `_in_flight`, a `Semaphore(1)` is inserted under a module-level `asyncio.Lock(_in_flight_lock)` before the first call — this covers the cold-start race. Pre-creation is preferred at config-load (no lock needed at call time).

**Runtime resize on config change (H3):** `asyncio.Semaphore` cannot be resized after creation. When `UPDATE_ADVISOR_CONFIG` arrives with a changed `max_concurrent`, the service:
1. Drains the old semaphore (waits until `_semaphore._value == old_max_concurrent`, i.e., no in-flight calls; timeout 10s).
2. Replaces `_in_flight[bot_id]` with a new `Semaphore(new_max_concurrent)`.
3. Logs `structlog.info("advisor.semaphore.resized", bot_id=..., old=..., new=...)`.

If drain times out (bot is in sustained high-frequency trading): the swap is deferred, metric `advisor_semaphore_resize_deferred_total` incremented, and the old semaphore is retained until next `UPDATE_ADVISOR_CONFIG` or child restart.

**Channel taxonomy (H4):**
- `bot:advisor:{bot_id}` — **FE-bound frames only** (`decision` events, `decision_overridden`, WS gateway subscribes this).
- `bot:advisor:config:{bot_id}` — **child-process-bound frames only** (`UPDATE_ADVISOR_CONFIG`, `account_config_updated` for child to refresh effective config). Child does NOT forward these to FE.
- Frames on both channels carry `v: 1` envelope. Unknown `type` values dropped at both FE and child. This resolves the H4 namespace collision: `account_config_updated` moves to `bot:advisor:config:{bot_id}` (child-only); FE never sees raw config-update frames.

**Per-account config endpoint** (§3.4) publishes to `bot:advisor:config:{bot_id}` (not `bot:advisor:{bot_id}`).

**Backward compatibility:** existing `bots.advisor_config` JSONB with no `max_concurrent` key → Pydantic default of `1` applies (lazy backfill already wired in Phase 21a HIGH-8 pattern).

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

- Requires `require_admin_jwt` + CSRF nonce.
- 404 if `decision_id` not found OR `bot_id` mismatch — identical 404 body shape in both cases (existence-oracle defence, same as Phase 11a job endpoints). `test_override_existence_oracle_parity` asserts body shape identity (M6).
- 409 if already overridden (`overridden_at IS NOT NULL`). **409 body includes `overridden_by` and `overridden_at`** so clients can distinguish "I already won" from "someone else won" (M1).
- **Override is audit-only.** A vetoed order that is overridden-to-approve does NOT re-run the order. The endpoint has no code path that calls `place_order`, `orders_service`, or any broker facade. Server-side: `override_action` field is never forwarded beyond the DB write (M3 — server is the real enforcer; FE hiding is UX-only).
- Emits `structlog.info("advisor.decision.overridden", bot_id=..., decision_id=..., override_action=..., jwt_subject=...)` (M2).
- Publishes `bot:advisor:{bot_id}` pubsub frame `{v:1, type:"decision_overridden", decision_id, override_action}` (FE-bound channel).
- Metric: `advisor_overrides_total{override_action}`.

**`AdvisorDecision` response model** gains `overridden_by`, `override_action`, `override_reason`, `overridden_at` fields (nullable). Regenerate types via `scripts/gen-types.sh` after Pydantic model update (L3). `AdvisorDecisionsTable` FE component shows an "Overridden" badge on affected rows. `AdvisorDecisionDrawer` shows override metadata when present, and an "Override" button when absent. FE hides the button for non-admin sessions (UX); `require_admin_jwt` on the server is the real enforcement (M3).

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
- Publishes `bot:advisor:config:{bot_id}` frame `{v:1, type:"account_config_updated", account_id}` (child-bound channel, H4). Running child re-resolves effective config per-call from the DB row on next `place_order` — no restart required.
- If `max_concurrent` changed in the override: child receives `account_config_updated` and triggers the semaphore-resize path (§3.2) for that account's effective config.
- No dedicated metric (low-frequency admin write; covered by existing `advisor_config_reloads_total`).

**FE:** new `AccountAdvisorConfigForm` component in `BotDetailPage` advisor tab. Rendered per `bot_accounts` row. Shows:
- Current `bot_accounts.advisor_config_override` (or "Using bot default" if null).
- Form fields: mode selector, capability selector, local_only toggle, timeout_ms, daily_budget_usd, max_concurrent.
- "Effective config" read-only preview (merge of bot default + override, same merge logic as Phase 21a §3.2 invariant #9).
- "Clear override" button sets override to null.
- Calls the new `PUT /api/bots/{id}/accounts/{account_id}/advisor-config` endpoint.

---

## 4. Data model

### Alembic 0064

```python
# upgrade():

# --- 1. Override columns on bot_advisor_decisions ---
op.add_column("bot_advisor_decisions", sa.Column("overridden_by", sa.Text()))
op.add_column("bot_advisor_decisions", sa.Column(
    "override_action", sa.Text(),
    sa.CheckConstraint("override_action IN ('approve', 'veto')", name="bad_advisor_override_action_check")
))
op.add_column("bot_advisor_decisions", sa.Column("override_reason", sa.Text()))
op.add_column("bot_advisor_decisions", sa.Column("overridden_at", sa.TIMESTAMPTZ()))

# --- 2. Partial index for override queries — CONCURRENTLY to avoid write stall (M5) ---
with op.get_context().autocommit_block():
    op.execute(
        "CREATE INDEX CONCURRENTLY bot_advisor_decisions_overridden_at_idx "
        "ON bot_advisor_decisions (overridden_at) WHERE overridden_at IS NOT NULL"
    )

# --- 3. Widen bots.advisor_config mode CHECK to include SHADOW ---
# Pre-flight assertion:
op.execute("""
    DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM bots
            WHERE advisor_config IS NOT NULL
              AND advisor_config->>'mode' NOT IN ('OFF','OBSERVE','VETO')
        ) THEN
            RAISE EXCEPTION 'bots.advisor_config has unknown mode values — cannot widen CHECK';
        END IF;
    END $$;
""")
op.drop_constraint("bots_advisor_config_mode_check", "bots")
op.create_check_constraint(
    "bots_advisor_config_mode_check",
    "bots",
    "advisor_config ? 'mode' AND advisor_config->>'mode' IN ('OFF','OBSERVE','VETO','SHADOW')",
)

# downgrade(): reverse order — drop SHADOW from CHECK, drop index, drop columns.
# NOTE: downgrade drops override columns, losing audit data. Document in migration docstring. (L2)
```

---

## 5. API surface

| Method | Path | Auth | Description |
|---|---|---|---|
| `PATCH` | `/api/bots/{id}/advisor-decisions/{decision_id}` | admin JWT + CSRF | Override a veto decision (audit-only; no order resubmission) |
| `PUT` | `/api/bots/{id}/accounts/{account_id}/advisor-config` | admin JWT + CSRF | Set per-account advisor config override |

Existing endpoints unchanged.

---

## 6. Frontend components

| Component | Change |
|---|---|
| `AdvisorDecision` type | Add `overridden_by`, `override_action`, `override_reason`, `overridden_at` (nullable); regenerate via `gen-types.sh` (L3) |
| `AdvisorDecisionsTable` | "Overridden" badge on rows with `overridden_at` |
| `AdvisorDecisionDrawer` | Override metadata section; "Override" button hidden for non-admins (UX-only — server enforces via `require_admin_jwt`) |
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
| `advisor_shadow_context_build_seconds` | Histogram | — | Context-build latency in SHADOW mode (separate from `advisor_latency_seconds` which covers AI-call paths only) |
| `advisor_semaphore_resize_deferred_total` | Counter | — | `max_concurrent` config changes deferred because old semaphore did not drain in time |

Existing metrics unchanged. `advisor_in_flight_skips_total` retained (semaphore exhaustion still fires it, including during SHADOW mode when all slots are occupied).

---

## 8. Tests

### Backend (~30 new tests)

- `test_shadow_mode_no_ai_call`: SHADOW mode → AI client never called; audit row persisted; `provider=None`.
- `test_shadow_mode_latency_metric`: `advisor_shadow_context_build_seconds` histogram has one observation after SHADOW call.
- `test_shadow_mode_semaphore_held`: SHADOW mode acquires semaphore slot; third concurrent SHADOW call → `fail_open(advisor_in_flight)`.
- `test_shadow_mode_check_constraint`: `advisor_config` with `mode=SHADOW` accepted by 0064 CHECK.
- `test_shadow_mode_check_constraint_unknown`: unknown mode value → DB CHECK violation.
- `test_max_concurrent_semaphore`: `max_concurrent=2` → 2 simultaneous calls proceed; 3rd → `fail_open(advisor_in_flight)`.
- `test_max_concurrent_default_one`: default `max_concurrent=1` → second simultaneous call → `fail_open`.
- `test_semaphore_creation_race_safe`: two goroutines first-calling same `bot_id` → single semaphore created (no double-creation).
- `test_semaphore_resize_drain_and_swap`: `max_concurrent` changed 1→2 → old semaphore drained → new semaphore with 2 slots active.
- `test_semaphore_resize_deferred_on_timeout`: resize during sustained in-flight calls → `advisor_semaphore_resize_deferred_total` incremented; old semaphore retained.
- `test_channel_taxonomy_config_frame_not_on_fe_channel`: `account_config_updated` frame published on `bot:advisor:config:{id}` only; `bot:advisor:{id}` (FE channel) has no config frame.
- `test_override_veto_decision`: PATCH endpoint → override columns set; structlog event; pubsub frame on FE channel.
- `test_override_already_overridden_409_body`: second PATCH → 409 with `overridden_by` and `overridden_at` in body.
- `test_override_wrong_bot_id`: decision exists but wrong `bot_id` → 404 (same body shape as non-existent decision).
- `test_override_existence_oracle_parity`: 404 body shape and timing identical for "wrong bot_id" vs "decision not found" (M6).
- `test_override_does_not_resubmit_order`: no new `orders` row, no `place_order` call after override-to-approve.
- `test_override_structlog_event`: structlog event `advisor.decision.overridden` emitted with `jwt_subject`, `bot_id`, `decision_id` (M2).
- `test_override_check_constraint`: invalid `override_action` value → DB CHECK violation.
- `test_account_advisor_config_put`: PUT endpoint → `bot_accounts.advisor_config_override` updated; pubsub on config channel.
- `test_account_advisor_config_clear`: PUT with `null` → override cleared; effective config reverts to bot default.
- `test_account_advisor_config_missing_account`: 404 when `account_id` not in `bot_accounts`.
- `test_effective_config_merge_after_account_override`: next `place_order` uses per-account override (integration test with real BotContext).
- `test_migration_0064_pre_flight_assertion`: inject bad `advisor_config->>'mode'` value before migration → migration raises, rolls back.

### Frontend (~10 new tests)

- `AdvisorDecisionsTable`: overridden row shows "Overridden" badge.
- `AdvisorDecisionDrawer`: shows override metadata when `overridden_at` set; hides "Override" button.
- `AdvisorDecisionDrawer`: "Override" button visible when no override; submits PATCH; shows confirmation copy: "This records your override for audit purposes. The original order was not re-submitted."
- `AdvisorDecisionDrawer`: "Override" button absent for non-admin session (M3 — UX hide test).
- `AccountAdvisorConfigForm`: renders per-account override fields; "Using bot default" when override null.
- `AccountAdvisorConfigForm`: effective config preview updates on field change.
- `AccountAdvisorConfigForm`: "Clear override" sets override to null; calls PUT with null body.

---

## 9. Implementation chunks

| Chunk | Files | Routing | Gate |
|---|---|---|---|
| **A — Schema + types** | Alembic 0064 (override cols + CONCURRENTLY index + pre-flight assertion + mode CHECK widen), `types.py` (SHADOW mode + `max_concurrent`), migration tests | Qwen | — |
| **B — Service changes** | `service.py` (semaphore pre-create + H3 resize-drain-swap + SHADOW path + H4 channel taxonomy), `metrics.py` (4 new metrics), tests | Qwen | after A |
| **C — REST endpoints** | `api/bots.py` (PATCH override + PUT account config), tests | Codex | after A + B |
| **D — Frontend** | `services/advisor/types.ts` (gen-types.sh), `api.ts`, `AdvisorDecisionDrawer` (override + admin-guard), `AccountAdvisorConfigForm`, `BotDetailPage` advisor tab | Codex | after C |
| **E — Close-out** | CLAUDE.md, CHANGELOG.md, TASKS.md, tag v0.21.1 | Opus direct | after all |

**Reviewer chain per chunk:** spec-compliance (haiku) + code-quality (sonnet) + lang-reviewer (haiku). Chunk A: + database-reviewer (sonnet). Chunk C: + security-reviewer (sonnet). Chunk D: + typescript-reviewer (haiku).

---

## 10. Resolved findings

| Finding | Resolution |
|---|---|
| H1: Mode CHECK migration under-specified | Exact `op.drop_constraint` + `op.create_check_constraint` + pre-flight assertion in §4 |
| H2: Lazy semaphore creation race | Pre-create at config-load; cold-start uses `_in_flight_lock`; documented in §3.2 |
| H3: Runtime `max_concurrent` resize | Drain-old-then-swap pattern; deferred metric on timeout; documented in §3.2 |
| H4: Channel namespace collision | Taxonomy split: `bot:advisor:{id}` = FE-bound; `bot:advisor:config:{id}` = child-bound; §3.2 + §3.4 updated |
| M1: 409 not idempotent for retry | 409 body includes `overridden_by` + `overridden_at` |
| M2: No structlog event on override | `structlog.info("advisor.decision.overridden", ...)` added to §3.3 |
| M3: FE guard framed as security | Spec clarified: FE hides (UX); `require_admin_jwt` is real enforcement |
| M4: Duplicate latency metric | `advisor_shadow_context_build_seconds` is the single shadow histogram; `advisor_latency_seconds` unchanged |
| M5: Index without CONCURRENTLY | `CREATE INDEX CONCURRENTLY` via `autocommit_block()` in §4 |
| M6: No existence-oracle parity test | `test_override_existence_oracle_parity` added |
| M7: No FE hide test for non-admin | FE test for "Override button absent for non-admin session" added |
| L1: `max_concurrent` upper bound | Documented: 4 reflects GPU saturation + Phase 11a REASONING semaphore (2) interaction |
| L2: Downgrade loses audit data | Noted in §4 migration docstring |
| L3: gen-types.sh not called out | Noted in §6 FE components table |
| L4: SHADOW + in-flight skips | Documented in §3.1: semaphore held during SHADOW; `advisor_in_flight_skips_total` fires correctly |

---

## 11. Deferred

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
