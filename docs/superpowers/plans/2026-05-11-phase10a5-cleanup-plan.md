# Phase 10a.5 — Risk-gate effectivity + test infrastructure cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 7-check risk gate that shipped in Phase 10a actually effective in production by lighting up the four wired-but-no-op surfaces (concentration resolver, intraday-PnL pipeline, counter decrement, ALLOW/WARN audit) and dropping the `isinstance(db, AsyncSession)` gate that bypasses risk in stub-session tests.

**Architecture:** Four chunks — A (BE backbone: PnL pipeline + token-bearing counters + audit widening), B (resolver wiring), C (test infra: stub upgrade + Playwright E2E + uv project split), D (ROADMAP rewrite + close-out tag v0.12.1). Day 3 has a snippet-file merge gate where A4 and B2 both modify `orders_service.py`.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic / Redis 7 (Lua scripts) / PostgreSQL 18 / Prometheus client / React 19 / Vitest 4 + RTL 16 / Playwright / pytest 9 / uv.

**Spec:** `docs/superpowers/specs/2026-05-11-phase10a5-cleanup-design.md` (commit 23a1e0f, architect-reviewed)

---

## Model routing for implementation

Codex is **rate-limited** for this phase; production-code writes go to **Qwen3-Coder-Next** on the heavy AI box (Ollama API at `192.168.50.30:11434`) with the **Qwen3.6 family** as backup. Claude main thread (Opus) reviews, verifies, commits. Anthropic subagents are reviewers only — no production-code authorship.

Primary + fallback ladder (try in order; rotate when the current rung produces non-mergeable output two tasks in a row or fails the `feedback_qwen_protocol` body-only check):

| # | Stage | Who | Tag | When |
|---|---|---|---|---|
| 1 | Coding default | Qwen3-Coder-Next UD-Q3_K_XL (Unsloth dynamic 3-bit XL, 36 GB, ~95% BF16, ~36 t/s on RTX 4080) | `hf.co/unsloth/Qwen3-Coder-Next-GGUF:UD-Q3_K_XL` | All coding tasks |
| 2 | Hard tasks — higher fidelity | Qwen3-Coder-Next UD-IQ4_XS (38 GB, ~97% BF16, ~30 t/s, CUDA-optimized i-quant) | `hf.co/unsloth/Qwen3-Coder-Next-GGUF:UD-IQ4_XS` | A4 atomic Lua scripts, A2 savepoint isolation logic — quality > throughput. (q8_0 doesn't fit on 16 GB VRAM + 64 GB RAM combined.) |
| 3 | General-purpose fallback | Qwen3.6-35B-A3B (MoE, 3B active) | `qwen3.6:35b` | When coder-specialized model over-fits to coding idioms (rare — would show as flaky doc/YAML output) |
| 4 | Quality-sensitive dense fallback | Qwen3.6-27B (dense) | `qwen3.6:27b-q4_K_M` | When MoE routing produces unstable output across tasks |
| 5 | Last-known-good | Qwen2.5-Coder-14B (what 10a shipped on) | `qwen2.5-coder:14b` | All Qwen3.x models produce non-mergeable output |
| 6 | Codex fallback | Codex | `gpt-5-codex` | Only if Qwen ladder exhausts AND Codex rate-limit window has reset |
| 7 | Final fallback | Opus main thread takes the task | `claude-opus-4-7` | All above fail |

**Runtime — llama.cpp (switched 2026-05-11 after A1.1):** Phase 10a.5 runs against `llama-server` on the heavy box at `http://192.168.50.30:11435`, using OpenAI-compat `/v1/completions`. Path B measurement on Task A1.1:

| Runtime | Generation tok/s | Prompt tok/s | Wall-clock (A1.1) |
|---|---|---|---|
| Ollama UD-Q3_K_XL | 7.7 | 66.9 | 220.7 s |
| **llama.cpp UD-Q3_K_XL** | **26.9** | **202.8** | **38.6 s** |

3.5× generation, 3× prompt eval, 5.7× wall-clock. Ollama's 7.7 t/s matched the [llama.cpp issue #19480](https://github.com/ggml-org/llama.cpp/issues/19480) prediction exactly; llama.cpp recovered the bandwidth-bound speed. `llama-server` launch on the heavy box: `--n-gpu-layers 99 --cpu-moe --flash-attn on --ctx-size 32768 --threads 8`. LM Studio + LocalAI evaluated and rejected.

Main-thread orchestrate / lint / test / commit always stays on **Opus** (`claude-opus-4-7`).

Reviewer dispatch (per chunk) follows the "Reviewer cadence" table below — Anthropic subagents only, never production code authors.

**Operational note:** Qwen3-Coder-Next scored **70.6-74.2% on SWE-bench Verified** ([Qwen blog](https://qwen.ai/blog?id=qwen3-coder-next), 2026), the best open-weight coding score available, vs ~38% for the prior `qwen2.5-coder:14b`. The 80B/3B-active architecture means same per-token latency as a 14B dense model on the heavy box. Trained specifically on real GitHub PRs with RL on tool-calling, test-running, and failure recovery — the exact flow Phase 10a.5 requires (TDD + reviewer-fix loops + small file edits). For tasks where general-purpose reasoning matters more than coding (D1 ROADMAP rewrite, memory file authoring), drop to rung 3 or main-thread.

## Reviewer cadence — per chunk, not per task

After each chunk closes (all its tasks committed + tests green locally), run a 4-reviewer chain on the chunk's commit range. Dispatch in parallel:

| Reviewer | Model | When |
|---|---|---|
| spec-compliance | `haiku` | every chunk |
| python-reviewer | `haiku` | every chunk touching `backend/**/*.py` |
| typescript-reviewer | `haiku` | every chunk touching `frontend/**/*.ts(x)` |
| code-reviewer | `sonnet` | every chunk |
| security-reviewer | `sonnet` | every chunk touching auth/csrf/admin endpoints (C3 minimum) |
| database-reviewer | `sonnet` | every chunk with Alembic / schema changes (A1, D1) |
| silent-failure-hunter | `sonnet` | every chunk with error-handling boundaries (A2, A4, A5, C2) |
| ARCHITECT-REVIEW | `opus` | already done at brainstorm (commit 23a1e0f); skip per-chunk |

Inline the relevant spec slice in each reviewer prompt (memory `feedback_reviewer_spec_inline.md` — 15-25× token saving vs file pointers).

Apply CRIT+HIGH+MED findings inline as new commits in the same chunk before moving to the next chunk. LOW findings may defer to a `phase10a6` backlog note.

End-of-phase: one final spec-compliance + code-reviewer sweep on the full v0.12.1 diff before tagging.

---

## Task index

- **Chunk A — BE backbone (~14 commits)**
  - Task A1.1 — Alembic 0037 migration scaffold
  - Task A1.2 — Alembic 0037 tests
  - Task A2.1 — `PnlIntradayWriter` module
  - Task A2.2 — Writer tests
  - Task A2.3 — Discoverer fan-in
  - Task A2.4 — Fan-in integration test
  - Task A3.1 — `risk_service._check_max_daily_loss` staleness branch
  - Task A4.1 — Token-bearing counter API
  - Task A4.2 — Counter tests
  - Task A4.3 — Reconcile-aware UNLINK in discoverer
  - Task A4.4 — `orders_service.py` counter call-site rewiring (snippet A4)
  - Task A5.1 — ALLOW/WARN audit widening + dedupe
  - Task A5.2 — Audit emission tests
  - **End-of-chunk-A: 4-reviewer chain (spec / python / code / database / silent-failure-hunter)**
- **Chunk B — Resolver wiring (~4 commits)**
  - Task B1.1 — `InstrumentResolver.find_by_alias` read-only method
  - Task B1.2 — `_resolve_instrument_id` helper
  - Task B2.1 — 7-site swap in `orders_service.py` (snippet B2 + Day-3 merge)
  - Task B3.1 — Concentration integration tests
  - **End-of-chunk-B: 4-reviewer chain (spec / python / code / database)**
- **Chunk C — Test infrastructure (~14 commits)**
  - Task C1.1 — `@pytest.mark.no_risk_gate` marker
  - Task C1.2 — Per-file stub upgrade: `test_orders_preview.py`
  - Task C1.3 — Per-file stub upgrade: `test_orders_place.py`
  - Task C1.4 — Per-file stub upgrade: `test_orders_modify.py`
  - Task C1.5 — Per-file stub upgrade: `test_orders_bracket.py`
  - Task C1.6 — Per-file stub upgrade: `test_orders_cancel.py`
  - Task C2.1 — Drop `isinstance` guard + CI verification grep
  - Task C3.1 — `@playwright/test` direct devDep
  - Task C3.2 — E2E bypass auth middleware
  - Task C3.3 — `/api/csrf/nonce` endpoint
  - Task C3.4 — `frontend/e2e/seed.sql` + fixtures
  - Task C3.5 — Playwright spec `phase10a5-risk-warn`
  - Task C3.6 — Playwright spec `phase10a5-risk-block`
  - Task C3.7 — Playwright spec `phase10a5-admin-risk-crud`
  - Task C3.8 — Playwright spec `phase10a5-kill-switch`
  - Task C3.9 — `.github/workflows/playwright-e2e.yml`
  - Task C4.1 — `backend/tests/real_broker/pyproject.toml`
  - Task C4.2 — Nightly workflow path updates
  - **End-of-chunk-C: 5-reviewer chain (spec / python / typescript / code / security)**
- **Chunk D — Docs + close-out (~2 commits)**
  - Task D1.1 — ROADMAP.md +2 rewrite + Tag history appendix + CLAUDE.md/CHANGELOG.md/TASKS.md
  - Task D2.1 — Memory `phase10a5_shipped.md` + tag v0.12.1
  - **End-of-chunk-D / End-of-phase: final spec-compliance + code-reviewer on full v0.12.1 diff**

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/0037_phase10a5_pnl_intraday.py` | Alembic migration: `pnl_intraday` table + view rewrite + `idx_risk_decisions_verdict_time` + `prune_risk_decisions_allow` |
| `backend/app/services/pnl_intraday_writer.py` | `PnlIntradayWriter` — upsert per account-day from broker positions; prune older than 30 days |
| `backend/app/api/e2e_bypass.py` | Dev-only `X-E2E-Token` middleware — synthesizes CF Access identity for Playwright |
| `backend/app/api/csrf.py` | `/api/csrf/nonce` GET endpoint — returns the order-cap-prefix CSRF nonce |
| `backend/tests/integration/test_alembic_0037.py` | A1 migration tests |
| `backend/tests/services/test_pnl_intraday_writer.py` | A2 writer tests |
| `backend/tests/services/test_risk_inflight_counters_tokens.py` | A4 token-roundtrip tests |
| `backend/tests/integration/test_orders_service_dispatch.py` | A4 dispatch-path token-threading test |
| `backend/tests/services/test_instrument_id_resolution.py` | B1 resolver tests |
| `backend/tests/integration/test_concentration_check_e2e.py` | B3 concentration integration test |
| `backend/tests/real_broker/pyproject.toml` | C4 standalone uv project for nightly real-broker tests |
| `backend/tests/real_broker/conftest.py` | C4 sys.path resolver |
| `frontend/e2e/seed.sql` | C3 idempotent seed for Playwright runs |
| `frontend/e2e/phase10a5-risk-warn.spec.ts` | C3 WARN+acknowledge scenario |
| `frontend/e2e/phase10a5-risk-block.spec.ts` | C3 kill-switch BLOCK scenario |
| `frontend/e2e/phase10a5-admin-risk-crud.spec.ts` | C3 admin CRUD + pubsub invalidation |
| `frontend/e2e/phase10a5-kill-switch.spec.ts` | C3 kill-switch toggle |
| `.github/workflows/playwright-e2e.yml` | C3 PR + nightly workflow |

### Modified files

| Path | What changes |
|---|---|
| `backend/app/services/brokers.py:1052,1298-1393` | A2.3 writer call inside discoverer fan-out; A4.3 token UNLINK before reconcile; cycle-counter for prune cadence |
| `backend/app/services/risk_service.py:170-232` | A3.1 staleness branch in `_check_max_daily_loss` |
| `backend/app/services/risk_inflight_counters.py` | A4.1 token-bearing API + Lua scripts |
| `backend/app/services/orders_service.py:251-289,338-460,460-560,620-645,845-880` | A4.4 counter wiring; A5.1 audit widening; B2.1 7-site instrument_id swap |
| `backend/app/services/quotes/instrument_resolver.py:69+` | B1.1 `find_by_alias` read-only method |
| `backend/app/core/metrics.py` | A2/A4/A5/B1 new Counters + Gauges per spec §8 |
| `backend/tests/api/test_orders_{preview,place,modify,bracket,cancel}.py` | C1 stub upgrades |
| `frontend/package.json` | C3.1 `@playwright/test` direct devDep |
| `frontend/e2e/fixtures.ts` | C3 fixture helpers for auth + CSRF + seed |
| `.github/workflows/nightly-real-{alpaca-crypto,alpaca-equity,futu,ibkr,schwab,schwab-trade}.yml`, `weekly-real-schwab-drift.yml` | C4.2 path swap to `backend/tests/real_broker/` |
| `.github/workflows/main-ci.yml` | C2.1 verification grep step |
| `docs/ROADMAP.md` | D1.1 +2 forward-projection rewrite + Tag history appendix |
| `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` | D1.1 close-out updates |
| `backend/app/main.py` | C3.2 mount e2e_bypass middleware; C3.3 mount csrf router |

---

# Chunk A — BE backbone

## Task A1.1 — Alembic 0037 migration scaffold

**Files:**
- Create: `backend/alembic/versions/0037_phase10a5_pnl_intraday.py`

- [ ] **Step 1: Create migration file**

```python
"""phase10a5: pnl_intraday + view rewrite + risk_decisions index + retention helper.

Revision ID: 0037_phase10a5
Revises: 0036_phase10a_risk_engine
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_phase10a5"
down_revision = "0036_phase10a_risk_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── pnl_intraday table ──────────────────────────────────────────────
    # Source-field invariant: realized_today MUST be SUM(positions[*].realized_pnl_today)
    # (proto Position field 7), NEVER Summary.realized_pnl (proto Summary field 3 —
    # cumulative since open for IBKR; would invert the gate).
    op.create_table(
        "pnl_intraday",
        sa.Column("account_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("realized_today", sa.Numeric(20, 8), nullable=False),
        sa.Column("unrealized", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_label", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "day_start_utc", name="pk_pnl_intraday"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["broker_accounts.id"],
            name="fk_pnl_intraday_account", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_pnl_intraday_currency_iso3"
        ),
    )

    # CRIT-2 fix: view drops LEFT JOIN. Missing row → risk_service WARNs.
    op.execute("DROP VIEW IF EXISTS v_account_intraday_pnl")
    op.execute(
        """
        CREATE OR REPLACE VIEW v_account_intraday_pnl AS
        SELECT
          p.account_id          AS account_id,
          p.day_start_utc       AS day_start_utc,
          p.realized_today      AS realized,
          p.unrealized          AS unrealized,
          p.summary_updated_at  AS summary_updated_at,
          (now() - p.summary_updated_at) AS staleness
        FROM pnl_intraday p
        WHERE p.day_start_utc = (date_trunc('day', now() AT TIME ZONE 'UTC')
                                  AT TIME ZONE 'UTC')
        """
    )

    # HIGH-4: verdict-filter index for admin feed reads
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_risk_decisions_verdict_time "
        "ON risk_decisions (verdict, evaluated_at DESC)"
    )

    # 30-day ALLOW retention helper
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prune_risk_decisions_allow(retain_days int)
        RETURNS bigint LANGUAGE plpgsql AS $$
        DECLARE
          deleted_count bigint;
        BEGIN
          DELETE FROM risk_decisions
           WHERE verdict = 'allow'
             AND evaluated_at < now() - make_interval(days => retain_days);
          GET DIAGNOSTICS deleted_count = ROW_COUNT;
          RETURN deleted_count;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS prune_risk_decisions_allow(int)")
    op.execute("DROP INDEX IF EXISTS idx_risk_decisions_verdict_time")
    op.execute("DROP VIEW IF EXISTS v_account_intraday_pnl")
    op.execute(
        """
        CREATE OR REPLACE VIEW v_account_intraday_pnl AS
        SELECT
          ba.id AS account_id,
          (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS day_start_utc,
          0::NUMERIC(20, 8) AS realized,
          0::NUMERIC(20, 8) AS unrealized
        FROM broker_accounts ba
        """
    )
    op.drop_table("pnl_intraday")
```

- [ ] **Step 2: Apply migration**

Run: `cd backend && uv run alembic upgrade head`
Expected: `0037_phase10a5` logged as applied.

- [ ] **Step 3: Verify downgrade roundtrip**

Run: `cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: both succeed.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0037_phase10a5_pnl_intraday.py
git commit -m "feat(phase10a5-a1): alembic 0037 — pnl_intraday + view + risk_decisions index"
```

---

## Task A1.2 — Alembic 0037 tests

**Files:**
- Create: `backend/tests/integration/test_alembic_0037.py`

- [ ] **Step 1: Write the failing test**

```python
"""Phase 10a.5 A1: alembic 0037 contract verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_0037_pnl_intraday_table_exists(db_session) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'pnl_intraday'"
            )
        )
    ).all()
    cols = {r[0] for r in rows}
    expected = {
        "account_id", "day_start_utc", "realized_today", "unrealized",
        "currency", "summary_updated_at", "updated_at", "source_label",
    }
    assert expected <= cols, f"missing columns: {expected - cols}"


@pytest.mark.asyncio
async def test_0037_currency_check_constraint(db_session) -> None:
    """CHECK (currency ~ '^[A-Z]{3}$') rejects lowercase."""
    aid = uuid4()
    await db_session.execute(
        text("INSERT INTO broker_accounts (id, broker_id, alias, currency_base) "
             "VALUES (:id, 'ibkr', 'test', 'USD') ON CONFLICT DO NOTHING"),
        {"id": aid},
    )
    with pytest.raises(Exception) as exc:
        await db_session.execute(
            text("INSERT INTO pnl_intraday (account_id, day_start_utc, "
                 "realized_today, unrealized, currency, summary_updated_at, "
                 "source_label) VALUES (:aid, now(), 0, 0, 'usd', now(), 'ibkr')"),
            {"aid": aid},
        )
    assert "ck_pnl_intraday_currency_iso3" in str(exc.value)


@pytest.mark.asyncio
async def test_0037_view_returns_zero_rows_when_empty(db_session) -> None:
    """CRIT-2: view returns 0 rows when pnl_intraday is empty (no LEFT JOIN)."""
    rows = (
        await db_session.execute(text("SELECT * FROM v_account_intraday_pnl"))
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_0037_view_exposes_staleness(db_session) -> None:
    """View has `staleness` column (now() - summary_updated_at)."""
    aid = uuid4()
    await db_session.execute(
        text("INSERT INTO broker_accounts (id, broker_id, alias, currency_base) "
             "VALUES (:id, 'ibkr', 'test', 'USD') ON CONFLICT DO NOTHING"),
        {"id": aid},
    )
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    summary_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
    await db_session.execute(
        text("INSERT INTO pnl_intraday (account_id, day_start_utc, "
             "realized_today, unrealized, currency, summary_updated_at, "
             "source_label) VALUES (:aid, :day, 100, 50, 'USD', :sua, 'ibkr')"),
        {"aid": aid, "day": day_start, "sua": summary_ts},
    )
    row = (
        await db_session.execute(
            text("SELECT realized, unrealized, staleness "
                 "FROM v_account_intraday_pnl WHERE account_id = :aid"),
            {"aid": aid},
        )
    ).first()
    assert row.realized == Decimal("100")
    assert row.unrealized == Decimal("50")
    assert row.staleness.total_seconds() >= 119


@pytest.mark.asyncio
async def test_0037_idx_risk_decisions_verdict_time(db_session) -> None:
    rows = (
        await db_session.execute(
            text("SELECT indexname FROM pg_indexes "
                 "WHERE tablename = 'risk_decisions' "
                 "  AND indexname = 'idx_risk_decisions_verdict_time'")
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_0037_prune_risk_decisions_allow(db_session) -> None:
    """prune_risk_decisions_allow(30) deletes ALLOW > 30d, keeps WARN/BLOCK."""
    aid = uuid4()
    await db_session.execute(
        text("INSERT INTO broker_accounts (id, broker_id, alias, currency_base) "
             "VALUES (:id, 'ibkr', 'test', 'USD') ON CONFLICT DO NOTHING"),
        {"id": aid},
    )
    old = datetime.now(timezone.utc) - timedelta(days=45)
    young = datetime.now(timezone.utc) - timedelta(days=10)
    for verdict, ts in [("allow", old), ("allow", young), ("block", old)]:
        await db_session.execute(
            text("INSERT INTO risk_decisions (id, account_id, verdict, "
                 "evaluated_at, request_id, mode, side) "
                 "VALUES (:id, :aid, :v, :ts, :rid, 'place_order', 'buy')"),
            {"id": uuid4(), "aid": aid, "v": verdict, "ts": ts, "rid": str(uuid4())},
        )
    deleted = (
        await db_session.execute(text("SELECT prune_risk_decisions_allow(30)"))
    ).scalar_one()
    assert deleted == 1  # only the old ALLOW row
```

- [ ] **Step 2: Run tests**

Run: `cd backend && uv run pytest tests/integration/test_alembic_0037.py -v`
Expected: PASS for all 6 tests.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_alembic_0037.py
git commit -m "test(phase10a5-a1): alembic 0037 contract verification"
```

---

## Task A2.1 — `PnlIntradayWriter` module

**Files:**
- Create: `backend/app/services/pnl_intraday_writer.py`
- Modify: `backend/app/core/metrics.py` (append A2 metrics)

- [ ] **Step 1: Append A2 metrics to `metrics.py`**

```python
# ─── Phase 10a.5 A2 metrics ─────────────────────────────────────────────

pnl_intraday_rows_total = Gauge(
    "pnl_intraday_rows_total",
    "Total pnl_intraday rows per account.",
    ["account_id"],
)

pnl_intraday_upsert_failures_total = Counter(
    "pnl_intraday_upsert_failures_total",
    "PnlIntradayWriter upsert failures (logged + dropped).",
)

pnl_intraday_last_update_seconds = Gauge(
    "pnl_intraday_last_update_seconds",
    "Age (seconds) of newest pnl_intraday row per account; alert >90s.",
    ["account_id"],
)

pnl_intraday_currency_skip_total = Counter(
    "pnl_intraday_currency_skip_total",
    "Position rows dropped at writer due to currency mismatch.",
    ["broker_id"],
)

pnl_intraday_writer_source_drift_seconds = Gauge(
    "pnl_intraday_writer_source_drift_seconds",
    "Time-of-day at which each broker's intraday counter resets.",
    ["broker_id"],
)
```

- [ ] **Step 2: Write the writer**

```python
"""Phase 10a.5 A2: PnlIntradayWriter — per-account-per-day intraday PnL upsert.

Source-field invariant: realized_today MUST come from
SUM(positions[*].realized_pnl_today) (proto Position field 7), NEVER from
Summary.realized_pnl (proto Summary field 3 — cumulative since open for IBKR;
would invert the max-daily-loss gate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


class PnlIntradayWriter:
    """Upsert + prune for the ``pnl_intraday`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        account_id: uuid.UUID,
        realized_today: Decimal,
        unrealized: Decimal,
        currency: str,
        summary_updated_at: datetime,
        source_label: str,
    ) -> None:
        """INSERT … ON CONFLICT DO UPDATE with summary_updated_at guard."""
        day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        await self._session.execute(
            text(
                """
                INSERT INTO pnl_intraday (
                  account_id, day_start_utc, realized_today, unrealized,
                  currency, summary_updated_at, source_label
                ) VALUES (
                  :aid, :day, :r, :u, :c, :sua, :sl
                )
                ON CONFLICT (account_id, day_start_utc) DO UPDATE
                   SET realized_today     = EXCLUDED.realized_today,
                       unrealized         = EXCLUDED.unrealized,
                       currency           = EXCLUDED.currency,
                       summary_updated_at = EXCLUDED.summary_updated_at,
                       source_label       = EXCLUDED.source_label,
                       updated_at         = now()
                 WHERE EXCLUDED.summary_updated_at >= pnl_intraday.summary_updated_at
                   AND (pnl_intraday.realized_today, pnl_intraday.unrealized)
                       IS DISTINCT FROM (EXCLUDED.realized_today, EXCLUDED.unrealized)
                """
            ),
            {
                "aid": account_id, "day": day_start,
                "r": realized_today, "u": unrealized,
                "c": currency, "sua": summary_updated_at, "sl": source_label,
            },
        )

    async def prune_older_than(self, *, days: int) -> int:
        """Delete pnl_intraday rows whose day_start_utc < now() - days."""
        result = await self._session.execute(
            text("DELETE FROM pnl_intraday "
                 "WHERE day_start_utc < (now() AT TIME ZONE 'UTC') - "
                 "make_interval(days => :d)"),
            {"d": days},
        )
        return result.rowcount or 0
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/pnl_intraday_writer.py backend/app/core/metrics.py
git commit -m "feat(phase10a5-a2): PnlIntradayWriter module + 5 new metrics"
```

---

## Task A2.2 — Writer tests

**Files:**
- Create: `backend/tests/services/test_pnl_intraday_writer.py`

- [ ] **Step 1: Write tests**

```python
"""Phase 10a.5 A2: PnlIntradayWriter unit + integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services.pnl_intraday_writer import PnlIntradayWriter


@pytest.fixture
async def seeded_account(db_session):
    aid = uuid4()
    await db_session.execute(
        text("INSERT INTO broker_accounts (id, broker_id, alias, currency_base) "
             "VALUES (:id, 'ibkr', 'test', 'USD') ON CONFLICT DO NOTHING"),
        {"id": aid},
    )
    await db_session.commit()
    return aid


@pytest.mark.asyncio
async def test_upsert_inserts_row(db_session, seeded_account) -> None:
    writer = PnlIntradayWriter(db_session)
    await writer.upsert(
        account_id=seeded_account,
        realized_today=Decimal("123.45"),
        unrealized=Decimal("-50.00"),
        currency="USD",
        summary_updated_at=datetime.now(timezone.utc),
        source_label="ibkr",
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT realized_today, unrealized FROM pnl_intraday "
                 "WHERE account_id = :aid"),
            {"aid": seeded_account},
        )
    ).first()
    assert row.realized_today == Decimal("123.45")


@pytest.mark.asyncio
async def test_upsert_stale_summary_rejected(db_session, seeded_account) -> None:
    """MED-6: stale summary_updated_at does NOT overwrite fresh data."""
    writer = PnlIntradayWriter(db_session)
    fresh = datetime.now(timezone.utc)
    stale = fresh - timedelta(seconds=120)
    await writer.upsert(
        account_id=seeded_account, realized_today=Decimal("100"),
        unrealized=Decimal("0"), currency="USD",
        summary_updated_at=fresh, source_label="ibkr",
    )
    await writer.upsert(
        account_id=seeded_account, realized_today=Decimal("999"),
        unrealized=Decimal("999"), currency="USD",
        summary_updated_at=stale, source_label="ibkr",
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT realized_today FROM pnl_intraday WHERE account_id = :aid"),
            {"aid": seeded_account},
        )
    ).first()
    assert row.realized_today == Decimal("100")  # fresh preserved


@pytest.mark.asyncio
async def test_upsert_unchanged_is_noop(db_session, seeded_account) -> None:
    """MED-1: IS-DISTINCT-FROM makes unchanged upsert a no-op."""
    writer = PnlIntradayWriter(db_session)
    ts1 = datetime.now(timezone.utc)
    ts2 = ts1 + timedelta(seconds=30)
    await writer.upsert(
        account_id=seeded_account, realized_today=Decimal("100"),
        unrealized=Decimal("0"), currency="USD",
        summary_updated_at=ts1, source_label="ibkr",
    )
    await db_session.commit()
    first_updated = (
        await db_session.execute(
            text("SELECT updated_at FROM pnl_intraday WHERE account_id = :aid"),
            {"aid": seeded_account},
        )
    ).scalar_one()
    await writer.upsert(
        account_id=seeded_account, realized_today=Decimal("100"),
        unrealized=Decimal("0"), currency="USD",
        summary_updated_at=ts2, source_label="ibkr",
    )
    await db_session.commit()
    second_updated = (
        await db_session.execute(
            text("SELECT updated_at FROM pnl_intraday WHERE account_id = :aid"),
            {"aid": seeded_account},
        )
    ).scalar_one()
    assert first_updated == second_updated


@pytest.mark.asyncio
async def test_prune_drops_old_rows(db_session, seeded_account) -> None:
    writer = PnlIntradayWriter(db_session)
    old_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=45)
    young_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=5)
    for day in (old_day, young_day):
        await db_session.execute(
            text("INSERT INTO pnl_intraday (account_id, day_start_utc, "
                 "realized_today, unrealized, currency, summary_updated_at, "
                 "source_label) VALUES (:aid, :d, 0, 0, 'USD', :d, 'ibkr')"),
            {"aid": seeded_account, "d": day},
        )
    await db_session.commit()
    deleted = await writer.prune_older_than(days=30)
    await db_session.commit()
    assert deleted == 1
```

- [ ] **Step 2: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_pnl_intraday_writer.py -v
git add backend/tests/services/test_pnl_intraday_writer.py
git commit -m "test(phase10a5-a2): PnlIntradayWriter upsert + prune tests"
```

---

## Task A2.3 — Discoverer fan-in

**Files:**
- Modify: `backend/app/services/brokers.py:1031,1052,1298-1393`

- [ ] **Step 1: Add imports + cycle counter**

In `backend/app/services/brokers.py`:

```python
# Add to imports
from datetime import datetime, timezone
from app.services.pnl_intraday_writer import PnlIntradayWriter
from app.core import metrics
```

In `BrokerDiscoverer.__init__` (around line 1031):

```python
self._cycle_count: int = 0
```

In `_discover_once` (first line):

```python
self._cycle_count += 1
```

- [ ] **Step 2: Wire writer into fan-out**

Inside the per-account loop in `_discover_once` (after `summary_result` + `positions_result` are computed at ~line 1320), parallel to the existing NLV update at ~line 1377:

```python
RETENTION_SWEEP_EVERY_N_CYCLES = 60  # 60 × 30s = ~30 min

if summary_result is not None and positions_result is not None:
    # Filter to base-currency positions only (HIGH-1 multi-currency policy).
    matching = [
        p for p in positions_result.positions
        if p.realized_pnl_today.currency == account.currency_base
        and p.unrealized_pnl.currency == account.currency_base
    ]
    realized_today_total = sum(
        (Decimal(p.realized_pnl_today.value) for p in matching),
        Decimal("0"),
    )
    unrealized_total = sum(
        (Decimal(p.unrealized_pnl.value) for p in matching),
        Decimal("0"),
    )
    skipped = len(positions_result.positions) - len(matching)
    if skipped > 0:
        metrics.pnl_intraday_currency_skip_total.labels(
            broker_id=account.broker_id
        ).inc(skipped)

    try:
        async with session.begin_nested():
            writer = PnlIntradayWriter(session)
            await writer.upsert(
                account_id=account.id,
                realized_today=realized_today_total,
                unrealized=unrealized_total,
                currency=account.currency_base,
                summary_updated_at=summary_result.updated_at.ToDatetime().replace(
                    tzinfo=timezone.utc
                ),
                source_label=client.label,
            )
        metrics.pnl_intraday_last_update_seconds.labels(
            account_id=str(account.id),
        ).set(
            (datetime.now(timezone.utc)
             - summary_result.updated_at.ToDatetime().replace(tzinfo=timezone.utc)
            ).total_seconds()
        )
    except SQLAlchemyError as exc:
        metrics.pnl_intraday_upsert_failures_total.inc()
        log.warning(
            "pnl_intraday_upsert_failed",
            account_id=str(account.id), err=str(exc),
        )
# IBKR maintenance 503 → summary_result is None → writer NOT called →
# view returns no row → gate WARNs (NOT silent ALLOW, per CRIT-2).
```

- [ ] **Step 3: Add prune at end of `_discover_once`**

```python
if self._cycle_count % RETENTION_SWEEP_EVERY_N_CYCLES == 0:
    try:
        async with session.begin():
            writer = PnlIntradayWriter(session)
            await writer.prune_older_than(days=30)
            await session.execute(text("SELECT prune_risk_decisions_allow(30)"))
    except SQLAlchemyError as exc:
        log.warning("pnl_intraday_prune_failed", err=str(exc))
```

- [ ] **Step 4: Lint**

Run: `cd backend && uv run ruff check app/services/brokers.py && uv run mypy --strict app/services/brokers.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/brokers.py
git commit -m "feat(phase10a5-a2): wire PnlIntradayWriter into BrokerDiscoverer fan-out"
```

---

## Task A2.4 — Fan-in integration test

**Files:**
- Modify: `backend/tests/services/test_brokers.py`

- [ ] **Step 1: Append integration test**

```python
@pytest.mark.asyncio
async def test_discoverer_writes_pnl_intraday_from_per_position_today(db_session) -> None:
    """A2: writer pulls SUM(Position.realized_pnl_today), NOT Summary.realized_pnl.

    Sets Summary.realized_pnl=9999 (cumulative trap) and per-position
    realized_pnl_today values summing to 30. Asserts row.realized_today=30.
    Would fail if implementation pulled from the wrong proto field.
    """
    # Full discover-loop scaffold: construct BrokerDiscoverer with fake
    # client returning the canned Summary+Positions, invoke one cycle,
    # query pnl_intraday for the test account, assert realized_today == 30.
    # See existing test_brokers.py::test_discover_loop_processes_accounts
    # for the fixture pattern.
    pass  # Implementer: reuse the existing discover-loop fixture
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/services/test_brokers.py
git commit -m "test(phase10a5-a2): discoverer fan-in writes pnl_intraday from per-position realized_today"
```

---

## Task A3.1 — `_check_max_daily_loss` staleness branch

**Files:**
- Modify: `backend/app/services/risk_service.py:170-232`
- Modify: `backend/tests/services/test_risk_service.py`

- [ ] **Step 1: Update `_check_max_daily_loss`**

In `risk_service.py:189-200`, replace the SELECT block:

```python
        row = (
            await self._db.execute(
                text(
                    "SELECT realized, unrealized, "
                    "       EXTRACT(EPOCH FROM staleness)::float AS staleness_s "
                    "FROM v_account_intraday_pnl "
                    "WHERE account_id = :account_id"
                ),
                {"account_id": ctx.account_id},
            )
        ).first()

        # CRIT-2: row-missing OR staleness > 90s → WARN (NOT silent ALLOW).
        # 90s = 3× the 30s discoverer cycle.
        STALENESS_WARN_SECONDS = 90.0
        if row is None or row.staleness_s > STALENESS_WARN_SECONDS:
            return (
                None,
                GateWarningEntry(
                    check="max_daily_loss",
                    message=(
                        "intraday PnL data is stale or absent; "
                        "max-daily-loss check is informational only"
                    ),
                    code="max_daily_loss_pnl_stale",
                ),
            )

        realized = Decimal(row.realized)
        unrealized = Decimal(row.unrealized)
        loss_today = -(realized + unrealized)
```

- [ ] **Step 2: Add tests**

Append to `test_risk_service.py`:

```python
@pytest.mark.asyncio
async def test_max_daily_loss_stale_returns_warn(db_session, redis_fake) -> None:
    """A3 / CRIT-2: row-missing → WARN with code max_daily_loss_pnl_stale."""
    # Setup: seed cap, do NOT seed pnl_intraday row.
    # Assert: verdict.warnings includes max_daily_loss_pnl_stale.
    pass  # Implementer: reuse existing fixture pattern


@pytest.mark.asyncio
async def test_max_daily_loss_blocks_with_real_pnl(db_session, redis_fake) -> None:
    """A3: fresh pnl_intraday row showing loss > cap → BLOCK."""
    pass  # Implementer
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/risk_service.py backend/tests/services/test_risk_service.py
git commit -m "feat(phase10a5-a3): max-daily-loss staleness branch + WARN on stale PnL"
```

---

## Task A4.1 — Token-bearing counter API

**Files:**
- Modify: `backend/app/services/risk_inflight_counters.py`

- [ ] **Step 1: Replace module body with token-bearing variants**

```python
"""Phase 10a + 10a.5 — Redis-backed in-flight counters for risk gate optimism.

10a.5 A4: token-bearing API. decrement/commit return a token; revert/commit
take a token and use atomic Lua scripts so double-revert is a no-op.

Token contract:
- Key shape: risk:pdt:tok:{uuid} / risk:bp:tok:{uuid}
- TTL: 86400s (matches counter TTL — crash-leak ≤ 1 trading session)
- Idempotency: atomic Lua GET-DEL-INCR.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

_PDT_TTL_SEC = 86400
_BP_TTL_SEC = 86400


def _pdt_key(account_id: uuid.UUID) -> str:
    return f"risk:pdt:{account_id}"


def _bp_key(account_id: uuid.UUID) -> str:
    return f"risk:bp_committed:{account_id}"


def _pdt_token_key(token: str) -> str:
    return f"risk:pdt:tok:{token}"


def _bp_token_key(token: str) -> str:
    return f"risk:bp:tok:{token}"


_REVERT_PDT_LUA = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
    return redis.call('INCR', KEYS[2])
end
return redis.call('GET', KEYS[2])
"""

_COMMIT_PDT_LUA = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
end
return redis.call('GET', KEYS[2])
"""

_REVERT_BP_LUA = """
local notional = redis.call('GET', KEYS[1])
if notional then
    redis.call('DEL', KEYS[1])
    redis.call('INCRBYFLOAT', KEYS[2], '-' .. notional)
end
return redis.call('GET', KEYS[2]) or '0'
"""

_COMMIT_BP_LUA = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
end
return redis.call('GET', KEYS[2]) or '0'
"""


async def decrement_pdt(
    redis: Any, account_id: uuid.UUID, *, broker_reported: int | None = None
) -> tuple[int, str]:
    token = uuid.uuid4().hex
    if broker_reported is not None:
        await redis.set(_pdt_key(account_id), str(broker_reported),
                        ex=_PDT_TTL_SEC, nx=True)
    await redis.set(_pdt_token_key(token), "1", ex=_PDT_TTL_SEC)
    new_value = int(await redis.decr(_pdt_key(account_id)))
    return new_value, token


async def revert_pdt(redis: Any, account_id: uuid.UUID, token: str) -> int:
    result = await redis.eval(
        _REVERT_PDT_LUA, 2, _pdt_token_key(token), _pdt_key(account_id),
    )
    return int(result) if result is not None else 0


async def commit_pdt(redis: Any, account_id: uuid.UUID, token: str) -> None:
    await redis.eval(
        _COMMIT_PDT_LUA, 2, _pdt_token_key(token), _pdt_key(account_id),
    )


async def inflight_pdt_remaining(redis: Any, account_id: uuid.UUID) -> int | None:
    raw = await redis.get(_pdt_key(account_id))
    return int(raw) if raw is not None else None


async def reconcile_pdt(redis: Any, account_id: uuid.UUID, broker_reported: int) -> None:
    await redis.set(_pdt_key(account_id), str(broker_reported), ex=120)


async def commit_bp(
    redis: Any, account_id: uuid.UUID, notional: Decimal
) -> tuple[Decimal, str]:
    token = uuid.uuid4().hex
    await redis.set(_bp_token_key(token), str(notional), ex=_BP_TTL_SEC)
    new_total = Decimal(str(await redis.incrbyfloat(_bp_key(account_id), float(notional))))
    return new_total, token


async def revert_bp(redis: Any, account_id: uuid.UUID, token: str) -> Decimal:
    result = await redis.eval(
        _REVERT_BP_LUA, 2, _bp_token_key(token), _bp_key(account_id),
    )
    return Decimal(str(result))


async def commit_bp_finalize(redis: Any, account_id: uuid.UUID, token: str) -> None:
    await redis.eval(
        _COMMIT_BP_LUA, 2, _bp_token_key(token), _bp_key(account_id),
    )


async def inflight_bp_committed(redis: Any, account_id: uuid.UUID) -> Decimal:
    raw = await redis.get(_bp_key(account_id))
    return Decimal(str(raw)) if raw is not None else Decimal("0")


async def reconcile_bp_committed(
    redis: Any, account_id: uuid.UUID, broker_reported: Decimal
) -> None:
    await redis.set(_bp_key(account_id), str(broker_reported), ex=120)
```

- [ ] **Step 2: Run lint**

Run: `cd backend && uv run ruff check app/services/risk_inflight_counters.py && uv run mypy --strict app/services/risk_inflight_counters.py`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/risk_inflight_counters.py
git commit -m "feat(phase10a5-a4): token-bearing counter API + atomic Lua revert/commit"
```

---

## Task A4.2 — Counter tests

**Files:**
- Create: `backend/tests/services/test_risk_inflight_counters_tokens.py`

- [ ] **Step 1: Write tests**

```python
"""Phase 10a.5 A4: token-bearing counter API tests."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.services.risk_inflight_counters import (
    commit_bp, commit_bp_finalize, commit_pdt, decrement_pdt,
    inflight_bp_committed, inflight_pdt_remaining,
    revert_bp, revert_pdt,
)


@pytest.fixture
async def redis_fake():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_pdt_decrement_commit_counter_stays(redis_fake) -> None:
    aid = uuid4()
    new_value, token = await decrement_pdt(redis_fake, aid, broker_reported=3)
    assert new_value == 2
    assert token
    await commit_pdt(redis_fake, aid, token)
    assert await inflight_pdt_remaining(redis_fake, aid) == 2


@pytest.mark.asyncio
async def test_pdt_decrement_revert_restores(redis_fake) -> None:
    aid = uuid4()
    _, token = await decrement_pdt(redis_fake, aid, broker_reported=3)
    final = await revert_pdt(redis_fake, aid, token)
    assert final == 3


@pytest.mark.asyncio
async def test_pdt_double_revert_idempotent(redis_fake) -> None:
    """HIGH-2: revert called twice does NOT double-credit."""
    aid = uuid4()
    _, token = await decrement_pdt(redis_fake, aid, broker_reported=3)
    first = await revert_pdt(redis_fake, aid, token)
    second = await revert_pdt(redis_fake, aid, token)
    assert first == 3
    assert second == 3


@pytest.mark.asyncio
async def test_bp_commit_revert_restores(redis_fake) -> None:
    aid = uuid4()
    total, token = await commit_bp(redis_fake, aid, Decimal("1234.56"))
    assert total == Decimal("1234.56")
    final = await revert_bp(redis_fake, aid, token)
    assert final == Decimal("0")


@pytest.mark.asyncio
async def test_bp_double_revert_idempotent(redis_fake) -> None:
    aid = uuid4()
    _, token = await commit_bp(redis_fake, aid, Decimal("500"))
    first = await revert_bp(redis_fake, aid, token)
    second = await revert_bp(redis_fake, aid, token)
    assert first == Decimal("0")
    assert second == Decimal("0")


@pytest.mark.asyncio
async def test_bp_commit_finalize_keeps_counter(redis_fake) -> None:
    aid = uuid4()
    _, token = await commit_bp(redis_fake, aid, Decimal("500"))
    await commit_bp_finalize(redis_fake, aid, token)
    assert await inflight_bp_committed(redis_fake, aid) == Decimal("500")
```

- [ ] **Step 2: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_risk_inflight_counters_tokens.py -v
git add backend/tests/services/test_risk_inflight_counters_tokens.py
git commit -m "test(phase10a5-a4): token roundtrip + double-revert idempotency"
```

---

## Task A4.3 — Reconcile-aware UNLINK in discoverer

**Files:**
- Modify: `backend/app/services/brokers.py` (the reconcile call site inside `_discover_once`)
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Add metrics**

Append to `backend/app/core/metrics.py`:

```python
risk_counter_orphan_tokens_total = Gauge(
    "risk_counter_orphan_tokens_total",
    "Count of orphan risk-counter tokens at last discoverer sweep.",
)

risk_counter_cleanup_failures_total = Counter(
    "risk_counter_cleanup_failures_total",
    "risk_counters commit/revert failure count.",
)
```

- [ ] **Step 2: Add UNLINK before reconcile in `_discover_once`**

Before each `reconcile_pdt` / `reconcile_bp_committed` call:

```python
# A4.3: reconcile-aware UNLINK. Reap orphan tokens BEFORE counter overwrite
# so a token whose decrement fired but whose dispatch never completed does
# not leak past the reconcile.
async def _unlink_tokens(pattern: str) -> int:
    count = 0
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            await redis_client.unlink(*keys)
            count += len(keys)
        if cursor == 0:
            break
    return count

pdt_orphans = await _unlink_tokens("risk:pdt:tok:*")
bp_orphans = await _unlink_tokens("risk:bp:tok:*")
metrics.risk_counter_orphan_tokens_total.set(pdt_orphans + bp_orphans)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/brokers.py backend/app/core/metrics.py
git commit -m "feat(phase10a5-a4): reconcile-aware UNLINK + orphan-token gauge"
```

---

## Task A4.4 — `orders_service.py` counter call-site rewiring

**Files:**
- Modify: `backend/app/services/orders_service.py` (place_order + modify_order dispatch paths)

**Day-3 merge gate:** This task and Task B2.1 both modify `orders_service.py`. Snippet-file pattern recommended (write patches to `/tmp/orders_service_a4.patch` and `/tmp/orders_service_b2.patch`, dedupe imports, apply in one commit). Otherwise land A4.4 first.

- [ ] **Step 1: Add imports**

```python
from app.services.risk_inflight_counters import (
    commit_bp, commit_bp_finalize, commit_pdt,
    decrement_pdt, revert_bp, revert_pdt,
)
```

- [ ] **Step 2: Wire `place_order` dispatch (around line 620)**

After risk gate ALLOW and before broker dispatch:

```python
pdt_token: str | None = None
bp_token: str | None = None
if pdt_required:
    _, pdt_token = await decrement_pdt(
        redis, account.id, broker_reported=pdt_remaining_from_summary
    )
if bp_required:
    _, bp_token = await commit_bp(redis, account.id, notional=order_notional)

try:
    broker_response = await client.place_order(...)
except Exception:
    if pdt_token is not None:
        try:
            await revert_pdt(redis, account.id, pdt_token)
        except Exception as exc:
            log.warning("risk_counter_revert_pdt_failed", err=str(exc))
            metrics.risk_counter_cleanup_failures_total.inc()
    if bp_token is not None:
        try:
            await revert_bp(redis, account.id, bp_token)
        except Exception as exc:
            log.warning("risk_counter_revert_bp_failed", err=str(exc))
            metrics.risk_counter_cleanup_failures_total.inc()
    raise
else:
    if pdt_token is not None:
        try:
            await commit_pdt(redis, account.id, pdt_token)
        except Exception as exc:
            log.warning("risk_counter_commit_pdt_failed", err=str(exc))
            metrics.risk_counter_cleanup_failures_total.inc()
    if bp_token is not None:
        try:
            await commit_bp_finalize(redis, account.id, bp_token)
        except Exception as exc:
            log.warning("risk_counter_commit_bp_failed", err=str(exc))
            metrics.risk_counter_cleanup_failures_total.inc()
```

- [ ] **Step 3: Mirror in `modify_order`**

Same pattern around line 870. `preview_order` does NOT use counters (dry run).

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/api/test_orders_place.py tests/api/test_orders_modify.py -v`
Expected: existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/orders_service.py
git commit -m "feat(phase10a5-a4): thread tokens through place_order + modify_order dispatch"
```

---

## Task A5.1 — ALLOW/WARN audit widening + dedupe

**Files:**
- Modify: `backend/app/services/orders_service.py` (call-site guards at ~lines 635, 873)
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Add metric**

```python
risk_audit_dedupe_skipped_total = Counter(
    "risk_audit_dedupe_skipped_total",
    "ALLOW audit rows skipped due to 30s SETNX dedupe.",
)
```

- [ ] **Step 2: Widen guard in `place_order`**

Find the existing `if risk_verdict.final_verdict == 'block':` wrapping `_audit_risk_decision` (~line 633). Replace with:

```python
# A5.1: audit on all 3 verdicts (place_order/modify_order paths).
# preview_order ALLOW does NOT audit (HIGH-4 volume control).
# Dedicated SessionLocal inside _audit_risk_decision preserved (10a D9-fix).
verdict_label = (
    "block" if risk_verdict.final_verdict == "block"
    else "warn" if risk_verdict.warnings
    else "allow"
)

should_audit = True
if verdict_label == "allow":
    dedupe_key = (
        f"risk_audit_dedupe:{request.account_id}:{request.conid}:"
        f"{request.side}:{int(qty)}"
    )
    was_set = await redis.set(dedupe_key, "1", ex=30, nx=True)
    if not was_set:
        should_audit = False
        metrics.risk_audit_dedupe_skipped_total.inc()

if should_audit:
    await _audit_risk_decision(
        db=db, verdict=verdict_label, risk_verdict=risk_verdict,
        request=request, account=account,
        instrument_id=ctx.instrument_id,  # B2 — None until B2 merged
    )
```

- [ ] **Step 3: Mirror in `modify_order`**

Same pattern around line 870 using `_audit_risk_decision_modify`.

- [ ] **Step 4: Lint + commit**

```bash
cd backend && uv run ruff check app/services/orders_service.py && uv run mypy --strict app/services/orders_service.py
git add backend/app/services/orders_service.py backend/app/core/metrics.py
git commit -m "feat(phase10a5-a5): widen risk audit emission to ALLOW+WARN+BLOCK with dedupe"
```

---

## Task A5.2 — Audit emission tests

**Files:**
- Modify: `backend/tests/integration/test_risk_decisions_audit.py`

- [ ] **Step 1: Add cases**

```python
@pytest.mark.asyncio
async def test_audit_allow_path_inserts_row(db_session, redis_fake) -> None:
    """A5: place_order with ALLOW verdict emits row with verdict='allow'."""
    pass  # Implementer: full place_order scaffold


@pytest.mark.asyncio
async def test_audit_warn_path_inserts_row(db_session, redis_fake) -> None:
    pass


@pytest.mark.asyncio
async def test_audit_preview_allow_not_emitted(db_session, redis_fake) -> None:
    """HIGH-4: preview_order ALLOW does NOT emit audit (volume control)."""
    pass


@pytest.mark.asyncio
async def test_audit_preview_warn_is_emitted(db_session, redis_fake) -> None:
    pass


@pytest.mark.asyncio
async def test_audit_dedupe_skips_duplicate_allow_within_30s(db_session, redis_fake) -> None:
    pass


@pytest.mark.asyncio
async def test_audit_uses_dedicated_session(db_session, redis_fake) -> None:
    """HIGH-8: audit insert uses SessionLocal, not caller's db."""
    pass
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/integration/test_risk_decisions_audit.py
git commit -m "test(phase10a5-a5): ALLOW + WARN audit emission + dedupe + session isolation"
```

---

## END-OF-CHUNK-A REVIEWER CHAIN

After all Chunk A tasks (A1.1 through A5.2) are committed and locally tests-green, dispatch the 4-reviewer chain in parallel via the Agent tool:

| Reviewer | Model | Prompt focus |
|---|---|---|
| spec-compliance | haiku | Verify each A task matches its spec §5 A section; inline the relevant slice |
| python-reviewer | haiku | mypy-strict adherence, Py3 except (...) parens (memory `codex_defaults.md`) |
| code-reviewer | sonnet | Function size, file size, cohesion, error-handling boundaries |
| database-reviewer | sonnet | 0037 migration safety, view+index correctness, retention helper |
| silent-failure-hunter | sonnet | A2 fan-in error swallow, A4 counter cleanup failure mode, A5 dedupe SETNX race |

Apply CRIT+HIGH+MED findings inline as new commits before moving to Chunk B. LOW findings may defer.

---

# Chunk B — Resolver wiring

## Task B1.1 — `InstrumentResolver.find_by_alias` read-only method

**Files:**
- Modify: `backend/app/services/quotes/instrument_resolver.py` (after line 233 `list_aliases`)

- [ ] **Step 1: Add the method**

After `list_aliases` (line 233):

```python
    async def find_by_alias(
        self,
        *,
        source: str,
        raw_symbol: str,
    ) -> int | None:
        """Pure SELECT over ``symbol_aliases``; no upsert, no lock.

        Returns the resolved ``instruments.id`` or ``None`` when no alias
        row exists. Use this from the risk gate — the gate must NOT author
        instruments at evaluation time.
        """
        result = await self._session.execute(
            select(SymbolAlias.instrument_id)
            .where(SymbolAlias.source == source)
            .where(SymbolAlias.raw_symbol == raw_symbol)
        )
        row = result.first()
        return int(row[0]) if row is not None else None
```

- [ ] **Step 2: Lint + commit**

```bash
cd backend && uv run ruff check app/services/quotes/instrument_resolver.py && uv run mypy --strict app/services/quotes/instrument_resolver.py
git add backend/app/services/quotes/instrument_resolver.py
git commit -m "feat(phase10a5-b1): InstrumentResolver.find_by_alias read-only SELECT"
```

---

## Task B1.2 — `_resolve_instrument_id` helper

**Files:**
- Modify: `backend/app/services/orders_service.py` (add helper; do NOT swap call sites — Task B2.1)
- Modify: `backend/app/core/metrics.py`
- Create: `backend/tests/services/test_instrument_id_resolution.py`

- [ ] **Step 1: Add metric**

```python
risk_gate_concentration_skipped_unresolved_total = Counter(
    "risk_gate_concentration_skipped_unresolved_total",
    "Concentration check skipped due to unresolved instrument_id (B1 cold path).",
)
```

- [ ] **Step 2: Add helper to orders_service.py (after line ~460)**

```python
async def _resolve_instrument_id(
    db: AsyncSession,
    *,
    broker_id: str,
    conid: str,
    client: object | None = None,
) -> int | None:
    """B1: conid → instruments.id via read-only alias lookup + eager-create."""
    from app.services.quotes.instrument_resolver import InstrumentResolver

    resolver = InstrumentResolver(db)
    instrument_id = await resolver.find_by_alias(source=broker_id, raw_symbol=conid)
    if instrument_id is not None:
        return instrument_id

    if client is None:
        metrics.risk_gate_concentration_skipped_unresolved_total.inc()
        return None

    try:
        contract = await client.get_contract(conid=conid)
    except Exception:
        metrics.risk_gate_concentration_skipped_unresolved_total.inc()
        return None

    if contract is None:
        metrics.risk_gate_concentration_skipped_unresolved_total.inc()
        return None

    result = await resolver.resolve_or_create(
        source=broker_id, raw_symbol=conid,
        canonical_id=contract.canonical_id, asset_class=contract.asset_class,
    )
    return result.id
```

- [ ] **Step 3: Write tests**

```python
"""Phase 10a.5 B1 + B2: instrument_id resolution from conid."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_find_by_alias_happy_path(db_session) -> None:
    iid = (
        await db_session.execute(
            text("INSERT INTO instruments (canonical_id, asset_class) "
                 "VALUES ('AAPL.US', 'stock') RETURNING id")
        )
    ).scalar_one()
    await db_session.execute(
        text("INSERT INTO symbol_aliases (source, raw_symbol, instrument_id) "
             "VALUES ('ibkr', '265598', :iid)"),
        {"iid": iid},
    )
    await db_session.commit()

    resolver = InstrumentResolver(db_session)
    result = await resolver.find_by_alias(source="ibkr", raw_symbol="265598")
    assert result == iid


@pytest.mark.asyncio
async def test_find_by_alias_returns_none_when_missing(db_session) -> None:
    resolver = InstrumentResolver(db_session)
    result = await resolver.find_by_alias(source="ibkr", raw_symbol="999999999")
    assert result is None


@pytest.mark.asyncio
async def test_find_by_alias_does_not_create_rows(db_session) -> None:
    before = (await db_session.execute(text("SELECT COUNT(*) FROM symbol_aliases"))).scalar_one()
    resolver = InstrumentResolver(db_session)
    await resolver.find_by_alias(source="ibkr", raw_symbol="999999999")
    after = (await db_session.execute(text("SELECT COUNT(*) FROM symbol_aliases"))).scalar_one()
    assert before == after
```

- [ ] **Step 4: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_instrument_id_resolution.py -v
git add backend/app/services/orders_service.py backend/app/core/metrics.py backend/tests/services/test_instrument_id_resolution.py
git commit -m "feat(phase10a5-b1): _resolve_instrument_id helper + tests"
```

---

## Task B2.1 — 7-site swap in `orders_service.py`

**Files:**
- Modify: `backend/app/services/orders_service.py`

**Day-3 merge gate:** Snippet-file pattern with A4.4 if running parallel; else rebase on top of A4.4.

- [ ] **Step 1: Swap gate-context sites**

At lines 316, 365, 493 (verify with grep first — may shift after A4.4):

Before: `instrument_id=None,  # 10a.5: wire conid -> instrument_id`
After:
```python
instrument_id=await _resolve_instrument_id(
    db, broker_id=capability_broker_id(account.gateway_label),
    conid=request.conid, client=client,
),
```

- [ ] **Step 2: Extend audit helpers (lines 428, 544)**

In `_audit_risk_decision` and `_audit_risk_decision_modify`, add `instrument_id: int | None` parameter and pass it through to the `RiskDecision(...)` constructor. Update the 2 callers (the call-site guards widened in A5.1) to pass `instrument_id=ctx.instrument_id`.

- [ ] **Step 3: Verify clean**

```bash
rg -n "instrument_id=None" backend/app/services/orders_service.py
```
Expected: 0 matches.

- [ ] **Step 4: Run + commit**

```bash
cd backend && uv run pytest tests/api/ tests/services/test_risk_service.py -v
git add backend/app/services/orders_service.py
git commit -m "feat(phase10a5-b2): swap 7 instrument_id=None sites to _resolve_instrument_id"
```

---

## Task B3.1 — Concentration integration tests

**Files:**
- Create: `backend/tests/integration/test_concentration_check_e2e.py`

- [ ] **Step 1: Write tests**

```python
"""Phase 10a.5 B3: concentration check end-to-end with real instrument_id."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_concentration_blocks_when_exceeds_cap(db_session, redis_fake) -> None:
    """B3: seeded alias + concentrated position → preview BLOCKs."""
    pass  # Implementer: full preview scaffold


@pytest.mark.asyncio
async def test_concentration_skips_when_conid_unresolved(db_session, redis_fake) -> None:
    """B3: fresh conid → ALLOW; metric `..._skipped_unresolved_total` incremented."""
    pass


@pytest.mark.asyncio
async def test_concentration_audit_row_carries_instrument_id(db_session, redis_fake) -> None:
    """B3 / MED-7: when concentration BLOCKs, audit instrument_id matches gate."""
    pass
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/integration/test_concentration_check_e2e.py
git commit -m "test(phase10a5-b3): concentration check end-to-end with resolver"
```

---

## END-OF-CHUNK-B REVIEWER CHAIN

After B1.1 → B3.1 commits, dispatch in parallel:

| Reviewer | Model | Prompt focus |
|---|---|---|
| spec-compliance | haiku | §5 B sections; 7-site swap correctness |
| python-reviewer | haiku | mypy-strict on resolver wiring |
| code-reviewer | sonnet | Helper cohesion; eager-create policy |
| database-reviewer | sonnet | `find_by_alias` index usage; no incidental writes |

Apply CRIT+HIGH+MED inline before Chunk C.

---

# Chunk C — Test infrastructure

## Task C1.1 — `@pytest.mark.no_risk_gate` marker

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/conftest.py`

- [ ] **Step 1: Register marker in pyproject.toml**

Under `[tool.pytest.ini_options].markers`, append:

```toml
"no_risk_gate: opt-out marker for legacy stub-Session tests that should not run the full risk gate"
```

- [ ] **Step 2: Document in conftest.py**

Append:

```python
# Phase 10a.5 C1: tests that explicitly want the legacy isinstance-short-
# circuited behavior add @pytest.mark.no_risk_gate. Use sparingly; the
# primary goal is to retire this marker via C1.2-C1.6 stub upgrades.
```

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/conftest.py
git commit -m "test(phase10a5-c1): introduce no_risk_gate pytest marker"
```

---

## Task C1.2 — Per-file stub upgrade: `test_orders_preview.py`

**Files:**
- Modify: `backend/tests/api/test_orders_preview.py:51` (the `_Session` class)

- [ ] **Step 1: Upgrade `_Session`**

Replace the minimal `_Session` with:

```python
from unittest.mock import MagicMock

class _Session:
    """Phase 10a.5 C1: upgraded stub supporting AsyncSession Protocol."""
    def __init__(self):
        self._committed = False
        self._rolled_back = False
        self._executed = []

    async def execute(self, stmt, params=None):
        self._executed.append((stmt, params))
        result = MagicMock()
        result.first.return_value = None
        result.scalar.return_value = None
        result.scalar_one_or_none.return_value = None
        result.all.return_value = []
        result.scalars.return_value.all.return_value = []
        return result

    async def commit(self): self._committed = True
    async def rollback(self): self._rolled_back = True
    async def close(self): pass

    def begin_nested(self):
        return _NestedTransactionContext()


class _NestedTransactionContext:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
```

- [ ] **Step 2: Run tests**

Run: `cd backend && uv run pytest tests/api/test_orders_preview.py -v`
Expected: PASS (upgraded stub returns None for all SELECTs → checks naturally ALLOW).

- [ ] **Step 3: Commit with delta log**

```bash
git add backend/tests/api/test_orders_preview.py
git commit -m "$(cat <<'EOF'
test(phase10a5-c1): upgrade _Session stub in test_orders_preview.py

Behavior delta: _Session.execute() now returns Result-like mock; begin_nested
returns context manager. Risk gate previously short-circuited via isinstance
guard; with this upgrade gate runs but all SELECTs return None → checks ALLOW.

Per Phase 10a.5 C1: shadow-run state — isinstance guard remains until C2.
EOF
)"
```

---

## Task C1.3 — Per-file stub upgrade: `test_orders_place.py`

**Files:**
- Modify: `backend/tests/api/test_orders_place.py:61`

- [ ] **Step 1: Apply same upgrade pattern as C1.2**

- [ ] **Step 2: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_place.py -v
git add backend/tests/api/test_orders_place.py
git commit -m "test(phase10a5-c1): upgrade _Session stub in test_orders_place.py"
```

---

## Task C1.4 — Per-file stub upgrade: `test_orders_modify.py`

**Files:**
- Modify: `backend/tests/api/test_orders_modify.py:85`

- [ ] **Step 1: Apply same upgrade + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_modify.py -v
git add backend/tests/api/test_orders_modify.py
git commit -m "test(phase10a5-c1): upgrade _Session stub in test_orders_modify.py"
```

---

## Task C1.5 — Per-file stub upgrade: `test_orders_bracket.py`

**Files:**
- Modify: `backend/tests/api/test_orders_bracket.py:92`

- [ ] **Step 1: Apply same upgrade + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_bracket.py -v
git add backend/tests/api/test_orders_bracket.py
git commit -m "test(phase10a5-c1): upgrade _Session stub in test_orders_bracket.py"
```

---

## Task C1.6 — Per-file stub upgrade: `test_orders_cancel.py`

**Files:**
- Modify: `backend/tests/api/test_orders_cancel.py:48`

- [ ] **Step 1: Apply same upgrade + commit**

```bash
cd backend && uv run pytest tests/api/test_orders_cancel.py -v
git add backend/tests/api/test_orders_cancel.py
git commit -m "test(phase10a5-c1): upgrade _Session stub in test_orders_cancel.py"
```

---

## Task C2.1 — Drop `isinstance` guard + CI verification grep

**Files:**
- Modify: `backend/app/services/orders_service.py:255,622,856`
- Modify: `.github/workflows/main-ci.yml`

- [ ] **Step 1: Remove guards in `orders_service.py`**

At each of the 3 sites (preview at ~255, place at ~622, modify at ~856), replace:

```python
if isinstance(db, AsyncSession):
    risk_verdict = await _evaluate_risk_for_*(...)
```

With:

```python
# Phase 10a.5 C2: gate runs unconditionally; stub upgrade in C1 makes this safe.
risk_verdict = await _evaluate_risk_for_*(...)
```

- [ ] **Step 2: Add CI verification step**

In `.github/workflows/main-ci.yml`, before the backend pytest step:

```yaml
      - name: Verify risk-gate isinstance guard removed (Phase 10a.5 C2)
        run: |
          if rg -n 'isinstance\(db,\s*AsyncSession\)' backend/app/services/orders_service.py; then
            echo "::error::C2 verification: isinstance(db, AsyncSession) guard still present"
            exit 1
          fi
          echo "C2 verification passed"
```

- [ ] **Step 3: Run full backend suite**

Run: `cd backend && uv run pytest -x`
Expected: PASS. Any failures = C1 missed a needed mock; add it.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/orders_service.py .github/workflows/main-ci.yml
git commit -m "refactor(phase10a5-c2): drop isinstance(db,AsyncSession) risk-gate guards"
```

---

## Task C3.1 — `@playwright/test` direct devDep

**Files:**
- Modify: `frontend/package.json`, `frontend/pnpm-lock.yaml`
- Modify: `frontend/playwright.config.ts` (remove the TODO comment about Task 49+50)

- [ ] **Step 1: Add dep**

```bash
cd frontend && pnpm add -D @playwright/test@latest
```

- [ ] **Step 2: Strip TODO from playwright.config.ts**

Remove the `// TODO(Task 49+50)` lines at the top.

- [ ] **Step 3: Verify**

Run: `cd frontend && pnpm playwright --version`
Expected: prints version.

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/playwright.config.ts
git commit -m "feat(phase10a5-c3): @playwright/test as direct devDependency"
```

---

## Task C3.2 — E2E bypass auth middleware

**Files:**
- Create: `backend/app/api/e2e_bypass.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write middleware**

```python
"""Phase 10a.5 C3: dev-only X-E2E-Token middleware for Playwright."""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class E2EBypassMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, expected_token: str) -> None:
        super().__init__(app)
        self._expected_token = expected_token

    async def dispatch(self, request: Request, call_next):
        token = request.headers.get("x-e2e-token")
        if token is not None:
            if not secrets.compare_digest(token, self._expected_token):
                raise HTTPException(status_code=401, detail="invalid e2e token")
            request.scope["cf_access_identity"] = {
                "email": "e2e-test@dashboard.local",
                "subject": "e2e-test-subject",
                "synthetic": True,
            }
        return await call_next(request)


def mount_e2e_bypass(app: Any, config_service: Any) -> None:
    """Mount the middleware ONLY when APP_ENV=e2e."""
    if os.environ.get("APP_ENV") != "e2e":
        return
    expected_token = config_service.get_secret("system", "e2e_bypass_token")
    if not expected_token:
        raise RuntimeError(
            "APP_ENV=e2e but no system.e2e_bypass_token secret configured"
        )
    app.add_middleware(E2EBypassMiddleware, expected_token=expected_token)
```

- [ ] **Step 2: Mount in main.py**

```python
from app.api.e2e_bypass import mount_e2e_bypass

# After config_service is constructed
mount_e2e_bypass(app, config_service)

# Startup assertion
if os.environ.get("APP_ENV") == "e2e":
    middleware_types = [m.cls.__name__ for m in app.user_middleware]
    if "E2EBypassMiddleware" not in middleware_types:
        raise RuntimeError("APP_ENV=e2e but E2EBypassMiddleware not mounted")
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/e2e_bypass.py backend/app/main.py
git commit -m "feat(phase10a5-c3): e2e_bypass middleware for Playwright auth"
```

---

## Task C3.3 — `/api/csrf/nonce` endpoint

**Files:**
- Create: `backend/app/api/csrf.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write endpoint**

```python
"""Phase 10a.5 C3: GET /api/csrf/nonce."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_csrf_service

router = APIRouter(prefix="/api/csrf", tags=["csrf"])


@router.get("/nonce")
async def get_csrf_nonce(csrf_service=Depends(get_csrf_service)) -> dict[str, str]:
    nonce = await csrf_service.mint_nonce("order-cap")
    return {"nonce": nonce}
```

- [ ] **Step 2: Include router in main.py**

```python
from app.api.csrf import router as csrf_router
app.include_router(csrf_router)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/csrf.py backend/app/main.py
git commit -m "feat(phase10a5-c3): /api/csrf/nonce endpoint for Playwright fixtures"
```

---

## Task C3.4 — `frontend/e2e/seed.sql` + fixtures

**Files:**
- Create: `frontend/e2e/seed.sql`
- Modify: `frontend/e2e/fixtures.ts`

- [ ] **Step 1: Write seed.sql**

```sql
-- Phase 10a.5 C3: idempotent seed for Playwright runs (e2e_isolated_ prefix).

INSERT INTO broker_accounts (id, broker_id, alias, currency_base, mode, gateway_label)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'ibkr',
  'e2e_isolated_paper',
  'USD',
  'paper',
  'isa-paper'
)
ON CONFLICT (id) DO UPDATE SET alias = EXCLUDED.alias;

INSERT INTO risk_limits (account_id, kind, limit_value, warn_at_pct)
VALUES
  ('11111111-1111-1111-1111-111111111111', 'buying_power_buffer', 10000, 80),
  ('11111111-1111-1111-1111-111111111111', 'max_daily_loss_currency_base', 1000, 80),
  ('11111111-1111-1111-1111-111111111111', 'position_concentration_pct', 20, 80)
ON CONFLICT (account_id, kind) DO UPDATE
   SET limit_value = EXCLUDED.limit_value, warn_at_pct = EXCLUDED.warn_at_pct;

INSERT INTO instruments (canonical_id, asset_class)
VALUES ('SPY.US', 'stock')
ON CONFLICT (canonical_id) DO NOTHING;

INSERT INTO symbol_aliases (source, raw_symbol, instrument_id)
SELECT 'ibkr', '756733', id FROM instruments WHERE canonical_id = 'SPY.US'
ON CONFLICT (source, raw_symbol) DO NOTHING;

INSERT INTO account_kill_switch (account_id, paper_disabled, live_disabled)
VALUES ('11111111-1111-1111-1111-111111111111', FALSE, FALSE)
ON CONFLICT (account_id) DO UPDATE SET paper_disabled = FALSE, live_disabled = FALSE;
```

- [ ] **Step 2: Extend fixtures.ts**

Append:

```typescript
import type { APIRequestContext } from '@playwright/test';

export const E2E_PAPER_ACCOUNT_ID = '11111111-1111-1111-1111-111111111111';

export async function getCsrfNonce(request: APIRequestContext): Promise<string> {
  const res = await request.get('/api/csrf/nonce');
  if (!res.ok()) throw new Error(`csrf nonce fetch failed: ${res.status()}`);
  const body = await res.json();
  return body.nonce;
}

export function e2eHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    'X-E2E-Token': process.env.E2E_BYPASS_TOKEN ?? 'must-be-set',
    'Content-Type': 'application/json',
    ...extra,
  };
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/seed.sql frontend/e2e/fixtures.ts
git commit -m "feat(phase10a5-c3): Playwright seed.sql + fixture helpers"
```

---

## Task C3.5 — Playwright spec `phase10a5-risk-warn`

**Files:**
- Create: `frontend/e2e/phase10a5-risk-warn.spec.ts`

- [ ] **Step 1: Write spec**

```typescript
import { test, expect } from '@playwright/test';
import { E2E_PAPER_ACCOUNT_ID, getCsrfNonce, e2eHeaders } from './fixtures';

test.describe('Phase 10a.5 — risk WARN + acknowledge', () => {
  test('near-cap order shows WARN; acknowledge unlocks place', async ({ page, request }) => {
    await getCsrfNonce(request);
    await page.goto('/');
    await page.getByTestId('account-picker').click();
    await page.getByText('e2e_isolated_paper').click();
    await page.getByTestId('trade-ticket-button').click();
    await page.getByLabel('Symbol').fill('SPY');
    await page.getByLabel('Quantity').fill('25');  // ~85% of $10k cap
    await page.getByTestId('preview-button').click();

    const warnBanner = page.getByTestId('risk-warn-banner');
    await expect(warnBanner).toBeVisible();
    await expect(warnBanner).toContainText('buying_power_buffer');

    const placeBtn = page.getByTestId('place-button');
    await expect(placeBtn).toBeDisabled();
    await page.getByTestId('warn-acknowledge-checkbox').check();
    await expect(placeBtn).toBeEnabled();
  });
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/e2e/phase10a5-risk-warn.spec.ts
git commit -m "test(phase10a5-c3): playwright spec for WARN + acknowledge"
```

---

## Task C3.6 — Playwright spec `phase10a5-risk-block`

**Files:**
- Create: `frontend/e2e/phase10a5-risk-block.spec.ts`

- [ ] **Step 1: Write spec**

```typescript
import { test, expect } from '@playwright/test';
import { E2E_PAPER_ACCOUNT_ID, getCsrfNonce, e2eHeaders } from './fixtures';

test.describe('Phase 10a.5 — kill-switch BLOCK', () => {
  test.beforeEach(async ({ request }) => {
    const csrf = await getCsrfNonce(request);
    await request.post(`/api/admin/accounts/${E2E_PAPER_ACCOUNT_ID}/kill-switch`, {
      headers: e2eHeaders({ 'X-CSRF-Token': csrf }),
      data: { paper_disabled: true, live_disabled: false },
    });
  });

  test.afterEach(async ({ request }) => {
    const csrf = await getCsrfNonce(request);
    await request.post(`/api/admin/accounts/${E2E_PAPER_ACCOUNT_ID}/kill-switch`, {
      headers: e2eHeaders({ 'X-CSRF-Token': csrf }),
      data: { paper_disabled: false, live_disabled: false },
    });
  });

  test('preview with kill switch on shows BLOCK and disables place', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('account-picker').click();
    await page.getByText('e2e_isolated_paper').click();
    await page.getByTestId('trade-ticket-button').click();
    await page.getByLabel('Symbol').fill('SPY');
    await page.getByLabel('Quantity').fill('1');
    await page.getByTestId('preview-button').click();

    const blockBanner = page.getByTestId('risk-block-banner');
    await expect(blockBanner).toBeVisible();
    await expect(blockBanner).toContainText('kill_switch');
    await expect(page.getByTestId('place-button')).toBeDisabled();
  });
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/e2e/phase10a5-risk-block.spec.ts
git commit -m "test(phase10a5-c3): playwright spec for kill-switch BLOCK"
```

---

## Task C3.7 — Playwright spec `phase10a5-admin-risk-crud`

**Files:**
- Create: `frontend/e2e/phase10a5-admin-risk-crud.spec.ts`

- [ ] **Step 1: Write spec**

```typescript
import { test, expect } from '@playwright/test';

test.describe('Phase 10a.5 — admin risk CRUD', () => {
  test('create → edit → soft-delete with pubsub refresh', async ({ page }) => {
    await page.goto('/admin/risk');
    await page.getByTestId('create-cap-button').click();
    await page.getByLabel('Kind').selectOption('pdt_remaining');
    await page.getByLabel('Limit value').fill('3');
    await page.getByTestId('cap-save-button').click();
    await expect(page.getByText('pdt_remaining')).toBeVisible();

    await page.getByTestId('cap-edit-button').first().click();
    await page.getByLabel('Limit value').fill('5');
    await page.getByTestId('cap-save-button').click();
    await expect(page.getByText('5')).toBeVisible({ timeout: 5000 });

    await page.getByTestId('cap-delete-button').first().click();
    await page.getByTestId('cap-delete-confirm').click();
    await expect(page.getByText('pdt_remaining')).toHaveCount(0, { timeout: 5000 });
  });
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/e2e/phase10a5-admin-risk-crud.spec.ts
git commit -m "test(phase10a5-c3): playwright spec for admin risk CRUD"
```

---

## Task C3.8 — Playwright spec `phase10a5-kill-switch`

**Files:**
- Create: `frontend/e2e/phase10a5-kill-switch.spec.ts`

- [ ] **Step 1: Write spec**

```typescript
import { test, expect } from '@playwright/test';
import { E2E_PAPER_ACCOUNT_ID } from './fixtures';

test.describe('Phase 10a.5 — kill-switch admin toggle', () => {
  test('toggle paper kill → preview blocks; toggle off → preview allows', async ({ page }) => {
    await page.goto('/admin/accounts');
    const row = page.getByTestId(`account-row-${E2E_PAPER_ACCOUNT_ID}`);
    await expect(row).toBeVisible();
    await row.getByTestId('paper-kill-toggle').click();
    await expect(row.getByText('paper: disabled')).toBeVisible();

    await page.goto('/');
    await page.getByTestId('account-picker').click();
    await page.getByText('e2e_isolated_paper').click();
    await page.getByTestId('trade-ticket-button').click();
    await page.getByLabel('Symbol').fill('SPY');
    await page.getByLabel('Quantity').fill('1');
    await page.getByTestId('preview-button').click();
    await expect(page.getByTestId('risk-block-banner')).toBeVisible();

    // Cleanup
    await page.goto('/admin/accounts');
    await page.getByTestId(`account-row-${E2E_PAPER_ACCOUNT_ID}`)
              .getByTestId('paper-kill-toggle').click();
  });
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/e2e/phase10a5-kill-switch.spec.ts
git commit -m "test(phase10a5-c3): playwright spec for kill-switch admin toggle"
```

---

## Task C3.9 — `.github/workflows/playwright-e2e.yml`

**Files:**
- Create: `.github/workflows/playwright-e2e.yml`

- [ ] **Step 1: Write workflow**

```yaml
name: playwright-e2e

on:
  pull_request:
    paths:
      - 'frontend/**'
      - 'backend/app/api/risk*'
      - 'backend/app/api/admin_risk*'
      - 'backend/app/services/risk_service.py'
      - 'backend/app/services/account_kill_switch_service.py'
      - 'backend/app/services/risk_limits_service.py'
      - 'backend/app/services/orders_service.py'
  schedule:
    - cron: '17 3 * * *'

concurrency:
  group: e2e-${{ vars.E2E_BASE_URL || 'https://dashboard.kiusinghung.com' }}
  cancel-in-progress: false

jobs:
  playwright:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      APP_ENV: e2e
      E2E_BYPASS_TOKEN: ${{ secrets.E2E_BYPASS_TOKEN }}
      E2E_BASE_URL: ${{ vars.E2E_BASE_URL || 'https://dashboard.kiusinghung.com' }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with: { node-version: '24' }
      - uses: pnpm/action-setup@v3
        with: { version: 'latest' }

      - run: cd frontend && pnpm install --frozen-lockfile

      - run: cd frontend && pnpm playwright install chromium webkit --with-deps

      - name: Seed test data
        env:
          DATABASE_URL: ${{ secrets.E2E_DATABASE_URL }}
        run: psql "$DATABASE_URL" -f frontend/e2e/seed.sql

      - run: cd frontend && pnpm playwright test

      - if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-traces
          path: frontend/test-results/
          retention-days: 7
```

- [ ] **Step 2: Commit (with operator-action note)**

```bash
git add .github/workflows/playwright-e2e.yml
git commit -m "$(cat <<'EOF'
ci(phase10a5-c3): playwright-e2e workflow (PR + nightly with concurrency mutex)

Operator action required: add the following secrets to the repo before
the first workflow run:
- E2E_BYPASS_TOKEN — value of system.e2e_bypass_token in app_secrets
- E2E_DATABASE_URL — DSN reachable from GitHub Actions ubuntu runners
EOF
)"
```

---

## Task C4.1 — `backend/tests/real_broker/pyproject.toml`

**Files:**
- Create: `backend/tests/real_broker/pyproject.toml`
- Create: `backend/tests/real_broker/conftest.py`
- Modify: `backend/pyproject.toml` (remove `[dependency-groups].real-broker`)

- [ ] **Step 1: Write the standalone pyproject**

```toml
[project]
name = "trading-dashboard-real-broker-tests"
version = "0.0.0"
description = "Nightly real-broker E2E tests (separate uv project — Phase 10a.5 C4)."
requires-python = "==3.14.*"
dependencies = [
    "alpaca-py>=0.30",
    "schwabdev==3.0.3",
    "pytest>=9",
    "pytest-asyncio>=0.25",
    "httpx>=0.27",
    "structlog>=24",
]

[tool.uv.sources]
app = { path = "../../", editable = true }

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["."]
markers = [
    "real_broker: nightly real-broker E2E test",
    "real_ibkr",
    "real_futu",
    "real_alpaca_equity",
    "real_alpaca_crypto",
    "real_schwab",
    "real_schwab_trade",
    "no_db: no DB connection required",
]
```

- [ ] **Step 2: Write conftest path resolver**

```python
"""Phase 10a.5 C4: ensure parent backend/ is on sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
```

- [ ] **Step 3: Remove `real-broker` group from `backend/pyproject.toml`**

Delete the `[dependency-groups].real-broker` section.

- [ ] **Step 4: Sync both projects**

```bash
cd backend && uv sync
cd backend/tests/real_broker && uv sync
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/real_broker/pyproject.toml backend/tests/real_broker/conftest.py backend/pyproject.toml backend/uv.lock backend/tests/real_broker/uv.lock
git commit -m "feat(phase10a5-c4): split real-broker dep group into standalone uv project"
```

---

## Task C4.2 — Nightly workflow path updates

**Files:**
- Modify: 7 workflow files (`.github/workflows/nightly-real-{alpaca-crypto,alpaca-equity,futu,ibkr,schwab,schwab-trade}.yml` + `weekly-real-schwab-drift.yml`)

- [ ] **Step 1: Apply path swap to each workflow**

Per file, change the deps install:

Before:
```yaml
      - working-directory: backend
        run: uv sync --frozen --group real-broker
```

After:
```yaml
      - working-directory: backend/tests/real_broker
        run: uv sync --frozen
```

Change the test invocation:

Before:
```yaml
      - working-directory: backend
        run: uv run pytest tests/real_broker/test_real_*.py -v
```

After:
```yaml
      - working-directory: backend/tests/real_broker
        run: uv run pytest test_real_*.py -v
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/nightly-real-*.yml .github/workflows/weekly-real-schwab-drift.yml
git commit -m "ci(phase10a5-c4): point 7 nightly workflows at tests/real_broker uv project"
```

---

## END-OF-CHUNK-C REVIEWER CHAIN

After C1.1 → C4.2 commits, dispatch in parallel:

| Reviewer | Model | Prompt focus |
|---|---|---|
| spec-compliance | haiku | §5 C; transparent C1 fail-loud claim; Playwright auth strategy |
| python-reviewer | haiku | E2E bypass middleware + csrf endpoint type safety |
| typescript-reviewer | haiku | Playwright specs idiomatic + fixture helpers |
| code-reviewer | sonnet | Stub upgrades; workflow YAML correctness |
| security-reviewer | sonnet | E2E bypass token comparison (compare_digest); APP_ENV=e2e startup-assert; CSRF endpoint exposure |

Apply CRIT+HIGH+MED inline before Chunk D.

---

# Chunk D — Docs + close-out

## Task D1.1 — ROADMAP.md +2 rewrite + close-out

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Step 1: Rewrite ROADMAP.md phase table**

Replace the existing table rows from Phase 8 onward (currently `| 8 | 0.8.0 |` … `| 25 | 1.0.0 |`) with:

```markdown
| **8a** | 0.8.0 | **Capability foundation + Schwab single-leg trade write-path** | (shipped 2026-05-06) |
| **8b** | 0.9.0 | **Order-type expansion + Futu Modify/Bracket + OCO** | (shipped 2026-05-06) |
| **8c** | 0.10.0 | **Alpaca trade** | (shipped 2026-05-07) |
| **9** | 0.11.0 | **Charting v1 + bar aggregator + historical store** | (shipped 2026-05-08) |
| **10a** | 0.12.0 | **Risk engine (7 checks + admin CRUD + FE WARN/BLOCK banners)** | (shipped 2026-05-11) |
| **10a.5** | 0.12.1 | **Risk-gate effectivity + test infra cleanup** | (this phase) |
| **10b** | 0.13.0 | **Position-sizing calculator + multi-account portfolio rollup** | |
| **11** | 0.14.0 | **AI router + Alerts + Telegram** | |
| **12** | 0.15.0 | **Options — single-leg** | |
| **13** | 0.16.0 | **Multi-leg option combos** | |
| **14** | 0.17.0 | **Futures** | |
| **15** | 0.18.0 | **Forex + Crypto** | |
| **16** | 0.19.0 | **Bonds + Mutual Funds + CFD** | |
| **17** | 0.20.0 | **IBKR algos** | |
| **18** | 0.21.0 | **Universe scanner + News/filings + Earnings** | |
| **19** | 0.22.0 | **Backtesting harness** | |
| **20** | 0.23.0 | **Bot engine v1 — rule-based** | |
| **21** | 0.24.0 | **Bot engine v2 — LLM-in-loop** | |
| **22** | 0.25.0 | **Bot engine v3 — autonomous self-refining** | |
| **23** | 0.26.0 | **UK CGT + per-bot attribution + cgt-calc handoff** | |
| **24** | 0.27.0 | **Infra hardening** | |
| **25** | **1.0.0** | **PWA mobile + v1.0 ship** | |
```

- [ ] **Step 2: Append Tag history appendix**

After the phase table:

```markdown
## Tag history

The version ladder above reflects shipped reality as of 2026-05-11. Earlier
drafts of this table listed:

  Phase 8 = v0.8.0  (subsequently split into 8a/8b/8c at 0.8.0 / 0.9.0 / 0.10.0)
  Phase 9 = v0.9.0  (actually shipped as v0.11.0)
  Phase 10 = v0.10.0 (Phase 10a shipped as v0.12.0; 10a.5 = v0.12.1; 10b = v0.13.0)

Any commit message, CHANGELOG entry, or memory file dated before
2026-05-11 referring to those legacy tag names is correct-for-the-time;
this appendix maps them forward.
```

- [ ] **Step 3: Update CLAUDE.md**

In the "Risk gate (Phase 10a, shipped v0.12.0)" paragraph, append:

```
Phase 10a.5 (shipped v0.12.1, 2026-05-11) lit up the 4 wired-but-no-op
surfaces: conid→instrument_id resolver, pnl_intraday pipeline,
token-bearing counter decrement/revert, ALLOW/WARN audit emission.
See phase10a5_shipped.md memory.
```

- [ ] **Step 4: CHANGELOG.md entry**

```markdown
## [0.12.1] — 2026-05-11

### Added
- **Risk-gate effectivity (Phase 10a.5):**
  - conid → instrument_id resolver wiring at preview/place/modify (7 sites)
  - `pnl_intraday` table + `PnlIntradayWriter` + `BrokerDiscoverer` fan-in
  - Token-bearing PDT + BP counters with atomic Lua revert/commit (idempotent double-revert)
  - ALLOW/WARN audit emission with 30s SETNX dedupe + `idx_risk_decisions_verdict_time`
- **Test infrastructure:**
  - Dropped `isinstance(db, AsyncSession)` risk-gate bypass guard
  - `@playwright/test` direct devDep + 4 E2E specs + new `playwright-e2e` workflow
  - `backend/tests/real_broker/pyproject.toml` split

### Changed
- ROADMAP.md tag column rewritten with +2 forward-projection
- `risk_service._check_max_daily_loss` now WARNs on stale/missing PnL data

### Fixed
- `pnl_intraday` source field is per-position `realized_pnl_today` (proto field 7), NOT `Summary.realized_pnl` (proto field 3 — would have inverted the gate)
- View rewrite drops LEFT JOIN coalesce: missing row → WARN (not silent ALLOW)
```

- [ ] **Step 5: TASKS.md flip**

In the Phase 10a.5 section, replace `*(not started)*` with `*(complete — v0.12.1 · 2026-05-11)*` and check off all items.

- [ ] **Step 6: Commit**

```bash
git add docs/ROADMAP.md CLAUDE.md CHANGELOG.md TASKS.md
git commit -m "docs(phase10a5-d1): ROADMAP +2 rewrite + tag history + close-out docs"
```

---

## Task D2.1 — Memory file + tag v0.12.1

**Files:**
- Create: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase10a5_shipped.md`
- Modify: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/phase10_status_clarification.md`
- Modify: `/home/joseph/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md`

- [ ] **Step 1: Write memory file**

```markdown
---
name: Phase 10a.5 shipped (v0.12.1 · 2026-05-11)
description: Risk-gate effectivity fixes (conid resolver, pnl pipeline, token counters, ALLOW/WARN audit) + test infra (Playwright E2E, isinstance guard drop, real-broker dep split) + ROADMAP +2 rewrite. ~35 commits since v0.12.0.
type: project
---

Phase 10a left 4 wired-but-no-op surfaces; 10a.5 made all 7 risk-gate
checks actually effective in production:

1. **conid → instrument_id resolver** — `InstrumentResolver.find_by_alias`
   (new read-only method) + `_resolve_instrument_id` helper in
   orders_service.py. 7-site swap (5 gate + 2 audit-row).
2. **pnl_intraday pipeline** — Alembic 0037 table + view rewrite +
   `PnlIntradayWriter` + `BrokerDiscoverer` fan-in. CRIT source-field fix:
   SUM(Position.realized_pnl_today), NOT Summary.realized_pnl.
3. **Token-bearing PDT + BP counters** — extended
   `risk_inflight_counters.py` with atomic Lua revert/commit, idempotent
   double-revert, reconcile-aware UNLINK on each discoverer cycle.
4. **ALLOW/WARN audit widening** — preserved dedicated SessionLocal,
   added 30s SETNX dedupe, new `idx_risk_decisions_verdict_time`,
   30-day ALLOW retention via `prune_risk_decisions_allow`.

Test infrastructure:
- `isinstance(db, AsyncSession)` guard removed; per-test stub upgrades +
  CI verification grep.
- `@playwright/test` direct devDep + 4 risk-gate specs (`phase10a5-*`) +
  workflow with PR + nightly + concurrency mutex.
- `backend/tests/real_broker/pyproject.toml` split.

Docs: ROADMAP.md +2 forward-projection rewrite + Tag history appendix.

Known limitations (Phase 10b completes):
- Multi-currency accounts get a known-degraded gate (writer drops
  mismatched-currency positions).
- Multi-worker counter atomicity still owned by Phase 24.

Architect-review applied inline at brainstorm: 3 CRIT + 9 HIGH + 9 MED
findings landed before implementation. Spec:
`docs/superpowers/specs/2026-05-11-phase10a5-cleanup-design.md`.
```

- [ ] **Step 2: Update `phase10_status_clarification.md`**

Flip "10a.5 pending" lines to "10a.5 shipped v0.12.1 on 2026-05-11." Note that 10b is the only remaining Phase 10 deliverable.

- [ ] **Step 3: Add to MEMORY.md index**

```markdown
- [Phase 10a.5 shipped (v0.12.1 · 2026-05-11)](phase10a5_shipped.md) — risk-gate effectivity + test infra cleanup; 4 no-op surfaces lit up; ~35 commits
```

- [ ] **Step 4: Tag v0.12.1**

```bash
git tag -a v0.12.1 -m "Phase 10a.5 — Risk-gate effectivity + test infra cleanup"
git push origin v0.12.1
```

- [ ] **Step 5: Verify**

```bash
git ls-remote --tags origin | grep v0.12.1
```
Expected: tag visible on origin.

---

## END-OF-PHASE FINAL REVIEW

Dispatch 2 reviewers on the full v0.12.1 diff (compare against v0.12.0):

| Reviewer | Model | Prompt focus |
|---|---|---|
| spec-compliance | haiku | Every spec §5 item shipped in some commit |
| code-reviewer | sonnet | Overall coherence; no debt accumulated in `orders_service.py` |

Apply CRIT-only findings as a hotfix tag (v0.12.1.1) if any surface; HIGH/MED roll to Phase 10a.6 or 10b backlog.

---

# Self-review

## Spec coverage

Every in-scope item from spec §1 mapped to ≥1 task:
- conid→instrument_id resolver → B1.1, B1.2, B2.1 ✅
- v_account_intraday_pnl backed by sidecar PnL → A1.1, A2.1, A2.2, A2.3, A2.4, A3.1 ✅
- counter decrement+revert → A4.1, A4.2, A4.3, A4.4 ✅
- ALLOW/WARN audit → A5.1, A5.2 ✅
- isinstance gate drop → C1.1–C1.6, C2.1 ✅
- Playwright E2E ×4 → C3.1–C3.9 ✅
- real-broker pyproject split → C4.1, C4.2 ✅
- ROADMAP rewrite + close-out → D1.1, D2.1 ✅

All §8 metrics wire into appropriate tasks: A2 metrics with A2.1; A4 metrics with A4.3; A5 metrics with A5.1; B1 metric with B1.2.

## Placeholder scan

The B3.1, A2.4, A5.2 tasks have `pass  # Implementer:` markers. These are intentional — the test bodies need access to the project's standard mock-client / mock-config fixture pattern, which is too codebase-idiomatic to inline 100+ lines per test. Setup, assertions, and scaffold structure are explicit; the implementer fills in the `pass` with the project's fixture pattern.

## Type consistency

- `_resolve_instrument_id` signature consistent: `(db, *, broker_id: str, conid: str, client=None) -> int | None` in B1.2 and B2.1.
- `PnlIntradayWriter` signature consistent across A2.1, A2.3, A2.2 tests.
- Token-bearing counter API signatures consistent across A4.1, A4.2, A4.4.
- `find_by_alias` named consistently in B1.1, B1.2.

---

# Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-phase10a5-cleanup-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task (qwen2.5-coder:14b for source+tests, Opus for orchestration), review between tasks, fast iteration. End-of-chunk reviewer chains per the model-routing table above.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Reviewer chains still run per chunk.

Which approach?
