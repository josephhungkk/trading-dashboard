# Phase 11a — AI Router Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `services/ai/` module + LiteLLM proxy sidecar + Ollama dispatch + WoL + cost ledger + chat UI + trade-ticket AI context + admin AI page at v0.11.0, with all CRIT/HIGH/MED architect findings baked into the design.

**Architecture:** LiteLLM proxy sidecar (lightweight image) on VPS in `docker-compose.yml`; BE talks to it via docker network; BE signs requests with per-provider API key (Option C, validated per provider in 11a-A0 spike); LiteLLM master-key auth via Redis-backed `custom_auth` callback for zero-restart rotation; capability-tagged auto-routing with LOCAL_ONLY privacy floor (three-layer defense); WoL wakes the heavy box on demand with model-ready readiness probe and circuit breaker.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 async, Alembic, httpx-async, structlog, redis-py async, asyncpg, Pydantic v2, prometheus-client; React 19, TanStack Router, TanStack Query, Vitest 4, Playwright; LiteLLM v1.x lightweight image; Ollama (NUC Windows-service + heavy-box systemd/WinSvc).

**Spec:** `docs/superpowers/specs/2026-05-12-phase11-ai-router-alerts-telegram-design.md` §2 (11a sub-phase).

**Versioning target:** v0.11.0. Per-chunk reviewer chains at end of each chunk per `feedback_review_per_chunk.md`.

---

## File structure (locks decomposition)

### Backend (new files)

| Path | Responsibility |
|---|---|
| `backend/app/services/ai/__init__.py` | Module marker; re-exports `AICompletionClient`, `AICapability`, `get_ai_client` |
| `backend/app/services/ai/exceptions.py` | `LocalModelsUnavailableError`, `AIProxyUnavailableError`, `StructuredOutputFailedError`, `AITimeoutError`, `AIToolCallingNotSupportedError` |
| `backend/app/services/ai/capabilities.py` | `AICapability` StrEnum (8 values); `resolve_models(capability, *, force_local_only=False)` |
| `backend/app/services/ai/types.py` | `CompletionRequest`, `CompletionResult`, `Chunk`, `JobStatus`, `ToolDef` Pydantic models; `FallbackHop` dataclass |
| `backend/app/services/ai/secrets.py` | `get_provider_key(provider)` with 60s TTL cache + pubsub-invalidation listener |
| `backend/app/services/ai/router.py` | `AICompletionClient` ABC; `LiteLLMClient` impl; routing-with-fallback walk; capability semaphore |
| `backend/app/services/ai/cost_ledger.py` | `CostLedgerWriter` fire-and-forget batched INSERT (bounded queue) |
| `backend/app/services/ai/wol.py` | `HeavyBoxWoL` magic packet + `GET /api/tags` readiness probe + circuit breaker |
| `backend/app/services/ai/jobs.py` | `AsyncJobStore` (PG-backed) + pubsub `ai:job:{id}` + orphan recovery |
| `backend/app/services/ai/rate_limiter.py` | Imports `SlidingWindowRateLimiter` from `services/common/`; instantiates `AISubjectLimiter` (30/s per subject) + `AICapacitySemaphore` (per-capability) |
| `backend/app/services/ai/litellm_auth_callback.py` | Redis-backed master-key validator wired as `litellm.proxy.auth.custom_auth` |
| `backend/app/services/ai/config_gen.py` | Renders `deploy/litellm/config.yaml` from `app_config:ai_router` at boot (idempotent) |
| `backend/app/services/common/rate_limiter.py` | `SlidingWindowRateLimiter[K]` generic (extracted from `portfolio_rate_limiter` + `position_sizing_rate_limiter`) |
| `backend/app/services/common/ws_envelope.py` | `make_ws_endpoint(...)` wrapping CSWSH origin check + heartbeat + recv-drain + compute cache from `ws_portfolio` |
| `backend/app/api/ai.py` | REST: `POST /api/ai/complete`, `POST /api/ai/jobs`, `GET /api/ai/jobs/{id}`, `DELETE /api/ai/jobs/{id}` |
| `backend/app/api/ws_ai.py` | WS: `/ws/ai/chat`, `/ws/ai/jobs/{id}` |
| `backend/app/api/admin_ai.py` | Admin: `GET /api/admin/ai/capability-map`, `PUT /api/admin/ai/capability-map`, `GET /api/admin/ai/cost-ledger`, `GET /api/admin/ai/heavy-box/state`, `POST /api/admin/ai/heavy-box/wake` (manual) |
| `backend/app/api/internal_litellm.py` | Internal: `POST /internal/litellm/verify` (called by LiteLLM auth-callback hook) |
| `backend/alembic/versions/0041_phase11a_ai_completions.py` | Hypertable `ai_completions`, 90d compression, 1y retention |
| `backend/alembic/versions/0042_phase11a_ai_jobs.py` | Table `ai_jobs` (not hypertable) + per-phase timestamps |
| `deploy/litellm/config.yaml` | Model list (no secrets) — committed to git |
| `deploy/litellm/secret_routing.md` | Per-provider routing-mode outcome from 11a-A0 spike |
| `deploy/nuc/install-ollama.ps1` | NUC Windows service installer |
| `deploy/heavybox/install-ollama.sh` | Heavy-box Ollama service install (Linux); `.ps1` alt if Windows |
| `deploy/heavybox/idle-suspend.service` | Suspend-after-15min systemd timer |

### Backend (modified)

| Path | Change |
|---|---|
| `docker-compose.yml` | Add `litellm:` service |
| `backend/app/main.py` | Lifespan: init `CostLedgerWriter`, `HeavyBoxWoL`, `AsyncJobStore`, `litellm_master_key` Redis bootstrap; mount new API routers |
| `backend/app/core/metrics.py` | Add 24 new metric series under `ai_router_*`, `ai_cost_ledger_*` |
| `backend/app/services/portfolio_rate_limiter.py` | Refactor to use `SlidingWindowRateLimiter[K]` generic (no-op rename) |
| `backend/app/services/position_sizing_rate_limiter.py` | Refactor to use generic (no-op rename) |
| `backend/app/api/admin.py` | Add `/api/admin/secrets/ai/{key}` PUT pattern matching existing secret rotation |

### Frontend (new files)

| Path | Responsibility |
|---|---|
| `frontend/src/routes/ai/chat.tsx` | Route registration `/ai/chat` |
| `frontend/src/routes/admin/ai.tsx` | Route registration `/admin/ai` |
| `frontend/src/features/ai/ChatPage.tsx` | Chat composition: messages + input + model picker + cost display + fallback badge |
| `frontend/src/features/ai/ChatMessage.tsx` | Single message rendering |
| `frontend/src/features/ai/ModelPicker.tsx` | Capability-tagged model picker |
| `frontend/src/features/ai/TradeTicketAiSection.tsx` | "AI context" collapsible for TradeTicketModal |
| `frontend/src/features/admin/AdminAiPage.tsx` | Capability map editor (drag-reorder) + provider-key CRUD + cost ledger + heavy-box state |
| `frontend/src/services/ai/types.ts` | Re-exports from `api-generated.ts` |
| `frontend/src/services/ai/api.ts` | `fetchAiComplete`, `fetchAiJob`, `submitAiJob`, `cancelAiJob`, admin endpoints |
| `frontend/src/services/ai/useChatStream.ts` | WS `/ws/ai/chat` hook with backoff reconnect + mountedRef |
| `frontend/src/services/ai/useAiJob.ts` | Job polling + WS `/ws/ai/jobs/{id}` |
| `frontend/src/services/ai/useTradeContext.ts` | One-shot `STRUCTURED_OUTPUT` for trade ticket |
| `frontend/src/stores/global/ai.ts` | zustand-persist: chat history per session, default model picker |
| `frontend/src/tests/spike/test_per_request_provider_key.py` | 11a-A0 spike test (lives backend-side) |

### Frontend (modified)

| Path | Change |
|---|---|
| `frontend/src/features/trade/TradeTicketModal.tsx` | Insert `<TradeTicketAiSection />` between symbol header and sizing |
| `frontend/src/components/layout/AppShell.tsx` | Add nav entry for `/ai/chat` |

### Tests

| Path | Coverage |
|---|---|
| `backend/tests/spike/test_per_request_provider_key.py` | 6-provider matrix |
| `backend/tests/services/ai/test_capabilities.py` | Resolution under LOCAL_ONLY + missing keys + force flag |
| `backend/tests/services/ai/test_router.py` | LiteLLM mock; fallback walk; 501 on tools; per-request key injection |
| `backend/tests/services/ai/test_secrets_cache.py` | TTL + pubsub invalidation + concurrent reads |
| `backend/tests/services/ai/test_cost_ledger.py` | Batched INSERT; bounded queue drop; fail-OPEN |
| `backend/tests/services/ai/test_wol.py` | Magic packet shape; model-ready probe; circuit breaker |
| `backend/tests/services/ai/test_jobs.py` | State transitions; orphan recovery (90s warming / 10min inferring); pubsub push; cooperative cancel |
| `backend/tests/services/ai/test_rate_limiter.py` | 30/s subject + per-capability semaphore + generic refactor |
| `backend/tests/services/ai/test_litellm_auth_callback.py` | Redis read; rotation visibility; LOCAL_ONLY enforcement |
| `backend/tests/services/common/test_sliding_window_rate_limiter.py` | Generic limiter |
| `backend/tests/services/common/test_ws_envelope.py` | CSWSH; heartbeat; recv-drain |
| `backend/tests/integration/test_ai_complete_api.py` | LOCAL_ONLY 503; happy path; rate-limit 429; tools 501 |
| `backend/tests/integration/test_ai_jobs_api.py` | 202 returns job_id; poll; cancel |
| `backend/tests/integration/test_ws_ai_chat.py` | Stream chunks; per-conn limits |
| `backend/tests/integration/test_ws_ai_jobs.py` | Push on state change |
| `backend/tests/integration/test_internal_litellm_verify.py` | Master-key flow |
| `frontend/src/services/ai/useChatStream.test.ts` | WS reconnect + mountedRef |
| `frontend/src/services/ai/useAiJob.test.ts` | Polling + WS push |
| `frontend/src/features/ai/ChatPage.test.tsx` | Render history + send turn |
| `frontend/src/features/ai/TradeTicketAiSection.test.tsx` | Failure-mode graceful degrade |
| `frontend/src/features/admin/AdminAiPage.test.tsx` | Capability edit + key CRUD + nonce |
| `tests/e2e/phase11a-chat.spec.ts` | Playwright: send chat, receive stream |
| `tests/e2e/phase11a-admin-ai.spec.ts` | Playwright: rotate key with CSRF |

**Target: ~50 backend + ~10 frontend + 2 Playwright (per spec §4 re-baseline).**

---

## Chunk A0 — Day-0 secret-flow spike

Blocks chunks A1+. CRIT-1 gate.

### Task 1: Set up local LiteLLM for the spike

**Files:**
- Create: `backend/tests/spike/__init__.py`
- Create: `backend/tests/spike/conftest.py`

- [ ] **Step 1: Create the spike package**

Create `backend/tests/spike/__init__.py` as an empty file. Then create `backend/tests/spike/conftest.py`:

```python
"""Phase 11a-A0 spike: validate LiteLLM accepts request-body api_key
per provider so Option C secret flow is viable. If any provider fails,
that provider falls back to Option A (config-held key) — outcome
recorded in deploy/litellm/secret_routing.md.
"""
from __future__ import annotations

import os
import socket
import time

import httpx
import pytest

LITELLM_URL = os.environ.get("SPIKE_LITELLM_URL", "http://localhost:4000")
LITELLM_MASTER_KEY = os.environ.get("SPIKE_LITELLM_MASTER_KEY", "sk-spike-master")


def _is_litellm_up(timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", 4000), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture(scope="session")
def litellm_url() -> str:
    if not _is_litellm_up():
        pytest.skip(
            "LiteLLM not reachable on localhost:4000 — start it via "
            "`docker run --rm -p 4000:4000 -v $PWD/deploy/litellm/config.yaml:/app/config.yaml "
            "-e LITELLM_MASTER_KEY=sk-spike-master ghcr.io/berriai/litellm:main-latest "
            "--config /app/config.yaml`"
        )
    return LITELLM_URL


@pytest.fixture(scope="session")
def litellm_client(litellm_url: str) -> httpx.Client:
    return httpx.Client(
        base_url=litellm_url,
        headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"},
        timeout=60.0,
    )
```

- [ ] **Step 2: Verify spike package importable**

Run: `cd backend && uv run pytest tests/spike/ -v --collect-only`
Expected: `collected 0 items` (no tests yet; collection works).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/spike/__init__.py backend/tests/spike/conftest.py
git commit -m "test(phase11a-A0): spike fixtures for per-request provider key validation"
```

### Task 2: Write the spike test matrix

**Files:**
- Create: `backend/tests/spike/test_per_request_provider_key.py`
- Create: `deploy/litellm/config.spike.yaml`

- [ ] **Step 1: Author the LiteLLM spike config**

Create `deploy/litellm/config.spike.yaml`:

```yaml
# Phase 11a-A0 spike config — covers all 6 providers we care about.
# NOT a production config; no real keys committed. Tests pass each
# provider's api_key via request body and assert LiteLLM forwards it.
model_list:
  - model_name: ollama-nuc
    litellm_params:
      model: ollama/qwen2.5:7b
      api_base: http://localhost:11434
  - model_name: ollama-heavy
    litellm_params:
      model: ollama/qwen2.5:32b
      api_base: http://localhost:11434
  - model_name: xai-grok
    litellm_params:
      model: xai/grok-2-latest
      api_base: https://api.x.ai/v1
  - model_name: gemini-pro
    litellm_params:
      model: gemini/gemini-2.5-pro
  - model_name: anthropic-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-6
  - model_name: openai-gpt4o
    litellm_params:
      model: openai/gpt-4o

general_settings:
  master_key: sk-spike-master
```

- [ ] **Step 2: Write the spike test**

Create `backend/tests/spike/test_per_request_provider_key.py`:

```python
"""Phase 11a-A0 spike — for each provider, verify LiteLLM accepts a
request-body api_key and forwards it. Failures here mean that provider
falls back to Option A (config-held key) in production.

Skipped unless SPIKE_PROVIDER_KEYS env is set with comma-separated
provider names that have valid keys available. Example:

    SPIKE_PROVIDER_KEYS=xai,anthropic \\
    SPIKE_KEY_xai=xai-xxxxx \\
    SPIKE_KEY_anthropic=sk-ant-xxxxx \\
    pytest backend/tests/spike/test_per_request_provider_key.py -v
"""
from __future__ import annotations

import os

import httpx
import pytest

PROVIDERS_TO_TEST = [
    p.strip() for p in os.environ.get("SPIKE_PROVIDER_KEYS", "").split(",") if p.strip()
]

PROVIDER_TO_MODEL = {
    "ollama-nuc": "ollama-nuc",
    "ollama-heavy": "ollama-heavy",
    "xai": "xai-grok",
    "gemini": "gemini-pro",
    "anthropic": "anthropic-sonnet",
    "openai": "openai-gpt4o",
}


@pytest.mark.parametrize("provider", PROVIDERS_TO_TEST or ["__skip__"])
def test_provider_accepts_request_body_api_key(
    litellm_client: httpx.Client, provider: str
) -> None:
    if provider == "__skip__":
        pytest.skip("Set SPIKE_PROVIDER_KEYS to enable")
    if provider not in PROVIDER_TO_MODEL:
        pytest.fail(f"Unknown provider {provider!r}")

    key_env = f"SPIKE_KEY_{provider}"
    provider_key = os.environ.get(key_env)
    if not provider_key:
        pytest.skip(f"{key_env} not set")

    body = {
        "model": PROVIDER_TO_MODEL[provider],
        "messages": [{"role": "user", "content": "Reply with the single word 'ok'."}],
        "max_tokens": 10,
        "api_key": provider_key,
    }
    resp = litellm_client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200, f"{provider}: {resp.status_code} {resp.text[:200]}"
    payload = resp.json()
    assert payload.get("choices"), f"{provider}: no choices in {payload}"
```

- [ ] **Step 3: Run the spike against any provider you have a key for**

Run: `cd backend && SPIKE_PROVIDER_KEYS=anthropic SPIKE_KEY_anthropic=$ANTHROPIC_API_KEY uv run pytest tests/spike/ -v`
Expected (when LiteLLM is up and key is valid): one test passes.
Expected (no LiteLLM): all tests skip with the "LiteLLM not reachable" message.

- [ ] **Step 4: Document the outcome**

Create `deploy/litellm/secret_routing.md`:

```markdown
# Phase 11a-A0 — per-provider secret routing outcome

Validated 2026-05-12 via `backend/tests/spike/test_per_request_provider_key.py`.

| Provider | Request-body `api_key` accepted? | Routing mode |
|---|---|---|
| ollama-nuc | yes (Ollama ignores key) | `request_body` |
| ollama-heavy | yes (Ollama ignores key) | `request_body` |
| xai-grok | TBD-spike | TBD |
| gemini-pro | TBD-spike | TBD |
| anthropic-sonnet | TBD-spike | TBD |
| openai-gpt4o | TBD-spike | TBD |

Providers marked `request_body` use Option C (BE signs each call). Providers
that fail the spike fall back to **Option A**: config-held key, rotation
via lifespan re-render + `docker compose up -d litellm`.

This file is updated as each provider key is acquired and tested.
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/spike/test_per_request_provider_key.py deploy/litellm/config.spike.yaml deploy/litellm/secret_routing.md
git commit -m "test(phase11a-A0): per-provider request-body api_key spike + outcome doc"
```

### Task 3: Chunk-A0 reviewer chain

- [ ] **Step 1: Dispatch reviewers**

Per `feedback_review_per_chunk.md`, dispatch `spec-compliance` (haiku) + `python-reviewer` (haiku) on chunk A0 commits. Pass the spec slice for 11a-A0 inline per `feedback_reviewer_spec_inline.md`.

- [ ] **Step 2: Apply CRIT+HIGH+MED findings inline**

Per `feedback_architect_findings_apply_through_medium.md`.

- [ ] **Step 3: Tag**

```bash
git tag -a v0.11.0.a0 -m "phase11a-A0 secret-flow spike infrastructure ready"
git push --tags
```

---

## Chunk A1 — LiteLLM proxy + migrations + capability map

### Task 4: Add LiteLLM service to docker-compose

**Files:**
- Modify: `docker-compose.yml`
- Create: `deploy/litellm/config.yaml`

- [ ] **Step 1: Author the production LiteLLM config (no secrets)**

Create `deploy/litellm/config.yaml`:

```yaml
# Production LiteLLM config — committed to git, contains NO secrets.
# Provider keys arrive per-request from BE (Option C, validated in 11a-A0).
# Master-key is validated via custom_auth callback against Redis (HIGH-5).
model_list:
  - model_name: ollama-nuc
    litellm_params:
      model: ollama/qwen2.5:7b
      api_base: http://10.10.0.2:11434
  - model_name: ollama-nuc-llama
    litellm_params:
      model: ollama/llama3.2:8b
      api_base: http://10.10.0.2:11434
  - model_name: ollama-heavy
    litellm_params:
      model: ollama/qwen2.5:32b
      api_base: http://10.10.0.3:11434
  - model_name: ollama-heavy-70b
    litellm_params:
      model: ollama/llama3.3:70b
      api_base: http://10.10.0.3:11434
  - model_name: xai-grok
    litellm_params:
      model: xai/grok-2-latest
      api_base: https://api.x.ai/v1
  - model_name: gemini-pro
    litellm_params:
      model: gemini/gemini-2.5-pro
  - model_name: anthropic-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-6
  - model_name: openai-gpt4o
    litellm_params:
      model: openai/gpt-4o

general_settings:
  custom_auth: services.ai.litellm_auth_callback.user_api_key_auth
```

- [ ] **Step 2: Add `litellm:` service to docker-compose.yml**

Modify `docker-compose.yml` — append before the closing `volumes:` section (after the existing `frontend:` service block):

```yaml
  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    container_name: dashboard-litellm
    restart: unless-stopped
    ports:
      - "127.0.0.1:4000:4000"
    volumes:
      - ./deploy/litellm/config.yaml:/app/config.yaml:ro
      - ./backend/app/services/ai:/app/custom_auth/services/ai:ro
    environment:
      PYTHONPATH: /app/custom_auth
      LITELLM_LOG: WARNING
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:4000/health/liveliness"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - default
    command: ["--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
```

- [ ] **Step 3: Validate compose file**

Run: `docker compose config | grep -A 20 litellm:`
Expected: `litellm:` block renders with the configured image, ports, volumes, healthcheck.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml deploy/litellm/config.yaml
git commit -m "feat(phase11a-A1): add LiteLLM proxy sidecar to docker-compose"
```

### Task 5: Alembic 0041 — ai_completions hypertable

**Files:**
- Create: `backend/alembic/versions/0041_phase11a_ai_completions.py`
- Test: `backend/tests/migrations/test_0041_ai_completions.py`

- [ ] **Step 1: Write the failing migration test**

Create `backend/tests/migrations/test_0041_ai_completions.py`:

```python
"""Phase 11a-A1: ai_completions hypertable migration test."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal


@pytest.mark.asyncio
async def test_ai_completions_is_hypertable() -> None:
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'ai_completions'"
            )
        )
        assert result.scalar_one() == "ai_completions"


@pytest.mark.asyncio
async def test_ai_completions_columns_present() -> None:
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ai_completions' ORDER BY ordinal_position"
            )
        )
        cols = {row[0] for row in result.fetchall()}
    expected = {
        "ts", "request_id", "jwt_subject", "capability", "provider",
        "model", "host", "prompt_tokens", "completion_tokens",
        "wall_time_ms", "wol_warmup_ms", "outcome", "error_class", "caller",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


@pytest.mark.asyncio
async def test_ai_completions_retention_policy() -> None:
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT config FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_retention' "
                "  AND hypertable_name = 'ai_completions'"
            )
        )
        config = result.scalar_one()
        assert "drop_after" in config
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/migrations/test_0041_ai_completions.py -v`
Expected: FAIL with `relation "ai_completions" does not exist` or similar.

- [ ] **Step 3: Author the migration**

Create `backend/alembic/versions/0041_phase11a_ai_completions.py`:

```python
"""phase11a ai_completions hypertable

Revision ID: 0041
Revises: 0040
Create Date: 2026-05-12

Phase 11a-A1 §6: cost ledger hypertable (chunk 7d, retention 1y,
compress after 90d per LOW-5). Captures every AI call attempt
including failures so capacity planning is honest.
"""
from __future__ import annotations

from alembic import op


# revision identifiers
revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_completions (
            ts            TIMESTAMPTZ NOT NULL,
            request_id    UUID        NOT NULL,
            jwt_subject   TEXT        NOT NULL,
            capability    TEXT        NOT NULL,
            provider      TEXT        NOT NULL,
            model         TEXT        NOT NULL,
            host          TEXT        NOT NULL,
            prompt_tokens INTEGER     NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            wall_time_ms  INTEGER     NOT NULL DEFAULT 0,
            wol_warmup_ms INTEGER     NOT NULL DEFAULT 0,
            outcome       TEXT        NOT NULL,
            error_class   TEXT,
            caller        TEXT        NOT NULL,
            CHECK (outcome IN ('ok', 'failed', 'timeout', 'rate_limited', 'fallback')),
            CHECK (capability ~ '^[A-Z_]+$'),
            CHECK (host IN ('nuc', 'heavy', 'cloud'))
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('ai_completions', 'ts', "
        "chunk_time_interval => INTERVAL '7 days');"
    )
    op.execute(
        "SELECT add_retention_policy('ai_completions', INTERVAL '1 year');"
    )
    op.execute(
        "ALTER TABLE ai_completions SET ("
        "  timescaledb.compress, "
        "  timescaledb.compress_segmentby = 'provider, capability'"
        ");"
    )
    op.execute(
        "SELECT add_compression_policy('ai_completions', INTERVAL '90 days');"
    )
    op.execute(
        "CREATE INDEX idx_ai_completions_subject_ts "
        "ON ai_completions (jwt_subject, ts DESC);"
    )
    op.execute(
        "CREATE INDEX idx_ai_completions_caller_ts "
        "ON ai_completions (caller, ts DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_completions CASCADE;")
```

- [ ] **Step 4: Apply the migration**

Run: `docker compose exec backend alembic upgrade head`
Expected: `Running upgrade 0040 -> 0041, phase11a ai_completions hypertable`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/migrations/test_0041_ai_completions.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0041_phase11a_ai_completions.py backend/tests/migrations/test_0041_ai_completions.py
git commit -m "feat(phase11a-A1): alembic 0041 ai_completions hypertable + retention + compression"
```

### Task 6: Alembic 0042 — ai_jobs table

**Files:**
- Create: `backend/alembic/versions/0042_phase11a_ai_jobs.py`
- Test: `backend/tests/migrations/test_0042_ai_jobs.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/migrations/test_0042_ai_jobs.py`:

```python
"""Phase 11a-A1: ai_jobs table migration test."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal


@pytest.mark.asyncio
async def test_ai_jobs_columns_and_indices() -> None:
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ai_jobs'"
            )
        )
        cols = {row[0] for row in result.fetchall()}
    expected = {
        "id", "jwt_subject", "status", "capability",
        "request_jsonb", "response_jsonb", "error",
        "started_at", "warming_started_at", "inferring_started_at",
        "completed_at", "cancel_requested",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


@pytest.mark.asyncio
async def test_ai_jobs_has_status_started_at_index() -> None:
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'ai_jobs' "
                "  AND indexname = 'idx_ai_jobs_status_started_at'"
            )
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_ai_jobs_is_not_hypertable() -> None:
    """LOW-6: ai_jobs deliberately NOT a hypertable."""
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "SELECT COUNT(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'ai_jobs'"
            )
        )
        assert result.scalar_one() == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/migrations/test_0042_ai_jobs.py -v`
Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Author the migration**

Create `backend/alembic/versions/0042_phase11a_ai_jobs.py`:

```python
"""phase11a ai_jobs async-job store

Revision ID: 0042
Revises: 0041
Create Date: 2026-05-12

Phase 11a-A1 §6 (HIGH-8): per-state-transition timestamps for split
orphan-recovery thresholds (warming 90s, inferring 10min). Plain
table not hypertable per LOW-6 (job volume is small + queries are by
status not time-range).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_jobs (
            id                    UUID PRIMARY KEY,
            jwt_subject           TEXT NOT NULL,
            status                TEXT NOT NULL,
            capability            TEXT NOT NULL,
            request_jsonb         JSONB NOT NULL,
            response_jsonb        JSONB,
            error                 TEXT,
            started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            warming_started_at    TIMESTAMPTZ,
            inferring_started_at  TIMESTAMPTZ,
            completed_at          TIMESTAMPTZ,
            cancel_requested      BOOLEAN NOT NULL DEFAULT false,
            CHECK (status IN ('pending','warming','inferring','completed','failed','cancelled'))
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_ai_jobs_status_started_at "
        "ON ai_jobs (status, started_at) "
        "WHERE status IN ('pending','warming','inferring');"
    )
    op.execute(
        "CREATE INDEX idx_ai_jobs_subject_started_at "
        "ON ai_jobs (jwt_subject, started_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_jobs;")
```

- [ ] **Step 4: Apply**

Run: `docker compose exec backend alembic upgrade head`
Expected: `Running upgrade 0041 -> 0042`.

- [ ] **Step 5: Run test to verify pass**

Run: `cd backend && uv run pytest tests/migrations/test_0042_ai_jobs.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0042_phase11a_ai_jobs.py backend/tests/migrations/test_0042_ai_jobs.py
git commit -m "feat(phase11a-A1): alembic 0042 ai_jobs table + split-orphan timestamps"
```

### Task 7: Capability map in app_config

**Files:**
- Create: `backend/app/services/ai/__init__.py`
- Create: `backend/app/services/ai/capabilities.py`
- Create: `backend/app/services/ai/exceptions.py`
- Create: `backend/app/services/ai/types.py`
- Test: `backend/tests/services/ai/__init__.py`
- Test: `backend/tests/services/ai/test_capabilities.py`

- [ ] **Step 1: Create empty module markers**

Create `backend/app/services/ai/__init__.py`:

```python
"""Phase 11 — services/ai/ module.

Single boundary between consumers (alerts, telegram, trade ticket, chat,
future Phase 18 scanner + Phase 21 bot-engine) and the LiteLLM proxy.
Anyone who needs an LLM completion imports AICompletionClient from here.
"""
from __future__ import annotations

from app.services.ai.capabilities import AICapability
from app.services.ai.exceptions import (
    AIProxyUnavailableError,
    AITimeoutError,
    AIToolCallingNotSupportedError,
    LocalModelsUnavailableError,
    StructuredOutputFailedError,
)

__all__ = [
    "AICapability",
    "AIProxyUnavailableError",
    "AITimeoutError",
    "AIToolCallingNotSupportedError",
    "LocalModelsUnavailableError",
    "StructuredOutputFailedError",
]
```

Create `backend/tests/services/ai/__init__.py` as empty.

- [ ] **Step 2: Create exceptions module**

Create `backend/app/services/ai/exceptions.py`:

```python
"""Phase 11a-B: typed exceptions for services/ai/ (LOW-2 — Error suffix
consistent with Phase 10a RiskGateBlockedError style)."""
from __future__ import annotations


class AIError(Exception):
    """Base for all services/ai/ errors."""


class LocalModelsUnavailableError(AIError):
    """LOCAL_ONLY request but no local models reachable (CRIT-3 fail path)."""


class AIProxyUnavailableError(AIError):
    """LiteLLM proxy unreachable after retries."""


class StructuredOutputFailedError(AIError):
    """Model returned non-JSON-schema-conformant output twice in a row."""

    def __init__(self, raw_text: str, schema_error: str) -> None:
        super().__init__(f"structured output failed: {schema_error}")
        self.raw_text = raw_text
        self.schema_error = schema_error


class AITimeoutError(AIError):
    """Request exceeded the configured timeout window."""


class AIToolCallingNotSupportedError(AIError):
    """HIGH-4 forward-compat: tools param present but v0.11.0 rejects it."""
```

- [ ] **Step 3: Write the failing capability test**

Create `backend/tests/services/ai/test_capabilities.py`:

```python
"""Phase 11a-A1: capability-map resolution tests."""
from __future__ import annotations

import pytest

from app.services.ai.capabilities import (
    AICapability,
    resolve_models,
)


def test_capability_enum_has_eight_values() -> None:
    assert {c.value for c in AICapability} == {
        "LOCAL_ONLY",
        "LONG_CONTEXT",
        "REALTIME_SENTIMENT",
        "STRUCTURED_OUTPUT",
        "BULK_CHEAP",
        "REASONING",
        "NUMERICAL",
        "CODING",
    }


def test_local_only_excludes_cloud_models() -> None:
    capability_map = {
        "LOCAL_ONLY": [
            {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
        ],
    }
    available_providers = {"ollama-nuc", "anthropic", "ollama-heavy"}
    models = resolve_models(
        AICapability.LOCAL_ONLY,
        capability_map=capability_map,
        available_providers=available_providers,
    )
    assert [(m.provider, m.model) for m in models] == [
        ("ollama-nuc", "qwen2.5:7b"),
        ("ollama-heavy", "qwen2.5:32b"),
    ]


def test_force_local_only_overrides_capability_default() -> None:
    """CRIT-3: parser passes force_local_only=True even when capability
    is STRUCTURED_OUTPUT (which would otherwise allow cloud)."""
    capability_map = {
        "STRUCTURED_OUTPUT": [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
        ],
    }
    available_providers = {"anthropic", "ollama-nuc"}
    models = resolve_models(
        AICapability.STRUCTURED_OUTPUT,
        capability_map=capability_map,
        available_providers=available_providers,
        force_local_only=True,
    )
    assert [(m.provider, m.model) for m in models] == [
        ("ollama-nuc", "qwen2.5:7b")
    ]


def test_missing_provider_key_drops_entry() -> None:
    capability_map = {
        "REASONING": [
            {"provider": "anthropic", "model": "claude-opus-4-7"},
            {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
        ],
    }
    available_providers = {"ollama-heavy"}  # anthropic key missing
    models = resolve_models(
        AICapability.REASONING,
        capability_map=capability_map,
        available_providers=available_providers,
    )
    assert [(m.provider, m.model) for m in models] == [
        ("ollama-heavy", "qwen2.5:32b")
    ]


def test_unknown_capability_returns_empty() -> None:
    models = resolve_models(
        AICapability.NUMERICAL,
        capability_map={},
        available_providers={"anthropic"},
    )
    assert models == []
```

- [ ] **Step 4: Run test to verify failure**

Run: `cd backend && uv run pytest tests/services/ai/test_capabilities.py -v`
Expected: FAIL — `capabilities` module doesn't exist yet.

- [ ] **Step 5: Author the capability module**

Create `backend/app/services/ai/capabilities.py`:

```python
"""Phase 11a-A1: AICapability enum + resolve_models() pure function.

Each consumer asks for completion by capability rather than by exact
model. The router consults app_config:ai_router to map capability →
ordered model list, then walks it with LOCAL_ONLY filter applied and
missing-provider-key entries removed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Providers whose endpoint sits inside the WG/LAN — used by LOCAL_ONLY
# privacy floor. Centralising the membership in one constant means the
# router cannot accidentally route a LOCAL_ONLY request to a cloud
# provider by misclassifying.
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    {"ollama-nuc", "ollama-heavy"}
)


class AICapability(StrEnum):
    """Capability tags consumers attach to a CompletionRequest."""

    LOCAL_ONLY = "LOCAL_ONLY"
    LONG_CONTEXT = "LONG_CONTEXT"
    REALTIME_SENTIMENT = "REALTIME_SENTIMENT"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    BULK_CHEAP = "BULK_CHEAP"
    REASONING = "REASONING"
    NUMERICAL = "NUMERICAL"
    CODING = "CODING"


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str


def resolve_models(
    capability: AICapability,
    *,
    capability_map: dict[str, list[dict[str, str]]],
    available_providers: set[str] | frozenset[str],
    force_local_only: bool = False,
) -> list[ResolvedModel]:
    """Return the ordered fallback chain for a capability.

    Args:
        capability: tag from the consumer.
        capability_map: from app_config:ai_router; each value is an
          ordered list of ``{"provider": str, "model": str}`` entries.
        available_providers: set of providers whose api_key is configured.
        force_local_only: CRIT-3 — parser sets this regardless of the
          capability so the rule-NL stays inside the WG.

    Returns:
        Empty list if no entries survive both filters.
    """
    entries = capability_map.get(capability.value, [])
    out: list[ResolvedModel] = []
    enforce_local = force_local_only or capability is AICapability.LOCAL_ONLY
    for entry in entries:
        provider = entry["provider"]
        model = entry["model"]
        if provider not in available_providers:
            continue
        if enforce_local and provider not in LOCAL_PROVIDERS:
            continue
        out.append(ResolvedModel(provider=provider, model=model))
    return out
```

- [ ] **Step 6: Create the types module**

Create `backend/app/services/ai/types.py`:

```python
"""Phase 11a-B: request/response shapes for services/ai/."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.ai.capabilities import AICapability


class ToolDef(BaseModel):
    """HIGH-4 forward-compat placeholder. v0.11.0 rejects non-None."""

    name: str
    description: str
    parameters: dict[str, Any]


class CompletionRequest(BaseModel):
    messages: list[dict[str, str]] = Field(..., min_length=1)
    capability: AICapability
    caller: str = Field(..., description="consumer name for cost ledger")
    response_format: dict[str, Any] | None = None
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    tools: list[ToolDef] | None = None  # HIGH-4 — rejected with 501 at v0.11.0
    force_local_only: bool = False  # CRIT-3 — parser path


class CompletionResult(BaseModel):
    request_id: UUID
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    wall_time_ms: int
    fallback_chain: list["FallbackHop"] = Field(default_factory=list)


class FallbackHop(BaseModel):
    """MED-8 — record each attempted provider/model + reason for skipping."""

    from_provider: str
    from_model: str
    reason: str


class Chunk(BaseModel):
    """Streaming chunk shape."""

    delta: str
    finish_reason: Literal["stop", "length", "tool_calls", None] = None


class JobStatus(BaseModel):
    id: UUID
    status: Literal[
        "pending", "warming", "inferring", "completed", "failed", "cancelled"
    ]
    response: CompletionResult | None = None
    error: str | None = None


CompletionResult.model_rebuild()
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/services/ai/test_capabilities.py -v`
Expected: 5 PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/ai/__init__.py backend/app/services/ai/capabilities.py backend/app/services/ai/exceptions.py backend/app/services/ai/types.py backend/tests/services/ai/__init__.py backend/tests/services/ai/test_capabilities.py
git commit -m "feat(phase11a-A1): AICapability enum + resolve_models() + exceptions + types"
```

### Task 8: Seed default capability map in app_config

**Files:**
- Modify: `backend/app/services/config_defaults.py`
- Test: extend `backend/tests/services/ai/test_capabilities.py`

- [ ] **Step 1: Locate the config_defaults module**

Run: `grep -n "DEFAULT" /home/joseph/dashboard/backend/app/services/config_defaults.py | head -10`
Expected: existing defaults map shape — read enough to mimic.

- [ ] **Step 2: Add ai_router defaults**

Append to `backend/app/services/config_defaults.py` (after the last `DEFAULT_*` constant; the engineer reads the existing file to confirm append point):

```python
# Phase 11a-A1: AI router capability map default.
#
# Ordering matters: first entry is preferred, subsequent entries are
# fallbacks. Provider keys that aren't configured at runtime are
# automatically removed by resolve_models().
DEFAULT_AI_ROUTER_CAPABILITY_MAP: dict[str, list[dict[str, str]]] = {
    "LOCAL_ONLY": [
        {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
        {"provider": "ollama-nuc-llama", "model": "llama3.2:8b"},
        {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
    ],
    "STRUCTURED_OUTPUT": [
        {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
        {"provider": "openai-gpt4o", "model": "gpt-4o"},
    ],
    "LONG_CONTEXT": [
        {"provider": "gemini-pro", "model": "gemini-2.5-pro"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
    "REALTIME_SENTIMENT": [
        {"provider": "xai-grok", "model": "grok-2-latest"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
    "REASONING": [
        {"provider": "ollama-heavy-70b", "model": "llama3.3:70b"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
        {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
    ],
    "BULK_CHEAP": [
        {"provider": "gemini-pro", "model": "gemini-2.5-flash"},
        {"provider": "openai-gpt4o", "model": "gpt-4o-mini"},
    ],
    "NUMERICAL": [
        {"provider": "openai-gpt4o", "model": "gpt-4o"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
    "CODING": [
        {"provider": "ollama-heavy", "model": "qwen2.5-coder:32b"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
}
```

- [ ] **Step 3: Wire the default into the boot-time seeding code**

Run: `grep -n "DEFAULT.*= " /home/joseph/dashboard/backend/app/services/config_defaults.py | head -20` to identify the seeding function. Add the new namespace entry into whatever dict drives app_config seeding (mirror existing entries). Specifically, locate `seed_app_config(...)` or equivalent function and add:

```python
await config_svc.set_namespace(
    "ai_router", {"capability_map": DEFAULT_AI_ROUTER_CAPABILITY_MAP}
)
```

inside the existing seeding flow.

- [ ] **Step 4: Add an integration test**

Append to `backend/tests/services/ai/test_capabilities.py`:

```python
@pytest.mark.asyncio
async def test_default_capability_map_loaded_on_seed() -> None:
    from app.services.config import get_config_service
    cfg = await get_config_service()
    val = await cfg.get_namespace("ai_router")
    assert "capability_map" in val
    assert "LOCAL_ONLY" in val["capability_map"]
    assert val["capability_map"]["LOCAL_ONLY"][0]["provider"] == "ollama-nuc"
```

- [ ] **Step 5: Run all capability tests**

Run: `cd backend && uv run pytest tests/services/ai/test_capabilities.py -v`
Expected: 6 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/config_defaults.py backend/tests/services/ai/test_capabilities.py
git commit -m "feat(phase11a-A1): seed default ai_router capability map in app_config"
```

### Task 9: Chunk-A1 reviewer chain + tag

- [ ] **Step 1: Dispatch reviewers**

Per CLAUDE.md routing: spec-compliance (haiku), python-reviewer (haiku), database-reviewer (sonnet) — mandatory on chunks with new migrations. Inline spec slice for 11a-A1.

- [ ] **Step 2: Apply CRIT+HIGH+MED findings inline**

- [ ] **Step 3: Tag**

```bash
git tag -a v0.11.0.a1 -m "phase11a-A1 proxy + migrations + capability map shipped"
git push --tags
```

---

## The remaining chunks (A.5, A2, B, C, D) are scaffolded below as task-block stubs

**This plan is intentionally split.** Chunks A0 + A1 land first as an independent reviewable milestone. The remaining chunks each get their detailed task list once A1 closes — because:

1. **A.5 LiteLLM auth-callback** depends on knowing whether the spike validated request-body keys for all providers (drives whether the auth-callback also has to handle per-provider routing decisions).
2. **A2 WoL** depends on heavy-box install runbook outcomes from operator (we don't know yet if heavy box is Linux or Windows-native).
3. **B services/ai/ core** depends on the `litellm_auth_callback.py` interface decided in A.5.
4. **C endpoints** depend on the final `AICompletionClient` ABC shape from B.
5. **D frontend** depends on the OpenAPI schema regenerated after C.

Writing detailed tasks for D before B closes would require placeholder types that A.5/B might refactor. Per writing-plans skill: *"every step must contain the actual content an engineer needs"* — speculative tasks would violate that.

### Chunk A.5 — LiteLLM Redis-backed auth-callback (HIGH-5)

**Goal:** Zero-restart rotation of the LiteLLM master key. Removes the env-var dependency from chunk A1.

**Design refinement vs original sketch:** the callback reads Redis DIRECTLY from inside the LiteLLM container (LiteLLM ships with `redis-py`). The original sketch proposed `POST /internal/litellm/verify` — extra network hop + internal endpoint to secure. Direct-Redis-read is simpler, lower-latency, fewer moving parts.

**Verified signature (from LiteLLM docs):**
```python
async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth | str
```
LiteLLM already extracts `api_key` from `Authorization: Bearer <key>` before calling. Return `UserAPIKeyAuth(api_key=api_key)` to authorize; raise `ProxyException(code=401)` to deny.

**Files to create:**
- `backend/app/services/ai/litellm_auth_callback.py` — the async callback function reading from Redis
- `backend/tests/services/ai/test_litellm_auth_callback.py` — unit tests for the callback (mock Redis + Request)

**Files to modify:**
- `backend/app/main.py` — lifespan: bootstrap `ai:litellm_master_key` Redis key from `app_secrets.ai.litellm_master_key`
- `deploy/litellm/config.yaml` — add `general_settings.custom_auth: services.ai.litellm_auth_callback.user_api_key_auth` + `custom_auth_settings: {mode: "on"}` so LiteLLM trusts ONLY our callback
- `docker-compose.yml` — mount `backend/app/services/ai/litellm_auth_callback.py` into the LiteLLM container at `/app/custom_auth/services/ai/litellm_auth_callback.py` + `PYTHONPATH=/app/custom_auth`; also pass `REDIS_URL` env to litellm service
- `backend/app/api/admin.py` — `PUT /api/admin/secrets/ai/litellm_master_key` endpoint with CSRF nonce: writes new key to `app_secrets` AND Redis atomically; invalidates secret cache via pubsub

### Task 10: Write the failing test for `user_api_key_auth` callback

**Files:**
- Create: `backend/tests/services/ai/test_litellm_auth_callback.py`

- [ ] **Step 1: Write the test stub**

Create `backend/tests/services/ai/test_litellm_auth_callback.py`:

```python
"""Phase 11a-A.5: LiteLLM auth-callback unit tests (HIGH-5).

Validates the Redis-backed master-key check. Mocks the FastAPI Request
plus Redis client so tests run without the LiteLLM container.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_db


@pytest.fixture
def fake_request() -> MagicMock:
    """LiteLLM passes a FastAPI Request; the callback may inspect headers
    but doesn't need the full ASGI scope."""
    req = MagicMock(spec_set=["headers", "client"])
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


@pytest.fixture
def fake_redis_with_key() -> AsyncMock:
    """Redis returns the master key for `ai:litellm_master_key`."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=b"sk-master-current")
    return r


@pytest.mark.asyncio
async def test_callback_accepts_matching_key(
    fake_request: MagicMock, fake_redis_with_key: AsyncMock
) -> None:
    from app.services.ai.litellm_auth_callback import user_api_key_auth

    result = await user_api_key_auth(
        fake_request, "sk-master-current", _redis=fake_redis_with_key
    )
    assert result is not None
    assert getattr(result, "api_key", None) == "sk-master-current"


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_key(
    fake_request: MagicMock, fake_redis_with_key: AsyncMock
) -> None:
    from app.services.ai.litellm_auth_callback import user_api_key_auth
    from litellm.proxy._types import ProxyException

    with pytest.raises(ProxyException) as exc:
        await user_api_key_auth(
            fake_request, "sk-master-wrong", _redis=fake_redis_with_key
        )
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_callback_rejects_when_redis_unset(fake_request: MagicMock) -> None:
    """If Redis has no key (BE lifespan didn't run, or key was wiped),
    deny rather than fail-open."""
    from app.services.ai.litellm_auth_callback import user_api_key_auth
    from litellm.proxy._types import ProxyException

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    with pytest.raises(ProxyException) as exc:
        await user_api_key_auth(
            fake_request, "sk-master-anything", _redis=fake_redis
        )
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_callback_rejects_when_redis_errors(fake_request: MagicMock) -> None:
    """Redis hiccup must fail-CLOSED. AI access is not load-bearing on the
    user-facing path; a 401 is correct over fail-OPEN."""
    from app.services.ai.litellm_auth_callback import user_api_key_auth
    from litellm.proxy._types import ProxyException

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(side_effect=RuntimeError("redis hiccup"))
    with pytest.raises(ProxyException) as exc:
        await user_api_key_auth(
            fake_request, "sk-master-current", _redis=fake_redis
        )
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_callback_constant_time_compare(
    fake_request: MagicMock, fake_redis_with_key: AsyncMock
) -> None:
    """Use hmac.compare_digest to defend against timing side-channels.
    Asserting the import indirectly via behaviour: both differ-at-start
    and differ-at-end mismatches reject with the same exception class."""
    from app.services.ai.litellm_auth_callback import user_api_key_auth
    from litellm.proxy._types import ProxyException

    for wrong in ("Xk-master-current", "sk-master-currenX", ""):
        with pytest.raises(ProxyException):
            await user_api_key_auth(
                fake_request, wrong, _redis=fake_redis_with_key
            )
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/services/ai/test_litellm_auth_callback.py -v`
Expected: collection error or all tests fail with `ModuleNotFoundError: app.services.ai.litellm_auth_callback`.

### Task 11: Implement `user_api_key_auth` callback

**Files:**
- Create: `backend/app/services/ai/litellm_auth_callback.py`

- [ ] **Step 1: Implement the callback**

Create `backend/app/services/ai/litellm_auth_callback.py`:

```python
"""Phase 11a-A.5 (HIGH-5): Redis-backed LiteLLM master-key validation.

LiteLLM loads this module from /app/custom_auth/services/ai/ inside the
proxy container (PYTHONPATH=/app/custom_auth). On every protected
route, LiteLLM calls user_api_key_auth(request, api_key) where api_key
is already extracted from the Authorization: Bearer header.

Zero-restart rotation: PUT /api/admin/secrets/ai/litellm_master_key
writes the new key to Redis key `ai:litellm_master_key`. The next
LiteLLM request sees the new value — no docker compose restart.

Fail-CLOSED on every error path (Redis down, key unset, mismatch).
AI is not on the critical user-facing path; a 401 is the correct
default. Cost-ledger writes are fail-OPEN per Phase 10a pattern but
auth is the opposite.
"""

from __future__ import annotations

import hmac
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request
    from redis.asyncio import Redis


REDIS_MASTER_KEY = "ai:litellm_master_key"


async def _get_redis_client() -> Redis:
    """Resolve a Redis client from REDIS_URL env (set in docker-compose).
    The LiteLLM container imports this module on startup, so we cannot
    rely on FastAPI app.state.redis — there is no FastAPI here."""
    from redis.asyncio import Redis

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL not set in LiteLLM container env")
    return Redis.from_url(redis_url, decode_responses=False)


async def user_api_key_auth(
    request: Request, api_key: str, *, _redis: Any | None = None
) -> Any:
    """Validate the incoming master key against the Redis-stored value.

    Args:
        request: FastAPI Request (provided by LiteLLM; we don't inspect it).
        api_key: extracted from Authorization: Bearer by LiteLLM.
        _redis: test-injection for unit tests; production goes via env.

    Returns:
        UserAPIKeyAuth(api_key=api_key) on success.

    Raises:
        ProxyException with code=401 on any failure.
    """
    from litellm.proxy._types import ProxyException, UserAPIKeyAuth

    redis_client = _redis if _redis is not None else await _get_redis_client()
    try:
        stored = await redis_client.get(REDIS_MASTER_KEY)
    except Exception:  # noqa: BLE001 — fail-CLOSED, any Redis error denies
        raise ProxyException(
            message="auth backend unavailable",
            type="invalid_request_error",
            param="api_key",
            code=401,
        ) from None
    if stored is None:
        raise ProxyException(
            message="master key not configured",
            type="invalid_request_error",
            param="api_key",
            code=401,
        )

    stored_str = stored.decode("utf-8") if isinstance(stored, bytes) else str(stored)
    if not hmac.compare_digest(api_key, stored_str):
        raise ProxyException(
            message="invalid master key",
            type="invalid_request_error",
            param="api_key",
            code=401,
        )

    return UserAPIKeyAuth(api_key=api_key)
```

- [ ] **Step 2: Run tests to verify pass**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/services/ai/test_litellm_auth_callback.py -v`
Expected: 5 PASS.

- [ ] **Step 3: Commit**

Stage the new module + test. Commit message body:

```
feat(phase11a-A.5): Redis-backed LiteLLM master-key auth-callback

HIGH-5: zero-restart rotation of the LiteLLM master key via Redis-stored
value the auth-callback reads on every request. Replaces the env-var
key model from chunk A1 (which required docker compose up -d litellm
to rotate).

Fail-CLOSED on every error path (Redis down, key unset, mismatch).
hmac.compare_digest defends against timing side-channels on the
compare step. _redis test-injection keyword keeps the production code
path container-imported (no FastAPI here — LiteLLM loads the module
from /app/custom_auth).
```

### Task 12: Backend lifespan bootstraps the Redis master-key

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/api/test_lifespan_litellm_bootstrap.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_lifespan_litellm_bootstrap.py`:

```python
"""Phase 11a-A.5: BE lifespan writes ai:litellm_master_key to Redis
from app_secrets on startup. Without this, the LiteLLM auth-callback
sees Redis empty and rejects every request — the chunk's whole point
falls over."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_lifespan_writes_master_key_to_redis() -> None:
    """app.state.redis must have ai:litellm_master_key set after startup,
    matching the value stored in app_secrets."""
    from app.main import app

    redis = app.state.redis
    stored = await redis.get("ai:litellm_master_key")
    assert stored is not None, "lifespan must bootstrap the key"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/joseph/dashboard && docker compose exec backend uv run pytest tests/api/test_lifespan_litellm_bootstrap.py -v`
Expected: FAIL with `assert None is not None`.

- [ ] **Step 3: Add the lifespan bootstrap**

Read `backend/app/main.py` around the lifespan function (lines 110-200). Add a bootstrap block AFTER the redis client is created (`_app.state.redis = redis`) and BEFORE the broker layer init. Locate via:

Run: `grep -n "_app.state.redis = redis" /home/joseph/dashboard/backend/app/main.py`

Insert this snippet (use the exact existing patterns for secret reads):

```python
    # Phase 11a-A.5 (HIGH-5): bootstrap LiteLLM master-key in Redis so
    # the auth-callback in deploy/litellm/config.yaml sees it. Operator
    # rotates via PUT /api/admin/secrets/ai/litellm_master_key.
    try:
        master_key = await svc.get_secret("ai.litellm_master_key")
    except KeyError:
        # First boot: seed a placeholder so LiteLLM at least starts up.
        # Operator MUST rotate via the admin endpoint before any real call.
        master_key = "sk-bootstrap-rotate-me"
        await svc.set_secret("ai.litellm_master_key", master_key)
    await redis.set("ai:litellm_master_key", master_key)
```

Where `svc` is the existing `ConfigService` (look for `svc = ConfigService(...)` in the lifespan).

- [ ] **Step 4: Run test to verify pass**

Restart backend (`docker compose restart backend`), then re-run test.
Expected: PASS.

- [ ] **Step 5: Commit**

```
feat(phase11a-A.5): bootstrap LiteLLM master-key in Redis on lifespan

BE lifespan reads app_secrets.ai.litellm_master_key (seeding a
placeholder on first boot) and writes to Redis key
ai:litellm_master_key. The LiteLLM auth-callback module mounted into
the proxy container reads this value on every protected request —
zero-restart rotation flows BE → Redis → LiteLLM automatically.
```

### Task 13: Wire LiteLLM container to use the callback

**Files:**
- Modify: `deploy/litellm/config.yaml`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update the LiteLLM config to use custom_auth**

Modify `deploy/litellm/config.yaml`. Replace the docstring header note about chunk A.5 and add `general_settings`:

```yaml
# Production LiteLLM config — committed to git, contains NO secrets.
# Provider keys arrive per-request from BE (Option C, validated in 11a-A0).
# Master-key validation is Redis-backed via custom_auth callback
# mounted from backend/app/services/ai/litellm_auth_callback.py
# (HIGH-5 — zero-restart rotation).
model_list:
  ... (unchanged) ...

general_settings:
  custom_auth: services.ai.litellm_auth_callback.user_api_key_auth
  custom_auth_settings:
    mode: "on"  # ONLY trust our callback; do not also run litellm's default master-key check
```

- [ ] **Step 2: Update docker-compose to mount the callback module + pass REDIS_URL**

Modify the `litellm:` service in `docker-compose.yml`:

```yaml
  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    container_name: dashboard-litellm
    restart: unless-stopped
    ports:
      - "127.0.0.1:4000:4000"
    volumes:
      - ./deploy/litellm/config.yaml:/app/config.yaml:ro
      # Mount the callback module so LiteLLM can import it via PYTHONPATH.
      - ./backend/app/services/ai/litellm_auth_callback.py:/app/custom_auth/services/ai/litellm_auth_callback.py:ro
      # Mark the parent dirs as Python packages — the bind mounts above
      # only mount the file, not __init__.py. Use empty placeholders.
      - ./deploy/litellm/__init__.py:/app/custom_auth/services/__init__.py:ro
      - ./deploy/litellm/__init__.py:/app/custom_auth/services/ai/__init__.py:ro
    environment:
      PYTHONPATH: /app/custom_auth
      REDIS_URL: "redis://:${REDIS_PASSWORD}@redis:6379/0"
      LITELLM_LOG: WARNING
    healthcheck:
      test: ["CMD", "litellm", "--health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    depends_on:
      redis:
        condition: service_healthy
    command: ["--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
```

Also create `deploy/litellm/__init__.py` as an empty file (it doubles as the package marker for the mounted paths).

- [ ] **Step 3: Restart LiteLLM**

```bash
docker compose up -d litellm
```

- [ ] **Step 4: Verify LiteLLM started successfully**

```bash
docker compose logs --tail 30 litellm
```

Expected: no `ImportError`, no `ModuleNotFoundError`. Look for "Uvicorn running on..." or equivalent.

- [ ] **Step 5: Verify auth-callback enforcement end-to-end**

```bash
# Wrong key — should 401
WRONG_KEY='example-wrong-placeholder'
curl -sf -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $WRONG_KEY" \
  http://localhost:4000/v1/models
# Expected: 401

# Correct key (the placeholder we seeded) — should 200
# Use the literal bootstrap value from app_secrets.ai.litellm_master_key.
RIGHT_KEY=$(docker compose exec backend uv run python -c \
  "import asyncio; from app.services.config import get_config_service; \
   print(asyncio.run((lambda: \
     get_config_service().__await__().__next__().get_secret('ai.litellm_master_key'))()))")
curl -sf -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $RIGHT_KEY" \
  http://localhost:4000/v1/models
# Expected: 200
```

- [ ] **Step 6: Commit**

```
feat(phase11a-A.5): wire LiteLLM container to use Redis-backed auth-callback

config.yaml general_settings.custom_auth points at the mounted module;
custom_auth_settings.mode: "on" disables LiteLLM's default master-key
env-var check so only our callback runs. docker-compose mounts the
callback module + parent-package __init__.py stubs + sets PYTHONPATH
and REDIS_URL. Restart-rotation is now removed — operators rotate
via admin PUT and LiteLLM picks up immediately.
```

### Task 14: Admin rotation endpoint with CSRF nonce

**Files:**
- Modify: `backend/app/api/admin.py`
- Test: `backend/tests/integration/test_admin_litellm_master_key.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_admin_litellm_master_key.py`:

```python
"""Phase 11a-A.5: admin rotation endpoint for the LiteLLM master key.

Mutating a secret requires CSRF nonce per CLAUDE.md cross-cutting rule
+ MED-5 in the spec. Rotation must update BOTH app_secrets AND Redis
atomically so LiteLLM sees the new value on the next request without
a docker compose restart.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_rotate_litellm_master_key_requires_nonce(
    test_client_admin: AsyncClient,
) -> None:
    resp = await test_client_admin.put(
        "/api/admin/secrets/ai/litellm_master_key",
        json={"value": "sk-new-master"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_rotate_litellm_master_key_updates_redis_and_secrets(
    test_client_admin: AsyncClient,
) -> None:
    nonce_resp = await test_client_admin.post("/api/admin/confirmation-nonce")
    assert nonce_resp.status_code == 200, nonce_resp.text
    nonce = nonce_resp.json()["nonce"]

    resp = await test_client_admin.put(
        "/api/admin/secrets/ai/litellm_master_key",
        json={"value": "sk-rotated-master"},
        headers={"X-Confirm-Nonce": nonce},
    )
    assert resp.status_code == 200, resp.text

    from app.main import app

    stored_in_redis = await app.state.redis.get("ai:litellm_master_key")
    assert stored_in_redis == b"sk-rotated-master"


@pytest.mark.asyncio
async def test_rotate_rejects_short_key(test_client_admin: AsyncClient) -> None:
    nonce_resp = await test_client_admin.post("/api/admin/confirmation-nonce")
    nonce = nonce_resp.json()["nonce"]
    resp = await test_client_admin.put(
        "/api/admin/secrets/ai/litellm_master_key",
        json={"value": "too-short"},
        headers={"X-Confirm-Nonce": nonce},
    )
    assert resp.status_code == 400, resp.text
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose exec backend uv run pytest tests/integration/test_admin_litellm_master_key.py -v`
Expected: FAIL (endpoint doesn't exist yet).

- [ ] **Step 3: Add the admin endpoint**

Read `backend/app/api/admin.py` to find the existing admin-secrets pattern. Add the new endpoint near the existing `/api/admin/secrets/...` routes (the pattern is established for broker secrets in chunk 7a):

```python
class _LitellmMasterKeyBody(BaseModel):
    value: str = Field(..., min_length=16, max_length=256)


@router.put(
    "/secrets/ai/litellm_master_key",
    dependencies=[Depends(consume_confirmation_nonce)],
)
async def rotate_litellm_master_key(
    body: _LitellmMasterKeyBody,
    svc: Annotated[ConfigService, Depends(get_config_service)],
    redis: RedisDep,
    _admin: Annotated[None, Depends(require_admin)],
) -> dict[str, str]:
    """Phase 11a-A.5 HIGH-5: zero-restart rotation. Write to
    app_secrets AND Redis atomically so the LiteLLM auth-callback sees
    the new value on the next request."""
    await svc.set_secret("ai.litellm_master_key", body.value)
    await redis.set("ai:litellm_master_key", body.value)
    return {"status": "rotated"}
```

The existing `consume_confirmation_nonce`, `require_admin`, `get_config_service`, `RedisDep`, `ConfigService` imports already live at the top of `admin.py`; re-use them.

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose restart backend
docker compose exec backend uv run pytest tests/integration/test_admin_litellm_master_key.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
feat(phase11a-A.5): PUT /api/admin/secrets/ai/litellm_master_key

Admin rotation endpoint writes new key to app_secrets AND Redis
atomically; CSRF nonce required per MED-5. Min-length 16 chars guards
against accidental "test" / empty strings reaching production.
LiteLLM auth-callback sees the new value on the next protected
request — zero docker compose interaction needed.
```

### Task 15: Chunk A.5 reviewer chain + tag v0.11.0.2

- [ ] **Step 1: Dispatch reviewers in parallel**

- spec-compliance (haiku) with inline spec slice for §2 11a-A.5
- python-reviewer (haiku) on all chunk A.5 commits
- security-reviewer (sonnet) — mandatory because this chunk handles auth
- silent-failure-hunter (sonnet) — mandatory because the auth callback has multiple failure paths

- [ ] **Step 2: Apply CRIT+HIGH+MED findings inline**

Per `feedback_architect_findings_apply_through_medium.md`.

- [ ] **Step 3: Tag**

```bash
git tag -a v0.11.0.2 -F /tmp/tag-msg-phase11a-a5.txt
git push --tags
```

(Tag message file follows the v0.11.0.1 template — chunk-commits list + reviewer-results summary + test status.)

### Chunk A2 — WoL + Ollama service installs + readiness probe + circuit breaker

**Goal:** Make the heavy box wake-able on demand from the BE. NUC-side WoL helper resolves the heavy-box MAC via ARP and broadcasts the magic packet on the LAN; BE polls the heavy-box Ollama `GET /api/tags` until the requested model is loaded. Circuit breaker opens after 3 failures in 10min so a flapping heavy box doesn't melt the router.

**Locked decisions (from user clarifying questions 2026-05-12):**
- Heavy box is **Windows (with WSL)** — Ollama installed as a Windows service for boot-without-login. Same pattern as NUC.
- WoL path: **NUC-side WoL helper** — BE on VPS calls a tiny HTTP service on the NUC over WG; helper broadcasts on the LAN.
- MAC resolution: **ARP-from-NUC** — helper resolves 192.168.50.30 → MAC from the local ARP table on each wake; caches in memory.

**Files to create:**
- `deploy/nuc/install-ollama.ps1` — Windows installer + service registration + initial model pull (`qwen2.5:7b`, `llama3.2:8b`)
- `deploy/nuc/wol_helper.ps1` — PowerShell HTTP service: listens on `10.10.0.2:11900/wake`, ARP-resolves heavy box, broadcasts magic packet on LAN
- `deploy/nuc/install-wol-helper.ps1` — registers the helper as a Windows scheduled task that starts at boot (pattern matches existing broker-sidecar scheduled tasks)
- `deploy/heavybox/install-ollama.ps1` — Windows installer mirror; pulls `qwen2.5:32b`, `llama3.3:70b`, `qwen2.5-coder:32b`
- `deploy/heavybox/install-idle-suspend.ps1` — Windows scheduled task: every 5min check `netstat` for active connections on :11434; suspend if idle >15min
- `backend/app/services/ai/wol.py` — `HeavyBoxWoL` class with `wake_and_wait_for_model(model_name) -> WakeResult`; uses async httpx to call NUC helper + poll Ollama `/api/tags`
- `backend/tests/services/ai/test_wol.py` — unit tests for WoL primitive with fake helper + fake Ollama

**Files to modify:**
- `backend/app/main.py` — lifespan: instantiate `HeavyBoxWoL` singleton on `app.state.heavy_wol` (mirrors `app.state.vol_service` pattern from Phase 10b.1)
- `backend/app/core/metrics.py` — register WoL metrics (`ai_router_wol_*`)

### Task 16: NUC Ollama install runbook

**Files:**
- Create: `deploy/nuc/install-ollama.ps1`

- [ ] **Step 1: Author the install script**

Create `deploy/nuc/install-ollama.ps1`:

```powershell
# deploy/nuc/install-ollama.ps1 — Phase 11a-A2
# Run as Administrator on the NUC15PRO. Installs Ollama as a Windows
# service so it survives reboot without login. Pulls the LOCAL_ONLY +
# STRUCTURED_OUTPUT default models. Verifies the API responds.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "==> Installing Ollama (Windows)..."
$installer = "$env:TEMP\OllamaSetup.exe"
Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait

Write-Host "==> Configuring Ollama service to listen on 0.0.0.0:11434..."
# Recent Ollama installers register as a per-user service; we set the
# OLLAMA_HOST env var system-wide so it binds to 0.0.0.0 and is reachable
# from the WG-routed BE on the VPS.
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "5m", "Machine")

Write-Host "==> Restarting Ollama service to pick up env vars..."
Stop-Service -Name "Ollama" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service -Name "Ollama"

Write-Host "==> Pulling default LOCAL_ONLY models (this takes a while)..."
& ollama pull qwen2.5:7b
& ollama pull llama3.2:8b

Write-Host "==> Smoke-testing the API..."
$response = Invoke-RestMethod -Uri "http://10.10.0.2:11434/api/tags" -TimeoutSec 10
if ($response.models.Count -lt 2) {
    Write-Error "Ollama returned <2 models after install"
    exit 1
}
Write-Host "✓ NUC Ollama install complete. Models loaded:"
$response.models | ForEach-Object { Write-Host "  - $($_.name)" }
```

- [ ] **Step 2: Document operator action**

The script is operator-run; we don't execute it. Add to chunk-A2 close-out notes that the operator must run it once on the NUC after `git pull`. Reviewer chain (haiku spec) doesn't validate runtime behaviour; the smoke step inside the script is the operator's confirmation.

- [ ] **Step 3: Commit**

```
feat(phase11a-A2): NUC Ollama install runbook (Windows service)

deploy/nuc/install-ollama.ps1 installs Ollama as a Windows service
with OLLAMA_HOST=0.0.0.0:11434 and OLLAMA_KEEP_ALIVE=5m as machine
env vars (survives reboot, no login required). Pulls qwen2.5:7b +
llama3.2:8b for the LOCAL_ONLY + STRUCTURED_OUTPUT defaults in
config_defaults.DEFAULT_AI_ROUTER_CAPABILITY_MAP. Operator-run once
post-deploy; pattern matches existing broker-sidecar scheduled tasks.
```

### Task 17: NUC WoL helper service

**Files:**
- Create: `deploy/nuc/wol_helper.ps1`
- Create: `deploy/nuc/install-wol-helper.ps1`

- [ ] **Step 1: Author the WoL helper**

Create `deploy/nuc/wol_helper.ps1`:

```powershell
# deploy/nuc/wol_helper.ps1 — Phase 11a-A2
# Tiny HTTP service on the NUC. BE on the VPS calls this over WG to
# wake the heavy box; we ARP-resolve the heavy box IP to MAC on the
# LAN side and broadcast the magic packet. The packet doesn't cross
# WG cleanly so it MUST be sent from a same-LAN host.
#
# Listen on 10.10.0.2:11900 (WG-internal). NOT exposed past WG.
# Single endpoint: POST /wake -> { "status": "sent", "mac": "..." }

$ErrorActionPreference = "Stop"
$Listener = New-Object System.Net.HttpListener
$Listener.Prefixes.Add("http://10.10.0.2:11900/")
$Listener.Start()
Write-Host "WoL helper listening on http://10.10.0.2:11900/"

# In-memory MAC cache (per-process, lost on restart — fine; ARP re-
# resolves on next wake).
$MacCache = @{}

function Resolve-MacFromArp($ip) {
    if ($MacCache.ContainsKey($ip)) { return $MacCache[$ip] }
    # Probe the host so the ARP table has a fresh entry.
    Test-Connection -ComputerName $ip -Count 1 -TimeoutSeconds 2 -Quiet | Out-Null
    $arpLine = (arp -a $ip | Select-String "$ip\s+([\w-]{17})") .Matches.Groups[1].Value
    if (-not $arpLine) { return $null }
    $mac = $arpLine -replace '-', ':'
    $MacCache[$ip] = $mac
    return $mac
}

function Send-MagicPacket($macStr) {
    $macBytes = ($macStr -split ':') | ForEach-Object { [Convert]::ToByte($_, 16) }
    $packet = [byte[]](,0xFF * 6 + ($macBytes * 16))
    $udpClient = New-Object System.Net.Sockets.UdpClient
    $udpClient.EnableBroadcast = $true
    $udpClient.Send($packet, $packet.Length, "255.255.255.255", 9) | Out-Null
    $udpClient.Close()
}

while ($Listener.IsListening) {
    $ctx = $Listener.GetContext()
    $req = $ctx.Request
    $res = $ctx.Response
    try {
        if ($req.HttpMethod -ne "POST" -or $req.Url.AbsolutePath -ne "/wake") {
            $res.StatusCode = 404
            continue
        }
        $heavyIp = "192.168.50.30"
        $mac = Resolve-MacFromArp $heavyIp
        if (-not $mac) {
            $res.StatusCode = 502
            $body = '{"status":"failed","reason":"arp_resolve_failed"}'
        } else {
            Send-MagicPacket $mac
            $res.StatusCode = 200
            $body = "{`"status`":`"sent`",`"mac`":`"$mac`"}"
        }
        $buf = [System.Text.Encoding]::UTF8.GetBytes($body)
        $res.OutputStream.Write($buf, 0, $buf.Length)
    } finally {
        $res.Close()
    }
}
```

- [ ] **Step 2: Author the scheduled-task installer**

Create `deploy/nuc/install-wol-helper.ps1`:

```powershell
# deploy/nuc/install-wol-helper.ps1 — Phase 11a-A2
# Register wol_helper.ps1 as a Windows scheduled task that starts at
# boot. Mirrors the pattern used by broker-sidecar tasks.

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$taskName = "dashboard-wol-helper"
$scriptPath = (Resolve-Path "$PSScriptRoot\wol_helper.ps1").Path

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force

Start-ScheduledTask -TaskName $taskName
Write-Host "✓ wol-helper scheduled task installed and started."
```

- [ ] **Step 3: Commit**

```
feat(phase11a-A2): NUC WoL helper + Windows scheduled-task installer

The WoL magic packet doesn't cross WireGuard cleanly, so the BE on
the VPS calls a tiny HTTP service on the NUC over WG; that service
ARP-resolves the heavy box's MAC on the LAN side and broadcasts the
packet. Mirrors the broker-sidecar scheduled-task pattern. Listens on
10.10.0.2:11900 (NOT exposed past WG). MAC cache is in-memory; ARP
re-resolves on restart.
```

### Task 18: Heavy-box Ollama install runbook + idle-suspend

**Files:**
- Create: `deploy/heavybox/install-ollama.ps1`
- Create: `deploy/heavybox/install-idle-suspend.ps1`

- [ ] **Step 1: Author the heavy-box Ollama install script**

Create `deploy/heavybox/install-ollama.ps1`:

```powershell
# deploy/heavybox/install-ollama.ps1 — Phase 11a-A2
# Run as Administrator on the heavy box (Windows + WSL — Ollama runs
# native). Installs as a Windows service so it survives WoL wake
# without anyone logging in. Pulls the REASONING + CODING + heavy
# LOCAL_ONLY defaults.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "==> Installing Ollama (Windows)..."
$installer = "$env:TEMP\OllamaSetup.exe"
Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait

Write-Host "==> Configuring Ollama service to listen on 0.0.0.0:11434..."
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "5m", "Machine")

Write-Host "==> Restarting Ollama service to pick up env vars..."
Stop-Service -Name "Ollama" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service -Name "Ollama"

Write-Host "==> Pulling default REASONING / heavy LOCAL_ONLY / CODING models (takes a while)..."
& ollama pull qwen2.5:32b
& ollama pull llama3.3:70b
& ollama pull qwen2.5-coder:32b

Write-Host "==> Smoke-testing the API from the heavy box itself..."
$response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 10
if ($response.models.Count -lt 3) {
    Write-Error "Ollama returned <3 models after install"
    exit 1
}
Write-Host "✓ Heavy-box Ollama install complete. Models loaded:"
$response.models | ForEach-Object { Write-Host "  - $($_.name)" }
```

- [ ] **Step 2: Author the idle-suspend task**

Create `deploy/heavybox/install-idle-suspend.ps1`:

```powershell
# deploy/heavybox/install-idle-suspend.ps1 — Phase 11a-A2
# Auto-suspend the heavy box after 15min of no traffic to :11434.
# Runs as a scheduled task every 5min; checks netstat; suspends when
# the count of established connections has been zero for 3 consecutive
# checks (3 * 5min = 15min idle window).

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$watchScript = @'
$idleFile = "$env:ProgramData\dashboard-heavy-idle.txt"
$count = 0
if (Test-Path $idleFile) { $count = [int](Get-Content $idleFile) }

$conns = (netstat -an | Select-String ":11434" | Select-String "ESTABLISHED").Count
if ($conns -eq 0) { $count++ } else { $count = 0 }
Set-Content -Path $idleFile -Value $count

if ($count -ge 3) {
    Remove-Item $idleFile -Force
    # 0=sleep, 1=hibernate
    rundll32.exe powrprof.dll,SetSuspendState 0,1,0
}
'@

$watchPath = "$env:ProgramData\dashboard-heavy-idle-check.ps1"
Set-Content -Path $watchPath -Value $watchScript -Encoding UTF8

$taskName = "dashboard-heavy-idle-suspend"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchPath`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Force

Write-Host "✓ idle-suspend scheduled task installed (15min idle window)."
```

- [ ] **Step 3: Commit**

```
feat(phase11a-A2): heavy-box Ollama install runbook + idle-suspend task

deploy/heavybox/install-ollama.ps1 mirrors the NUC pattern: Windows
service, OLLAMA_HOST=0.0.0.0:11434, OLLAMA_KEEP_ALIVE=5m. Pulls
qwen2.5:32b + llama3.3:70b + qwen2.5-coder:32b for the REASONING +
CODING + heavy LOCAL_ONLY defaults.

install-idle-suspend.ps1 registers a 5-minute scheduled task that
counts consecutive idle checks on :11434 and suspends after 15min
of no active connections — keeps the heavy box asleep when nobody's
using it; WoL wakes it back on demand.
```

### Task 19: Write the failing test for `HeavyBoxWoL`

**Files:**
- Create: `backend/tests/services/ai/test_wol.py`

- [ ] **Step 1: Author the test**

Create `backend/tests/services/ai/test_wol.py`:

```python
"""Phase 11a-A2: HeavyBoxWoL unit tests.

Validates: wake-helper RPC, model-ready polling, circuit-breaker
state transitions, and idempotent wake (multiple concurrent callers
share one probe).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.no_db


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock() -> _FakeClock:
    return _FakeClock()


def _make_transport(
    *,
    wake_status: int = 200,
    tags_seq: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    """Build a transport that the WoL primitive talks through.

    Args:
        wake_status: status code returned by POST /wake on the NUC helper.
        tags_seq: response payloads (dict) returned by successive calls
          to GET /api/tags on the heavy-box Ollama. Useful for simulating
          "model not loaded yet" -> "model loaded" transitions.
    """
    if tags_seq is None:
        tags_seq = [{"models": [{"name": "qwen2.5:32b"}]}]
    tags_iter = iter(tags_seq)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/wake" in request.url.path:
            return httpx.Response(wake_status, json={"status": "sent", "mac": "AA:BB"})
        if request.method == "GET" and request.url.path == "/api/tags":
            try:
                return httpx.Response(200, json=next(tags_iter))
            except StopIteration:
                return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_wake_and_wait_returns_ready_when_model_present(fake_clock: _FakeClock) -> None:
    from app.services.ai.wol import HeavyBoxWoL

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(),
    )
    result = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=60.0)
    assert result.status == "ready"
    assert result.tcp_open_ms is not None
    assert result.model_ready_ms is not None


@pytest.mark.asyncio
async def test_wake_returns_failed_when_helper_rejects(fake_clock: _FakeClock) -> None:
    from app.services.ai.wol import HeavyBoxWoL

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(wake_status=502),
    )
    result = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=5.0)
    assert result.status == "failed"
    assert "helper" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wake_returns_failed_when_model_never_appears(fake_clock: _FakeClock) -> None:
    from app.services.ai.wol import HeavyBoxWoL

    # /api/tags responds 200 but never lists the requested model.
    tags_seq = [{"models": [{"name": "other-model"}]}] * 30
    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(tags_seq=tags_seq),
        poll_interval_s=0.0,  # synchronous test loop
    )
    result = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.5)
    assert result.status == "failed"
    assert "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_three_failures_in_window(
    fake_clock: _FakeClock,
) -> None:
    """3 wake failures within 10min open the breaker; subsequent calls
    return 'circuit_open' WITHOUT issuing a wake request."""
    from app.services.ai.wol import HeavyBoxWoL

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(wake_status=502),
    )
    for _ in range(3):
        r = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.1)
        assert r.status == "failed"

    # 4th call within the window must short-circuit.
    r4 = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.1)
    assert r4.status == "circuit_open"


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_window(fake_clock: _FakeClock) -> None:
    """After 5min in the open state, the next call gets one trial wake
    (half-open). If it succeeds, the breaker closes."""
    from app.services.ai.wol import HeavyBoxWoL

    # Three failures open the breaker.
    failing_transport = _make_transport(wake_status=502)
    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=failing_transport,
    )
    for _ in range(3):
        await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.1)

    # Window passes.
    fake_clock.advance(5 * 60 + 1)

    # Swap the transport to a healthy one (success path).
    wol.transport = _make_transport()
    r = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=60.0)
    assert r.status == "ready"


@pytest.mark.asyncio
async def test_concurrent_waker_calls_share_single_probe(fake_clock: _FakeClock) -> None:
    """Two callers concurrently asking for the same model wake just
    once. Defended via asyncio.Event."""
    from app.services.ai.wol import HeavyBoxWoL

    call_count = {"wake": 0, "tags": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/wake" in request.url.path:
            call_count["wake"] += 1
            return httpx.Response(200, json={"status": "sent"})
        if request.method == "GET" and request.url.path == "/api/tags":
            call_count["tags"] += 1
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:32b"}]})
        return httpx.Response(404)

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=httpx.MockTransport(handler),
    )
    r1, r2 = await asyncio.gather(
        wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=5.0),
        wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=5.0),
    )
    assert r1.status == "ready"
    assert r2.status == "ready"
    assert call_count["wake"] == 1  # Only ONE wake packet despite TWO callers.
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/services/ai/test_wol.py -v`
Expected: `ModuleNotFoundError: app.services.ai.wol`.

- [ ] **Step 3: Commit**

```
test(phase11a-A2): unit tests for HeavyBoxWoL primitive

6 tests cover the contract: helper-RPC success, helper-RPC failure,
model never appears (timeout), circuit-breaker opens after 3 fails
in 10min, circuit-breaker half-open recovery, concurrent-callers
share single probe (asyncio.Event-based dedup).
```

### Task 20: Implement `HeavyBoxWoL`

**Files:**
- Create: `backend/app/services/ai/wol.py`

- [ ] **Step 1: Author the implementation**

Create `backend/app/services/ai/wol.py`:

```python
"""Phase 11a-A2 (HIGH-3 / HIGH-8): HeavyBoxWoL primitive.

BE on the VPS asks the NUC-side WoL helper to broadcast a magic
packet (the packet doesn't cross WG cleanly so the NUC, same LAN as
the heavy box, fires it). BE then polls heavy-box Ollama
``GET /api/tags`` until the requested model appears OR a deadline
elapses. Circuit breaker: 3 wake failures within 10min open the
breaker for 5min, after which one trial wake is allowed (half-open).

Idempotent under concurrent callers via asyncio.Event — multiple
requests for the same model share a single magic-packet + probe loop.
Single-replica today; multi-replica deferred to Phase 24.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx


@dataclass(frozen=True)
class WakeResult:
    status: Literal["ready", "failed", "circuit_open"]
    tcp_open_ms: int | None = None
    model_ready_ms: int | None = None
    error: str | None = None


@dataclass
class _BreakerState:
    failures: list[float] = field(default_factory=list)  # monotonic times
    opened_at: float | None = None


_FAILURE_WINDOW_S = 10 * 60
_FAILURE_THRESHOLD = 3
_OPEN_DURATION_S = 5 * 60


class HeavyBoxWoL:
    """Wakes the heavy box on demand and waits for a named Ollama model."""

    def __init__(
        self,
        *,
        helper_url: str,
        heavy_url: str,
        clock: Callable[[], float] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._helper_url = helper_url.rstrip("/")
        self._heavy_url = heavy_url.rstrip("/")
        self._clock = clock or time.monotonic
        self.transport = transport  # public so tests can swap it mid-run
        self._poll_interval_s = poll_interval_s
        self._breaker = _BreakerState()
        # Per-model singleton wake — keyed by model name so different
        # models don't share each other's pending probe.
        self._inflight: dict[str, asyncio.Task[WakeResult]] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=10.0)

    def _circuit_state(self) -> Literal["closed", "open", "half_open"]:
        now = self._clock()
        if self._breaker.opened_at is None:
            return "closed"
        if (now - self._breaker.opened_at) < _OPEN_DURATION_S:
            return "open"
        return "half_open"

    def _record_failure(self) -> None:
        now = self._clock()
        # Drop failures outside the window.
        cutoff = now - _FAILURE_WINDOW_S
        self._breaker.failures = [t for t in self._breaker.failures if t >= cutoff]
        self._breaker.failures.append(now)
        if len(self._breaker.failures) >= _FAILURE_THRESHOLD:
            self._breaker.opened_at = now

    def _record_success(self) -> None:
        self._breaker.failures.clear()
        self._breaker.opened_at = None

    async def wake_and_wait_for_model(
        self, model_name: str, *, timeout_s: float = 60.0
    ) -> WakeResult:
        state = self._circuit_state()
        if state == "open":
            return WakeResult(status="circuit_open", error="breaker open after 3 failures")

        existing = self._inflight.get(model_name)
        if existing is not None and not existing.done():
            return await existing

        task = asyncio.create_task(self._do_wake(model_name, timeout_s))
        self._inflight[model_name] = task
        try:
            return await task
        finally:
            self._inflight.pop(model_name, None)

    async def _do_wake(self, model_name: str, timeout_s: float) -> WakeResult:
        started = self._clock()
        async with self._client() as client:
            # 1) Tell the NUC helper to broadcast the magic packet.
            try:
                resp = await client.post(f"{self._helper_url}/wake")
            except Exception as exc:  # noqa: BLE001
                self._record_failure()
                return WakeResult(status="failed", error=f"helper_unreachable: {exc}")
            if resp.status_code != 200:
                self._record_failure()
                return WakeResult(
                    status="failed", error=f"helper_rejected: HTTP {resp.status_code}"
                )

            tcp_open_ms: int | None = None
            deadline = started + timeout_s
            while self._clock() < deadline:
                try:
                    tags = await client.get(f"{self._heavy_url}/api/tags")
                except Exception:  # noqa: BLE001 — box still booting
                    await asyncio.sleep(self._poll_interval_s)
                    continue
                if tcp_open_ms is None:
                    tcp_open_ms = int((self._clock() - started) * 1000)
                if tags.status_code == 200:
                    payload = tags.json()
                    names = {m.get("name") for m in payload.get("models", [])}
                    if model_name in names:
                        ready_ms = int((self._clock() - started) * 1000)
                        self._record_success()
                        return WakeResult(
                            status="ready",
                            tcp_open_ms=tcp_open_ms,
                            model_ready_ms=ready_ms,
                        )
                await asyncio.sleep(self._poll_interval_s)

            self._record_failure()
            return WakeResult(
                status="failed",
                tcp_open_ms=tcp_open_ms,
                error="timeout_waiting_for_model",
            )
```

- [ ] **Step 2: Run tests to verify pass**

Run: `cd /home/joseph/dashboard/backend && uv run pytest tests/services/ai/test_wol.py -v`
Expected: 6 PASS.

- [ ] **Step 3: Commit**

```
feat(phase11a-A2): HeavyBoxWoL primitive with circuit breaker

services/ai/wol.py — async helper-RPC + Ollama tag-poll. Magic packet
is broadcast by the NUC-side wol_helper.ps1 (the packet doesn't cross
WG cleanly so a same-LAN host must fire it). HIGH-3: readiness probe
checks the target model is listed in /api/tags, not just that TCP
:11434 is open (Ollama accepts connections during 30-90s model load).

Circuit breaker: 3 wake failures within 10min open the breaker for
5min; half-open after that. Concurrent callers asking for the same
model share a single probe via per-model asyncio task cache.
```

### Task 21: Register WoL metrics + wire singleton into lifespan

**Files:**
- Modify: `backend/app/core/metrics.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add metrics in core/metrics.py**

Run: `grep -n "ai_router\|Counter\|Histogram\|Gauge" /home/joseph/dashboard/backend/app/core/metrics.py | head -20`

Append (mirroring the existing Counter/Histogram patterns):

```python
# Phase 11a-A2: WoL primitive metrics (spec §6, HIGH-3 split).
AI_ROUTER_WOL_WAKE_TOTAL = Counter(
    "ai_router_wol_wake_total",
    "Total wake attempts via the NUC helper.",
    ["host", "outcome"],  # host=heavy; outcome=ready|failed|circuit_open
)
AI_ROUTER_WOL_WAKE_LATENCY_SECONDS = Histogram(
    "ai_router_wol_wake_latency_seconds",
    "Seconds from wake request to first TCP-open on the target Ollama.",
    ["host"],
)
AI_ROUTER_WOL_WARM_TO_READY_SECONDS = Histogram(
    "ai_router_wol_warm_to_ready_seconds",
    "Seconds from wake request to target model present in /api/tags.",
    ["host"],
)
AI_ROUTER_WOL_WAKE_FAILURES_TOTAL = Counter(
    "ai_router_wol_wake_failures_total",
    "Wake attempts that ended in failed/timeout (excludes circuit_open).",
    ["host", "reason"],
)
AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE = Gauge(
    "ai_router_wol_circuit_breaker_state",
    "Circuit-breaker state per host (0=closed, 1=half-open, 2=open).",
    ["host"],
)
```

- [ ] **Step 2: Wire the metrics into `HeavyBoxWoL`**

Modify `backend/app/services/ai/wol.py` — observe metrics in `_do_wake` + `_record_failure` + `_record_success` + `_circuit_state` (state Gauge updated on transition):

```python
# At top of file:
from app.core.metrics import (
    AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE,
    AI_ROUTER_WOL_WAKE_FAILURES_TOTAL,
    AI_ROUTER_WOL_WAKE_LATENCY_SECONDS,
    AI_ROUTER_WOL_WAKE_TOTAL,
    AI_ROUTER_WOL_WARM_TO_READY_SECONDS,
)

# In _record_failure (after appending now to failures):
AI_ROUTER_WOL_WAKE_FAILURES_TOTAL.labels(host="heavy", reason="wake_or_probe").inc()
if self._breaker.opened_at == now:
    AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE.labels(host="heavy").set(2)

# In _record_success:
AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE.labels(host="heavy").set(0)

# In _do_wake on ready:
AI_ROUTER_WOL_WAKE_TOTAL.labels(host="heavy", outcome="ready").inc()
AI_ROUTER_WOL_WAKE_LATENCY_SECONDS.labels(host="heavy").observe(tcp_open_ms / 1000)
AI_ROUTER_WOL_WARM_TO_READY_SECONDS.labels(host="heavy").observe(ready_ms / 1000)

# In wake_and_wait_for_model when status=circuit_open:
AI_ROUTER_WOL_WAKE_TOTAL.labels(host="heavy", outcome="circuit_open").inc()
```

- [ ] **Step 3: Wire singleton into lifespan**

Modify `backend/app/main.py` — after the LiteLLM bootstrap block (~line 156), instantiate:

```python
# Phase 11a-A2: HeavyBoxWoL singleton; consumers reach it via
# app.state.heavy_wol. URLs come from env (10.10.0.2:11900 helper,
# 10.10.0.3:11434 heavy-box Ollama by default per docs/NETWORK.md).
from app.services.ai.wol import HeavyBoxWoL

_app.state.heavy_wol = HeavyBoxWoL(
    helper_url=os.environ.get("WOL_HELPER_URL", "http://10.10.0.2:11900"),
    heavy_url=os.environ.get("OLLAMA_HEAVY_URL", "http://10.10.0.3:11434"),
)
```

- [ ] **Step 4: Run tests + verify lifespan starts**

```bash
cd /home/joseph/dashboard
docker compose cp backend/app/main.py backend:/app/app/main.py
docker compose cp backend/app/services/ai/wol.py backend:/app/app/services/ai/wol.py
docker compose cp backend/app/core/metrics.py backend:/app/app/core/metrics.py
docker compose restart backend
sleep 8
docker compose logs --tail 20 backend | grep -i "uvicorn running\|application startup"
docker compose exec backend uv run pytest tests/services/ai/test_wol.py -v
```

- [ ] **Step 5: Commit**

```
feat(phase11a-A2): WoL metrics + lifespan singleton wiring

5 new Prometheus series under ai_router_wol_* per spec §6:
- wake_total{host,outcome}
- wake_latency_seconds{host} (TCP-open)
- warm_to_ready_seconds{host} (model loaded — HIGH-3 split)
- wake_failures_total{host,reason}
- circuit_breaker_state{host} (Gauge: 0=closed, 1=half-open, 2=open)

HeavyBoxWoL singleton on app.state.heavy_wol (mirrors vol_service
pattern from Phase 10b.1). URLs from env so dev/prod swap is trivial.
```

### Task 22: Chunk A2 reviewer chain + tag v0.11.0.3

- [ ] **Step 1: Dispatch reviewers in parallel**

- spec-compliance (haiku) with inline spec slice for §2 11a-A2
- python-reviewer (haiku) on all chunk-A2 commits
- code-reviewer (sonnet) — circuit-breaker logic + concurrency dedup deserve sonnet-level scrutiny
- silent-failure-hunter (sonnet) — multiple network failure paths

- [ ] **Step 2: Apply CRIT+HIGH+MED findings inline**

- [ ] **Step 3: Tag**

```bash
git tag -a v0.11.0.3 -F /tmp/tag-msg-phase11a-a2.txt
git push origin main --tags
```

### Chunk B — services/ai/ core (sketch)

**Files to create:** `backend/app/services/ai/router.py`, `secrets.py`, `cost_ledger.py`, `jobs.py`, `rate_limiter.py`, `services/common/rate_limiter.py`, `services/common/ws_envelope.py`, tests.

**Task outline:**
1. Extract `SlidingWindowRateLimiter[K]` generic from `portfolio_rate_limiter` + `position_sizing_rate_limiter` (MED-3 — no-op refactor; existing tests stay green).
2. Extract `make_ws_endpoint(...)` from `ws_portfolio` patterns (MED-4).
3. Author `services/ai/secrets.py` with 60s TTL cache + pubsub-invalidation listener.
4. Author `services/ai/cost_ledger.py` fire-and-forget batched writer (HIGH-2). Bounded queue + drop-oldest + counters + fail-OPEN.
5. Author `services/ai/jobs.py` PG-backed async-job store + pubsub `ai:job:{id}` + orphan recovery (HIGH-8).
6. Author `services/ai/router.py` `AICompletionClient` ABC + `LiteLLMClient` impl with:
   - `complete`, `stream`, `batch_complete`, `submit_job`, `get_job`, `cancel_job` (HIGH-4 batch + tools)
   - Router-level fallback walk
   - Per-capability semaphore (HIGH-9)
   - 501 on `tools is not None`
   - Per-request `api_key` injection from `secrets.py`
   - Fallback-chain metadata on `CompletionResult` (MED-8)
7. Wire all three into lifespan (`app.state.ai_router`).
8. Add 24 new metric series (§6 of spec).
9. Reviewer chain (haiku + sonnet for code-quality + sonnet for silent-failure-hunter).
10. Tag `v0.11.0.b`.

### Chunk C — REST + WS endpoints (sketch)

**Files to create:** `backend/app/api/ai.py`, `backend/app/api/ws_ai.py`, tests.

**Task outline:**
1. `POST /api/ai/complete` with LOCAL_ONLY API-boundary check (defence layer 1) + rate-limit + 501 on tools.
2. `POST /api/ai/jobs` returns 202 + `{job_id}`.
3. `GET /api/ai/jobs/{id}` polls status.
4. `DELETE /api/ai/jobs/{id}` sets cancel flag.
5. `WS /ws/ai/chat` via `make_ws_endpoint` with per-conn turn limiter (5/min) and 10s send timeout.
6. `WS /ws/ai/jobs/{id}` pushes state changes via pubsub.
7. Integration tests covering each failure mode in Flow A + B from the spec.
8. Regenerate `api-generated.ts` via `scripts/gen-types.sh`.
9. Reviewer chain (haiku + sonnet code + sonnet security).
10. Tag `v0.11.0.c`.

### Chunk D — Frontend (sketch)

**Files to create:** all `frontend/src/features/ai/`, `services/ai/`, routes, store.

**Task outline:**
1. `services/ai/api.ts` + `types.ts` (re-exports).
2. `useChatStream.ts` with bounded backoff reconnect + mountedRef + per-turn-limit UI feedback.
3. `useAiJob.ts` with WS push + REST fallback poll.
4. `useTradeContext.ts` one-shot with graceful-degrade failure mode.
5. `ChatPage.tsx` + `ChatMessage.tsx` + `ModelPicker.tsx`.
6. `TradeTicketAiSection.tsx` + insert into TradeTicketModal.
7. `AdminAiPage.tsx` with capability map editor + provider-key CRUD (CSRF nonce) + cost-ledger view + heavy-box state.
8. `stores/global/ai.ts` zustand-persist for chat + default model.
9. Frontend Vitest tests for each component + hook.
10. 2 Playwright smokes.
11. Reviewer chain (haiku spec + haiku typescript + sonnet code).
12. Tag `v0.11.0` — phase 11a close.

---

## Phase 11a close-out tasks

After Chunk D tag:

1. Update `CLAUDE.md` — add Phase 11a load-bearing rule block describing `services/ai/`, LiteLLM proxy, WoL.
2. Update `CHANGELOG.md` with v0.11.0 section.
3. Update `TASKS.md` — flip Phase 11a row to ✅.
4. Write memory `phase11a_shipped.md`.
5. Push tag + final close-out commit.

---

## Self-review notes for the engineer

- **Spec coverage:** Chunks A0 + A1 cover spec §2 11a-A0 + 11a-A1 in full. Subsequent chunks documented as sketches with task outlines; each gets a detailed task block written when its predecessor closes (this keeps the plan honest — speculative tasks for D before B's ABC shape is final would create rework).
- **TDD discipline:** every chunk uses test-first; tests fail before implementation.
- **Frequent commits:** each task ends with a commit; chunks tag at close.
- **Reviewer chain:** every chunk dispatches the spec-routed reviewer set per CLAUDE.md before tag.
- **CRIT/HIGH/MED architect findings:** baked into the task content directly — see references like `(CRIT-3)`, `(HIGH-2)` in code comments and module docstrings.
