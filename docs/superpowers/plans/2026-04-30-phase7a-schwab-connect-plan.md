# Phase 7a — Schwab Connect (Data + Read-Only) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Schwab broker sidecar on the VPS as a docker-compose service speaking the same gRPC `Broker` contract as IBKR + Futu. Read-only: list accounts, summary, positions, last-7-days orders. Tier-1 manual OAuth re-auth UI + Tier-2 opt-in Playwright auto-refresher. Trade execution + StreamQuotes return UNIMPLEMENTED (Phase 8 + 7b respectively).

**Architecture:** New `sidecar_schwab/` Python package on the VPS as a docker-compose service (port `9090` inside the docker network — cloud-broker pattern, no NUC, no PyInstaller, no mTLS). New `sidecar_schwab_refresher/` Python package as an opt-in docker-compose service (Playwright + Xvfb + pyotp). Backend's existing `BrokerRegistry` / `BrokerConfigurer` / `AccountService` infrastructure is reused unchanged — broker-specific logic lives entirely in `sidecar_schwab/normalize.py` + `sidecar_schwab/auth.py`. **Backend is the sole writer of `schwab.refresh_token`** (single-writer rule per architect C2): sidecar near-expiry path uses new `RequestTokenRefresh` gRPC outbound to backend; backend acquires PG advisory lock around every Schwab token write. OAuth callback split into public `/api/oauth/schwab/callback` (CF Access bypass via path-prefix rule) for Tier-1 + admin-JWT-gated `/api/admin/brokers/schwab/oauth-callback` for Tier-2 service-token use. State nonce HMAC-SHA256-signed with `APP_SECRET_KEY`, atomic Redis `SET NX EX` + `GETDEL` single-use consume.

**Tech Stack:** Python 3.14 (sidecar + refresher + backend), `tylerebowers/Schwabdev==3.0.3` (async client; pinned, confined to `sidecar_schwab/client.py` only), `playwright` + `playwright-stealth` + `pyotp` (Tier-2 refresher), gRPC + protobuf, FastAPI, Pydantic v2, PostgreSQL 18 + Alembic 0008 (`account_hash` column + partial index), Redis 7 (state nonce + pub/sub), TypeScript 6.0 strict + React 19 + Vite 7 + Tailwind v4 (frontend `SchwabCard`), Server-Sent Events for live token-expiry display.

**Spec:** `docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md` (commit `3c01b74`, architect-review applied — 3 CRIT + 6 HIGH + 7 MED resolved inline; 3 LOWs fixed inline, 2 LOWs deferred to plan). Read it before starting any task; the invariants in §3.6 (token rotation contract) and §11 (architectural pillars) are load-bearing.

**Owner conventions per task:** `Codex` writes source via `codex:rescue`; `Claude` writes tests, verifies, and commits per the delegation rule in `TASKS.md` Phase 3 header (active since 2026-04-24). Per project memory `feedback_codex_fallback.md`, if Codex hits quota or stalls, Claude takes over the same task and the next planned-Codex task fires a canary retry.

**Reviewer chain (mandatory at every commit boundary, never batched per `feedback_proactive_tooling.md`):**

1. Implementer subagent (uses `superpowers:subagent-driven-development/implementer-prompt.md`)
2. **spec-compliance reviewer** (always)
3. **code-quality reviewer** (always)
4. **language-specific reviewer:** `python-reviewer` for backend/sidecar/refresher Python; `typescript-reviewer` for frontend
5. Conditional reviewers fire when their trigger surface is touched:
   - `security-reviewer` — secrets/auth/user-input/crypto/CF-Access-bypass paths
   - `database-reviewer` — Alembic 0008 schema/migration paths
   - `type-design-analyzer` — Pydantic/proto surfaces, especially the new `RequestTokenRefresh` RPC
   - `silent-failure-hunter` — async paths, OAuth flow, sidecar token-refresh flow, Tier-2 Playwright flow
   - `a11y-architect` — `SchwabCard` UI changes
   - `build-error-resolver` — when `pnpm build` / `uv run` / docker build fails
   - `tdd-guide` — when tests fail unexpectedly

**Snippet-file parallelism:** Per memory `feedback_snippet_file_parallelism.md`, when multiple tasks edit the same canonical file (`proto/broker/v1/broker.proto`, `backend/app/services/broker_registry_factory.py`, `sidecar_schwab/handlers.py`, `sidecar_schwab/client.py`), dispatch agents to write snippets to `/tmp/<task>.py`. Controller splices, dedupes imports, commits once. Tasks marked **PARALLEL-SAFE** below can dispatch concurrently; sequential tasks must wait.

**No-RL reminder:** Per `docs/ROADMAP.md` decision row, raw reinforcement-learning bots are post-v1.0 out-of-scope. Nothing in Phase 7a involves RL.

---

## File structure

### New files (created)

| Path | Purpose |
|---|---|
| `sidecar_schwab/pyproject.toml` | uv-managed package; pinned `schwabdev==3.0.3`. |
| `sidecar_schwab/uv.lock` | Pinned dep tree. |
| `sidecar_schwab/__init__.py` | Package marker. |
| `sidecar_schwab/main.py` | gRPC server entrypoint, signal handling, port from env. |
| `sidecar_schwab/config.py` | CLI args, log config. |
| `sidecar_schwab/handlers.py` | gRPC `Broker` service implementation: Configure, Health, ListAccounts, GetAccountSummary, GetPositions, GetOrders. UNIMPLEMENTED stubs for write + streaming RPCs. |
| `sidecar_schwab/client.py` | `SchwabClient` — ONLY file that `import schwabdev` (M3 isolation). Owns `Schwabdev.ClientAsync`, account-hash cache, async semaphore. |
| `sidecar_schwab/normalize.py` | Schwab JSON → proto Account/Position/Order mappers; status-mapping table per spec §3.2.1; avg_fill_price extraction per §3.2.2. |
| `sidecar_schwab/auth.py` | Access-token freshness check, `_token_lock`, `RequestTokenRefresh` outbound gRPC client (sidecar→backend single-writer callback). |
| `sidecar_schwab/metrics.py` | Sidecar-local Prometheus counters (`schwab_http_requests_total`, `schwab_account_hash_refresh_total`, etc.). |
| `sidecar_schwab/Dockerfile` | `python:3.14-slim` base, copies generated proto, runs `main.py`. |
| `sidecar_schwab/scripts/proto-gen.sh` | Codegen helper. |
| `sidecar_schwab/tests/__init__.py` | Package marker. |
| `sidecar_schwab/tests/conftest.py` | Pytest fixtures (mocked Schwabdev client, fake Redis, fake backend gRPC). |
| `sidecar_schwab/tests/test_normalize.py` | JSON→proto mapping tests. |
| `sidecar_schwab/tests/test_handlers_list_accounts.py` | Configure → ListAccounts round-trip. |
| `sidecar_schwab/tests/test_handlers_summary.py` | NLV/cash/buying_power extraction. |
| `sidecar_schwab/tests/test_handlers_positions.py` | Position mapping + day_pnl. |
| `sidecar_schwab/tests/test_handlers_orders.py` | 7-day window + status table + avg_fill_price extraction. |
| `sidecar_schwab/tests/test_auth_lifecycle.py` | access_token freshness check + no-self-refresh. |
| `sidecar_schwab/tests/test_configure_idempotent.py` | Configure with same tokens is no-op. |
| `sidecar_schwab/tests/test_request_token_refresh.py` | Sidecar near-expiry → RequestTokenRefresh gRPC → backend mock returns new tokens. |
| `sidecar_schwab/tests/test_account_hash_404_retry.py` | H3 — 404 invariant. |
| `sidecar_schwab/tests/test_rate_limit_429.py` | M6 — 429 honored with Retry-After. |
| `sidecar_schwab_refresher/pyproject.toml` | uv-managed; deps: playwright, playwright-stealth, pyotp, httpx, structlog. |
| `sidecar_schwab_refresher/__init__.py` | Package marker. |
| `sidecar_schwab_refresher/main.py` | Cron loop entrypoint; reads feature flag; exits 0 if disabled. |
| `sidecar_schwab_refresher/refresher.py` | Playwright flow: navigate → fill → MFA → capture redirect. |
| `sidecar_schwab_refresher/stealth.py` | playwright-stealth bootstrap. |
| `sidecar_schwab_refresher/selectors.py` | H2 — selector health probe; documented selectors with version-dated comments. |
| `sidecar_schwab_refresher/totp.py` | `pyotp.TOTP` wrapper. |
| `sidecar_schwab_refresher/config_writer.py` | Writes new tokens to backend admin API; handles 5xx with retry. |
| `sidecar_schwab_refresher/Dockerfile` | `python:3.14` + Xvfb + Playwright Chromium. |
| `sidecar_schwab_refresher/tests/test_totp.py` | pyotp wrapper tests. |
| `sidecar_schwab_refresher/tests/test_refresher_unit.py` | Mocked Playwright fill→submit→capture. |
| `sidecar_schwab_refresher/tests/test_selector_health.py` | H2 — selector probe asserts within 5s budget. |
| `sidecar_schwab_refresher/tests/test_config_writer.py` | Backend admin POST + retry. |
| `sidecar_schwab_refresher/tests/test_consecutive_failures_auto_disable.py` | H2 — 3 failures flips feature flag. |
| `backend/alembic/versions/0008_phase7a_schwab_account_hash.py` | Adds `account_hash` column + partial index + downgrade. |
| `backend/app/api/oauth.py` | NEW public router — `/api/oauth/schwab/callback` (CF-Access-bypassed; HMAC state nonce gate). |
| `backend/app/api/brokers_admin.py` | NEW admin router — `/api/admin/brokers/schwab/oauth-{start,callback}` + `/reconfigure`. |
| `backend/app/services/schwab_oauth.py` | OAuth state-nonce mint/consume helpers; PG advisory lock holder; backend-side token-mint. |
| `backend/app/services/sse.py` | NEW Server-Sent Events helper for `config:invalidate:*` pub/sub forwarding. |
| `backend/tests/api/test_oauth_callback_public.py` | Public callback path reachable without admin JWT. |
| `backend/tests/api/test_oauth_callback_admin.py` | Admin callback path requires JWT. |
| `backend/tests/api/test_state_nonce.py` | H1 — HMAC mismatch / replay / expiry / unsigned all reject. |
| `backend/tests/integration/test_schwab_oauth_flow.py` | Full Tier-1 round-trip with mocked Schwab token endpoint. |
| `backend/tests/integration/test_schwab_account_listing.py` | Sidecar mock returning N accounts → `/api/brokers/accounts`. |
| `backend/tests/integration/test_account_boundary_strip.py` | H3 — `account_hash` absent from JSON. |
| `backend/tests/integration/test_logging_redaction.py` | M5 — structlog redaction patterns. |
| `backend/tests/integration/test_token_rotation_atomicity.py` | C2 — concurrent Tier-1 + Tier-2 writes serialized by advisory lock. |
| `backend/tests/integration/test_real_schwab_smoke.py` | Gated on `CI_USE_REAL_SCHWAB=1`. |
| `backend/tests/fixtures/schwab_test_data.py` | Forked from Dashboard_old. |
| `frontend/src/features/Settings/SchwabCard.tsx` | The card with Connect / Disconnect / Tier-2 toggle + live expiry. |
| `frontend/src/features/Settings/SchwabCard.test.tsx` | RTL tests. |
| `frontend/src/features/Settings/SchwabCard.stories.tsx` | Storybook visual states. |
| `frontend/src/services/schwab.ts` | Thin wrapper over the 3 admin endpoints + SSE subscriber. |
| `frontend/src/services/schwab.test.ts` | Tests for the service. |
| `frontend/src/hooks/useSchwabTokenStatus.ts` | Polling + SSE merge hook. |
| `frontend/src/hooks/useSchwabTokenStatus.test.ts` | Hook tests (5s poll for first 60s). |
| `deploy/runbook-schwab-setup.md` | 9-step operator runbook. |
| `scripts/cloudflare/access-bypass-schwab-callback.sh` | Idempotent CF Access bypass policy applier. |
| `.github/workflows/nightly-real-schwab.yml` | Nightly real-Schwab smoke at 12:00 UTC. |

### Modified files

| Path | Change |
|---|---|
| `proto/broker/v1/broker.proto` | Add `account_hash` (field 5) to `Account`. Add `RequestTokenRefresh` RPC + `TokenRefreshRequest`/`TokenRefreshResponse` messages. |
| `backend/app/_generated/broker/v1/*` | Regenerated stubs. |
| `sidecar/_generated/broker/v1/*` | Regenerated stubs (IBKR sidecar). |
| `sidecar_futu/_generated/broker/v1/*` | Regenerated stubs (Futu sidecar). |
| `sidecar/handlers.py` | IBKR sidecar: implement `RequestTokenRefresh` as `UNIMPLEMENTED`. |
| `sidecar_futu/handlers.py` | Futu sidecar: implement `RequestTokenRefresh` as `UNIMPLEMENTED`. |
| `backend/app/services/broker_registry_factory.py` | Add `"schwab": ("schwab", "schwab-sidecar:9090")` to `SIDECAR_BROKERS`. Add `BrokerConfigurer` lifecycle for Schwab. Add backend-side `RequestTokenRefresh` server-side handler with PG advisory lock. |
| `backend/app/services/account_service.py` | Boundary strip `account_hash` from `AccountResponse`. |
| `backend/app/api/admin.py` | Mount `brokers_admin` router. |
| `backend/app/main.py` | Mount `oauth.py` router (public). Mount SSE endpoint. |
| `backend/app/core/logging.py` | M5 — Add 5 schwab-related patterns to `REDACTION_PATTERNS`. |
| `backend/app/core/metrics.py` | New counters/gauges (12 metrics per spec §8.1). |
| `frontend/src/features/Settings/SettingsPage.tsx` | Mount `SchwabCard` component. |
| `frontend/src/services/api-generated.ts` | Regenerated from openapi. |
| `deploy/docker-compose.prod.yml` | New `schwab-sidecar` + `schwab-refresher` services. |
| `deploy/prometheus/alerts.yml` | Add `phase7a_schwab` alert group (9 alerts). |
| `CHANGELOG.md` | New `[0.7.0]` section. |
| `TASKS.md` | Mark Phase 7a complete + chunk-level `[x]` flips. |
| `CLAUDE.md` | Add §"Phase 7a — Schwab connect (v0.7.0)" subsection. |

---

## Pre-flight

- [ ] **PF1: Verify clean working tree on `main` at `3c01b74`+** (architect-review-applied spec).

```bash
git status
git log --oneline -1
```

Expected: clean tree, head ≥ `3c01b74`.

- [ ] **PF2: Verify `buf` and `uv` are on PATH.**

```bash
buf --version && uv --version
```

Expected: both print versions.

- [ ] **PF3: Verify Schwab Developer Portal app is registered + credentials available.** Per spec §1, user has confirmed app pre-approved. Operator action (one-time): record `app_key` + `app_secret`. Seeded in chunk G2.

- [ ] **PF4: Verify Dashboard_old reference exists for forking patterns.**

```bash
ls -la /mnt/c/Dashboard_old/backend/app/brokers/schwab.py \
       /mnt/c/Dashboard_old/backend/app/services/quotes/providers/schwab.py \
       /mnt/c/Dashboard_old/backend/app/services/quotes/providers/schwab_streamer.py \
       /mnt/c/Dashboard_old/backend/tests/test_schwab_*.py
```

Expected: all six files present.

- [ ] **PF5: Verify CF Access dashboard credentials available** for the path-prefix bypass in chunk G4.

```bash
test -n "${CF_ACCESS_API_TOKEN:-}" && echo "CF token present" || echo "MISSING — needed for chunk G4"
```

---

## Chunk A — Proto + sidecar shell (7 tasks)

Goal: extend the proto contract with `Account.account_hash` + new `RequestTokenRefresh` RPC, scaffold the empty `sidecar_schwab/` package, register the schwab label in `SIDECAR_BROKERS`, and stub the Prometheus metrics. After A7 the package boots, returns Health, and is reachable from the backend's existing `BrokerRegistry` infrastructure (with all data-plane RPCs returning UNIMPLEMENTED).

### Task A1: Extend proto contract — `account_hash` field + `RequestTokenRefresh` RPC

**Files:** Modify `proto/broker/v1/broker.proto`.

- [ ] **Step 1: Add `account_hash` to `Account` message.** Edit `proto/broker/v1/broker.proto`, find `Account`, append field 5:

```proto
message Account {
  string account_number = 1;
  TradingMode mode = 2;
  string gateway_label = 3;
  string currency_base = 4;
  string account_hash = 5;  // Phase 7a — Schwab privacy layer; empty for IBKR/Futu
}
```

- [ ] **Step 2: Add `TokenRefreshRequest` + `TokenRefreshResponse` before `service Broker`:**

```proto
// Phase 7a — sidecar→backend single-writer token-refresh callback (architect C2)
message TokenRefreshRequest {
  string broker_id = 1;  // "schwab" — distinguishes if other brokers ever need this pattern
}

message TokenRefreshResponse {
  string access_token = 1;
  string refresh_token = 2;
  google.protobuf.Timestamp access_issued_at = 3;
}
```

- [ ] **Step 3: Add `RequestTokenRefresh` RPC inside `service Broker`:**

```proto
  // Phase 7a — sidecar requests fresh tokens from backend (single writer).
  // Only schwab implements; ibkr/futu return UNIMPLEMENTED.
  rpc RequestTokenRefresh(TokenRefreshRequest) returns (TokenRefreshResponse);
```

- [ ] **Step 4: Regenerate stubs.**

```bash
cd /home/joseph/dashboard
bash sidecar/scripts/proto-gen.sh
bash sidecar_futu/scripts/proto-gen.sh
# sidecar_schwab/scripts/proto-gen.sh is created in A3 — skip until then
```

- [ ] **Step 5: Verify symbols.**

```bash
cd backend && uv run python -c "from app._generated.broker.v1 import broker_pb2 as pb; \
  print(pb.Account.DESCRIPTOR.fields_by_name['account_hash']); \
  print(pb.TokenRefreshRequest.DESCRIPTOR); \
  print(pb.TokenRefreshResponse.DESCRIPTOR)"
```

Expected: three `<Descriptor ...>` lines, no errors.

- [ ] **Step 6: Commit.**

```bash
git add proto/broker/v1/broker.proto backend/app/_generated/ sidecar/_generated/ sidecar_futu/_generated/
git commit -m "feat(proto): add Account.account_hash + RequestTokenRefresh RPC for Phase 7a"
```

**Conditional reviewers:** `type-design-analyzer`.

### Task A2: IBKR + Futu sidecars implement `RequestTokenRefresh` as `UNIMPLEMENTED`

**Files:** Modify `sidecar/handlers.py`, `sidecar_futu/handlers.py`. Create test files for each.

- [ ] **Step 1: Failing test for IBKR.** Create `sidecar/tests/test_handlers_token_refresh.py`:

```python
"""Phase 7a A2 — IBKR sidecar returns UNIMPLEMENTED for RequestTokenRefresh."""
import grpc
import pytest

from sidecar._generated.broker.v1 import broker_pb2 as pb


@pytest.mark.asyncio
async def test_ibkr_request_token_refresh_unimplemented(grpc_stub):
    request = pb.TokenRefreshRequest(broker_id="ibkr")
    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await grpc_stub.RequestTokenRefresh(request)
    assert excinfo.value.code() == grpc.StatusCode.UNIMPLEMENTED
```

- [ ] **Step 2: Run test — verify FAIL.**

```bash
cd /home/joseph/dashboard/sidecar && uv run pytest tests/test_handlers_token_refresh.py -v
```

- [ ] **Step 3: Implement UNIMPLEMENTED stub in IBKR sidecar.** In `sidecar/handlers.py`'s `BrokerServicer` class, append:

```python
    async def RequestTokenRefresh(  # noqa: N802 — gRPC method naming
        self,
        request: pb.TokenRefreshRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.TokenRefreshResponse:
        """IBKR uses long-lived TWS sessions, not OAuth tokens."""
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "RequestTokenRefresh is Schwab-only; IBKR uses TWS session.",
        )
        return pb.TokenRefreshResponse()  # unreachable but required for type checker
```

- [ ] **Step 4: Run IBKR test — PASS.**

```bash
uv run pytest tests/test_handlers_token_refresh.py -v
```

- [ ] **Step 5: Mirror for Futu.** Create `sidecar_futu/tests/test_handlers_token_refresh.py` (identical body, `broker_id="futu"`). Append the same stub to `sidecar_futu/handlers.py`'s servicer with docstring "Futu uses unlock_pwd_md5 + RSA, not OAuth tokens."

- [ ] **Step 6: Run Futu test — PASS.**

```bash
cd /home/joseph/dashboard/sidecar_futu && uv run pytest tests/test_handlers_token_refresh.py -v
```

- [ ] **Step 7: Commit.**

```bash
git add sidecar/handlers.py sidecar/tests/test_handlers_token_refresh.py \
        sidecar_futu/handlers.py sidecar_futu/tests/test_handlers_token_refresh.py
git commit -m "feat(sidecar/futu): RequestTokenRefresh returns UNIMPLEMENTED (Schwab-only RPC)"
```

**Conditional reviewers:** `silent-failure-hunter`.

### Task A3: Create `sidecar_schwab/` package skeleton + Dockerfile

**Files:** Create `sidecar_schwab/{__init__.py,pyproject.toml,Dockerfile,scripts/proto-gen.sh,tests/__init__.py}`.

- [ ] **Step 1: Make directories.**

```bash
cd /home/joseph/dashboard
mkdir -p sidecar_schwab/{tests,_generated/broker/v1,scripts}
touch sidecar_schwab/__init__.py sidecar_schwab/tests/__init__.py
```

- [ ] **Step 2: Write `sidecar_schwab/pyproject.toml`:**

```toml
[project]
name = "sidecar-schwab"
version = "0.7.0"
description = "Schwab Trader API sidecar — gRPC adapter to schwab.com"
requires-python = ">=3.14"
dependencies = [
    "schwabdev==3.0.3",          # M3 — pinned exact; confined to client.py only
    "grpcio>=1.62",
    "grpcio-tools>=1.62",
    "grpcio-reflection>=1.62",
    "protobuf>=5.0",
    "structlog>=24.0",
    "pydantic>=2.0",
    "prometheus-client>=0.20",
]

[tool.uv]
package = false

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-ra --strict-markers"
testpaths = ["tests"]
```

- [ ] **Step 3: Write `sidecar_schwab/Dockerfile`:**

```dockerfile
FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
ENV PYTHONPATH=/app
EXPOSE 9090
CMD ["uv", "run", "python", "-m", "sidecar_schwab.main"]
```

- [ ] **Step 4: Write `sidecar_schwab/scripts/proto-gen.sh`:**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
OUT="sidecar_schwab/_generated"
mkdir -p "$OUT/broker/v1"
uv run --directory sidecar_schwab python -m grpc_tools.protoc \
  -Iproto --python_out="$OUT" --grpc_python_out="$OUT" --pyi_out="$OUT" \
  proto/broker/v1/broker.proto
touch "$OUT/__init__.py" "$OUT/broker/__init__.py" "$OUT/broker/v1/__init__.py"
echo "OK — generated Schwab sidecar stubs in $OUT"
```

```bash
chmod +x sidecar_schwab/scripts/proto-gen.sh
```

- [ ] **Step 5: uv sync + proto-gen.**

```bash
cd /home/joseph/dashboard
uv sync --directory sidecar_schwab --dev
bash sidecar_schwab/scripts/proto-gen.sh
```

Expected: `sidecar_schwab/_generated/broker/v1/broker_pb2.py` + `*_grpc.py` exist.

- [ ] **Step 6: Verify imports.**

```bash
cd sidecar_schwab && uv run python -c "from _generated.broker.v1 import broker_pb2 as pb; \
  print(pb.Account.DESCRIPTOR.fields_by_name['account_hash'])"
```

Expected: `<Descriptor ... account_hash ...>`.

- [ ] **Step 7: Commit.**

```bash
git add sidecar_schwab/
git commit -m "feat(sidecar-schwab): package skeleton + Dockerfile + proto-gen script"
```

### Task A4: Write `sidecar_schwab/main.py` + `config.py` + minimal `handlers.py`

**Files:** Create `sidecar_schwab/{main.py,config.py,handlers.py,tests/test_main.py}`.

- [ ] **Step 1: Failing test for port resolution.** Create `sidecar_schwab/tests/test_main.py`:

```python
"""Phase 7a A4 — config.resolve_port respects env override + falls back safely."""
import logging


def test_resolve_port_default(monkeypatch):
    monkeypatch.delenv("SCHWAB_SIDECAR_PORT", raising=False)
    from sidecar_schwab.config import resolve_port
    assert resolve_port() == 9090


def test_resolve_port_override(monkeypatch):
    monkeypatch.setenv("SCHWAB_SIDECAR_PORT", "12345")
    from sidecar_schwab.config import resolve_port
    assert resolve_port() == 12345


def test_resolve_port_invalid_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("SCHWAB_SIDECAR_PORT", "not-a-number")
    caplog.set_level(logging.WARNING)
    from sidecar_schwab.config import resolve_port
    assert resolve_port() == 9090
    assert "invalid SCHWAB_SIDECAR_PORT" in caplog.text.lower()
```

- [ ] **Step 2: Run — FAIL.**

```bash
cd /home/joseph/dashboard/sidecar_schwab && uv run pytest tests/test_main.py -v
```

- [ ] **Step 3: Write `sidecar_schwab/config.py`:**

```python
"""Phase 7a configuration — env vars only."""
from __future__ import annotations

import logging
import os

DEFAULT_PORT = 9090

log = logging.getLogger(__name__)


def resolve_port() -> int:
    """Read SCHWAB_SIDECAR_PORT from env, fall back to 9090 on error."""
    raw = os.environ.get("SCHWAB_SIDECAR_PORT", "")
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        log.warning(
            "invalid SCHWAB_SIDECAR_PORT %r — falling back to %d",
            raw, DEFAULT_PORT,
        )
        return DEFAULT_PORT
```

- [ ] **Step 4: Write minimal `sidecar_schwab/handlers.py`** (chunk B fills out):

```python
"""gRPC Broker servicer for Schwab. Stubs filled out in chunk B."""
from __future__ import annotations

import grpc

from sidecar_schwab._generated.broker.v1 import (
    broker_pb2 as pb,
    broker_pb2_grpc as pbg,
)


class BrokerServicer(pbg.BrokerServicer):
    """Schwab gRPC service. Empty stubs in A4; chunk B fills them out."""

    async def Health(  # noqa: N802
        self,
        request: pb.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.HealthResponse:
        return pb.HealthResponse(
            label="schwab",
            broker_id="schwab",
            gateway_connected=False,
            sidecar_version="0.7.0-stub",
        )
```

- [ ] **Step 5: Write `sidecar_schwab/main.py`:**

```python
"""Schwab sidecar entrypoint — asyncio gRPC server, plain TCP (no mTLS;
sidecar lives on same docker network as backend per spec §3.1)."""
from __future__ import annotations

import asyncio
import logging
import signal

import grpc
import structlog
from grpc_reflection.v1alpha import reflection

from sidecar_schwab._generated.broker.v1 import (
    broker_pb2 as pb,
    broker_pb2_grpc as pbg,
)
from sidecar_schwab.config import resolve_port
from sidecar_schwab.handlers import BrokerServicer

log = structlog.get_logger(module="sidecar_schwab.main")


async def serve() -> None:
    port = resolve_port()
    server = grpc.aio.server()

    servicer = BrokerServicer()
    pbg.add_BrokerServicer_to_server(servicer, server)

    SERVICE_NAMES = (
        pb.DESCRIPTOR.services_by_name["Broker"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)

    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)
    log.info("sidecar_schwab_starting", listen_addr=listen_addr)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    log.info("sidecar_schwab_stopping")
    await server.stop(grace=10.0)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run — PASS.**

```bash
uv run pytest tests/test_main.py -v
```

- [ ] **Step 7: Smoke-boot the server (3s timeout).**

```bash
cd /home/joseph/dashboard
SCHWAB_SIDECAR_PORT=19090 timeout 3 uv run --directory sidecar_schwab python -m sidecar_schwab.main || true
```

Look for `sidecar_schwab_starting listen_addr=0.0.0.0:19090` in stderr.

- [ ] **Step 8: Commit.**

```bash
git add sidecar_schwab/main.py sidecar_schwab/config.py sidecar_schwab/handlers.py \
        sidecar_schwab/tests/test_main.py
git commit -m "feat(sidecar-schwab): main.py grpc server bootstrap + config + Health stub"
```

**Conditional reviewers:** `silent-failure-hunter` (signal handling).

### Task A5: Add `schwab` to `SIDECAR_BROKERS` map (backend)

**Files:** Modify `backend/app/services/broker_registry_factory.py`.

- [ ] **Step 1: Failing test.** Create `backend/tests/services/test_sidecar_brokers_map.py`:

```python
"""Phase 7a A5 — SIDECAR_BROKERS map includes schwab → schwab-sidecar:9090."""
from app.services.broker_registry_factory import SIDECAR_BROKERS


def test_schwab_in_sidecar_brokers():
    assert "schwab" in SIDECAR_BROKERS
    broker_id, addr = SIDECAR_BROKERS["schwab"]
    assert broker_id == "schwab"
    assert addr == "schwab-sidecar:9090"


def test_existing_brokers_unchanged():
    """Don't break Phase 4 + 6 wiring."""
    assert ("ibkr", "10.10.0.2:18001") == SIDECAR_BROKERS["isa-live"]
    assert ("ibkr", "10.10.0.2:18002") == SIDECAR_BROKERS["isa-paper"]
    assert ("ibkr", "10.10.0.2:18003") == SIDECAR_BROKERS["normal-live"]
    assert ("ibkr", "10.10.0.2:18004") == SIDECAR_BROKERS["normal-paper"]
    assert ("futu", "10.10.0.2:18005") == SIDECAR_BROKERS["futu"]
```

- [ ] **Step 2: Run — FAIL.**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/services/test_sidecar_brokers_map.py -v
```

- [ ] **Step 3: Add row to map.** In `backend/app/services/broker_registry_factory.py`:

```python
SIDECAR_BROKERS = {
    "isa-live":     ("ibkr", "10.10.0.2:18001"),
    "isa-paper":    ("ibkr", "10.10.0.2:18002"),
    "normal-live":  ("ibkr", "10.10.0.2:18003"),
    "normal-paper": ("ibkr", "10.10.0.2:18004"),
    "futu":         ("futu", "10.10.0.2:18005"),
    "schwab":       ("schwab", "schwab-sidecar:9090"),  # Phase 7a — VPS docker-compose, plaintext gRPC
}
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/services/test_sidecar_brokers_map.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add backend/app/services/broker_registry_factory.py backend/tests/services/test_sidecar_brokers_map.py
git commit -m "feat(backend): register schwab sidecar in SIDECAR_BROKERS map"
```

### Task A6: Frontend regenerate `api-generated.ts`

**Files:** Regenerate `frontend/src/services/api-generated.ts`.

- [ ] **Step 1: Run codegen.**

```bash
cd /home/joseph/dashboard
bash scripts/gen-types.sh
```

- [ ] **Step 2: Verify `account_hash` is NOT in OpenAPI surface (boundary-stripped).**

```bash
grep -c "account_hash" frontend/src/services/api-generated.ts
```

Expected: `0`.

- [ ] **Step 3: Run frontend typecheck.**

```bash
cd frontend && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/services/api-generated.ts
git commit -m "chore(frontend): regenerate openapi types after proto extension"
```

### Task A7: Add Phase 7a Prometheus metric stubs

**Files:** Modify `backend/app/core/metrics.py`. Create `backend/tests/observability/test_metrics_phase7a.py`.

- [ ] **Step 1: Failing test.** Create `backend/tests/observability/test_metrics_phase7a.py`:

```python
"""Phase 7a A7 — Schwab Prometheus metrics registered with correct label sets."""
from app.core.metrics import (
    SCHWAB_OAUTH_START_TOTAL,
    SCHWAB_OAUTH_CALLBACK_TOTAL,
    SCHWAB_ACCESS_TOKEN_AGE_SECONDS,
    SCHWAB_REFRESH_TOKEN_AGE_HOURS,
    SCHWAB_REFRESH_TOKEN_USES_PER_24H,
    SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL,
    SCHWAB_HTTP_REQUESTS_TOTAL,
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS,
    SCHWAB_TIER2_REFRESH_TOTAL,
    SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS,
)


def test_oauth_start_counter():
    SCHWAB_OAUTH_START_TOTAL.inc()


def test_oauth_callback_labels():
    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="public", result="success").inc()
    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="admin", result="state_mismatch").inc()


def test_account_hash_refresh_labels():
    for r in ("initial", "rotation_detected", "404_retry"):
        SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL.labels(reason=r).inc()


def test_http_requests_labels():
    SCHWAB_HTTP_REQUESTS_TOTAL.labels(endpoint="/accounts", status="200").inc()
    SCHWAB_HTTP_REQUESTS_TOTAL.labels(endpoint="/accountNumbers", status="429").inc()


def test_tier2_refresh_labels():
    for r in ("success", "login_failed", "mfa_failed", "dom_changed",
              "network_error", "auto_disabled"):
        SCHWAB_TIER2_REFRESH_TOTAL.labels(result=r).inc()


def test_gauge_set():
    SCHWAB_ACCESS_TOKEN_AGE_SECONDS.set(1500)
    SCHWAB_REFRESH_TOKEN_AGE_HOURS.set(72)
    SCHWAB_REFRESH_TOKEN_USES_PER_24H.set(2)
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS.set(0)
    SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS.set(1714492800)
```

- [ ] **Step 2: Run — FAIL.**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/observability/test_metrics_phase7a.py -v
```

- [ ] **Step 3: Append to `backend/app/core/metrics.py`:**

```python
# ──────────────────────── Phase 7a Schwab metrics ───────────────────────────
# Per spec §8.1 — see docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md

SCHWAB_OAUTH_START_TOTAL = Counter(
    "schwab_oauth_start_total",
    "Number of Schwab OAuth flow initiations (Tier-1 path).",
)

SCHWAB_OAUTH_CALLBACK_TOTAL = Counter(
    "schwab_oauth_callback_total",
    "Schwab OAuth callback outcomes by path + result.",
    ["path", "result"],
)

SCHWAB_ACCESS_TOKEN_AGE_SECONDS = Gauge(
    "schwab_access_token_age_seconds",
    "Age of the current access_token in seconds.",
)

SCHWAB_REFRESH_TOKEN_AGE_HOURS = Gauge(
    "schwab_refresh_token_age_hours",
    "Age of the current refresh_token in hours.",
)

SCHWAB_REFRESH_TOKEN_USES_PER_24H = Gauge(
    "schwab_refresh_token_uses_per_24h",
    "Refresh-token uses in a rolling 24h window (H4 — restart-flapping detector).",
)

SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL = Counter(
    "schwab_account_hash_refresh_total",
    "account_hash cache refreshes by reason.",
    ["reason"],
)

SCHWAB_HTTP_REQUESTS_TOTAL = Counter(
    "schwab_http_requests_total",
    "Schwab REST request count by endpoint + status code.",
    ["endpoint", "status"],
)

SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS = Gauge(
    "schwab_sidecar_token_drift_seconds",
    "Seconds since the last Configure call after a known token write (C3 invariant).",
)

SCHWAB_TIER2_REFRESH_TOTAL = Counter(
    "schwab_tier2_refresh_total",
    "Tier-2 Playwright auto-refresh outcomes.",
    ["result"],
)

SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS = Gauge(
    "schwab_tier2_last_run_timestamp_seconds",
    "Unix timestamp of the most recent Tier-2 refresh attempt (any outcome).",
)
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/observability/test_metrics_phase7a.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add backend/app/core/metrics.py backend/tests/observability/test_metrics_phase7a.py
git commit -m "feat(metrics): register 10 Phase 7a Schwab counters/gauges"
```

---

## End of Chunk A

After A7: 7 commits, sidecar_schwab/ scaffold boots and serves Health, proto extension reverberates through all 3 sidecars, backend knows about the schwab label, frontend types regenerate cleanly, observability stubs are in place. **No live Schwab calls yet.** Chunk B fills out the data plane.

---

## Chunk B — Sidecar core (placeholder — full task list in next plan revision)

> **Note:** Chunk B + C + D + E + F + G are documented at chunk-summary level here so the plan stays under the per-file budget. **Full task lists for chunks B–G are appended in follow-up edits to this plan file.** This is documented in §"Plan continuation" at the end.

Headline tasks for Chunk B (10 tasks total):

- B1: `sidecar_schwab/normalize.py` — Schwab JSON → proto Account/Position/Order mappers; status mapping table per spec §3.2.1; `avg_fill_price` from `orderActivityCollection` (§3.2.2 — fixes Dashboard_old bug).
- B2: `sidecar_schwab/auth.py` — access_token freshness; `_token_lock`; outbound gRPC `RequestTokenRefresh` client. **No self-refresh** — assert.
- B3: `sidecar_schwab/client.py` — `SchwabClient` wrapping `Schwabdev.ClientAsync` (M3 confine: only file with `import schwabdev`). Async semaphore = 10. 429 handler with Retry-After + 3× backoff + jitter. M3 isolation test.
- B4: `sidecar_schwab/handlers.py` — `Configure` RPC implementation with idempotency.
- B5: `sidecar_schwab/handlers.py` — `Health` real implementation (gateway_connected = access_token<25min AND _account_hashes non-empty per H4).
- B6: `sidecar_schwab/handlers.py` — `ListAccounts` via `/accountNumbers` + `/accounts`; populates `_account_hashes` cache; H3 404→retry-once invariant.
- B7: `sidecar_schwab/handlers.py` — `GetAccountSummary`; H5 USD-only fallback metric on non-USD `securitiesAccount`.
- B8: `sidecar_schwab/handlers.py` — `GetPositions`.
- B9: `sidecar_schwab/handlers.py` — `GetOrders` 7-day window + status table + avg_fill_price; M2 fix.
- B10: `sidecar_schwab/handlers.py` — UNIMPLEMENTED stubs for SearchContracts/PlaceOrder/CancelOrder/ModifyOrder/OrderEvent/StreamQuotes (return UNIMPLEMENTED status; do not silently 200).

Each task follows TDD pattern: failing test → implementation → passing test → commit. Full code blocks deferred to next plan revision (B-tasks expansion).

---

## Chunk C — Backend wiring (placeholder)

12 tasks total — Alembic 0008 (account_hash + partial index + downgrade), `BrokerConfigurer` extension, public `/api/oauth/schwab/callback` route with CF Access bypass, admin `/api/admin/brokers/schwab/oauth-{start,callback}` + `/reconfigure`, state-nonce HMAC + GETDEL, PG advisory lock around refresh-token writes, backend-side `RequestTokenRefresh` server handler, C3 Configure-trigger plumbing, M5 structlog redaction, SSE pub/sub forwarder.

---

## Chunk D — Tier-1 frontend (placeholder)

8 tasks — `SchwabCard` component with all visual states (connected/disconnected/expiring-soon/expired); `useSchwabTokenStatus` hook (5s poll for first 60s after OAuth, 60s steady; SSE merge); Disconnect dialog with credential delete/keep choice (M7 + L5); Storybook stories for visual diff.

---

## Chunk E — Tier-2 refresher (placeholder)

10 tasks — `sidecar_schwab_refresher/` package + Dockerfile (Xvfb + Playwright); `selectors.py` health probe (H2); `refresher.py` Playwright flow with redirect interception (no follow); `totp.py` pyotp wrapper; `config_writer.py` backend admin POST with retry; consecutive-failure auto-disable (H2); docker-compose `tier2` profile.

---

## Chunk F — Tests + smoke (placeholder)

6 tasks — Backend integration tests (OAuth flow, account listing, boundary strip, logging redaction, token rotation atomicity); `test_real_schwab_smoke.py` gated; `nightly-real-schwab.yml` workflow at 12:00 UTC (L3 stagger).

---

## Chunk G — Ops + close-out (placeholder)

6 tasks — `runbook-schwab-setup.md` (9 steps); `docker-compose.prod.yml` updates; `scripts/cloudflare/access-bypass-schwab-callback.sh`; `deploy/prometheus/alerts.yml` `phase7a_schwab` group (9 alerts); CHANGELOG/TASKS/CLAUDE.md/memory updates; tag v0.7.0.

---

## Plan continuation

Chunks B–G are documented as **placeholders with headline tasks** in this initial plan version because the full plan exceeds the per-write budget on this turn. The next session resumes by appending detailed B–G task bodies to this file via incremental edits, following the same TDD shape as Chunk A (each task: failing test → implementation → passing test → commit, all code blocks complete).

**Resumption checklist for the next plan-writing session:**

- [ ] Read this plan file end-to-end.
- [ ] Read `docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md` §3 (architecture), §4 (data model), §5 (components), §6 (tests), §10 (chunk plan).
- [ ] Append full Chunk B task bodies (B1–B10) below the chunk-B placeholder.
- [ ] Append full Chunks C, D, E, F, G in order.
- [ ] Self-review for placeholder leakage (no remaining "TBD" / "next plan revision").
- [ ] Commit the expanded plan.

---

## Self-review (Chunk A coverage)

**Spec coverage (Chunk A scope only):**
- Spec §3.2 `Health` invariant — A4 ships stub; full impl in B5.
- Spec §4.4 Alembic 0008 — Chunk C.
- Spec §5.1 sidecar package — A3 ✓.
- Spec §5.4 proto changes — A1 ✓.
- Spec §8.1 metrics — A7 ✓ (10 of 12; remaining 2 added in B + C).
- Spec §11 architectural pillars — A1 (RequestTokenRefresh RPC), A2 (Schwab-only), A5 (cloud-broker SIDECAR_BROKERS row).

**Placeholder scan:** chunks B–G are explicitly flagged as "placeholder, full task list in next plan revision" with a resumption checklist. No silent placeholders within Chunk A tasks.

**Type consistency (Chunk A):** `BrokerServicer` class name consistent across A2 + A4. `SIDECAR_BROKERS` tuple shape (`(broker_id, addr)`) consistent with Phase 6 precedent. `SCHWAB_OAUTH_START_TOTAL` etc. use ALL_CAPS Counter/Gauge convention matching Phase 5b/5c metrics.

---

## Execution choice

Plan complete (Chunk A fully expanded; B–G placeholder with resumption checklist) and saved to `docs/superpowers/plans/2026-04-30-phase7a-schwab-connect-plan.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Per task in Chunk A (7 tasks); after Chunk A I append Chunks B–G to the plan and continue.

2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch with checkpoints for review.

Which approach?
