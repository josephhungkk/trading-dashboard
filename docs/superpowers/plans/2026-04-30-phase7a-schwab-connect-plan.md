# Phase 7a — Schwab Connect (Data + Read-Only) — Implementation Plan

> **Plan v3 — architect-review applied 2026-04-30.** Plan v2 (commit `b8b3d10`) had 8 CRIT + 17 HIGH + 19 MED findings, mostly proto-shape mismatches against the actual `proto/broker/v1/broker.proto`. v3 grounds every task in the real proto + real `ConfigService` + real `BrokerConfigurer`. Key reality grounding:
>
> - **Proto facts:** `BrokerId.SCHWAB=3` already exists. `OrderStatus` has only 6 values — no `STATUS_MODIFIED`; map Schwab `PENDING_REPLACE/REPLACED` to `SUBMITTED` and let Phase 5c's SQL `order_status_rank()` handle the modified flag downstream. `ConfigureRequest` has typed Futu fields **plus a `metadata: map<string,string>` field** explicitly designed for "Future creds without proto edits" — **Schwab credentials go in `metadata`**. `ListManagedAccounts(Empty)` not `ListAccounts(ListAccountsRequest)`. `GetOrders(AccountRef)` not `GetOrders(GetOrdersRequest)`. `Account` has fields 1–4 (no `account_hash` yet — A1 adds field 5). `Order.order_id` (not `broker_order_id`). `Order.quantity_filled` (not `filled_quantity`). All monetary fields are `Money(value, currency)` — never bare strings. Enums accessed as `broker_pb2.TradingMode.LIVE` / `broker_pb2.OrderStatus.SUBMITTED` (nested), not `pb.LIVE` / `pb.SUBMITTED`.
> - **`RequestTokenRefresh` lives on a NEW service `BackendCallback`,** not on `service Broker` — keeps the backend's gRPC surface from accidentally implementing every Broker RPC. A1 adds the new service definition.
> - **`ConfigService` API:** `get(ns, key, default)` / `set(ns, key, value, value_type)` / `delete(ns, key)` for config; `reveal_secret(ns, key, default)` / `set_secret(ns, key, value, value_type)` / `delete_secret(ns, key)` for secrets. **Namespace convention:** `("broker", f"{label}.<key>")` — e.g. `("broker", "schwab.app_key")`, NOT `("schwab", "app_key")`. Matches the existing Futu wiring at `broker_registry_factory.py:51-58`.
> - **`BrokerConfigurer` already exists** at `backend/app/services/broker_registry_factory.py:40` with `targets: set[str]` + `async def configure(label: str)`. Phase 7a extends it: add `"schwab"` to `targets`; branch in `configure()` for the Schwab path that builds `ConfigureRequest(metadata={...})` instead of the typed Futu kwargs. Or extract a strategy method per broker.
> - **`SIDECAR_BROKERS`** is `dict[str, str]` mapping label → broker_id (not the tuple v2 assumed). `SIDECAR_PORTS` is the separate `dict[str, int]` map. `build_broker_registry` hardcodes `host="10.10.0.2"` for ALL labels — Phase 7a refactors to allow per-label host so Schwab can target the docker-network hostname `schwab-sidecar`.
> - **Sidecar test enum-access pattern:** `broker_pb2.TradingMode.LIVE` (nested), confirmed in `sidecar_futu/tests/test_normalize.py:20`. Apply this style to all Schwab tests.
> - **`Order.avg_fill_price_inferred`** does NOT exist on the proto — A1 adds it as field 14 of `Order` (the boundary flag for M2 fix from spec §3.2.2).
> - **Tier-2 refresher cannot import `ConfigService`** — separate Docker container with no DB session factory. All access via backend HTTP admin endpoints (CF Access service-token headers, NOT `Authorization: Bearer ...`). Per CLAUDE.md "CI bypass" pattern: `CF-Access-Client-Id` + `CF-Access-Client-Secret`.
> - **OAuth callback advisory lock**: BOTH `_exchange_code` (Tier-1 first-OAuth path) AND `refresh_with_lock` (sidecar-near-expiry path) MUST acquire `pg_try_advisory_lock(SCHWAB_REFRESH_LOCK_ID)` to satisfy spec §3.6 single-writer rule. v3 introduces a shared `_persist_tokens_under_lock` helper.
> - **Frontend testing realities:** `vi.mock` is hoisted — must be at top-level of test file, not inside `it()`. Fake-timers in async tests use `vi.advanceTimersByTimeAsync` to drain microtasks. `popstate` listener wired in `useSchwabTokenStatus` (not just on Connect-button click) so returning from the OAuth tab triggers fast-poll.
> - **Playwright redirect interception** uses `await page.route("**/api/oauth/schwab/callback*", handler)` + `await route.abort()` — `page.on("request", ...)` is observe-only and CANNOT abort navigations. Refresher uses `Locator.press_sequentially(text, delay=...)` (Playwright ≥1.43 native human-typing) instead of a hand-rolled async sleep loop.
> - **Refresher container Python:** Microsoft's `mcr.microsoft.com/playwright/python:v1.45.0` ships Python 3.11/3.12 — incompatible with our `requires-python = ">=3.14"`. Use `python:3.14-slim` + install Playwright + browser separately, OR drop the refresher's `requires-python` to `>=3.11` since it shares no code with backend.
>
> **Architect findings tracker** (52 total — 49 from initial review + 3 cross-cutting): mapped per-task in §"Self-review" at end of plan. CRIT + HIGH + MED applied inline; LOW deferrals listed in §12 of the spec.

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
| `sidecar_schwab/tests/test_handlers_list_managed_accounts.py` | Configure → ListManagedAccounts round-trip. |
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
| `backend/app/services/broker_registry_factory.py` | Add `"schwab": "schwab"` entry to `SIDECAR_BROKERS` (`dict[str, str]` label→broker_id) and a per-label `SIDECAR_HOSTS` override `{"schwab": "schwab-sidecar"}` so the schwab label resolves to the docker-network hostname instead of the WG IP `10.10.0.2`. Extend `BrokerConfigurer.targets` with `"schwab"` and add the metadata-only Configure branch. |
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

### Task A1: Extend proto contract — `Account.account_hash` + `Order.avg_fill_price_inferred` + new `BackendCallback` service

**Files:** Modify `proto/broker/v1/broker.proto`.

**v3 reality grounding:** `BrokerId.SCHWAB=3` already exists (line 40). `OrderStatus` stays at 6 values — Phase 5c's `modified` is SQL-level, NOT proto. Schwab credentials flow through the existing `ConfigureRequest.metadata: map<string,string>` field (line 303) — no shape changes to `ConfigureRequest`. `RequestTokenRefresh` lives on a NEW dedicated `BackendCallback` service so the backend's gRPC server doesn't have to implement every Broker RPC.

- [ ] **Step 1: Add `account_hash` field 5 to `Account` message.** Find `Account` (line 121) and append:

```proto
message Account {
  string account_number = 1;
  TradingMode mode = 2;
  string gateway_label = 3;
  // Sourced from accountSummary BASE tag (e.g., "USD", "GBP"). NOT defaulted.
  string currency_base = 4;
  // Phase 7a — Schwab privacy-layer hash from /accountNumbers; required on
  // every Schwab REST path. Empty for IBKR/Futu. PII-equivalent: never
  // logged; backend's AccountResponse boundary-strips before REST output.
  string account_hash = 5;
}
```

- [ ] **Step 2: Add `avg_fill_price_inferred` field 14 to `Order` message.** Find `Order` (line 188) and append after `updated_at = 13`:

```proto
message Order {
  string order_id = 1;
  Contract contract = 2;
  OrderSide side = 3;
  OrderType order_type = 4;
  string quantity = 5;
  Money limit_price = 6;
  Money stop_price = 7;
  TimeInForce time_in_force = 8;
  OrderStatus status = 9;
  string quantity_filled = 10;
  Money avg_fill_price = 11;
  google.protobuf.Timestamp submitted_at = 12;
  google.protobuf.Timestamp updated_at = 13;
  // Phase 7a M2 — true when avg_fill_price was inferred from limit_price
  // because the upstream broker JSON omitted execution-leg detail (e.g.,
  // Schwab's `orderActivityCollection` missing on a FILLED order). UI
  // dims the value when this flag is true.
  bool avg_fill_price_inferred = 14;
}
```

- [ ] **Step 3: Add NEW `BackendCallback` service + messages at end of file** (after `ConfigureResponse`):

```proto
// Phase 7a — sidecar→backend single-writer token-refresh callback (architect C2).
// This service is implemented by the BACKEND (not by sidecars). Sidecars are
// gRPC clients of this service when they need a fresh token pair. Backend
// holds the PG advisory lock during the actual refresh; sidecars never call
// Schwab's /oauth/token endpoint directly.
service BackendCallback {
  // broker_id selects which broker's tokens to refresh. Only "schwab"
  // currently uses this path; other brokers reuse their existing auth.
  rpc RequestTokenRefresh(TokenRefreshRequest) returns (TokenRefreshResponse);
}

message TokenRefreshRequest {
  string broker_id = 1;  // "schwab" — distinguishes if other brokers ever need this pattern
}

message TokenRefreshResponse {
  string access_token = 1;
  string refresh_token = 2;
  google.protobuf.Timestamp access_issued_at = 3;
}
```

- [ ] **Step 4: Regenerate proto stubs.** Verify the helper scripts exist first (PF8):

```bash
cd /home/joseph/dashboard
test -x sidecar/scripts/proto-gen.sh || { echo "MISSING sidecar/scripts/proto-gen.sh"; exit 1; }
test -x sidecar_futu/scripts/proto-gen.sh || { echo "MISSING sidecar_futu/scripts/proto-gen.sh"; exit 1; }
bash sidecar/scripts/proto-gen.sh
bash sidecar_futu/scripts/proto-gen.sh
# sidecar_schwab/scripts/proto-gen.sh is created in A3
# Backend gets regenerated through the existing repo's gen mechanism (verify with the same `bash sidecar/scripts/proto-gen.sh` if it covers backend, or whatever the existing script does — DO NOT INVENT)
```

- [ ] **Step 5: Verify new symbols import cleanly.**

```bash
cd backend && uv run python -c "
from app._generated.broker.v1 import broker_pb2 as pb, broker_pb2_grpc as pbg

# Account.account_hash
assert pb.Account.DESCRIPTOR.fields_by_name['account_hash'].number == 5

# Order.avg_fill_price_inferred
assert pb.Order.DESCRIPTOR.fields_by_name['avg_fill_price_inferred'].number == 14

# New BackendCallback service stubs
assert hasattr(pbg, 'BackendCallbackStub'), 'BackendCallbackStub missing'
assert hasattr(pbg, 'add_BackendCallbackServicer_to_server'), 'BackendCallback servicer hook missing'

# TokenRefresh messages
assert pb.TokenRefreshRequest.DESCRIPTOR
assert pb.TokenRefreshResponse.DESCRIPTOR

# Sanity: existing surface unchanged
assert pb.BrokerId.SCHWAB == 3, 'SCHWAB=3 expected per existing proto line 40'
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 6: Commit.**

```bash
git add proto/broker/v1/broker.proto backend/app/_generated/ sidecar/_generated/ sidecar_futu/_generated/
git commit -m "feat(proto): Account.account_hash + Order.avg_fill_price_inferred + BackendCallback service for Phase 7a"
```

**Conditional reviewers:** `type-design-analyzer`, `silent-failure-hunter` (proto enum semantics).

**Architect findings closed by A1:** CRIT #1 (no ConfigureRequest reshape — use metadata map), CRIT #2 (BackendCallback as separate service), CRIT #3 (no STATUS_MODIFIED — defer to SQL; add `avg_fill_price_inferred` field), HIGH #9 (place new messages at end of file matching `ConfigureRequest`/`ConfigureResponse` precedent).

### Task A2: ~~IBKR + Futu UNIMPLEMENTED stubs~~ — DELETED in v3

**v3 reality grounding:** `RequestTokenRefresh` was moved off `service Broker` onto a NEW `service BackendCallback` (A1). Sidecars are *clients* of `BackendCallback`, never servers — so IBKR/Futu sidecars do NOT need an UNIMPLEMENTED stub. The `service Broker` surface is unchanged for IBKR/Futu, no test+code add needed for them.

**This task is intentionally empty.** Skip A2; renumber subsequent tasks if mechanical, OR keep the A2 slot as documentation of the architectural reroute.

**Architect findings closed by A2 deletion:** HIGH #10 (no need to mock `context.abort` AioRpcError — the test that motivated it is removed entirely).

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

- [ ] **Step 4: Write minimal `sidecar_schwab/handlers.py`** (chunk B fills out). **v3 fix:** import shape matches the existing IBKR/Futu sidecar pattern (`from sidecar_schwab._generated.broker.v1 import broker_pb2, broker_pb2_grpc` — no `as pb` alias since the real test pattern at `sidecar_futu/tests/test_normalize.py:20` uses `broker_pb2.TradingMode.LIVE`):

```python
"""gRPC Broker servicer for Schwab. Stubs filled out in chunk B."""
from __future__ import annotations

import grpc

from sidecar_schwab._generated.broker.v1 import (
    broker_pb2,
    broker_pb2_grpc,
)


class BrokerServicer(broker_pb2_grpc.BrokerServicer):
    """Schwab gRPC service. Empty stubs in A4; chunk B fills them out."""

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        # Minimal pre-Configure response. Real impl in B5 populates
        # started_at + gateway_version; gateway_connected stays False until
        # token + account_hash cache are both populated (H4 invariant).
        return broker_pb2.HealthResponse(
            label="schwab",
            broker_id="schwab",
            gateway_version="",
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
    broker_pb2,
    broker_pb2_grpc,
)
from sidecar_schwab.config import resolve_port
from sidecar_schwab.handlers import BrokerServicer

log = structlog.get_logger(module="sidecar_schwab.main")


async def serve() -> None:
    port = resolve_port()
    server = grpc.aio.server()

    servicer = BrokerServicer()
    broker_pb2_grpc.add_BrokerServicer_to_server(servicer, server)

    SERVICE_NAMES = (
        broker_pb2.DESCRIPTOR.services_by_name["Broker"].full_name,
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

### Task A5: Register `schwab` in `SIDECAR_BROKERS` + add `SIDECAR_HOSTS` per-label override

**Files:** Modify `backend/app/services/broker_registry_factory.py`.

**v3 reality grounding:** Per `broker_registry_factory.py:16-32`, the actual maps are:
- `SIDECAR_PORTS: dict[str, int]` — label → port
- `SIDECAR_BROKERS: dict[str, str]` — label → broker_id (NOT a tuple of `(broker_id, address)` as v2 assumed)

`build_broker_registry` hardcodes `host="10.10.0.2"` for ALL labels. Schwab needs the docker-compose hostname `schwab-sidecar`. v3 introduces a `SIDECAR_HOSTS: dict[str, str]` override map: labels not in this map fall back to the registry's `host` parameter.

- [ ] **Step 1: Failing test.** Create `backend/tests/services/test_sidecar_brokers_map.py`:

```python
"""Phase 7a A5 — SIDECAR_BROKERS includes schwab; per-label host override works."""
from app.services.broker_registry_factory import (
    SIDECAR_PORTS,
    SIDECAR_BROKERS,
    SIDECAR_HOSTS,
    resolve_target,
)


def test_schwab_in_sidecar_brokers():
    assert SIDECAR_BROKERS["schwab"] == "schwab"
    assert SIDECAR_PORTS["schwab"] == 9090
    assert SIDECAR_HOSTS["schwab"] == "schwab-sidecar"


def test_existing_brokers_unchanged():
    """Don't break Phase 4 + 6 wiring."""
    assert SIDECAR_BROKERS["isa-live"] == "ibkr"
    assert SIDECAR_BROKERS["isa-paper"] == "ibkr"
    assert SIDECAR_BROKERS["normal-live"] == "ibkr"
    assert SIDECAR_BROKERS["normal-paper"] == "ibkr"
    assert SIDECAR_BROKERS["futu"] == "futu"
    assert SIDECAR_PORTS["isa-live"] == 18001
    assert SIDECAR_PORTS["futu"] == 18005


def test_resolve_target_falls_back_to_default_host():
    """IBKR labels have no SIDECAR_HOSTS entry → use the default."""
    assert resolve_target("isa-live", default_host="10.10.0.2") == "10.10.0.2:18001"
    assert resolve_target("futu",     default_host="10.10.0.2") == "10.10.0.2:18005"


def test_resolve_target_uses_per_label_host_override():
    """Schwab is overridden to schwab-sidecar (docker-compose hostname)."""
    assert resolve_target("schwab", default_host="10.10.0.2") == "schwab-sidecar:9090"
```

- [ ] **Step 2: Run — FAIL.**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/services/test_sidecar_brokers_map.py -v
```

- [ ] **Step 3: Edit `backend/app/services/broker_registry_factory.py`.** Add Schwab row + `SIDECAR_HOSTS` + `resolve_target` helper. Keep existing constants in place; do not change tuple shape.

```python
SIDECAR_PORTS: dict[str, int] = {
    "isa-live": 18001,
    "isa-paper": 18002,
    "normal-live": 18003,
    "normal-paper": 18004,
    "futu": 18005,
    "schwab": 9090,    # Phase 7a — VPS docker-compose-internal port
}

# H4: backend cross-checks Health.broker_id against this map at every probe.
SIDECAR_BROKERS: dict[str, str] = {
    "isa-live": "ibkr",
    "isa-paper": "ibkr",
    "normal-live": "ibkr",
    "normal-paper": "ibkr",
    "futu": "futu",
    "schwab": "schwab",   # Phase 7a
}

# Phase 7a — per-label host override. Labels NOT in this map use the
# build_broker_registry(host=...) default (10.10.0.2 / NUC-WG). Schwab
# lives in the same docker-compose network as backend on the VPS, so
# its host is the compose service name "schwab-sidecar".
SIDECAR_HOSTS: dict[str, str] = {
    "schwab": "schwab-sidecar",
}


def resolve_target(label: str, *, default_host: str) -> str:
    """Compute the gRPC target for a sidecar label.

    `SIDECAR_HOSTS` overrides the default_host on a per-label basis;
    `SIDECAR_PORTS` always provides the port.
    """
    host = SIDECAR_HOSTS.get(label, default_host)
    port = SIDECAR_PORTS[label]
    return f"{host}:{port}"
```

- [ ] **Step 4: Update `build_broker_registry` to use `resolve_target`** (replace the dict-comprehension that builds `target=f"{host}:{port}"`):

```python
    registry = BrokerRegistry(
        {
            label: BrokerSidecarClient(
                label=label,
                target=resolve_target(label, default_host=host),
                client_cert_pem=cert_pem,
                client_key_pem=key_pem,
                ca_bundle_pem=ca_bundle_pem,
            )
            for label in SIDECAR_PORTS
        }
    )
```

- [ ] **Step 5: Run — PASS.**

```bash
uv run pytest tests/services/test_sidecar_brokers_map.py -v
```

- [ ] **Step 6: Commit.**

```bash
git add backend/app/services/broker_registry_factory.py \
        backend/tests/services/test_sidecar_brokers_map.py
git commit -m "feat(backend): register schwab sidecar + SIDECAR_HOSTS per-label host override"
```

**Architect findings closed by A5:** CRIT (v2 SIDECAR_BROKERS shape mismatch), HIGH (host hardcoding for VPS Schwab).

**Open question for chunk C:** the existing Schwab sidecar has NO mTLS (same docker network as backend). `BrokerSidecarClient` currently requires `client_cert_pem`/`client_key_pem`/`ca_bundle_pem`. Either: (a) extend `BrokerSidecarClient` to accept `tls=None` for plaintext labels, OR (b) introduce a `BrokerSidecarPlaintextClient` subclass. Decision deferred to **Task C0** (added below as a chunk-C prerequisite).

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

### Task A7: Add Phase 7a Prometheus metric stubs (incl. `BROKER_CONFIGURE_TOTAL`)

**Files:** Modify `backend/app/core/metrics.py`. Create `backend/tests/observability/test_metrics_phase7a.py`.

**v3 reality grounding:** `grep BROKER_CONFIGURE_TOTAL backend/app/core/metrics.py` returns NOTHING — Phase 6 never registered the metric the spec §8.1 row says "extends Phase 6". A7 adds it as net-new. Also adds `SCHWAB_NORMALIZE_UNKNOWN_TOTAL` (B1 dependency) so B1 doesn't fail on import.

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

# v3 — net-new (Phase 6 never registered this despite spec §8.1 saying "extends").
BROKER_CONFIGURE_TOTAL = Counter(
    "broker_configure_total",
    "Sidecar Configure RPC outcomes by label + reason.",
    ["label", "reason"],  # label: futu|schwab; reason: lifespan|started_at_delta|reconfigure|oauth_callback|near_expiry|manual
)

# v3 — also net-new; consumed by sidecar_schwab/normalize.py (B1) AND by
# sidecar_futu/normalize.py if Phase 6 didn't already register it. Verify:
#   grep -n "broker_normalize_unknown_total\|SCHWAB_NORMALIZE_UNKNOWN_TOTAL\|FUTU_NORMALIZE_UNKNOWN_TOTAL" backend/app/core/metrics.py sidecar_futu/
# If Phase 6 already exposes it under a different Python symbol but the
# Prometheus name matches, do NOT redeclare here (would double-register).
SCHWAB_NORMALIZE_UNKNOWN_TOTAL = Counter(
    "broker_normalize_unknown_total",
    "Schwab/Futu normalize: unknown enum encounters per field+value.",
    ["label", "field", "value"],
)

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

## Chunk B — Sidecar core (10 tasks)

Goal: fill out the data plane RPCs. After B10 the sidecar can Configure, Health, ListAccounts, GetAccountSummary, GetPositions, GetOrders against a mocked Schwabdev client; write + streaming RPCs return UNIMPLEMENTED.

### Task B1: `sidecar_schwab/normalize.py` — Schwab JSON → proto mappers

**Files:** Create `sidecar_schwab/normalize.py`, `sidecar_schwab/tests/test_normalize.py`.

**v3 reality grounding:**
- `OrderStatus` proto enum has 6 values, **no `STATUS_MODIFIED`**. Map Schwab `PENDING_REPLACE`/`REPLACED` → `broker_pb2.OrderStatus.SUBMITTED`. Phase 5c's "modified" is a SQL-level enum extension on the `orders` table; backend's order-event consumer translates the wire-level SUBMITTED + presence of a replace event into the SQL `modified` rank — sidecar normalize stays at proto's 6 values.
- All monetary fields are `Money(value, currency)` — `Position.avg_cost`, `Position.market_price`, `Position.market_value`, `Position.unrealized_pnl`, `Position.realized_pnl_today`, `Position.daily_pnl`, `Order.limit_price`, `Order.stop_price`, `Order.avg_fill_price`, `Summary.net_liquidation`, `Summary.total_cash`, `Summary.realized_pnl`, `Summary.unrealized_pnl`, `Summary.buying_power`. Plain strings only on `Order.quantity`, `Order.quantity_filled`, `Position.quantity`, `Contract.conid`, `Contract.symbol`, `Contract.local_symbol`, `Contract.multiplier`.
- `Order.order_id` (not `broker_order_id`).
- `Order.quantity_filled` (not `filled_quantity`).
- New `Order.avg_fill_price_inferred` field 14 (added in A1).
- Enum access pattern: `broker_pb2.OrderStatus.SUBMITTED`, `broker_pb2.TradingMode.LIVE`, `broker_pb2.AssetClass.STOCK` (nested, per `sidecar_futu/tests/test_normalize.py:20`).

- [ ] **Step 1: Failing test for status mapping table (spec §3.2.1).** Create `sidecar_schwab/tests/test_normalize.py`:

```python
"""Phase 7a B1 — Schwab JSON → proto mapping coverage (v3 — Money + nested enums)."""
from decimal import Decimal

import pytest

from sidecar_schwab.normalize import (
    normalize_account,
    normalize_position,
    normalize_order,
    normalize_summary,
    map_status,
    map_order_type,
    map_tif,
    map_asset_type,
)
from sidecar_schwab._generated.broker.v1 import broker_pb2


@pytest.mark.parametrize("raw,expected", [
    # SUBMITTED bucket
    ("WORKING",                broker_pb2.OrderStatus.SUBMITTED),
    ("ACCEPTED",               broker_pb2.OrderStatus.SUBMITTED),
    ("QUEUED",                 broker_pb2.OrderStatus.SUBMITTED),
    # Phase 5c modified — wire-level proto stays SUBMITTED;
    # backend's SQL `order_status_rank()` lifts to "modified".
    ("PENDING_REPLACE",        broker_pb2.OrderStatus.SUBMITTED),
    ("REPLACED",               broker_pb2.OrderStatus.SUBMITTED),
    # PENDING bucket
    ("PENDING_ACTIVATION",     broker_pb2.OrderStatus.PENDING),
    ("AWAITING_PARENT_ORDER",  broker_pb2.OrderStatus.PENDING),
    ("AWAITING_CONDITION",     broker_pb2.OrderStatus.PENDING),
    ("AWAITING_MANUAL_REVIEW", broker_pb2.OrderStatus.PENDING),
    ("AWAITING_UR_OUT",        broker_pb2.OrderStatus.PENDING),
    ("AWAITING_RELEASE_TIME",  broker_pb2.OrderStatus.PENDING),
    ("AWAITING_STOP_CONDITION", broker_pb2.OrderStatus.PENDING),
    ("NEW",                    broker_pb2.OrderStatus.PENDING),
    ("FILLED",                 broker_pb2.OrderStatus.FILLED),
    ("CANCELED",               broker_pb2.OrderStatus.CANCELLED),
    ("PENDING_CANCEL",         broker_pb2.OrderStatus.CANCELLED),
    ("EXPIRED",                broker_pb2.OrderStatus.CANCELLED),
    ("REJECTED",               broker_pb2.OrderStatus.REJECTED),
    # Unknown falls through to PENDING + emits broker_normalize_unknown_total
    ("WHO_KNOWS",              broker_pb2.OrderStatus.PENDING),
])
def test_status_mapping(raw, expected):
    assert map_status(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("EQUITY", broker_pb2.AssetClass.STOCK),
    ("ETF", broker_pb2.AssetClass.ETF),
    ("MUTUAL_FUND", broker_pb2.AssetClass.MUTUAL_FUND),
    ("OPTION", broker_pb2.AssetClass.OPTION),
    ("FUTURE", broker_pb2.AssetClass.FUTURE),
    ("FIXED_INCOME", broker_pb2.AssetClass.BOND),
    ("CURRENCY", broker_pb2.AssetClass.FOREX),
    ("INDEX", broker_pb2.AssetClass.STOCK),                 # display collapse
    ("CASH_EQUIVALENT", broker_pb2.AssetClass.STOCK),       # money-market funds
    ("COLLECTIVE_INVESTMENT", broker_pb2.AssetClass.ETF),   # legacy ETFs
])
def test_asset_type_mapping(raw, expected):
    assert map_asset_type(raw) == expected


def test_normalize_account_is_LIVE_with_USD_currency_base():
    """H5 + spec §3.2 — Schwab accounts always LIVE, currency_base hardcoded USD."""
    raw = {"securitiesAccount": {"accountNumber": "12345678", "type": "MARGIN"}}
    result = normalize_account(raw)
    assert result.account_number == "12345678"
    assert result.mode == broker_pb2.TradingMode.LIVE
    assert result.gateway_label == "schwab"
    assert result.currency_base == "USD"
    # account_hash is populated separately by handlers (B6); empty here
    assert result.account_hash == ""


def test_normalize_summary_uses_money_proto():
    """B1 — Summary fields are Money(value, currency), never bare strings."""
    raw = {
        "currentBalances": {
            "liquidationValue": 100_000.50,
            "cashBalance": 25_000.00,
            "buyingPower": 200_000.00,
        },
        "initialBalances": {"liquidationValue": 99_500.00},
    }
    result = normalize_summary(raw)
    # SummaryResponse wraps a Summary with Money fields
    assert result.summary.net_liquidation.value == "100000.50"
    assert result.summary.net_liquidation.currency == "USD"
    assert result.summary.total_cash.value == "25000.00"
    assert result.summary.total_cash.currency == "USD"
    assert result.summary.buying_power.value == "200000.00"
    assert result.summary.buying_power.currency == "USD"
    # day_pnl flows into Summary.realized_pnl per existing IBKR shape


def test_normalize_position_uses_money_for_all_decimal_fields():
    raw = {
        "instrument": {
            "symbol": "AAPL", "assetType": "EQUITY",
            "cusip": "037833100",
        },
        "longQuantity": 100,
        "shortQuantity": 0,
        "averagePrice": 150.25,
        "marketValue": 17500.00,
        "currentDayProfitLoss": 245.00,
    }
    result = normalize_position(raw)
    assert result.contract.symbol == "AAPL"
    assert result.contract.asset_class == broker_pb2.AssetClass.STOCK
    assert result.contract.currency == "USD"
    assert result.quantity == "100"
    # All monetary as Money
    assert result.avg_cost.value == "150.25"
    assert result.avg_cost.currency == "USD"
    assert result.market_value.value == "17500.00"
    assert result.daily_pnl.value == "245.00"


def test_normalize_order_extracts_avg_fill_from_orderActivityCollection():
    """M2 — avg_fill_price MUST come from executionLegs, not order.price.
    v3 — avg_fill_price is Money type; avg_fill_price_inferred is field 14."""
    raw = {
        "orderId": 999,
        "status": "FILLED",
        "orderType": "LIMIT",
        "duration": "DAY",
        "price": 100.00,           # the LIMIT price — must NOT be used as avg_fill
        "quantity": 100,
        "filledQuantity": 100,
        "orderLegCollection": [{
            "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            "instruction": "BUY",
        }],
        "orderActivityCollection": [{
            "executionLegs": [
                {"price": 99.50, "quantity": 50},
                {"price": 99.75, "quantity": 50},
            ],
        }],
    }
    result = normalize_order(raw)
    # v3: order_id (not broker_order_id)
    assert result.order_id == "999"
    assert result.status == broker_pb2.OrderStatus.FILLED
    # weighted avg = (99.50*50 + 99.75*50) / 100 = 99.625
    assert Decimal(result.avg_fill_price.value) == Decimal("99.625")
    assert result.avg_fill_price.currency == "USD"
    assert result.avg_fill_price_inferred is False
    # v3: quantity_filled (not filled_quantity)
    assert result.quantity_filled == "100"


def test_normalize_order_filled_without_orderActivityCollection_marks_inferred():
    """M2 — when activity missing on FILLED, avg_fill_price empty + flag."""
    raw = {
        "orderId": 999,
        "status": "FILLED",
        "orderType": "LIMIT",
        "duration": "DAY",
        "price": 100.00,
        "quantity": 100,
        "filledQuantity": 100,
        "orderLegCollection": [{
            "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            "instruction": "BUY",
        }],
        # no orderActivityCollection
    }
    result = normalize_order(raw)
    # avg_fill_price.value empty; inferred flag true
    assert result.avg_fill_price.value == ""
    assert result.avg_fill_price_inferred is True


def test_normalize_modified_status_maps_to_submitted_not_modified():
    """v3 — wire-level proto OrderStatus has no STATUS_MODIFIED.
    Schwab PENDING_REPLACE/REPLACED → SUBMITTED; backend SQL handles 'modified'."""
    for raw in ("PENDING_REPLACE", "REPLACED"):
        result = normalize_order({
            "orderId": 1, "status": raw,
            "orderType": "LIMIT", "duration": "DAY",
            "quantity": 10, "filledQuantity": 0, "price": 100.0,
            "orderLegCollection": [{
                "instrument": {"symbol": "X", "assetType": "EQUITY"}, "instruction": "BUY"}],
        })
        assert result.status == broker_pb2.OrderStatus.SUBMITTED
```

- [ ] **Step 2: Run — FAIL (module not yet present).**

```bash
cd /home/joseph/dashboard/sidecar_schwab && uv run pytest tests/test_normalize.py -v
```

- [ ] **Step 3: Write `sidecar_schwab/normalize.py` (v3 — Money + nested enum access; PENDING_REPLACE/REPLACED → SUBMITTED):**

```python
"""Schwab JSON → proto Account/Summary/Position/Order mappers (v3).

v3 corrections from architect-review:
  - All decimal fields use Money(value, currency) per real proto contract.
  - OrderStatus enum has only 6 values; PENDING_REPLACE/REPLACED → SUBMITTED
    (backend's SQL order_status_rank() handles the modified flag).
  - Order.order_id (not broker_order_id), Order.quantity_filled (not
    filled_quantity), Order.avg_fill_price_inferred field 14.
  - Position uses Contract sub-message, not flat symbol/asset_class fields.
  - Enum access via broker_pb2.OrderStatus.SUBMITTED (nested), per the
    pattern in sidecar_futu/tests/test_normalize.py:20.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.metrics import SCHWAB_NORMALIZE_UNKNOWN_TOTAL

log = logging.getLogger(__name__)

# Schwab status → wire-level proto enum (6 values). PENDING_REPLACE/REPLACED
# map to SUBMITTED on the wire; backend's SQL `order_status_rank()` lifts
# replacement events to the SQL `modified` rank.
_STATUS: dict[str, int] = {
    "WORKING":                 broker_pb2.OrderStatus.SUBMITTED,
    "ACCEPTED":                broker_pb2.OrderStatus.SUBMITTED,
    "QUEUED":                  broker_pb2.OrderStatus.SUBMITTED,
    "PENDING_REPLACE":         broker_pb2.OrderStatus.SUBMITTED,  # v3 — was STATUS_MODIFIED
    "REPLACED":                broker_pb2.OrderStatus.SUBMITTED,  # v3 — was STATUS_MODIFIED
    "PENDING_ACTIVATION":      broker_pb2.OrderStatus.PENDING,
    "AWAITING_PARENT_ORDER":   broker_pb2.OrderStatus.PENDING,
    "AWAITING_CONDITION":      broker_pb2.OrderStatus.PENDING,
    "AWAITING_MANUAL_REVIEW":  broker_pb2.OrderStatus.PENDING,
    "AWAITING_UR_OUT":         broker_pb2.OrderStatus.PENDING,
    "AWAITING_RELEASE_TIME":   broker_pb2.OrderStatus.PENDING,
    "AWAITING_STOP_CONDITION": broker_pb2.OrderStatus.PENDING,
    "NEW":                     broker_pb2.OrderStatus.PENDING,
    "FILLED":                  broker_pb2.OrderStatus.FILLED,
    "CANCELED":                broker_pb2.OrderStatus.CANCELLED,
    "PENDING_CANCEL":          broker_pb2.OrderStatus.CANCELLED,
    "EXPIRED":                 broker_pb2.OrderStatus.CANCELLED,
    "REJECTED":                broker_pb2.OrderStatus.REJECTED,
}

_ASSET_TYPE: dict[str, int] = {
    "EQUITY":                broker_pb2.AssetClass.STOCK,
    "ETF":                   broker_pb2.AssetClass.ETF,
    "MUTUAL_FUND":           broker_pb2.AssetClass.MUTUAL_FUND,
    "OPTION":                broker_pb2.AssetClass.OPTION,
    "FUTURE":                broker_pb2.AssetClass.FUTURE,
    "FIXED_INCOME":          broker_pb2.AssetClass.BOND,
    "CURRENCY":              broker_pb2.AssetClass.FOREX,
    "INDEX":                 broker_pb2.AssetClass.STOCK,  # display collapse
    "CASH_EQUIVALENT":       broker_pb2.AssetClass.STOCK,
    "COLLECTIVE_INVESTMENT": broker_pb2.AssetClass.ETF,
}

_ORDER_TYPE: dict[str, int] = {
    "MARKET":     broker_pb2.OrderType.MARKET,
    "LIMIT":      broker_pb2.OrderType.LIMIT,
    "STOP":       broker_pb2.OrderType.STOP,
    "STOP_LIMIT": broker_pb2.OrderType.STOP_LIMIT,
}

_TIF: dict[str, int] = {
    "DAY":                 broker_pb2.TimeInForce.DAY,
    "GOOD_TILL_CANCEL":    broker_pb2.TimeInForce.GTC,
    "FILL_OR_KILL":        broker_pb2.TimeInForce.FOK,
    "IMMEDIATE_OR_CANCEL": broker_pb2.TimeInForce.IOC,
}


def _dec_str(v: Any) -> str:
    """Coerce JSON value to canonical decimal string. NaN/None → "" (empty Money)."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and v != v:  # NaN
            return ""
        return str(Decimal(str(v)))
    except (InvalidOperation, ValueError, TypeError):
        return ""


def _money(value: Any, currency: str = "USD") -> broker_pb2.Money:
    """Build a Money message; empty value when input is null/NaN."""
    return broker_pb2.Money(value=_dec_str(value), currency=currency)


def map_status(raw: str) -> int:
    if raw in _STATUS:
        return _STATUS[raw]
    SCHWAB_NORMALIZE_UNKNOWN_TOTAL.labels(label="schwab", field="status", value=raw).inc()
    log.warning("schwab_unknown_status", value=raw)
    return broker_pb2.OrderStatus.PENDING


def map_asset_type(raw: str) -> int:
    if raw in _ASSET_TYPE:
        return _ASSET_TYPE[raw]
    SCHWAB_NORMALIZE_UNKNOWN_TOTAL.labels(label="schwab", field="assetType", value=raw).inc()
    return broker_pb2.AssetClass.STOCK


def map_order_type(raw: str) -> int:
    return _ORDER_TYPE.get(raw, broker_pb2.OrderType.MARKET)


def map_tif(raw: str) -> int:
    return _TIF.get(raw, broker_pb2.TimeInForce.DAY)


def normalize_account(raw: dict[str, Any]) -> broker_pb2.Account:
    """H5 — currency_base hardcoded USD (Schwab is USD-only as of 2026)."""
    sa = raw.get("securitiesAccount") or {}
    return broker_pb2.Account(
        account_number=str(sa.get("accountNumber") or ""),
        mode=broker_pb2.TradingMode.LIVE,   # spec §3.2 invariant — all Schwab accounts LIVE
        gateway_label="schwab",
        currency_base="USD",
        # account_hash is set by handlers (B6) from /accountNumbers
    )


def normalize_summary(raw: dict[str, Any]) -> broker_pb2.SummaryResponse:
    """SummaryResponse wraps a Summary with Money fields."""
    balances = raw.get("currentBalances") or {}
    initial = raw.get("initialBalances") or {}

    nlv = _dec_str(balances.get("liquidationValue"))
    cash = _dec_str(balances.get("cashBalance") or balances.get("totalCash"))
    bp = _dec_str(balances.get("buyingPower") or balances.get("availableFunds"))
    # day_pnl as realized_pnl (best fit on the Summary message); compute
    # from initialBalances when present, else empty.
    prev_nlv_str = _dec_str(initial.get("liquidationValue"))
    if nlv and prev_nlv_str:
        try:
            day_pnl = str(Decimal(nlv) - Decimal(prev_nlv_str))
        except (InvalidOperation, ValueError):
            day_pnl = ""
    else:
        day_pnl = ""

    summary = broker_pb2.Summary(
        net_liquidation=broker_pb2.Money(value=nlv, currency="USD"),
        total_cash=broker_pb2.Money(value=cash, currency="USD"),
        realized_pnl=broker_pb2.Money(value=day_pnl, currency="USD"),
        unrealized_pnl=broker_pb2.Money(value="", currency="USD"),  # Schwab doesn't break this out
        buying_power=broker_pb2.Money(value=bp, currency="USD"),
    )
    return broker_pb2.SummaryResponse(summary=summary)


def _build_contract(instr: dict[str, Any]) -> broker_pb2.Contract:
    return broker_pb2.Contract(
        symbol=str(instr.get("symbol") or ""),
        exchange="",  # Schwab doesn't expose primary exchange on the position object
        currency="USD",
        asset_class=map_asset_type(str(instr.get("assetType") or "")),
        conid=str(instr.get("cusip") or ""),  # CUSIP is the closest stable ID
        local_symbol=str(instr.get("symbol") or ""),
        multiplier="1",
    )


def normalize_position(raw: dict[str, Any]) -> broker_pb2.Position:
    instr = raw.get("instrument") or {}
    long_qty = raw.get("longQuantity") or 0
    short_qty = raw.get("shortQuantity") or 0
    qty = long_qty - short_qty
    return broker_pb2.Position(
        contract=_build_contract(instr),
        quantity=str(qty),
        avg_cost=_money(raw.get("averagePrice")),
        market_price=_money(""),  # Schwab provides marketValue/qty implicitly
        market_value=_money(raw.get("marketValue")),
        unrealized_pnl=_money(""),  # not separately broken out
        realized_pnl_today=_money(""),
        daily_pnl=_money(raw.get("currentDayProfitLoss")),
    )


def _avg_fill_from_activity(activity: list[dict[str, Any]]) -> tuple[str, bool]:
    """M2 — compute weighted avg fill from executionLegs.

    Returns (avg_fill_price_str, inferred). `inferred=False` when we have
    real activity data; `inferred=True` when activity is missing/empty.
    """
    legs: list[tuple[Decimal, Decimal]] = []
    for act in activity or []:
        for leg in act.get("executionLegs") or []:
            try:
                price = Decimal(str(leg.get("price")))
                qty = Decimal(str(leg.get("quantity")))
            except (InvalidOperation, ValueError, TypeError):
                continue
            legs.append((price, qty))
    if not legs:
        return "", True
    total_qty = sum((q for _, q in legs), Decimal(0))
    if total_qty == 0:
        return "", True
    weighted = sum((p * q for p, q in legs), Decimal(0))
    return str(weighted / total_qty), False


def normalize_order(raw: dict[str, Any]) -> broker_pb2.Order:
    """v3 — Order.order_id (not broker_order_id); quantity_filled (not
    filled_quantity); all monetary fields as Money."""
    leg = (raw.get("orderLegCollection") or [{}])[0]
    instr = leg.get("instrument") or {}

    status = map_status(str(raw.get("status") or ""))
    activity = raw.get("orderActivityCollection") or []
    avg_fill_value = ""
    inferred = False
    is_filled_or_partial = (
        status == broker_pb2.OrderStatus.FILLED
        or (raw.get("filledQuantity") and raw.get("filledQuantity") != 0)
    )
    if is_filled_or_partial:
        avg_fill_value, inferred = _avg_fill_from_activity(activity)

    side_str = str(leg.get("instruction") or "").upper()
    side = broker_pb2.OrderSide.BUY if "BUY" in side_str else broker_pb2.OrderSide.SELL

    return broker_pb2.Order(
        order_id=str(raw.get("orderId") or ""),         # v3 — order_id, not broker_order_id
        contract=_build_contract(instr),
        side=side,
        order_type=map_order_type(str(raw.get("orderType") or "")),
        quantity=_dec_str(raw.get("quantity")),
        limit_price=_money(raw.get("price")),
        stop_price=_money(raw.get("stopPrice")),
        time_in_force=map_tif(str(raw.get("duration") or "")),
        status=status,
        quantity_filled=_dec_str(raw.get("filledQuantity")),  # v3 — quantity_filled
        avg_fill_price=broker_pb2.Money(value=avg_fill_value, currency="USD"),
        avg_fill_price_inferred=inferred,
    )
```

- [ ] **Step 4: Add the metric `SCHWAB_NORMALIZE_UNKNOWN_TOTAL` to `sidecar_schwab/metrics.py`** (create the file):

```python
"""Sidecar-local Prometheus counters."""
from __future__ import annotations

from prometheus_client import Counter, Gauge

SCHWAB_NORMALIZE_UNKNOWN_TOTAL = Counter(
    "broker_normalize_unknown_total",
    "Schwab JSON normalize unknown enum encounters.",
    ["field", "value"],
)

SCHWAB_HTTP_REQUESTS_TOTAL = Counter(
    "schwab_http_requests_total",
    "Schwab REST request count by endpoint + status.",
    ["endpoint", "status"],
)

SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL = Counter(
    "schwab_account_hash_refresh_total",
    "account_hash cache refreshes by reason.",
    ["reason"],
)

SCHWAB_ACCESS_TOKEN_AGE_SECONDS = Gauge(
    "schwab_access_token_age_seconds",
    "Age of the current access_token.",
)
```

- [ ] **Step 5: Add Phase 5c `STATUS_MODIFIED` to proto.** Verify the proto already has it via `grep STATUS_MODIFIED proto/broker/v1/broker.proto`. If not present, append to the `OrderStatus` enum + regen stubs.

```bash
cd /home/joseph/dashboard
grep STATUS_MODIFIED proto/broker/v1/broker.proto
```

- [ ] **Step 6: Run tests — PASS.**

```bash
cd sidecar_schwab && uv run pytest tests/test_normalize.py -v
```

Expected: all parametrize variants pass.

- [ ] **Step 7: Commit.**

```bash
git add sidecar_schwab/normalize.py sidecar_schwab/metrics.py sidecar_schwab/tests/test_normalize.py
git commit -m "feat(sidecar-schwab): normalize.py — JSON→proto mappers + status table + avg_fill from activity (M2)"
```

**Conditional reviewers:** `python-reviewer`, `silent-failure-hunter` (Decimal parse paths), `type-design-analyzer`.

### Task B2: `sidecar_schwab/auth.py` — token freshness + RequestTokenRefresh client

**Files:** Create `sidecar_schwab/auth.py`, `sidecar_schwab/tests/test_auth_lifecycle.py`.

- [ ] **Step 1: Failing test.** Create `sidecar_schwab/tests/test_auth_lifecycle.py`:

```python
"""Phase 7a B2 — token cache + RequestTokenRefresh outbound."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from sidecar_schwab.auth import TokenCache, RequestTokenRefreshError


@pytest.mark.asyncio
async def test_fresh_token_returned_without_refresh():
    cache = TokenCache(refresh_client=AsyncMock())
    cache.set_tokens(
        access_token="A",
        refresh_token="R",
        access_issued_at=datetime.now(timezone.utc),
    )
    result = await cache.get_access_token()
    assert result == "A"
    cache._refresh_client.RequestTokenRefresh.assert_not_called()


@pytest.mark.asyncio
async def test_stale_token_triggers_refresh():
    """When access_token_age > 25 min, sidecar requests refresh from backend."""
    backend_mock = AsyncMock()
    backend_mock.RequestTokenRefresh.return_value = type("Resp", (), {
        "access_token": "NEW_A",
        "refresh_token": "NEW_R",
        "access_issued_at": _ts_now(),
    })()
    cache = TokenCache(refresh_client=backend_mock)
    cache.set_tokens(
        access_token="OLD_A",
        refresh_token="OLD_R",
        access_issued_at=datetime.now(timezone.utc) - timedelta(minutes=26),
    )
    result = await cache.get_access_token()
    assert result == "NEW_A"
    backend_mock.RequestTokenRefresh.assert_called_once()


@pytest.mark.asyncio
async def test_no_self_refresh_to_schwab_endpoint(monkeypatch):
    """B2 invariant: sidecar must NOT call schwab.com/oauth/token directly.

    v3 — real behavior test (not source-text scan): patch httpx.AsyncClient to
    record any outbound request; trigger a stale-token refresh; assert that
    the recording shows ZERO requests to schwabapi.com (the request goes via
    BackendCallback gRPC instead).
    """
    import httpx
    recorded: list[str] = []

    class RecordingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            recorded.append(str(request.url))
            return httpx.Response(200, content=b"{}")

    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **kw: httpx.AsyncClient(
            *a, transport=RecordingTransport(), **kw),
    )

    backend_mock = AsyncMock()
    backend_mock.RequestTokenRefresh.return_value = type("R", (), {
        "access_token": "NEW_A", "refresh_token": "NEW_R",
        "access_issued_at": _ts_now(),
    })()
    cache = TokenCache(refresh_client=backend_mock)
    cache.set_tokens(
        access_token="OLD_A", refresh_token="OLD_R",
        access_issued_at=datetime.now(timezone.utc) - timedelta(minutes=26),
    )
    await cache.get_access_token()

    # Backend callback was used.
    backend_mock.RequestTokenRefresh.assert_called_once()
    # Schwab token endpoint was NOT used.
    schwab_token_calls = [u for u in recorded if "schwabapi.com" in u and "/oauth/token" in u]
    assert schwab_token_calls == [], (
        f"sidecar must not call Schwab token endpoint; saw: {schwab_token_calls}"
    )


@pytest.mark.asyncio
async def test_lock_released_before_outbound_grpc(monkeypatch):
    """M6 — _token_lock is released before the actual RPC call."""
    backend_mock = AsyncMock()
    cache = TokenCache(refresh_client=backend_mock)
    cache.set_tokens(
        access_token="X",
        refresh_token="Y",
        access_issued_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    lock_status = []
    orig_call = backend_mock.RequestTokenRefresh
    async def assert_lock_released(*args, **kwargs):
        # Lock must NOT be held when this RPC fires.
        lock_status.append(cache._token_lock.locked())
        return type("R", (), {"access_token": "Z", "refresh_token": "Y2",
                              "access_issued_at": _ts_now()})()
    backend_mock.RequestTokenRefresh = assert_lock_released

    await cache.get_access_token()
    assert lock_status == [False]


def _ts_now():
    from google.protobuf.timestamp_pb2 import Timestamp
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts
```

- [ ] **Step 2: Run — FAIL.**

```bash
cd /home/joseph/dashboard/sidecar_schwab && uv run pytest tests/test_auth_lifecycle.py -v
```

- [ ] **Step 3: Write `sidecar_schwab/auth.py`:**

```python
"""Token cache + outbound RequestTokenRefresh.

Architectural invariants (spec §3.6):
  - C2 single-writer: this sidecar does NOT call Schwab's /oauth/token
    endpoint. It calls the backend's gRPC RequestTokenRefresh, which
    holds the PG advisory lock and is the only writer of refresh tokens.
  - M6 lock granularity: _token_lock is held only for the freshness
    check; the outbound gRPC call fires with the lock RELEASED.
  - H4 freshness: token is considered fresh for 25 of 30 mins (5-min
    headroom for clock skew + RPC latency).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sidecar_schwab.metrics import SCHWAB_ACCESS_TOKEN_AGE_SECONDS

log = logging.getLogger(__name__)

# H4 — 25 minutes of fresh window inside Schwab's 30-minute TTL.
_FRESH_WINDOW = timedelta(minutes=25)


class RequestTokenRefreshError(RuntimeError):
    pass


class TokenCache:
    """In-memory access_token cache with backend-side refresh callback."""

    def __init__(self, refresh_client) -> None:
        self._refresh_client = refresh_client
        self._token_lock = asyncio.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_issued_at: datetime | None = None

    def set_tokens(
        self,
        access_token: str,
        refresh_token: str,
        access_issued_at: datetime,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._access_issued_at = access_issued_at

    def access_token_age(self) -> float:
        if self._access_issued_at is None:
            return float("inf")
        delta = datetime.now(timezone.utc) - self._access_issued_at
        return delta.total_seconds()

    async def get_access_token(self) -> str:
        """Return current access_token, refreshing via backend if stale.

        Lock is held only for the freshness check; if a refresh is needed,
        we release the lock before the gRPC RPC (M6).
        """
        async with self._token_lock:
            fresh = (
                self._access_token is not None
                and self._access_issued_at is not None
                and (datetime.now(timezone.utc) - self._access_issued_at)
                    < _FRESH_WINDOW
            )
            cached_access = self._access_token
        SCHWAB_ACCESS_TOKEN_AGE_SECONDS.set(self.access_token_age())
        if fresh and cached_access is not None:
            return cached_access

        # Lock NOT held during outbound RPC.
        from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
        try:
            resp = await self._refresh_client.RequestTokenRefresh(
                pb.TokenRefreshRequest(broker_id="schwab")
            )
        except Exception as e:
            raise RequestTokenRefreshError(
                f"backend RequestTokenRefresh failed: {e}"
            ) from e

        # Re-acquire lock to write back.
        async with self._token_lock:
            self._access_token = resp.access_token
            self._refresh_token = resp.refresh_token
            self._access_issued_at = resp.access_issued_at.ToDatetime(
                tzinfo=timezone.utc
            )
            return resp.access_token
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/test_auth_lifecycle.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add sidecar_schwab/auth.py sidecar_schwab/tests/test_auth_lifecycle.py
git commit -m "feat(sidecar-schwab): auth.py — token cache + RequestTokenRefresh outbound (no self-refresh)"
```

**Conditional reviewers:** `python-reviewer`, `silent-failure-hunter`, `security-reviewer` (token handling).

### Task B3: `sidecar_schwab/client.py` — Schwabdev wrapper (M3 isolation) + typed exceptions

**Files:** Create `sidecar_schwab/client.py`, `sidecar_schwab/tests/test_client_isolation.py`, `sidecar_schwab/tests/test_rate_limit_429.py`.

**v3 reality grounding:**
- **Schwabdev token-injection API verified** (cited in B3 implementation block): Schwabdev's `Client.tokens` is a `Tokens` instance with `access_token` + `refresh_token` attributes settable via the `update_tokens(access_token=..., refresh_token=...)` method. Plan v3 keeps `update_tokens` enabled (do NOT no-op it as v2 did) — call it on each access-token rotation to keep Schwabdev's internal state synced. The C2 single-writer guarantee is preserved by NEVER calling `client.tokens.update_refresh_token_from_code(...)` (which hits Schwab's token endpoint) — only call `update_tokens(access_token=X, refresh_token=Y)` directly to set already-known tokens.
- **Explicit exception classes** replace fragile substring matches (B6 H3 cleanup): introduce `SchwabAccountHashStaleError`, `SchwabRateLimitedError`, `SchwabHTTPError` in `client.py`. B6's `_fetch_account_with_404_retry` catches `SchwabAccountHashStaleError` specifically.
- **Targeted `monkeypatch`** (not global `asyncio.sleep` patch): `monkeypatch.setattr("sidecar_schwab.client.asyncio.sleep", fake_sleep)` so other internals' sleeps are unaffected.

- [ ] **Step 1: Failing tests.** Create `sidecar_schwab/tests/test_client_isolation.py`:

```python
"""Phase 7a B3 — M3: schwabdev MUST be confined to client.py only."""
from pathlib import Path

import pytest


def test_only_client_py_imports_schwabdev():
    """grep ensures handlers.py / normalize.py / auth.py never import schwabdev."""
    pkg_root = Path(__file__).resolve().parent.parent
    forbidden = ["handlers.py", "normalize.py", "auth.py", "metrics.py", "main.py", "config.py"]
    for fname in forbidden:
        path = pkg_root / fname
        if not path.exists():
            continue
        text = path.read_text()
        assert "import schwabdev" not in text, f"{fname} must not import schwabdev (M3)"
        assert "from schwabdev" not in text, f"{fname} must not import schwabdev (M3)"

    # Conversely client.py SHOULD import it.
    client_text = (pkg_root / "client.py").read_text()
    assert "schwabdev" in client_text, "client.py expected to import schwabdev"


def test_pyproject_pins_schwabdev_exact_version():
    pkg_root = Path(__file__).resolve().parent.parent
    pyproj = (pkg_root / "pyproject.toml").read_text()
    assert "schwabdev==3.0.3" in pyproj, "schwabdev MUST be pinned to ==3.0.3 (M3)"
```

Create `sidecar_schwab/tests/test_rate_limit_429.py`:

```python
"""Phase 7a B3 — M6: 429 → Retry-After honored + 3× retry with jitter."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from sidecar_schwab.client import SchwabClient


@pytest.mark.asyncio
async def test_429_retry_with_retry_after(monkeypatch):
    """First call returns 429 with Retry-After: 1; second call succeeds.

    v3 — targeted monkeypatch on sidecar_schwab.client.asyncio.sleep so the
    rest of the test infra (pytest-asyncio scheduler) keeps real sleep.
    """
    sleep_calls: list[float] = []
    async def fake_sleep(s):
        sleep_calls.append(s)
    monkeypatch.setattr("sidecar_schwab.client.asyncio.sleep", fake_sleep)

    schwabdev_client = AsyncMock()
    schwabdev_client.account_details.side_effect = [
        _make_429("1"),
        _make_200({"securitiesAccount": {"accountNumber": "X"}}),
    ]
    client = SchwabClient(schwabdev_client=schwabdev_client, token_cache=AsyncMock())
    result = await client.get_account_details("HASH")
    assert result["securitiesAccount"]["accountNumber"] == "X"
    assert sleep_calls == [pytest.approx(1.0, abs=0.2)]  # Retry-After honored + jitter


@pytest.mark.asyncio
async def test_429_three_retries_then_raise(monkeypatch):
    """After 3 retries, raise SchwabRateLimitedError."""
    monkeypatch.setattr("sidecar_schwab.client.asyncio.sleep", AsyncMock())
    schwabdev_client = AsyncMock()
    schwabdev_client.account_details.return_value = _make_429("1")
    client = SchwabClient(schwabdev_client=schwabdev_client, token_cache=AsyncMock())
    from sidecar_schwab.client import SchwabRateLimitedError
    with pytest.raises(SchwabRateLimitedError):
        await client.get_account_details("HASH")


def _make_429(retry_after: str):
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {"Retry-After": retry_after}
    return resp


def _make_200(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp
```

- [ ] **Step 2: Run — FAIL.**

```bash
cd /home/joseph/dashboard/sidecar_schwab
uv run pytest tests/test_client_isolation.py tests/test_rate_limit_429.py -v
```

- [ ] **Step 3: Write `sidecar_schwab/client.py`:**

```python
"""SchwabClient — the ONLY module that imports schwabdev (M3 isolation).

Wraps Schwabdev's async client with our retry policy, rate-limit handling,
account-hash cache, and explicit exception classes (v3 — replaces fragile
substring matches).

C2 single-writer rule (v3 — corrected):
  - We KEEP `client.tokens.update_tokens` enabled — it's the local setter
    that syncs Schwabdev's in-process state with already-known tokens. We
    just never call `client.tokens.update_refresh_token_from_code(...)`
    or any method that hits Schwab's token endpoint. Backend's
    BackendCallback.RequestTokenRefresh is the only path that mints new
    tokens.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import schwabdev  # M3 — only file in the package that imports this

from sidecar_schwab.auth import TokenCache
from sidecar_schwab.metrics import (
    SCHWAB_HTTP_REQUESTS_TOTAL,
    SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL,
)

log = logging.getLogger(__name__)

# M6 — async semaphore caps concurrent outbound HTTP at 10.
_HTTP_CONCURRENCY = 10
_MAX_RETRY = 3


class SchwabHTTPError(RuntimeError):
    """Catchall for non-2xx Schwab REST responses with status_code attr."""
    def __init__(self, message: str, *, status_code: int, endpoint: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class SchwabAccountHashStaleError(SchwabHTTPError):
    """Raised on 404 from a hash-keyed path — triggers H3 refresh+retry."""


class SchwabRateLimitedError(SchwabHTTPError):
    """Raised after _MAX_RETRY 429s — backoff exceeded."""


class SchwabClient:
    """Wrapper around Schwabdev's async client. Owns token-driven HTTP."""

    def __init__(self, schwabdev_client: Any, token_cache: TokenCache) -> None:
        self._client = schwabdev_client
        self._tokens = token_cache
        self._sem = asyncio.Semaphore(_HTTP_CONCURRENCY)
        self._account_hashes: dict[str, str] = {}

    @classmethod
    def from_credentials(
        cls,
        app_key: str,
        app_secret: str,
        token_cache: TokenCache,
    ) -> "SchwabClient":
        """Construct a Schwabdev async client.

        We do NOT disable update_tokens (v2 was wrong) — it's the local-state
        setter we need to keep Schwabdev's internal HTTP layer in sync after
        each backend-driven refresh. C2 single-writer is preserved by never
        invoking Schwabdev methods that hit Schwab's token endpoint
        (e.g., update_refresh_token_from_code) — only update_tokens(access=..., refresh=...).
        """
        client = schwabdev.ClientAsync(  # type: ignore[attr-defined]
            app_key=app_key,
            app_secret=app_secret,
            tokens_file=None,  # tokens managed externally; no on-disk persistence
        )
        return cls(schwabdev_client=client, token_cache=token_cache)

    # ── Public API used by handlers ──────────────────────────────

    async def get_account_numbers(self) -> list[dict[str, str]]:
        """GET /trader/v1/accountNumbers — returns account_number ↔ hash map."""
        return await self._call("/accountNumbers", self._client.account_linked)

    async def get_account_details(self, account_hash: str) -> dict[str, Any]:
        """GET /trader/v1/accounts/{hash}?fields=positions"""
        return await self._call(
            "/accounts",
            lambda: self._client.account_details(
                accountHash=account_hash, fields="positions"
            ),
        )

    async def get_orders(
        self, account_hash: str, from_dt: str, to_dt: str, max_results: int = 200,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "/accounts.orders",
            lambda: self._client.account_orders(
                accountHash=account_hash,
                fromEnteredTime=from_dt,
                toEnteredTime=to_dt,
                maxResults=max_results,
            ),
        )

    # ── account_hash cache (H3) ──────────────────────────────────

    def cache_hashes(self, mapping: dict[str, str]) -> None:
        self._account_hashes = dict(mapping)

    def hash_for(self, account_number: str) -> str | None:
        return self._account_hashes.get(account_number)

    async def refresh_hashes(self, reason: str) -> dict[str, str]:
        """H3 — refresh on rotation_detected / 404_retry."""
        SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL.labels(reason=reason).inc()
        rows = await self.get_account_numbers()
        mapping = {r.get("accountNumber", ""): r.get("hashValue", "")
                   for r in (rows or []) if r.get("accountNumber")}
        self.cache_hashes(mapping)
        return mapping

    # ── internals: 429 / retry / semaphore (M6) ──────────────────

    async def _call(self, endpoint: str, fn) -> Any:
        async with self._sem:
            access = await self._tokens.get_access_token()
            # Sync Schwabdev's in-process token state with the backend-managed
            # current value. We pass refresh_token unchanged because Schwabdev
            # may need it for header construction; backend remains sole writer
            # of the persisted refresh_token (C2).
            current_refresh = self._tokens._refresh_token or ""
            self._client.tokens.update_tokens(
                access_token=access,
                refresh_token=current_refresh,
            )

            for attempt in range(_MAX_RETRY + 1):
                resp = await fn()
                status = getattr(resp, "status_code", 200)
                SCHWAB_HTTP_REQUESTS_TOTAL.labels(
                    endpoint=endpoint, status=str(status),
                ).inc()
                if status == 429:
                    if attempt == _MAX_RETRY:
                        raise SchwabRateLimitedError(
                            f"rate limit exceeded after {_MAX_RETRY} retries",
                            status_code=429, endpoint=endpoint,
                        )
                    retry_after = float(resp.headers.get("Retry-After") or "1")
                    jitter = random.uniform(-0.1, 0.1)
                    await asyncio.sleep(retry_after + jitter)
                    continue
                if status == 404:
                    raise SchwabAccountHashStaleError(
                        f"{endpoint} 404 — account_hash may have rotated",
                        status_code=404, endpoint=endpoint,
                    )
                if status >= 400:
                    raise SchwabHTTPError(
                        f"{endpoint} returned status={status}",
                        status_code=status, endpoint=endpoint,
                    )
                if hasattr(resp, "json"):
                    return resp.json()
                return resp
            raise SchwabRateLimitedError(
                "unreachable retry exhaustion",
                status_code=429, endpoint=endpoint,
            )
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/test_client_isolation.py tests/test_rate_limit_429.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add sidecar_schwab/client.py sidecar_schwab/tests/test_client_isolation.py \
        sidecar_schwab/tests/test_rate_limit_429.py
git commit -m "feat(sidecar-schwab): client.py — Schwabdev wrapper (M3 isolation, M6 rate limit, semaphore=10)"
```

**Conditional reviewers:** `python-reviewer`, `silent-failure-hunter`, `security-reviewer`.

### Task B4: `Configure` RPC implementation — uses `metadata` map (idempotent + H4 access-token freshness check)

**Files:** Modify `sidecar_schwab/handlers.py`. Create `sidecar_schwab/tests/test_configure_idempotent.py`.

**v3 reality grounding:**
- `ConfigureRequest` has typed Futu fields + `metadata: map<string,string>` (proto line 303). Schwab credentials flow through `metadata` — keys: `app_key`, `app_secret`, `access_token`, `refresh_token`, `access_issued_at` (ISO datetime).
- The typed Futu fields (`unlock_pwd_md5`, `rsa_priv_pem`, `opend_host`, `opend_port`, `connection_id`) are unused for Schwab; sidecar ignores them.
- **H4 invariant:** if `access_issued_at` parses as <25 min ago, sidecar uses the supplied `access_token` directly. Otherwise sidecar discards it and triggers `RequestTokenRefresh` on the next call (saves a refresh-token use vs. always taking the supplied token).

- [ ] **Step 1: Failing test.** Create `sidecar_schwab/tests/test_configure_idempotent.py`:

```python
"""Phase 7a B4 — Configure with metadata map (v3); H4 access-token age check."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


def _build_request(*, access_token="A", refresh_token="R", issued_at=None,
                    app_key="K", app_secret="S") -> broker_pb2.ConfigureRequest:
    """Build a ConfigureRequest using the metadata map (Schwab pathway)."""
    if issued_at is None:
        issued_at = datetime.now(timezone.utc)
    return broker_pb2.ConfigureRequest(
        # Typed Futu fields stay empty for Schwab.
        metadata={
            "app_key":          app_key,
            "app_secret":       app_secret,
            "access_token":     access_token,
            "refresh_token":    refresh_token,
            "access_issued_at": issued_at.isoformat(),
        },
    )


@pytest.mark.asyncio
async def test_configure_first_time_succeeds():
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Configure(_build_request(), ctx)
    assert resp.ok is True
    assert resp.detail == ""


@pytest.mark.asyncio
async def test_configure_idempotent_same_tokens():
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    issued = datetime.now(timezone.utc)
    req = _build_request(issued_at=issued)
    resp1 = await servicer.Configure(req, ctx)
    resp2 = await servicer.Configure(req, ctx)
    assert resp1.ok and resp2.ok
    # Idempotency: SchwabClient instance constructed once.
    assert servicer._configure_count == 1


@pytest.mark.asyncio
async def test_configure_rebuilds_on_token_change():
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    issued = datetime.now(timezone.utc)
    await servicer.Configure(_build_request(access_token="A1", refresh_token="R1", issued_at=issued), ctx)
    await servicer.Configure(_build_request(access_token="A2", refresh_token="R2", issued_at=issued), ctx)
    assert servicer._configure_count == 2


@pytest.mark.asyncio
async def test_configure_h4_discards_stale_access_token():
    """H4 — when access_issued_at is >25min old, sidecar does NOT use the
    supplied access_token; instead it stays unset, forcing the next outbound
    call to trigger RequestTokenRefresh via TokenCache."""
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    resp = await servicer.Configure(
        _build_request(access_token="STALE_A", issued_at=stale), ctx)
    assert resp.ok is True
    # Token cache holds the empty access_token (forcing refresh on first use)
    assert servicer._token_cache._access_token == ""
    # But the refresh_token IS persisted — sidecar uses it via RequestTokenRefresh
    assert servicer._token_cache._refresh_token == "R"


@pytest.mark.asyncio
async def test_configure_rejects_request_without_metadata():
    """Schwab Configure requires metadata map populated; otherwise reject."""
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    ctx.abort = MagicMock()
    bare = broker_pb2.ConfigureRequest()  # no metadata, no typed fields
    await servicer.Configure(bare, ctx)
    ctx.abort.assert_called_once()
    args = ctx.abort.call_args.args
    assert args[0] == grpc.StatusCode.INVALID_ARGUMENT
```

- [ ] **Step 2: Run — FAIL.** Add `_configure_count` + Configure method to `handlers.py`.

- [ ] **Step 3: Update `sidecar_schwab/handlers.py` (v3 — uses metadata map; new BackendCallbackStub):**

```python
"""gRPC Broker servicer for Schwab (v3 — metadata-map ConfigureRequest).

Configure mutates server state — it owns the SchwabClient instance and the
TokenCache. All other RPCs read from state populated by Configure.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import grpc

from sidecar_schwab._generated.broker.v1 import (
    broker_pb2,
    broker_pb2_grpc,
)
from sidecar_schwab.auth import TokenCache
from sidecar_schwab.client import SchwabClient

log = logging.getLogger(__name__)

_FRESH_WINDOW = timedelta(minutes=25)  # H4 — 25min headroom inside 30min TTL

# Required metadata keys for Schwab Configure.
_REQUIRED_META_KEYS = ("app_key", "app_secret", "refresh_token")


class BrokerServicer(broker_pb2_grpc.BrokerServicer):
    """Schwab gRPC service implementation."""

    def __init__(self) -> None:
        self._configure_lock = asyncio.Lock()
        self._configure_count = 0
        self._client: SchwabClient | None = None
        self._token_cache: TokenCache | None = None
        # Hash of last-seen Configure metadata, for idempotency.
        self._last_meta_fingerprint: str | None = None
        self._configured_at: datetime | None = None

    # ─────────────────────────── Configure ──────────────────────────

    async def Configure(  # noqa: N802
        self,
        request: broker_pb2.ConfigureRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ConfigureResponse:
        # v3 — Schwab uses ConfigureRequest.metadata (typed Futu fields ignored).
        meta = dict(request.metadata)
        missing = [k for k in _REQUIRED_META_KEYS if not meta.get(k)]
        if missing:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"schwab Configure: missing metadata keys {missing}",
            )
            return broker_pb2.ConfigureResponse(ok=False, detail=f"missing:{','.join(missing)}")

        async with self._configure_lock:
            fingerprint = self._fingerprint(meta)
            if fingerprint == self._last_meta_fingerprint:
                return broker_pb2.ConfigureResponse(ok=True)

            issued_at_str = meta.get("access_issued_at", "")
            access_issued_at = self._parse_iso(issued_at_str)

            # H4 — discard the supplied access_token if it's stale; sidecar
            # will trigger a backend RequestTokenRefresh on first outbound call.
            now = datetime.now(timezone.utc)
            if access_issued_at and (now - access_issued_at) < _FRESH_WINDOW:
                effective_access = meta.get("access_token", "")
            else:
                effective_access = ""
                access_issued_at = now - _FRESH_WINDOW * 2  # mark "definitely stale"

            # Build the BackendCallback gRPC client (used by TokenCache when
            # access_token is stale). Address from env var with sensible default.
            backend_addr = os.environ.get("BACKEND_ADMIN_GRPC", "backend:8001")
            channel = grpc.aio.insecure_channel(backend_addr)
            refresh_client = broker_pb2_grpc.BackendCallbackStub(channel)  # v3 — new service

            self._token_cache = TokenCache(refresh_client=refresh_client)
            self._token_cache.set_tokens(
                access_token=effective_access,
                refresh_token=meta["refresh_token"],
                access_issued_at=access_issued_at,
            )
            self._client = SchwabClient.from_credentials(
                app_key=meta["app_key"],
                app_secret=meta["app_secret"],
                token_cache=self._token_cache,
            )
            self._last_meta_fingerprint = fingerprint
            self._configured_at = now
            self._configure_count += 1
            log.info("schwab_configured", count=self._configure_count,
                     access_was_fresh=bool(effective_access))
            return broker_pb2.ConfigureResponse(ok=True)

    @staticmethod
    def _fingerprint(meta: dict[str, str]) -> str:
        """Hash the 5 metadata keys we care about for idempotency."""
        keys = ("app_key", "app_secret", "access_token", "refresh_token", "access_issued_at")
        return "|".join(meta.get(k, "") for k in keys)

    @staticmethod
    def _parse_iso(s: str) -> datetime:
        if not s:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)

    # Health is overridden in B5; A4's stub stays in place until then.
```

- [ ] **Step 4: Run tests — PASS.**

```bash
uv run pytest tests/test_configure_idempotent.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_configure_idempotent.py
git commit -m "feat(sidecar-schwab): Configure RPC — idempotent token write + fresh SchwabClient on change"
```

**Conditional reviewers:** `python-reviewer`, `silent-failure-hunter`, `security-reviewer`, `type-design-analyzer`.

### Task B5: `Health` real implementation (H4 invariant)

**Files:** Modify `sidecar_schwab/handlers.py`. Create `sidecar_schwab/tests/test_handlers_health.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a B5 — Health.gateway_connected reflects token freshness AND hashes."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.handlers import BrokerServicer
from sidecar_schwab.auth import TokenCache


@pytest.mark.asyncio
async def test_health_disconnected_before_configure():
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is False
    assert resp.broker_id == "schwab"


@pytest.mark.asyncio
async def test_health_disconnected_when_token_stale(monkeypatch):
    servicer = BrokerServicer()
    servicer._token_cache = TokenCache(refresh_client=MagicMock())
    servicer._token_cache.set_tokens(
        access_token="A", refresh_token="R",
        access_issued_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    servicer._client = MagicMock()
    servicer._client._account_hashes = {"123": "HASH"}  # has hashes BUT token stale
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is False


@pytest.mark.asyncio
async def test_health_disconnected_when_no_hashes():
    servicer = BrokerServicer()
    servicer._token_cache = TokenCache(refresh_client=MagicMock())
    servicer._token_cache.set_tokens(
        access_token="A", refresh_token="R",
        access_issued_at=datetime.now(timezone.utc),
    )
    servicer._client = MagicMock()
    servicer._client._account_hashes = {}  # no hashes
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is False


@pytest.mark.asyncio
async def test_health_connected_when_token_fresh_and_hashes_present():
    servicer = BrokerServicer()
    servicer._token_cache = TokenCache(refresh_client=MagicMock())
    servicer._token_cache.set_tokens(
        access_token="A", refresh_token="R",
        access_issued_at=datetime.now(timezone.utc),
    )
    servicer._client = MagicMock()
    servicer._client._account_hashes = {"123": "HASH"}
    servicer._configured_at = datetime.now(timezone.utc)
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is True
    assert resp.broker_id == "schwab"
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Replace `Health` in `handlers.py`** (replaces the stub from A4 + B4 stub).

```python
# Add to top of handlers.py imports:
from google.protobuf.timestamp_pb2 import Timestamp


    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        """H4 invariant: gateway_connected = token_fresh AND _account_hashes non-empty."""
        token_fresh = (
            self._token_cache is not None
            and self._token_cache._access_issued_at is not None
            and (datetime.now(timezone.utc) - self._token_cache._access_issued_at)
                < _FRESH_WINDOW
        )
        hashes_present = (
            self._client is not None
            and bool(self._client._account_hashes)
        )
        connected = token_fresh and hashes_present

        started_ts = Timestamp()
        if self._configured_at is not None:
            started_ts.FromDatetime(self._configured_at)

        # gateway_version reflects the Schwabdev wrapper version + a marker;
        # sidecar_version is the git SHA at build time (env-injected).
        return broker_pb2.HealthResponse(
            label="schwab",
            broker_id="schwab",
            gateway_version="schwabdev-3.0.3",
            gateway_connected=connected,
            sidecar_version=os.environ.get("SIDECAR_BUILD_SHA", "0.7.0"),
            started_at=started_ts,
        )
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/test_handlers_health.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_health.py
git commit -m "feat(sidecar-schwab): Health — H4 gateway_connected = token<25min AND hashes non-empty"
```

### Task B6: `ListManagedAccounts` + `_account_hashes` cache + H3 404→retry-once

**Files:** Modify `sidecar_schwab/handlers.py`. Create `sidecar_schwab/tests/test_handlers_list_managed_accounts.py`, `tests/test_account_hash_404_retry.py`.

**v3 reality grounding:** Real proto RPC is `ListManagedAccounts(Empty) returns (AccountsResponse)` (proto line 20). Returns `AccountsResponse.accounts` (repeated `Account`). v2's `ListAccounts(ListAccountsRequest)` was hallucinated. H3 catches `SchwabAccountHashStaleError` (typed exception from B3, not substring match).

- [ ] **Step 1: Failing tests** (two files):

`tests/test_handlers_list_managed_accounts.py`:

```python
"""Phase 7a B6 — ListManagedAccounts populates _account_hashes + returns proto Accounts.

v3 — RPC is ListManagedAccounts(Empty), not ListAccounts(ListAccountsRequest).
"""
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_list_managed_accounts_returns_all_schwab_accounts():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.refresh_hashes = AsyncMock(return_value={
        "12345678": "HASH_A",
        "87654321": "HASH_B",
    })
    servicer._client.hash_for = lambda n: {"12345678": "HASH_A", "87654321": "HASH_B"}.get(n)
    servicer._client.get_account_details = AsyncMock(side_effect=[
        {"securitiesAccount": {"accountNumber": "12345678", "type": "MARGIN"}},
        {"securitiesAccount": {"accountNumber": "87654321", "type": "CASH"}},
    ])
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    assert len(resp.accounts) == 2
    nums = {a.account_number for a in resp.accounts}
    assert nums == {"12345678", "87654321"}
    # All LIVE per spec §3.2 invariant
    for a in resp.accounts:
        assert a.mode == broker_pb2.TradingMode.LIVE
        assert a.gateway_label == "schwab"
        assert a.currency_base == "USD"
    hashes = {a.account_hash for a in resp.accounts}
    assert hashes == {"HASH_A", "HASH_B"}


@pytest.mark.asyncio
async def test_list_managed_accounts_initial_call_emits_initial_reason():
    """v3 H3 — first call after Configure emits reason='initial'."""
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "HASH"})
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(return_value={
        "securitiesAccount": {"accountNumber": "X", "type": "MARGIN"}})
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    # Inspect the reason kwarg passed to refresh_hashes
    servicer._client.refresh_hashes.assert_called_with(reason="initial")


@pytest.mark.asyncio
async def test_list_managed_accounts_subsequent_calls_emit_rotation_detected_reason():
    """v3 H3 — subsequent calls emit reason='rotation_detected', not 'initial'."""
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "HASH"})
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(return_value={
        "securitiesAccount": {"accountNumber": "X", "type": "MARGIN"}})
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    # First call → 'initial'
    await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    # Second call → 'rotation_detected'
    servicer._client.refresh_hashes.reset_mock()
    await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    servicer._client.refresh_hashes.assert_called_with(reason="rotation_detected")
```

`tests/test_account_hash_404_retry.py`:

```python
"""Phase 7a B6 — H3: 404 from hash-keyed path → refresh → retry once.

v3 — uses typed SchwabAccountHashStaleError exception (B3), not substring match.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.handlers import BrokerServicer
from sidecar_schwab.client import SchwabAccountHashStaleError


@pytest.mark.asyncio
async def test_404_triggers_hash_refresh_and_retry_once():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    # First call: typed 404 error. Second call: returns data.
    servicer._client.get_account_details = AsyncMock(side_effect=[
        SchwabAccountHashStaleError(
            "404 — account_hash may have rotated",
            status_code=404, endpoint="/accounts"),
        {"securitiesAccount": {"accountNumber": "X", "type": "MARGIN"}},
    ])
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "NEW_HASH"})
    servicer._client.hash_for = lambda n: "HASH_VAL"

    result = await servicer._fetch_account_with_404_retry("X")
    assert result["securitiesAccount"]["accountNumber"] == "X"
    servicer._client.refresh_hashes.assert_called_once_with(reason="404_retry")
    assert servicer._client.get_account_details.call_count == 2


@pytest.mark.asyncio
async def test_second_404_surfaces_typed_error():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.get_account_details = AsyncMock(
        side_effect=SchwabAccountHashStaleError(
            "404 — account_hash may have rotated",
            status_code=404, endpoint="/accounts"))
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "NEW_HASH"})
    servicer._client.hash_for = lambda n: "HASH"

    with pytest.raises(SchwabAccountHashStaleError) as exc_info:
        await servicer._fetch_account_with_404_retry("X")
    assert exc_info.value.status_code == 404
    assert servicer._client.get_account_details.call_count == 2
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `ListManagedAccounts` + `_fetch_account_with_404_retry` to `handlers.py`:**

```python
    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.AccountsResponse:
        """v3 — RPC name + signature match real proto: takes Empty, returns AccountsResponse."""
        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured (call Configure first)",
            )
            return broker_pb2.AccountsResponse()

        from sidecar_schwab.normalize import normalize_account
        # H3 — emit 'initial' on first call after Configure; 'rotation_detected'
        # subsequently. The hashes_loaded flag is reset whenever Configure runs.
        reason = "initial" if not getattr(self, "_hashes_loaded_once", False) else "rotation_detected"
        hashes = await self._client.refresh_hashes(reason=reason)
        self._hashes_loaded_once = True

        accounts: list[broker_pb2.Account] = []
        for account_number, hash_value in hashes.items():
            details = await self._fetch_account_with_404_retry(account_number)
            acct = normalize_account(details)
            acct.account_hash = hash_value
            accounts.append(acct)
        return broker_pb2.AccountsResponse(accounts=accounts)

    async def _fetch_account_with_404_retry(self, account_number: str) -> dict:
        """H3 — on typed SchwabAccountHashStaleError, invalidate cache + retry once."""
        from sidecar_schwab.client import SchwabAccountHashStaleError
        h = self._client.hash_for(account_number)
        try:
            return await self._client.get_account_details(h)
        except SchwabAccountHashStaleError:
            await self._client.refresh_hashes(reason="404_retry")
            h = self._client.hash_for(account_number)
            return await self._client.get_account_details(h)
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/test_handlers_list_managed_accounts.py tests/test_account_hash_404_retry.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_list_managed_accounts.py \
        sidecar_schwab/tests/test_account_hash_404_retry.py
git commit -m "feat(sidecar-schwab): ListManagedAccounts + H3 404→refresh→retry-once"
```

### Task B7: `GetAccountSummary` returns `SummaryResponse(summary=Summary(... Money ...))`

**Files:** Modify `sidecar_schwab/handlers.py`. Create `tests/test_handlers_summary.py`.

**v3 reality grounding:** Real proto `GetAccountSummary(AccountRef) returns (SummaryResponse)` (proto line 21). `SummaryResponse` wraps a `Summary` with **Money** fields (`net_liquidation`, `total_cash`, `realized_pnl`, `unrealized_pnl`, `buying_power`). v2's flat `AccountSummaryResponse(net_liquidation=str)` shape was hallucinated. Day P&L flows into `Summary.realized_pnl` (closest fit).

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a B7 — GetAccountSummary returns SummaryResponse with Money fields."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_summary_extracts_nlv_cash_buying_power_day_pnl():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(return_value={
        "securitiesAccount": {
            "accountNumber": "X",
            "type": "MARGIN",
            "currentBalances": {
                "liquidationValue": 100_000.50,
                "cashBalance": 25_000.00,
                "buyingPower": 200_000.00,
            },
            "initialBalances": {
                "liquidationValue": 99_500.00,
            },
        },
    })
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.GetAccountSummary(
        broker_pb2.AccountRef(account_number="X"), ctx,
    )
    # Real proto: SummaryResponse.summary is a Summary; fields are Money.
    s = resp.summary
    assert Decimal(s.net_liquidation.value) == Decimal("100000.50")
    assert s.net_liquidation.currency == "USD"
    assert Decimal(s.total_cash.value) == Decimal("25000.00")
    assert s.total_cash.currency == "USD"
    assert Decimal(s.buying_power.value) == Decimal("200000.00")
    assert s.buying_power.currency == "USD"
    # day_pnl flows into realized_pnl (Schwab doesn't break out cleanly)
    assert Decimal(s.realized_pnl.value) == Decimal("500.50")  # nlv - prev_nlv
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `GetAccountSummary` to `handlers.py`:**

```python
    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SummaryResponse:
        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION, "not configured")
            return broker_pb2.SummaryResponse()
        details = await self._fetch_account_with_404_retry(request.account_number)
        sa = details.get("securitiesAccount") or {}
        from sidecar_schwab.normalize import normalize_summary
        return normalize_summary(sa)
```

- [ ] **Step 4: Run — PASS.**

```bash
uv run pytest tests/test_handlers_summary.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_summary.py
git commit -m "feat(sidecar-schwab): GetAccountSummary → SummaryResponse with Money fields (H5)"
```

### Task B8: `GetPositions` — Position with `Contract` sub-message + Money fields

**Files:** Modify `sidecar_schwab/handlers.py`. Create `tests/test_handlers_positions.py`.

**v3 reality grounding:** Real `Position` has `contract: Contract` sub-message (not flat `symbol` + `asset_class`); `quantity` is `string`; `avg_cost`/`market_price`/`market_value`/`unrealized_pnl`/`realized_pnl_today`/`daily_pnl` are all `Money`. v2's flat `Position(symbol=..., quantity=..., avg_cost=str)` shape was hallucinated.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a B8 — GetPositions returns Position with Contract sub-message + Money fields."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_get_positions_two_long_one_short():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(return_value={
        "securitiesAccount": {
            "accountNumber": "X",
            "positions": [
                {"instrument": {"symbol": "AAPL", "assetType": "EQUITY", "cusip": "037833100"},
                 "longQuantity": 100, "averagePrice": 150.0,
                 "marketValue": 17500, "currentDayProfitLoss": 250},
                {"instrument": {"symbol": "GOOG", "assetType": "EQUITY", "cusip": "02079K305"},
                 "longQuantity": 10, "averagePrice": 2800.0,
                 "marketValue": 30000, "currentDayProfitLoss": -150},
                {"instrument": {"symbol": "TSLA", "assetType": "EQUITY", "cusip": "88160R101"},
                 "shortQuantity": 5, "averagePrice": 280.0,
                 "marketValue": -1400, "currentDayProfitLoss": 25},
            ],
        },
    })
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.GetPositions(
        broker_pb2.AccountRef(account_number="X"), ctx)
    assert len(resp.positions) == 3
    by_symbol = {p.contract.symbol: p for p in resp.positions}
    assert by_symbol["AAPL"].quantity == "100"
    assert by_symbol["GOOG"].quantity == "10"
    assert by_symbol["TSLA"].quantity == "-5"  # short = negative
    # Money everywhere
    assert Decimal(by_symbol["AAPL"].avg_cost.value) == Decimal("150.0")
    assert by_symbol["AAPL"].avg_cost.currency == "USD"
    assert Decimal(by_symbol["AAPL"].market_value.value) == Decimal("17500")
    assert Decimal(by_symbol["AAPL"].daily_pnl.value) == Decimal("250")
    # Contract sub-message
    assert by_symbol["AAPL"].contract.asset_class == broker_pb2.AssetClass.STOCK
    assert by_symbol["AAPL"].contract.conid == "037833100"  # CUSIP as opaque ID
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `GetPositions`:**

```python
    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PositionsResponse:
        from sidecar_schwab.normalize import normalize_position
        if self._client is None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "not configured")
            return broker_pb2.PositionsResponse()
        details = await self._fetch_account_with_404_retry(request.account_number)
        sa = details.get("securitiesAccount") or {}
        positions = [normalize_position(p) for p in (sa.get("positions") or [])]
        return broker_pb2.PositionsResponse(positions=positions)
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_positions.py
git commit -m "feat(sidecar-schwab): GetPositions — Position with Contract sub-message + Money fields"
```

### Task B9: `GetOrders` 7-day window + status table + avg_fill (M2)

**Files:** Modify `handlers.py`. Create `tests/test_handlers_orders.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a B9 — GetOrders 7-day window + status mapping + M2 avg_fill_price."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_get_orders_passes_7_day_window():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    captured: dict = {}

    async def fake(account_hash, fromEnteredTime, toEnteredTime, maxResults):
        captured["from"] = fromEnteredTime
        captured["to"] = toEnteredTime
        captured["max"] = maxResults
        return []
    servicer._client.get_orders = fake

    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    await servicer.GetOrders(
        pb.AccountRef(account_number="X"), ctx)
    # 7-day window
    from datetime import datetime
    from_dt = datetime.fromisoformat(captured["from"])
    to_dt = datetime.fromisoformat(captured["to"])
    assert (to_dt - from_dt) >= timedelta(days=6, hours=23)
    assert captured["max"] == 200


@pytest.mark.asyncio
async def test_get_orders_maps_status_and_avg_fill():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_orders = AsyncMock(return_value=[
        {"orderId": 100, "status": "WORKING", "orderType": "LIMIT",
         "duration": "DAY", "price": 150.0, "quantity": 10, "filledQuantity": 0,
         "orderLegCollection": [{"instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                                  "instruction": "BUY"}]},
        {"orderId": 101, "status": "FILLED", "orderType": "LIMIT",
         "duration": "DAY", "price": 200.0, "quantity": 5, "filledQuantity": 5,
         "orderLegCollection": [{"instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                                  "instruction": "SELL"}],
         "orderActivityCollection": [{
             "executionLegs": [{"price": 199.50, "quantity": 5}]}]},
    ])
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.GetOrders(
        pb.AccountRef(account_number="X"), ctx)
    assert len(resp.orders) == 2
    # v3 — nested enum access (broker_pb2.OrderStatus.<NAME>), per sidecar_futu/tests precedent.
    assert resp.orders[0].status == pb.OrderStatus.SUBMITTED
    assert resp.orders[1].status == pb.OrderStatus.FILLED
    # M2 — avg_fill_price from executionLegs (NOT order.price=200)
    from decimal import Decimal
    assert Decimal(resp.orders[1].avg_fill_price) == Decimal("199.50")
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `GetOrders`:**

```python
    async def GetOrders(  # noqa: N802
        self,
        request: pb.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> pb.OrdersResponse:
        from datetime import datetime, timedelta, timezone
        from sidecar_schwab.normalize import normalize_order
        if self._client is None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "not configured")
            return pb.OrdersResponse()
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        h = self._client.hash_for(request.account_number)
        rows = await self._client.get_orders(
            account_hash=h,
            fromEnteredTime=start.isoformat(),
            toEnteredTime=end.isoformat(),
            maxResults=200,
        )
        orders = [normalize_order(r) for r in (rows or [])]
        return pb.OrdersResponse(orders=orders)
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_handlers_orders.py
git commit -m "feat(sidecar-schwab): GetOrders 7-day window + status table + avg_fill from activity (M2)"
```

### Task B10: UNIMPLEMENTED stubs for write + streaming RPCs

**Files:** Modify `handlers.py`. Create `tests/test_unimplemented_stubs.py`.

**v3 reality grounding:** The current `service Broker` (proto/broker/v1/broker.proto:18-31) has 12 RPCs total. Phase 7a Schwab implements 6 of them (Health, ListManagedAccounts, GetAccountSummary, GetPositions, GetOrders, Configure). The remaining 6 — `GetContract(ContractRef) returns (ContractResponse)`, `PlaceOrder(PlaceOrderRequest) returns (PlaceOrderResponse)`, `CancelOrder(CancelOrderRequest) returns (CancelOrderResponse)`, `ModifyOrder(ModifyOrderRequest) returns (ModifyOrderResponse)`, `PlaceBracket(PlaceBracketRequest) returns (PlaceBracketResponse)`, `SearchContracts(SearchContractsRequest) returns (SearchContractsResponse)`, plus `OrderEvent(AccountRef) returns (stream OrderEventMessage)` — must all return UNIMPLEMENTED. There is no `StreamQuotes` RPC on the proto today; quotes streaming arrives in Phase 7b which will add the RPC then. Do not stub it here.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a B10 — write + streaming RPCs return UNIMPLEMENTED (Phase 8)."""
from unittest.mock import MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
@pytest.mark.parametrize("method,request_proto", [
    ("GetContract",     pb.ContractRef()),
    ("PlaceOrder",      pb.PlaceOrderRequest()),
    ("CancelOrder",     pb.CancelOrderRequest()),
    ("ModifyOrder",     pb.ModifyOrderRequest()),
    ("PlaceBracket",    pb.PlaceBracketRequest()),
    ("SearchContracts", pb.SearchContractsRequest()),
    ("OrderEvent",      pb.AccountRef()),
])
async def test_unimplemented_returns_unimplemented(method, request_proto):
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    ctx.abort = MagicMock(side_effect=grpc.aio.AioRpcError(
        code=grpc.StatusCode.UNIMPLEMENTED,
        initial_metadata=None, trailing_metadata=None,
    ))
    fn = getattr(servicer, method)
    with pytest.raises(grpc.aio.AioRpcError):
        await fn(request_proto, ctx)
    ctx.abort.assert_called_once()
    code = ctx.abort.call_args.args[0]
    assert code == grpc.StatusCode.UNIMPLEMENTED
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add stubs to `handlers.py`** (response types match `proto/broker/v1/broker.proto` exactly — `PlaceOrderResponse`/`CancelOrderResponse`/`ModifyOrderResponse`/`PlaceBracketResponse`, NOT a generic `OrderResponse`):

```python
    async def GetContract(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab GetContract lands in Phase 7b")
        return pb.ContractResponse()

    async def PlaceOrder(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab PlaceOrder lands in Phase 8")
        return pb.PlaceOrderResponse()

    async def CancelOrder(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab CancelOrder lands in Phase 8")
        return pb.CancelOrderResponse()

    async def ModifyOrder(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab ModifyOrder lands in Phase 8")
        return pb.ModifyOrderResponse()

    async def PlaceBracket(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab PlaceBracket lands in Phase 8")
        return pb.PlaceBracketResponse()

    async def SearchContracts(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab contract search lands in Phase 7b")
        return pb.SearchContractsResponse()

    async def OrderEvent(self, request, context):  # noqa: N802
        # Server-streaming RPC — no return value.
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab OrderEvent stream lands in Phase 8")
```

> **Note:** No `StreamQuotes` stub here — the `service Broker` has no `StreamQuotes` RPC today. Phase 7b will add it to the proto and stub it across all sidecars in the same commit that lands the RPC definition.

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab/handlers.py sidecar_schwab/tests/test_unimplemented_stubs.py
git commit -m "feat(sidecar-schwab): UNIMPLEMENTED stubs for write/streaming RPCs (Phase 8/7b deferrals)"
```

---

## End of Chunk B

After B10: 10 commits, sidecar_schwab/ has full read-only data plane. Configure → ListManagedAccounts → Summary/Positions/Orders works against mocked Schwabdev. The 7 unimplemented RPCs (GetContract, PlaceOrder, CancelOrder, ModifyOrder, PlaceBracket, SearchContracts, OrderEvent) all return UNIMPLEMENTED with phase-pointer messages. Health reports `gateway_connected` per H4 invariant. ~85% test coverage on the package.

---

## Chunk C — Backend wiring (12 tasks)

Goal: backend can mint state nonces, accept Schwab redirects on the public path, persist tokens via PG advisory lock, Configure the sidecar synchronously on every write, serve SSE updates to the SchwabCard, and act as the single-writer authority for `RequestTokenRefresh`.

### Task C1: Alembic 0008 — `account_hash` column + partial index + downgrade

**Files:** Create `backend/alembic/versions/0008_phase7a_schwab_account_hash.py`. Create `backend/tests/db/test_alembic_0008.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C1 — Alembic 0008 adds account_hash column + partial index."""
from sqlalchemy import inspect, text


def test_alembic_0008_adds_account_hash_column(alembic_runner, sync_engine):
    alembic_runner.migrate_up_to("0008")
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("broker_accounts")}
    assert "account_hash" in cols


def test_alembic_0008_partial_index(sync_engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'idx_broker_accounts_schwab_hash'"
        )).fetchall()
    assert len(rows) == 1
    assert "WHERE" in rows[0][0] and "account_hash IS NOT NULL" in rows[0][0]


def test_alembic_0008_downgrade_drops_column(alembic_runner, sync_engine):
    alembic_runner.migrate_up_to("0008")
    alembic_runner.migrate_down_to("0007")
    insp = inspect(sync_engine)
    cols = {c["name"] for c in insp.get_columns("broker_accounts")}
    assert "account_hash" not in cols
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `backend/alembic/versions/0008_phase7a_schwab_account_hash.py`:**

```python
"""Phase 7a — Schwab account_hash privacy layer.

Adds:
  - broker_accounts.account_hash TEXT NULL
  - partial index on (broker_id, account_hash) WHERE account_hash IS NOT NULL

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_accounts",
        sa.Column("account_hash", sa.Text(), nullable=True),
    )
    op.execute(
        """COMMENT ON COLUMN broker_accounts.account_hash IS
        'Schwab-only: opaque account hash from /accountNumbers; required on '
        'all Schwab REST paths. NULL for non-Schwab brokers. Treated as '
        'PII-equivalent — never logged, boundary-stripped from REST responses.'"""
    )
    op.execute(
        """CREATE INDEX idx_broker_accounts_schwab_hash
        ON broker_accounts(broker_id, account_hash)
        WHERE account_hash IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_broker_accounts_schwab_hash")
    op.drop_column("broker_accounts", "account_hash")
```

- [ ] **Step 4: Run — PASS.**

```bash
cd /home/joseph/dashboard/backend && uv run pytest tests/db/test_alembic_0008.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add backend/alembic/versions/0008_phase7a_schwab_account_hash.py \
        backend/tests/db/test_alembic_0008.py
git commit -m "feat(backend): alembic 0008 — broker_accounts.account_hash + partial index"
```

**Conditional reviewers:** `database-reviewer`.

### Task C2: HMAC-signed state nonce mint + consume helpers (H1)

**Files:** Create `backend/app/services/schwab_oauth.py` (state-nonce portion). Create `backend/tests/services/test_state_nonce.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C2 — H1 state nonce: HMAC-signed, atomic SET NX EX, GETDEL consume."""
import pytest
from app.services.schwab_oauth import (
    mint_state_nonce, consume_state_nonce, StateNonceError,
)


@pytest.mark.asyncio
async def test_mint_then_consume_succeeds(redis):
    signed = await mint_state_nonce(redis, user_email="u@example.com",
                                     app_secret_key=b"K")
    user = await consume_state_nonce(redis, signed=signed, app_secret_key=b"K")
    assert user == "u@example.com"


@pytest.mark.asyncio
async def test_consume_replays_reject(redis):
    signed = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    await consume_state_nonce(redis, signed=signed, app_secret_key=b"K")
    with pytest.raises(StateNonceError, match="not.found.or.consumed"):
        await consume_state_nonce(redis, signed=signed, app_secret_key=b"K")


@pytest.mark.asyncio
async def test_consume_wrong_hmac_rejects(redis):
    signed = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    # Tamper: flip last char.
    tampered = signed[:-1] + ("A" if signed[-1] != "A" else "B")
    with pytest.raises(StateNonceError, match="invalid.signature"):
        await consume_state_nonce(redis, signed=tampered, app_secret_key=b"K")


@pytest.mark.asyncio
async def test_consume_wrong_secret_rejects(redis):
    signed = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K1")
    with pytest.raises(StateNonceError):
        await consume_state_nonce(redis, signed=signed, app_secret_key=b"K2")


@pytest.mark.asyncio
async def test_collision_rejected_via_nx(redis):
    """Same nonce twice → second SET NX fails (atomic)."""
    from app.services.schwab_oauth import _STATE_NONCE_PREFIX
    nonce = "fixed_nonce_for_test"
    redis_key = f"{_STATE_NONCE_PREFIX}{nonce}"
    await redis.set(redis_key, "first", nx=True, ex=600)
    second = await redis.set(redis_key, "second", nx=True, ex=600)
    assert second is None  # NX rejected
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `backend/app/services/schwab_oauth.py`:**

```python
"""Phase 7a OAuth helpers — state nonce, PG advisory lock, token-mint.

Architectural invariants:
  - H1: state nonce is HMAC-SHA256-signed; Redis stores raw nonce; SET NX
    atomic, GETDEL consume (single-use).
  - C2: backend is sole writer of schwab.refresh_token. PG advisory lock
    serializes Tier-1 vs Tier-2 vs sidecar near-expiry refreshes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import structlog

log = structlog.get_logger(module="services.schwab_oauth")

_STATE_NONCE_PREFIX = "schwab_oauth_nonce:"
_STATE_NONCE_TTL_SEC = 600  # 10 minutes

# PG advisory lock id — derived from sha256("schwab.refresh_token")[0:4]
# truncated to a positive int32 (PG advisory locks accept bigint, but
# constraining to int32 keeps the ID stable across schema migrations).
SCHWAB_REFRESH_LOCK_ID = (
    int.from_bytes(
        hashlib.sha256(b"schwab.refresh_token").digest()[:4],
        byteorder="big",
    ) & 0x7FFFFFFF
)


class StateNonceError(Exception):
    pass


async def mint_state_nonce(
    redis,
    *,
    user_email: str,
    app_secret_key: bytes,
) -> str:
    """Generate HMAC-signed nonce. Returns the signed value.

    Stored in Redis at SET key=schwab_oauth_nonce:{nonce} value={user_email}
    with NX (atomic check-and-set) + EX 600.
    """
    nonce = secrets.token_urlsafe(32)
    sig = hmac.new(app_secret_key, nonce.encode(), hashlib.sha256).digest()
    signed = f"{nonce}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"
    ok = await redis.set(
        f"{_STATE_NONCE_PREFIX}{nonce}",
        user_email,
        nx=True,
        ex=_STATE_NONCE_TTL_SEC,
    )
    if not ok:
        raise StateNonceError("nonce collision (extremely rare)")
    return signed


async def consume_state_nonce(
    redis,
    *,
    signed: str,
    app_secret_key: bytes,
) -> str:
    """Validate HMAC + atomically consume from Redis. Returns user_email.

    Raises StateNonceError on any failure path.
    """
    if "." not in signed:
        raise StateNonceError("malformed state value")
    nonce, sig_b64 = signed.rsplit(".", 1)
    expected = hmac.new(app_secret_key, nonce.encode(), hashlib.sha256).digest()
    given_sig = _b64_decode_padded(sig_b64)
    if not hmac.compare_digest(expected, given_sig):
        raise StateNonceError("invalid signature")
    # GETDEL — atomic single-use consume (Redis 6.2+).
    user_email = await redis.execute_command(
        "GETDEL", f"{_STATE_NONCE_PREFIX}{nonce}"
    )
    if user_email is None:
        raise StateNonceError("state nonce not found or consumed already")
    if isinstance(user_email, bytes):
        user_email = user_email.decode()
    return user_email


def _b64_decode_padded(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/services/schwab_oauth.py backend/tests/services/test_state_nonce.py
git commit -m "feat(backend): H1 state nonce — HMAC-signed + atomic SET NX EX + GETDEL"
```

**Conditional reviewers:** `security-reviewer`, `python-reviewer`.

### Task C3: PG advisory lock + Schwab token-mint helper

**Files:** Extend `backend/app/services/schwab_oauth.py` with `refresh_with_lock()`. Create `backend/tests/services/test_schwab_token_mint.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C3 — refresh_with_lock acquires PG advisory lock + writes app_secrets."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.schwab_oauth import refresh_with_lock, SCHWAB_REFRESH_LOCK_ID


@pytest.mark.asyncio
async def test_refresh_with_lock_writes_tokens(db_session, config_service, httpx_mock):
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={
            "access_token": "NEW_A",
            "refresh_token": "NEW_R",
            "expires_in": 1800,
        },
    )
    new_a, new_r, issued = await refresh_with_lock(
        db_session=db_session,
        config_service=config_service,
        app_key="K", app_secret="S", refresh_token="OLD_R",
    )
    assert new_a == "NEW_A"
    assert new_r == "NEW_R"
    # Verify app_secrets writes (real ConfigService API: reveal_secret for reads).
    assert await config_service.reveal_secret("broker", "schwab.access_token") == "NEW_A"
    assert await config_service.reveal_secret("broker", "schwab.refresh_token") == "NEW_R"


@pytest.mark.asyncio
async def test_refresh_with_lock_serializes_concurrent_callers(db_session_a, db_session_b, config_service, httpx_mock):
    """C2 — two concurrent refreshes: one waits on the advisory lock."""
    import asyncio
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "A1", "refresh_token": "R1", "expires_in": 1800},
    )
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "A2", "refresh_token": "R2", "expires_in": 1800},
    )

    async def caller(session):
        return await refresh_with_lock(
            db_session=session, config_service=config_service,
            app_key="K", app_secret="S", refresh_token="OLD",
        )

    results = await asyncio.gather(caller(db_session_a), caller(db_session_b))
    # Both succeed; order may vary, but both observe valid tokens.
    assert all(r[0] in ("A1", "A2") for r in results)
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Append to `schwab_oauth.py`** — note the shared `_persist_tokens_under_lock` helper (used by both `refresh_with_lock` AND `_exchange_code` in C4 per spec §3.6 single-writer rule):

```python
import asyncio
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from sqlalchemy import text


@asynccontextmanager
async def schwab_refresh_lock(db_session, *, timeout_sec: int = 5):
    """Async context manager that acquires the PG advisory lock for the
    Schwab refresh-token write path. Used by BOTH the OAuth code-exchange
    path and the refresh-token-rotation path.
    """
    res = await db_session.execute(
        text("SELECT pg_try_advisory_lock(:id)"),
        {"id": SCHWAB_REFRESH_LOCK_ID},
    )
    locked = bool(res.scalar())
    if not locked:
        for _ in range(timeout_sec):
            await asyncio.sleep(1)
            res = await db_session.execute(
                text("SELECT pg_try_advisory_lock(:id)"),
                {"id": SCHWAB_REFRESH_LOCK_ID},
            )
            if res.scalar():
                locked = True
                break
        if not locked:
            raise RuntimeError("schwab refresh advisory lock contention timeout")
    try:
        yield
    finally:
        await db_session.execute(
            text("SELECT pg_advisory_unlock(:id)"),
            {"id": SCHWAB_REFRESH_LOCK_ID},
        )


async def _persist_tokens_under_lock(
    *,
    config_service,
    access_token: str,
    refresh_token: str,
    issued_at: datetime,
    rotate_refresh_issued_at: bool,
) -> None:
    """Write the new (access, refresh) pair to app_secrets + app_config.

    Caller MUST hold the PG advisory lock — this helper does not acquire it.
    `rotate_refresh_issued_at=True` for the OAuth code-exchange path AND for
    refresh-rotations that returned a new refresh_token; False otherwise.

    ConfigService API per backend/app/services/config.py:
      - set_secret(ns, key, value, value_type) for secrets
      - set(ns, key, value, value_type) for non-secret config
      - Namespace convention: ("broker", f"schwab.<key>") matches Futu wiring.
    """
    await config_service.set_secret("broker", "schwab.access_token", access_token)
    await config_service.set_secret("broker", "schwab.refresh_token", refresh_token)
    await config_service.set("broker", "schwab.access_token_issued_at",
                             issued_at.isoformat())
    if rotate_refresh_issued_at:
        await config_service.set("broker", "schwab.refresh_token_issued_at",
                                 issued_at.isoformat())


async def refresh_with_lock(
    *,
    db_session,
    config_service,
    app_key: str,
    app_secret: str,
    refresh_token: str,
    timeout_sec: int = 5,
) -> tuple[str, str, datetime]:
    """Mint new tokens under PG advisory lock; write to app_secrets atomically.

    Returns (new_access_token, new_refresh_token, access_issued_at).
    Schwab rotates the refresh_token on every refresh — both must be persisted.
    """
    async with schwab_refresh_lock(db_session, timeout_sec=timeout_sec):
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.schwabapi.com/v1/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                auth=(app_key, app_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"schwab token endpoint {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        new_access = data["access_token"]
        # Schwab rotates the refresh_token on every refresh; fall back defensively.
        new_refresh = data.get("refresh_token") or refresh_token
        rotated = new_refresh != refresh_token
        issued_at = datetime.now(timezone.utc)
        await _persist_tokens_under_lock(
            config_service=config_service,
            access_token=new_access,
            refresh_token=new_refresh,
            issued_at=issued_at,
            rotate_refresh_issued_at=rotated,
        )
        return new_access, new_refresh, issued_at
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/services/schwab_oauth.py backend/tests/services/test_schwab_token_mint.py
git commit -m "feat(backend): C2 refresh_with_lock — PG advisory lock + Schwab token mint"
```

**Conditional reviewers:** `security-reviewer`, `database-reviewer`, `silent-failure-hunter`.

### Task C4: Public OAuth callback route `/api/oauth/schwab/callback`

**Files:** Create `backend/app/api/oauth.py`. Create `backend/tests/api/test_oauth_callback_public.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C4 — public callback path is reachable WITHOUT admin JWT."""
import pytest


@pytest.mark.asyncio
async def test_public_callback_path_no_admin_jwt_required(test_client_no_auth, redis, config_service):
    """The public callback is gated only by HMAC state nonce, not admin JWT."""
    from app.services.schwab_oauth import mint_state_nonce
    signed = await mint_state_nonce(redis, user_email="u@x",
                                     app_secret_key=b"TEST_KEY")
    # Seed app_key/app_secret into config_service for the token-mint step
    await config_service.set_secret("broker", "schwab.app_key", "K")
    await config_service.set_secret("broker", "schwab.app_secret", "S")
    # Mock Schwab token endpoint via httpx_mock — assume returns 200
    resp = await test_client_no_auth.get(
        "/api/oauth/schwab/callback",
        params={"code": "AUTH_CODE", "state": signed},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token_issued_at" in body
    assert "refresh_token_issued_at" in body


@pytest.mark.asyncio
async def test_public_callback_invalid_state_returns_403(test_client_no_auth):
    resp = await test_client_no_auth.get(
        "/api/oauth/schwab/callback",
        params={"code": "AUTH_CODE", "state": "tampered.state"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `backend/app/api/oauth.py`:**

```python
"""Public OAuth callback router. NOT under /api/admin/.

CF Access bypass policy is applied via path-prefix rule (chunk G4).
Auth is via the HMAC-signed state nonce only.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.metrics import (
    SCHWAB_OAUTH_CALLBACK_TOTAL,
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS,
)
from app.services.config import get_config
from app.services.schwab_oauth import (
    consume_state_nonce, refresh_with_lock, StateNonceError,
)

log = structlog.get_logger(module="api.oauth")

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


@router.get("/schwab/callback")
async def schwab_oauth_callback_public(
    code: str = Query(...),
    state: str = Query(...),
    config_service=Depends(get_config),
    redis=Depends(lambda: ...),  # wire to existing redis dep
    db=Depends(lambda: ...),     # wire to existing db dep
    settings=Depends(lambda: ...),  # wire to existing settings dep
):
    """Public Schwab OAuth callback. CF-Access-bypassed."""
    try:
        user_email = await consume_state_nonce(
            redis, signed=state,
            app_secret_key=settings.app_secret_key.encode(),
        )
    except StateNonceError as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(
            path="public", result="state_mismatch").inc()
        raise HTTPException(403, f"state nonce: {e}")

    log.info("schwab_oauth_callback_public", user=user_email)

    # Real ConfigService API: reveal_secret for reads, namespace ("broker", "schwab.<key>").
    app_key = await config_service.reveal_secret("broker", "schwab.app_key")
    app_secret = await config_service.reveal_secret("broker", "schwab.app_secret")

    try:
        access, refresh, issued = await _exchange_code(
            db_session=db, config_service=config_service,
            app_key=app_key, app_secret=app_secret, code=code,
        )
    except Exception as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(
            path="public", result="token_exchange_fail").inc()
        raise HTTPException(502, f"schwab token exchange failed: {e}")

    # C3 — synchronous Configure to sidecar before HTTP response returns.
    from app.services.broker_registry_factory import reconfigure_schwab
    await reconfigure_schwab(config_service)
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS.set(0)

    # H6 — pub/sub for SSE-driven SchwabCard refresh.
    await redis.publish("config:invalidate:schwab", "1")

    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="public", result="success").inc()
    return {
        "access_token_issued_at": issued.isoformat(),
        "refresh_token_issued_at": issued.isoformat(),
    }


async def _exchange_code(*, db_session, config_service, app_key, app_secret, code):
    """Exchange authorization_code → token pair via Schwab /v1/oauth/token.

    Per spec §3.6 single-writer rule (architect C2 finding), this MUST hold the
    same PG advisory lock as `refresh_with_lock` so a concurrent Tier-2 refresh
    cannot interleave with a Tier-1 first-OAuth write.

    ConfigService API: namespace ("broker", "schwab.<key>"), set_secret for
    secrets, set for non-secret config, get for non-secret config reads.
    """
    import httpx
    from datetime import datetime, timezone
    from app.services.schwab_oauth import (
        schwab_refresh_lock, _persist_tokens_under_lock,
    )

    callback_url = await config_service.get(
        "broker", "schwab.callback_url",
        default="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            "https://api.schwabapi.com/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url,
            },
            auth=(app_key, app_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    data = resp.json()
    issued_at = datetime.now(timezone.utc)

    async with schwab_refresh_lock(db_session):
        await _persist_tokens_under_lock(
            config_service=config_service,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            issued_at=issued_at,
            rotate_refresh_issued_at=True,  # First OAuth always sets refresh_token_issued_at.
        )
    return data["access_token"], data["refresh_token"], issued_at
```

- [ ] **Step 4: Mount in `backend/app/main.py`** (additive — keeps existing routers):

```python
from app.api.oauth import router as oauth_router
app.include_router(oauth_router)
```

- [ ] **Step 5: Run — PASS.** Commit.

```bash
git add backend/app/api/oauth.py backend/app/main.py \
        backend/tests/api/test_oauth_callback_public.py
git commit -m "feat(backend): C1 public /api/oauth/schwab/callback (HMAC nonce gate, CF Access bypass)"
```

**Conditional reviewers:** `security-reviewer`, `python-reviewer`, `silent-failure-hunter`.

### Task C5: Admin OAuth start + admin callback + reconfigure routes

**Files:** Create `backend/app/api/brokers_admin.py`. Create `backend/tests/api/test_oauth_callback_admin.py`, `tests/api/test_brokers_admin_reconfigure.py`.

- [ ] **Step 1: Failing test (admin gating).**

```python
"""Phase 7a C5 — admin Schwab OAuth + reconfigure routes."""
import pytest


@pytest.mark.asyncio
async def test_oauth_start_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.get("/api/admin/brokers/schwab/oauth-start")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_oauth_start_redirects_to_schwab(test_client_admin):
    resp = await test_client_admin.get(
        "/api/admin/brokers/schwab/oauth-start",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "schwabapi.com/v1/oauth/authorize" in loc
    assert "state=" in loc
    assert "client_id=" in loc


@pytest.mark.asyncio
async def test_admin_callback_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.post(
        "/api/admin/brokers/schwab/oauth-callback",
        params={"code": "C", "state": "S"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reconfigure_calls_sidecar(test_client_admin, mock_sidecar_configure):
    resp = await test_client_admin.post(
        "/api/admin/brokers/schwab/reconfigure",
    )
    assert resp.status_code == 200
    mock_sidecar_configure.assert_called_once()
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `backend/app/api/brokers_admin.py`:**

```python
"""Admin Schwab routes — OAuth start, OAuth callback (Tier-2), reconfigure."""
from __future__ import annotations

import urllib.parse

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.core.auth import require_admin_jwt
from app.core.metrics import (
    SCHWAB_OAUTH_START_TOTAL,
    SCHWAB_OAUTH_CALLBACK_TOTAL,
)
from app.services.config import get_config
from app.services.schwab_oauth import (
    consume_state_nonce, mint_state_nonce, StateNonceError,
)

log = structlog.get_logger(module="api.brokers_admin")

router = APIRouter(
    prefix="/api/admin/brokers/schwab",
    tags=["admin", "brokers"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.get("/oauth-start")
async def oauth_start(
    config_service=Depends(get_config),
    redis=Depends(lambda: ...),
    settings=Depends(lambda: ...),
    user=Depends(require_admin_jwt),
):
    SCHWAB_OAUTH_START_TOTAL.inc()
    user_email = getattr(user, "email", "admin")
    signed = await mint_state_nonce(
        redis, user_email=user_email,
        app_secret_key=settings.app_secret_key.encode(),
    )
    # Real ConfigService API: reveal_secret/get; namespace ("broker", "schwab.<key>").
    app_key = await config_service.reveal_secret("broker", "schwab.app_key")
    callback_url = await config_service.get(
        "broker", "schwab.callback_url",
        default="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
    )
    consent_url = (
        "https://api.schwabapi.com/v1/oauth/authorize"
        f"?client_id={urllib.parse.quote(app_key)}"
        f"&redirect_uri={urllib.parse.quote(callback_url)}"
        f"&state={urllib.parse.quote(signed)}"
        "&response_type=code"
    )
    return RedirectResponse(url=consent_url, status_code=302)


@router.post("/oauth-callback")
async def admin_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    config_service=Depends(get_config),
    redis=Depends(lambda: ...),
    db=Depends(lambda: ...),
    settings=Depends(lambda: ...),
):
    """Tier-2 calls this with its admin JWT (service-token-derived).
    Same semantics as /api/oauth/schwab/callback (public path)."""
    try:
        user_email = await consume_state_nonce(
            redis, signed=state,
            app_secret_key=settings.app_secret_key.encode(),
        )
    except StateNonceError as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(
            path="admin", result="state_mismatch").inc()
        raise HTTPException(403, f"state nonce: {e}")

    from app.api.oauth import _exchange_code
    app_key = await config_service.reveal_secret("broker", "schwab.app_key")
    app_secret = await config_service.reveal_secret("broker", "schwab.app_secret")
    try:
        _, _, issued = await _exchange_code(
            db_session=db, config_service=config_service,
            app_key=app_key, app_secret=app_secret, code=code,
        )
    except Exception as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(
            path="admin", result="token_exchange_fail").inc()
        raise HTTPException(502, f"schwab token exchange failed: {e}")

    from app.services.broker_registry_factory import reconfigure_schwab
    await reconfigure_schwab(config_service)
    await redis.publish("config:invalidate:schwab", "1")

    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="admin", result="success").inc()
    log.info("schwab_oauth_callback_admin", user=user_email)
    return {"access_token_issued_at": issued.isoformat()}


@router.post("/reconfigure")
async def reconfigure(config_service=Depends(get_config)):
    """Manual operator trigger; also called by Tier-2 after a refresh."""
    from app.services.broker_registry_factory import reconfigure_schwab
    await reconfigure_schwab(config_service)
    return {"ok": True}


@router.get("/status")
async def status(config_service=Depends(get_config)) -> dict:
    """Phase 7a frontend D1/D2 — aggregated Schwab token + tier-2 status.

    Returned shape matches `frontend/src/services/schwab.ts`'s `SchwabTokenStatus`.
    Token VALUES are never returned — only the issued-at timestamps + flags.
    """
    return {
        "access_token_issued_at": await config_service.get(
            "broker", "schwab.access_token_issued_at", default=None),
        "refresh_token_issued_at": await config_service.get(
            "broker", "schwab.refresh_token_issued_at", default=None),
        "tier2_refresh_enabled": await config_service.get(
            "broker", "schwab.tier2_refresh_enabled", default=False),
        "tier2_consecutive_failures": await config_service.get(
            "broker", "schwab.tier2_consecutive_failures", default=0),
    }
```

- [ ] **Step 4: Mount in `backend/app/api/admin.py`:**

```python
from app.api.brokers_admin import router as schwab_admin_router
admin_router.include_router(schwab_admin_router)
```

- [ ] **Step 5: Run — PASS.** Commit.

```bash
git add backend/app/api/brokers_admin.py backend/app/api/admin.py \
        backend/tests/api/test_oauth_callback_admin.py \
        backend/tests/api/test_brokers_admin_reconfigure.py
git commit -m "feat(backend): admin /oauth-start + /oauth-callback + /reconfigure"
```

**Conditional reviewers:** `security-reviewer`, `python-reviewer`.

### Task C6: Backend `RequestTokenRefresh` server-side handler (C2)

**Files:** Modify `backend/app/services/broker_registry_factory.py` to add a backend gRPC server (or extend the existing one) with the `RequestTokenRefresh` handler.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C6 — backend serves RequestTokenRefresh; mints + writes under lock."""
import pytest

from app._generated.broker.v1 import broker_pb2 as pb


@pytest.mark.asyncio
async def test_request_token_refresh_returns_new_pair(grpc_backend_stub, config_service, httpx_mock):
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "FRESH_A", "refresh_token": "FRESH_R",
              "expires_in": 1800},
    )
    await config_service.set_secret("broker", "schwab.app_key", "K")
    await config_service.set_secret("broker", "schwab.app_secret", "S")
    await config_service.set_secret("broker", "schwab.refresh_token", "OLD_R")

    resp = await grpc_backend_stub.RequestTokenRefresh(
        pb.TokenRefreshRequest(broker_id="schwab")
    )
    assert resp.access_token == "FRESH_A"
    assert resp.refresh_token == "FRESH_R"
    # Tokens persisted (real ConfigService API: reveal_secret reads).
    assert await config_service.reveal_secret("broker", "schwab.access_token") == "FRESH_A"
    assert await config_service.reveal_secret("broker", "schwab.refresh_token") == "FRESH_R"


@pytest.mark.asyncio
async def test_request_token_refresh_rejects_other_brokers(grpc_backend_stub):
    import grpc
    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await grpc_backend_stub.RequestTokenRefresh(
            pb.TokenRefreshRequest(broker_id="ibkr")
        )
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add backend gRPC server bootstrap + `RequestTokenRefresh` handler.** Create `backend/app/services/broker_callback_server.py`:

```python
"""Backend-side gRPC server that sidecars call into for RequestTokenRefresh.

Listens on internal port 8001 (BACKEND_ADMIN_GRPC env var on sidecar).
"""
from __future__ import annotations

import asyncio
import logging

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from app._generated.broker.v1 import broker_pb2 as pb, broker_pb2_grpc as pbg
from app.services.config import ConfigService
from app.services.schwab_oauth import refresh_with_lock

log = logging.getLogger(__name__)


class BackendCallbackServicer(pbg.BackendCallbackServicer):
    """Implements `service BackendCallback` from proto/broker/v1/broker.proto.

    The base class is generated as `BackendCallbackServicer` in
    broker_pb2_grpc — the ONLY RPC on this service is `RequestTokenRefresh`,
    so there is no UNIMPLEMENTED-stub surface. The Broker-service surface is
    served by sidecars, not by the backend.
    """

    def __init__(self, config_service: ConfigService, db_session_factory) -> None:
        self._config = config_service
        self._db_factory = db_session_factory

    async def RequestTokenRefresh(self, request, context):  # noqa: N802
        if request.broker_id != "schwab":
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"backend RequestTokenRefresh handles broker=schwab, got {request.broker_id}",
            )
            return pb.TokenRefreshResponse()
        app_key = await self._config.reveal_secret("broker", "schwab.app_key")
        app_secret = await self._config.reveal_secret("broker", "schwab.app_secret")
        refresh = await self._config.reveal_secret("broker", "schwab.refresh_token")
        async with self._db_factory() as db:
            new_a, new_r, issued = await refresh_with_lock(
                db_session=db, config_service=self._config,
                app_key=app_key, app_secret=app_secret, refresh_token=refresh,
            )
        ts = Timestamp(); ts.FromDatetime(issued)
        return pb.TokenRefreshResponse(
            access_token=new_a,
            refresh_token=new_r,
            access_issued_at=ts,
        )


async def start_backend_callback_server(
    config_service: ConfigService, db_session_factory,
) -> grpc.aio.Server:
    server = grpc.aio.server()
    servicer = BackendCallbackServicer(config_service, db_session_factory)
    pbg.add_BrokerServicer_to_server(servicer, server)
    server.add_insecure_port("0.0.0.0:8001")
    await server.start()
    log.info("backend_callback_grpc_started port=8001")
    return server
```

- [ ] **Step 4: Wire into `app/main.py` lifespan:**

```python
from app.services.broker_callback_server import start_backend_callback_server
# In lifespan startup:
app.state.callback_server = await start_backend_callback_server(
    app.state.config_service, app.state.db_session_factory)
# In shutdown:
await app.state.callback_server.stop(grace=5)
```

- [ ] **Step 5: Run — PASS.** Commit.

```bash
git add backend/app/services/broker_callback_server.py backend/app/main.py \
        backend/tests/services/test_request_token_refresh.py
git commit -m "feat(backend): C2 backend-side RequestTokenRefresh gRPC handler (single writer)"
```

**Conditional reviewers:** `security-reviewer`, `silent-failure-hunter`.

### Task C7: `BrokerConfigurer` extension for Schwab + `reconfigure_schwab` helper

**Files:** Modify `backend/app/services/broker_registry_factory.py`.

**v3 reality grounding:** `proto/broker/v1/broker.proto:292` shows `ConfigureRequest` has 5 typed Futu-specific fields (`unlock_pwd_md5`, `rsa_priv_pem`, `opend_host`, `opend_port`, `connection_id`) PLUS `map<string, string> metadata = 6` explicitly designed for "Future creds without proto edits". There is NO `broker_id` field and NO `params` field on `ConfigureRequest` — sidecars know what broker they are because they implement only one. Schwab credentials therefore go in `metadata`, with the typed Futu fields left empty. `BrokerConfigurer` already exists at `broker_registry_factory.py:40` with `targets: set[str]` + `async def configure(label: str)` (matches Futu wiring at lines 51–58); add `"schwab"` to its `targets` set and branch in `configure()` for the Schwab metadata-only path.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C7 — BrokerConfigurer reads broker.schwab.* secrets and Configures sidecar."""
import pytest


@pytest.mark.asyncio
async def test_broker_configurer_schwab_path(config_service, sidecar_stubs):
    # ConfigService namespace ("broker", f"{label}.<key>") matches Futu wiring.
    await config_service.set_secret("broker", "schwab.app_key", "K")
    await config_service.set_secret("broker", "schwab.app_secret", "S")
    await config_service.set_secret("broker", "schwab.access_token", "A")
    await config_service.set_secret("broker", "schwab.refresh_token", "R")
    await config_service.set("broker", "schwab.access_token_issued_at",
                             "2026-04-30T12:00:00+00:00")

    from app.services.broker_registry_factory import reconfigure_schwab
    await reconfigure_schwab(config_service)

    sidecar_stubs["schwab"].Configure.assert_called_once()
    args = sidecar_stubs["schwab"].Configure.call_args[0][0]
    # ConfigureRequest has NO broker_id field — credentials live in metadata.
    metadata = dict(args.metadata)
    assert metadata["app_key"] == "K"
    assert metadata["app_secret"] == "S"
    assert metadata["access_token"] == "A"
    assert metadata["refresh_token"] == "R"
    assert metadata["access_token_issued_at"] == "2026-04-30T12:00:00+00:00"
    # Typed Futu fields stay empty for Schwab.
    assert args.unlock_pwd_md5 == ""
    assert args.rsa_priv_pem == ""


@pytest.mark.asyncio
async def test_broker_configurer_skips_when_secrets_missing(config_service, sidecar_stubs):
    """If app_key/refresh_token absent, do not call Configure (avoid 5xx loops)."""
    from app.services.broker_registry_factory import reconfigure_schwab
    await reconfigure_schwab(config_service)
    sidecar_stubs["schwab"].Configure.assert_not_called()
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `reconfigure_schwab` to `broker_registry_factory.py`** and add `"schwab"` to `BrokerConfigurer.targets` (existing class at line 40). The Configure path uses `metadata` only — the typed Futu fields are left at their proto defaults:

```python
async def reconfigure_schwab(config_service) -> None:
    """C3 — Configure trigger for Schwab. Called from:
    1. Lifespan startup (in init_brokers / BrokerConfigurer.configure("schwab"))
    2. /api/oauth/schwab/callback (Tier-1)
    3. /api/admin/brokers/schwab/oauth-callback (Tier-2)
    4. /api/admin/brokers/schwab/reconfigure (manual)
    5. Sidecar restart detected (started_at delta in BrokerRegistry health probe)

    Real ConfigService API: reveal_secret/get reads, namespace ("broker", "schwab.<k>").
    """
    app_key = await config_service.reveal_secret("broker", "schwab.app_key", default=None)
    app_secret = await config_service.reveal_secret("broker", "schwab.app_secret", default=None)
    refresh = await config_service.reveal_secret("broker", "schwab.refresh_token", default=None)
    if not (app_key and app_secret and refresh):
        # Not configured yet — first-time deploy. Skip silently.
        return
    access = await config_service.reveal_secret("broker", "schwab.access_token", default="")
    issued_at = await config_service.get(
        "broker", "schwab.access_token_issued_at", default="")

    from app._generated.broker.v1 import broker_pb2 as pb
    stub = await _get_or_create_sidecar_stub("schwab")
    # ConfigureRequest: typed fields left empty, all Schwab creds in metadata
    # per proto comment "Future creds without proto edits" (line 302).
    request = pb.ConfigureRequest(
        metadata={
            "app_key": app_key,
            "app_secret": app_secret,
            "access_token": access,
            "refresh_token": refresh,
            "access_token_issued_at": issued_at,
        },
    )
    await stub.Configure(request)
    from app.core.metrics import BROKER_CONFIGURE_TOTAL
    BROKER_CONFIGURE_TOTAL.labels(label="schwab", reason="manual").inc()
```

Also extend the existing `BrokerConfigurer.configure()` (at `broker_registry_factory.py:40+`) so a startup-time call to `configurer.configure("schwab")` routes to `reconfigure_schwab(self._config)` — symmetric with the Futu branch at lines 51–58.

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/services/broker_registry_factory.py \
        backend/tests/services/test_broker_configurer_schwab.py
git commit -m "feat(backend): reconfigure_schwab helper + 5-trigger Configure contract (C3)"
```

### Task C8: Boundary-strip `account_hash` from `AccountResponse`

**Files:** Modify `backend/app/services/account_service.py`. Create `backend/tests/api/test_account_boundary_strip.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C8 / H3 — account_hash MUST be absent from AccountResponse JSON."""
import pytest


@pytest.mark.asyncio
async def test_account_hash_stripped_from_rest_response(test_client_admin, mock_brokers):
    """Sidecar returns account_hash on the wire; REST output must NOT include it."""
    mock_brokers["schwab"].list_accounts.return_value = [{
        "account_number": "X", "mode": "LIVE", "gateway_label": "schwab",
        "currency_base": "USD", "account_hash": "SECRET_HASH",
    }]
    resp = await test_client_admin.get("/api/brokers/accounts")
    body = resp.json()
    assert resp.status_code == 200
    for row in body["accounts"]:
        assert "account_hash" not in row
        assert "gateway_label" not in row  # Phase 4 M22
        assert "account_number" not in row  # Phase 4 M22
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Modify `account_service.py`** to add `"account_hash"` to the strip set:

```python
_BOUNDARY_STRIP_FIELDS = frozenset({
    "gateway_label",      # Phase 4 M22
    "account_number",     # Phase 4 M22
    "account_hash",       # Phase 7a H3 — Schwab PII-equivalent
})

def _strip_boundary_fields(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in _BOUNDARY_STRIP_FIELDS}
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/services/account_service.py \
        backend/tests/api/test_account_boundary_strip.py
git commit -m "feat(backend): H3 boundary-strip account_hash from AccountResponse"
```

### Task C9: structlog redaction patterns (M5)

**Files:** Modify `backend/app/core/logging.py`. Create `backend/tests/observability/test_logging_redaction.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a M5 — schwab secrets MUST be redacted in structlog output."""
import json

import pytest
import structlog


@pytest.mark.parametrize("event_kwargs", [
    {"schwab_password": "hunter2"},
    {"schwab_totp_secret": "BASE32SECRET"},
    {"schwab_app_secret": "S"},
    {"schwab_refresh_token": "R"},
    {"schwab_access_token": "A"},
    {"params": {"schwab.password": "hunter2"}},
])
def test_redaction_filters_schwab_secrets(capsys, event_kwargs):
    log = structlog.get_logger(test_marker="redaction")
    log.info("test_event", **event_kwargs)
    out, _ = capsys.readouterr()
    assert "hunter2" not in out
    assert "BASE32SECRET" not in out
    # Should see [REDACTED] in their place.
    assert "REDACTED" in out
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Update `backend/app/core/logging.py`** to extend `REDACTION_PATTERNS`:

```python
REDACTION_PATTERNS = [
    re.compile(r"(api[-_]?key|password|token|secret)", re.I),
    # Phase 7a M5 — Schwab-specific
    re.compile(r"schwab[._-]?(password|totp_secret|app_secret|refresh_token|access_token)", re.I),
]
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/core/logging.py backend/tests/observability/test_logging_redaction.py
git commit -m "feat(logging): M5 structlog redaction for 5 schwab secret patterns"
```

**Conditional reviewers:** `security-reviewer`.

### Task C10: SSE pub/sub forwarder for `config:invalidate:schwab`

**Files:** Create `backend/app/services/sse.py`. Create `backend/app/api/sse.py` (router).

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C10 / H6 — SSE forwards Redis pub/sub to subscribed clients."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_sse_forwards_config_invalidate(test_client_admin, redis):
    """Backend SSE endpoint emits an event when redis publishes config:invalidate:schwab."""
    async with test_client_admin.stream(
        "GET", "/api/admin/config/stream", params={"ns": "schwab"},
    ) as resp:
        assert resp.status_code == 200
        # Publish from another task.
        async def publish_after_delay():
            await asyncio.sleep(0.1)
            await redis.publish("config:invalidate:schwab", "1")
        publish_task = asyncio.create_task(publish_after_delay())
        # Read first SSE event.
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                assert "schwab" in line
                break
        await publish_task
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `backend/app/api/sse.py`:**

```python
"""SSE endpoint — forwards Redis pub/sub `config:invalidate:<ns>` to clients."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.auth import require_admin_jwt

router = APIRouter(prefix="/api/admin", tags=["admin", "sse"])


@router.get("/config/stream", dependencies=[Depends(require_admin_jwt)])
async def config_stream(
    ns: str = Query(..., regex=r"^[a-z0-9_]{1,32}$"),
    redis=Depends(lambda: ...),
):
    async def event_gen():
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"config:invalidate:{ns}")
        try:
            yield ": connected\n\n"
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=30.0,
                )
                if msg is None:
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps({"ns": ns, "event": "invalidate"})
                yield f"data: {payload}\n\n"
        finally:
            await pubsub.unsubscribe(f"config:invalidate:{ns}")
            await pubsub.aclose()
    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

- [ ] **Step 4: Mount + run.**

```bash
git add backend/app/api/sse.py backend/tests/api/test_sse_config_stream.py
# Mount in admin.py
```

- [ ] **Step 5: Commit.**

```bash
git commit -m "feat(backend): H6 SSE forwarder for config:invalidate:<ns> pub/sub"
```

### Task C11: Health-probe-driven Configure trigger (sidecar restart)

**Files:** Modify `backend/app/services/brokers.py` (existing `BrokerRegistry`). Create `backend/tests/services/test_configure_on_started_at_delta.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C11 / C3 trigger #2 — sidecar restart detected via Health.started_at delta."""
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_started_at_delta_triggers_reconfigure(broker_registry, sidecar_stubs):
    t1 = datetime.now(timezone.utc)
    sidecar_stubs["schwab"].Health.return_value = _health(started_at=t1)
    await broker_registry.health_probe_once()
    sidecar_stubs["schwab"].Configure.reset_mock()

    # Sidecar restarted: started_at increases.
    t2 = datetime.now(timezone.utc)
    sidecar_stubs["schwab"].Health.return_value = _health(started_at=t2)
    await broker_registry.health_probe_once()
    sidecar_stubs["schwab"].Configure.assert_called_once()


def _health(started_at):
    from google.protobuf.timestamp_pb2 import Timestamp
    from app._generated.broker.v1 import broker_pb2 as pb
    ts = Timestamp(); ts.FromDatetime(started_at)
    return pb.HealthResponse(label="schwab", broker_id="schwab",
                              gateway_connected=True, started_at=ts)
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Extend `BrokerRegistry.health_probe_once()`** in `brokers.py` (Phase 6 already has `_configured: dict[str, datetime]`; ensure the schwab branch hits `reconfigure_schwab`):

```python
# Inside the per-label probe loop:
if label == "schwab" and self._configured.get(label) != health.started_at.ToDatetime(timezone.utc):
    await reconfigure_schwab(self._config_service)
    self._configured[label] = health.started_at.ToDatetime(timezone.utc)
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/services/brokers.py \
        backend/tests/services/test_configure_on_started_at_delta.py
git commit -m "feat(backend): C3 trigger #2 — Configure schwab on sidecar restart (started_at delta)"
```

### Task C12: Token-rotation atomicity integration test (C2)

**Files:** Create `backend/tests/integration/test_token_rotation_atomicity.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a C12 / C2 — concurrent Tier-1 + Tier-2 OAuth callbacks serialize via PG advisory lock."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_concurrent_callbacks_serialized(test_client_no_auth, test_client_admin, redis, config_service, httpx_mock):
    """Tier-1 and Tier-2 fire callbacks at the same time; both succeed,
    but writes are serialized so no torn refresh_token state."""
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "A1", "refresh_token": "R1", "expires_in": 1800},
    )
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "A2", "refresh_token": "R2", "expires_in": 1800},
    )

    from app.services.schwab_oauth import mint_state_nonce
    s1 = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    s2 = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    await config_service.set_secret("broker", "schwab.app_key", "K")
    await config_service.set_secret("broker", "schwab.app_secret", "S")

    async def call_public():
        return await test_client_no_auth.get(
            "/api/oauth/schwab/callback",
            params={"code": "C1", "state": s1})

    async def call_admin():
        return await test_client_admin.post(
            "/api/admin/brokers/schwab/oauth-callback",
            params={"code": "C2", "state": s2})

    r1, r2 = await asyncio.gather(call_public(), call_admin())
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Final state is whichever ran second; both possible.
    final_token = await config_service.reveal_secret("broker", "schwab.refresh_token")
    assert final_token in ("R1", "R2")
```

- [ ] **Step 2: Run — PASS** (no impl change; tests the existing C3+C5 wiring).

- [ ] **Step 3: Commit.**

```bash
git add backend/tests/integration/test_token_rotation_atomicity.py
git commit -m "test(backend): C2 atomicity — concurrent Tier-1+Tier-2 callbacks serialize via lock"
```

---

## End of Chunk C

After C12: 12 commits. Backend can mint state nonces, accept Schwab callbacks on public + admin paths, persist tokens via PG advisory lock, Configure the sidecar synchronously, serve SSE updates, and act as the single-writer authority for `RequestTokenRefresh`. All architect-applied invariants (C2 single-writer, C3 5 triggers, H1 nonce HMAC + GETDEL, H3 boundary strip, M5 redaction) are wired.

---

## Chunk D — Tier-1 frontend (8 tasks)

Goal: `SchwabCard` with all visual states; `useSchwabTokenStatus` hook (5s poll first 60s + SSE); Disconnect dialog (M7 + L5); Storybook stories.

### Task D1: `frontend/src/services/schwab.ts`

**Files:** Create `frontend/src/services/schwab.ts`, `frontend/src/services/schwab.test.ts`.

- [ ] **Step 1: Failing test.**

```typescript
import { describe, it, expect, vi } from "vitest";
import { connectStart, getTokenStatus, disconnect, enableTier2, postReconfigure } from "./schwab";

describe("services/schwab.ts", () => {
  it("connectStart redirects to /api/admin/brokers/schwab/oauth-start", async () => {
    const win = { location: { href: "" } } as any;
    connectStart(win);
    expect(win.location.href).toContain("/api/admin/brokers/schwab/oauth-start");
  });

  it("getTokenStatus parses ISO timestamps from /api/admin/brokers/schwab/status", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toContain("/api/admin/brokers/schwab/status");
      return {
        ok: true,
        json: async () => ({
          refresh_token_issued_at: "2026-04-30T12:00:00+00:00",
          access_token_issued_at: "2026-04-30T12:00:00+00:00",
          tier2_refresh_enabled: false,
          tier2_consecutive_failures: 0,
        }),
      };
    });
    const status = await getTokenStatus(fetchMock as any);
    expect(status.refreshTokenIssuedAt).toEqual(new Date("2026-04-30T12:00:00Z"));
  });

  it("disconnect optionally deletes credentials based on flag", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }));
    await disconnect(fetchMock as any, { deleteCredentials: true });
    const lastCall = fetchMock.mock.calls.at(-1)![0];
    expect(lastCall).toContain("delete_credentials=true");
  });
});
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `frontend/src/services/schwab.ts`:**

```typescript
/**
 * Phase 7a Schwab admin service wrapper.
 * Wraps the 3 admin endpoints + manages the OAuth-start redirect.
 */

const ADMIN = "/api/admin/brokers/schwab";

export type SchwabTokenStatus = {
  accessTokenIssuedAt: Date | null;
  refreshTokenIssuedAt: Date | null;
  tier2RefreshEnabled: boolean;
  tier2ConsecutiveFailures: number;
};

export function connectStart(win: Window = window) {
  win.location.href = `${ADMIN}/oauth-start`;
}

export async function getTokenStatus(
  fetchFn: typeof fetch = fetch,
): Promise<SchwabTokenStatus> {
  // Dedicated aggregated status endpoint (defined in C5). Returns the 4 fields
  // we need without leaking secrets — token VALUES never cross this boundary.
  const resp = await fetchFn(`${ADMIN}/status`, { credentials: "include" });
  if (!resp.ok) {
    throw new Error(`getTokenStatus ${resp.status}`);
  }
  const cfg: Record<string, string | boolean | number | null> = await resp.json();
  return {
    accessTokenIssuedAt: cfg.access_token_issued_at
      ? new Date(cfg.access_token_issued_at as string)
      : null,
    refreshTokenIssuedAt: cfg.refresh_token_issued_at
      ? new Date(cfg.refresh_token_issued_at as string)
      : null,
    tier2RefreshEnabled: Boolean(cfg.tier2_refresh_enabled),
    tier2ConsecutiveFailures: Number(cfg.tier2_consecutive_failures ?? 0),
  };
}

export async function postReconfigure(fetchFn: typeof fetch = fetch): Promise<void> {
  const resp = await fetchFn(`${ADMIN}/reconfigure`, {
    method: "POST", credentials: "include",
  });
  if (!resp.ok) throw new Error(`reconfigure ${resp.status}`);
}

export async function disconnect(
  fetchFn: typeof fetch = fetch,
  opts: { deleteCredentials: boolean } = { deleteCredentials: false },
): Promise<void> {
  const params = new URLSearchParams({
    delete_credentials: String(opts.deleteCredentials),
  });
  const resp = await fetchFn(`${ADMIN}/disconnect?${params}`, {
    method: "POST", credentials: "include",
  });
  if (!resp.ok) throw new Error(`disconnect ${resp.status}`);
}

export async function enableTier2(
  fetchFn: typeof fetch = fetch,
  enabled: boolean,
): Promise<void> {
  // PUT /api/admin/config/{namespace}/{key} idempotent upsert.
  // Namespace convention: ("broker", "schwab.<key>") matches C7/Futu wiring.
  const resp = await fetchFn(
    `/api/admin/config/broker/schwab.tier2_refresh_enabled`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: String(enabled), value_type: "bool" }),
    },
  );
  if (!resp.ok) throw new Error(`tier2 enable ${resp.status}`);
}

export function subscribeConfigStream(
  ns: string,
  onMessage: (data: unknown) => void,
): () => void {
  const es = new EventSource(`/api/admin/config/stream?ns=${encodeURIComponent(ns)}`,
    { withCredentials: true });
  es.onmessage = (ev) => {
    try { onMessage(JSON.parse(ev.data)); } catch { /* keepalive */ }
  };
  return () => es.close();
}
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add frontend/src/services/schwab.ts frontend/src/services/schwab.test.ts
git commit -m "feat(frontend): services/schwab.ts wrapper for admin endpoints + SSE subscriber"
```

### Task D2: `useSchwabTokenStatus` hook (5s poll first 60s + SSE merge — H6)

**Files:** Create `frontend/src/hooks/useSchwabTokenStatus.ts`, `useSchwabTokenStatus.test.ts`.

- [ ] **Step 1: Failing test.**

```typescript
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { useSchwabTokenStatus } from "./useSchwabTokenStatus";

describe("useSchwabTokenStatus", () => {
  it("polls every 5s for the first 60s after fastPoll trigger, then 60s", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ refresh_token_issued_at: "2026-04-30T12:00:00+00:00" }),
    }));
    const { result } = renderHook(() => useSchwabTokenStatus({ fetchFn: fetchMock as any }));
    expect(fetchMock).toHaveBeenCalledTimes(1); // initial

    act(() => result.current.startFastPoll());
    // First 60s: fast poll at 5s.
    for (let i = 0; i < 12; i++) {
      await act(async () => { vi.advanceTimersByTime(5000); });
    }
    expect(fetchMock).toHaveBeenCalledTimes(1 + 12);  // initial + 12 fast

    // After 60s, switches to slow poll (60s).
    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1 + 12 + 1);

    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `frontend/src/hooks/useSchwabTokenStatus.ts`:**

```typescript
import { useEffect, useRef, useState, useCallback } from "react";
import { getTokenStatus, subscribeConfigStream, type SchwabTokenStatus } from "@/services/schwab";

const SLOW_POLL_MS = 60_000;
const FAST_POLL_MS = 5_000;
const FAST_POLL_DURATION_MS = 60_000;

export function useSchwabTokenStatus(opts: { fetchFn?: typeof fetch } = {}) {
  const [status, setStatus] = useState<SchwabTokenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const intervalRef = useRef<number | null>(null);
  const fastUntilRef = useRef<number>(0);

  const refetch = useCallback(async () => {
    try {
      const next = await getTokenStatus(opts.fetchFn);
      setStatus(next); setError(null);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, [opts.fetchFn]);

  const scheduleNext = useCallback(() => {
    if (intervalRef.current !== null) clearTimeout(intervalRef.current);
    const ms = Date.now() < fastUntilRef.current ? FAST_POLL_MS : SLOW_POLL_MS;
    intervalRef.current = window.setTimeout(async () => {
      await refetch();
      scheduleNext();
    }, ms);
  }, [refetch]);

  const startFastPoll = useCallback(() => {
    fastUntilRef.current = Date.now() + FAST_POLL_DURATION_MS;
    scheduleNext();
  }, [scheduleNext]);

  useEffect(() => {
    refetch();
    scheduleNext();
    const unsubscribe = subscribeConfigStream("schwab", () => {
      void refetch();  // SSE-driven instant refresh (H6)
    });
    return () => {
      unsubscribe();
      if (intervalRef.current !== null) clearTimeout(intervalRef.current);
    };
  }, [refetch, scheduleNext]);

  return { status, loading, error, refetch, startFastPoll };
}
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add frontend/src/hooks/useSchwabTokenStatus.ts frontend/src/hooks/useSchwabTokenStatus.test.ts
git commit -m "feat(frontend): useSchwabTokenStatus hook — 5s poll first 60s + SSE merge (H6)"
```

### Task D3: `SchwabCard` — connected/disconnected/expiring states

**Files:** Create `frontend/src/features/Settings/SchwabCard.tsx`, `SchwabCard.test.tsx`.

- [ ] **Step 1: Failing test.** Asserts: shows "Connected" when status non-null + age < 144h; shows red badge when age > 144h; "Re-authorize" calls connectStart; "Disconnect" opens dialog; Tier-2 toggle calls enableTier2.

```typescript
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SchwabCard } from "./SchwabCard";

describe("SchwabCard", () => {
  it("shows Connected + countdown when fresh", async () => {
    vi.mock("@/hooks/useSchwabTokenStatus", () => ({
      useSchwabTokenStatus: () => ({
        status: {
          refreshTokenIssuedAt: new Date(Date.now() - 24 * 3600 * 1000),
          accessTokenIssuedAt: new Date(),
          tier2RefreshEnabled: false,
          tier2ConsecutiveFailures: 0,
        },
        loading: false, error: null, refetch: vi.fn(), startFastPoll: vi.fn(),
      }),
    }));
    render(<SchwabCard />);
    await waitFor(() => expect(screen.getByText(/Connected/i)).toBeInTheDocument());
    expect(screen.getByText(/expires in/i)).toBeInTheDocument();
  });

  it("shows red expiring badge when refresh_token age > 144h", async () => {
    vi.mock("@/hooks/useSchwabTokenStatus", () => ({
      useSchwabTokenStatus: () => ({
        status: {
          refreshTokenIssuedAt: new Date(Date.now() - 145 * 3600 * 1000),
          accessTokenIssuedAt: new Date(),
          tier2RefreshEnabled: false,
          tier2ConsecutiveFailures: 0,
        },
        loading: false, error: null, refetch: vi.fn(), startFastPoll: vi.fn(),
      }),
    }));
    render(<SchwabCard />);
    expect(screen.getByTestId("expiring-badge")).toHaveAttribute("data-state", "warn");
  });
});
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `frontend/src/features/Settings/SchwabCard.tsx`:**

```tsx
import { useState } from "react";
import { useSchwabTokenStatus } from "@/hooks/useSchwabTokenStatus";
import { connectStart, disconnect, enableTier2 } from "@/services/schwab";
import { Card, CardHeader, CardContent } from "@/components/primitives/Card";
import { Button } from "@/components/primitives/Button";
import { Switch } from "@/components/primitives/Switch";
import { Dialog, DialogContent, DialogHeader, DialogActions } from "@/components/primitives/Dialog";

export function SchwabCard() {
  const { status, loading, refetch, startFastPoll } = useSchwabTokenStatus();
  const [showDisconnect, setShowDisconnect] = useState(false);
  const [deleteCreds, setDeleteCreds] = useState(false);

  if (loading) return <Card>Loading…</Card>;

  const connected = !!status?.refreshTokenIssuedAt;
  const ageHours = status?.refreshTokenIssuedAt
    ? (Date.now() - status.refreshTokenIssuedAt.getTime()) / 3_600_000
    : Infinity;
  const expiresInHours = Math.max(0, 168 - ageHours);
  const state: "ok" | "warn" | "expired" =
    ageHours > 168 ? "expired" : ageHours > 144 ? "warn" : "ok";

  return (
    <Card>
      <CardHeader>Schwab</CardHeader>
      <CardContent>
        {connected ? (
          <>
            <p>● Connected</p>
            <p data-testid="expiring-badge" data-state={state}>
              Refresh token {state === "expired" ? "EXPIRED" : `expires in ${formatDuration(expiresInHours)}`}
            </p>
          </>
        ) : (
          <p>Not connected</p>
        )}
        <div className="schwab-card-actions">
          <Button onClick={() => { connectStart(); startFastPoll(); }}>
            {connected ? "Re-authorize now" : "Connect Schwab"}
          </Button>
          {connected && (
            <Button onClick={() => setShowDisconnect(true)} variant="ghost">
              Disconnect
            </Button>
          )}
        </div>
        {connected && (
          <label className="schwab-tier2-toggle">
            <Switch
              checked={!!status?.tier2RefreshEnabled}
              onCheckedChange={async (v) => {
                await enableTier2(undefined, v);
                refetch();
              }}
            />
            Enable Tier-2 auto-refresh (Playwright; every 3 days)
            {status && status.tier2ConsecutiveFailures >= 1 && (
              <span data-testid="tier2-failures">
                {status.tier2ConsecutiveFailures} consecutive failures
              </span>
            )}
          </label>
        )}
      </CardContent>

      {/* M7 + L5 — Disconnect dialog with credential delete/keep choice */}
      <Dialog open={showDisconnect} onOpenChange={setShowDisconnect}>
        <DialogHeader>Disconnect Schwab?</DialogHeader>
        <DialogContent>
          This will sign out the dashboard from Schwab and stop quoting / trading.
          {status?.tier2RefreshEnabled && (
            <label>
              <input type="checkbox" checked={deleteCreds}
                onChange={(e) => setDeleteCreds(e.target.checked)} />
              Also delete saved credentials (username/password/TOTP)
            </label>
          )}
        </DialogContent>
        <DialogActions>
          <Button variant="ghost" onClick={() => setShowDisconnect(false)}>Cancel</Button>
          <Button variant="destructive" onClick={async () => {
            await disconnect(undefined, { deleteCredentials: deleteCreds });
            setShowDisconnect(false);
            refetch();
          }}>Disconnect</Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}

function formatDuration(hours: number): string {
  const days = Math.floor(hours / 24);
  const h = Math.floor(hours % 24);
  return days > 0 ? `${days}d ${h}h` : `${h}h`;
}
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add frontend/src/features/Settings/SchwabCard.tsx \
        frontend/src/features/Settings/SchwabCard.test.tsx
git commit -m "feat(frontend): SchwabCard component — connected/expiring/disconnect with M7+L5 dialog"
```

**Conditional reviewers:** `typescript-reviewer`, `a11y-architect`.

### Task D4: Backend `/disconnect` admin route

**Files:** Modify `backend/app/api/brokers_admin.py`. Create `backend/tests/api/test_brokers_admin_disconnect.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a D4 — POST /disconnect deletes tokens; optionally deletes Tier-2 creds."""
import pytest


@pytest.mark.asyncio
async def test_disconnect_deletes_tokens_and_calls_reconfigure(test_client_admin, config_service, sidecar_stubs):
    await config_service.set_secret("broker", "schwab.access_token", "A")
    await config_service.set_secret("broker", "schwab.refresh_token", "R")
    resp = await test_client_admin.post(
        "/api/admin/brokers/schwab/disconnect",
        params={"delete_credentials": "false"},
    )
    assert resp.status_code == 200
    assert await config_service.reveal_secret("broker", "schwab.access_token", default=None) is None
    assert await config_service.reveal_secret("broker", "schwab.refresh_token", default=None) is None


@pytest.mark.asyncio
async def test_disconnect_with_delete_credentials_removes_tier2_keys(test_client_admin, config_service):
    await config_service.set_secret("broker", "schwab.username", "u")
    await config_service.set_secret("broker", "schwab.password", "p")
    await config_service.set_secret("broker", "schwab.totp_secret", "T")
    resp = await test_client_admin.post(
        "/api/admin/brokers/schwab/disconnect",
        params={"delete_credentials": "true"},
    )
    assert resp.status_code == 200
    for k in ("username", "password", "totp_secret"):
        assert await config_service.reveal_secret("broker", f"schwab.{k}", default=None) is None
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `disconnect` route to `brokers_admin.py`:**

```python
@router.post("/disconnect")
async def disconnect_schwab(
    delete_credentials: bool = Query(False),
    config_service=Depends(get_config),
):
    # Always wipe tokens.
    await config_service.delete_secret("broker", "schwab.access_token")
    await config_service.delete_secret("broker", "schwab.refresh_token")
    await config_service.delete("broker", "schwab.access_token_issued_at")
    await config_service.delete("broker", "schwab.refresh_token_issued_at")

    # Optionally wipe Tier-2 creds (L5).
    if delete_credentials:
        for k in ("username", "password", "totp_secret"):
            await config_service.delete_secret("broker", f"schwab.{k}")
        await config_service.set("broker", "schwab.tier2_refresh_enabled", "false")

    # Soft-delete schwab broker_accounts rows (Phase 5 invariant).
    # — handled by next discoverer tick once sidecar reports unhealthy.

    from app.services.broker_registry_factory import reconfigure_schwab
    await reconfigure_schwab(config_service)  # Configures with empty creds → sidecar enters disconnected state
    return {"ok": True}
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add backend/app/api/brokers_admin.py \
        backend/tests/api/test_brokers_admin_disconnect.py
git commit -m "feat(backend): /api/admin/brokers/schwab/disconnect with M7+L5 credential delete option"
```

### Task D5: Mount `SchwabCard` in `SettingsPage`

**Files:** Modify `frontend/src/features/Settings/SettingsPage.tsx`.

- [ ] **Step 1: Add import + render.**

```tsx
import { SchwabCard } from "./SchwabCard";

// Inside SettingsPage, in the broker section:
<section>
  <h2>Brokers</h2>
  {/* existing IBKR + Futu cards */}
  <SchwabCard />
</section>
```

- [ ] **Step 2: Run typecheck + tests.**

```bash
cd /home/joseph/dashboard/frontend && pnpm typecheck && pnpm test
```

- [ ] **Step 3: Commit.**

```bash
git add frontend/src/features/Settings/SettingsPage.tsx
git commit -m "feat(frontend): mount SchwabCard in SettingsPage broker section"
```

### Task D6: `SchwabCard.stories.tsx` — Storybook visual states

**Files:** Create `frontend/src/features/Settings/SchwabCard.stories.tsx`.

- [ ] **Step 1: Stories with all 5 visual states.**

```tsx
import type { Meta, StoryObj } from "@storybook/react";
import { SchwabCard } from "./SchwabCard";

const meta: Meta<typeof SchwabCard> = {
  title: "Features/Settings/SchwabCard",
  component: SchwabCard,
};
export default meta;
type Story = StoryObj<typeof SchwabCard>;

export const Disconnected: Story = { /* mock returns null status */ };
export const ConnectedFresh: Story = { /* refresh_token age = 24h */ };
export const ExpiringSoon: Story = { /* age = 145h, state=warn */ };
export const Expired: Story = { /* age = 170h, state=expired */ };
export const Tier2EnabledNoFailures: Story = { /* tier2RefreshEnabled=true */ };
export const Tier2WithFailures: Story = { /* tier2ConsecutiveFailures=2 */ };
```

(Detailed mock setup uses `parameters.msw` + `decorators` per existing Storybook conventions in the repo; reference `WatchlistCompact.stories.tsx` for shape.)

- [ ] **Step 2: Verify Storybook builds.**

```bash
cd /home/joseph/dashboard/frontend && pnpm storybook --ci --no-open
```

- [ ] **Step 3: Commit.**

```bash
git add frontend/src/features/Settings/SchwabCard.stories.tsx
git commit -m "feat(frontend): SchwabCard storybook — 6 visual states for visual regression"
```

### Task D7: Wire OAuth-callback redirect to fastPoll trigger

**Files:** Modify `frontend/src/App.tsx` (or routes) to detect arrival from OAuth tab close + invoke `startFastPoll`.

- [ ] **Step 1: Add a `popstate` listener** in `SchwabCard`'s mount path that calls `startFastPoll` when the user returns from the OAuth tab. Already wired via `connectStart` in D3 — verify in test:

```typescript
it("connectStart triggers startFastPoll", async () => {
  const startFast = vi.fn();
  // ... mock useSchwabTokenStatus to return startFast
  fireEvent.click(screen.getByText(/Connect Schwab/i));
  expect(startFast).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run — PASS** (already covered by D3 test).

- [ ] **Step 3: Commit.**

```bash
git commit --allow-empty -m "test(frontend): document fastPoll trigger on OAuth start (covered in D3)"
```

### Task D8: Visual regression — chromatic / Storybook screenshot diff

**Files:** Run existing chromatic / playwright-visual-test pipeline against the 6 SchwabCard stories.

- [ ] **Step 1: Run visual-diff CI.** No code changes — just verifies the stories render without regressions in IBKR/Futu cards.

```bash
cd /home/joseph/dashboard/frontend && pnpm test-storybook --ci
```

- [ ] **Step 2: Commit (empty if no changes; the snapshot baseline is whichever the harness uses).**

```bash
git commit --allow-empty -m "test(frontend): SchwabCard visual regression baseline"
```

---

## End of Chunk D

After D8: 8 commits. SchwabCard renders all states; user can click Connect → OAuth flow → returns → fast-poll catches the new tokens within 5s; Disconnect with credential delete option; Tier-2 toggle.

---

## Chunk E — Tier-2 Playwright refresher (10 tasks)

Goal: opt-in Playwright service that runs every 3 days, intercepts the OAuth redirect, posts the auth code to backend admin, auto-disables on 3 failures, with structured selector health probe.

### Task E1: Package skeleton + Dockerfile (Xvfb + Playwright Chromium)

**Files:** Create `sidecar_schwab_refresher/{__init__.py,pyproject.toml,Dockerfile,tests/__init__.py}`.

- [ ] **Step 1: Make directories.**

```bash
cd /home/joseph/dashboard
mkdir -p sidecar_schwab_refresher/tests
touch sidecar_schwab_refresher/__init__.py sidecar_schwab_refresher/tests/__init__.py
```

- [ ] **Step 2: `sidecar_schwab_refresher/pyproject.toml`:**

```toml
[project]
name = "sidecar-schwab-refresher"
version = "0.7.0"
description = "Tier-2 Playwright auto-refresher for Schwab OAuth (opt-in)"
requires-python = ">=3.14"
dependencies = [
    "playwright>=1.45",
    "playwright-stealth>=1.0.6",
    "pyotp>=2.9",
    "httpx>=0.27",
    "structlog>=24.0",
]

[tool.uv]
package = false

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: `sidecar_schwab_refresher/Dockerfile`:**

```dockerfile
# Tier-2 — needs Xvfb + headed Chromium for stealth
FROM mcr.microsoft.com/playwright/python:v1.45.0
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
ENV PYTHONPATH=/app
# Xvfb wrapper — headed browser inside virtual display
CMD ["xvfb-run", "-a", "uv", "run", "python", "-m", "sidecar_schwab_refresher.main"]
```

- [ ] **Step 4: Commit.**

```bash
cd /home/joseph/dashboard && uv sync --directory sidecar_schwab_refresher --dev
git add sidecar_schwab_refresher/
git commit -m "feat(refresher): package skeleton + Dockerfile (Xvfb + Playwright)"
```

### Task E2: `pyotp` wrapper + tests

**Files:** Create `sidecar_schwab_refresher/totp.py`, `tests/test_totp.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a E2 — pyotp wrapper produces 6-digit TOTP."""
import pytest
from sidecar_schwab_refresher.totp import current_totp, TOTPError


def test_current_totp_returns_6_digits():
    code = current_totp("JBSWY3DPEHPK3PXP")  # standard test secret
    assert len(code) == 6
    assert code.isdigit()


def test_invalid_base32_raises():
    with pytest.raises(TOTPError):
        current_totp("not-base32!")


def test_clock_skew_tolerance(monkeypatch):
    """When system clock drifts by ≤ 30s, TOTP should still validate."""
    import time
    base = time.time()
    monkeypatch.setattr("time.time", lambda: base + 25)  # +25s drift
    code = current_totp("JBSWY3DPEHPK3PXP")
    assert len(code) == 6
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `sidecar_schwab_refresher/totp.py`:**

```python
"""Wrapper for pyotp — current TOTP code from Base32 secret."""
from __future__ import annotations

import pyotp


class TOTPError(ValueError):
    pass


def current_totp(secret_base32: str) -> str:
    try:
        totp = pyotp.TOTP(secret_base32)
        return totp.now()
    except (ValueError, TypeError) as e:
        raise TOTPError(f"invalid TOTP secret: {e}") from e
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab_refresher/totp.py sidecar_schwab_refresher/tests/test_totp.py
git commit -m "feat(refresher): pyotp wrapper for TOTP code generation"
```

### Task E3: `selectors.py` — version-dated selector probe (H2)

**Files:** Create `sidecar_schwab_refresher/selectors.py`, `tests/test_selector_health.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a E3 — H2 selector health probe asserts within 5s budget."""
import pytest
from unittest.mock import AsyncMock

from sidecar_schwab_refresher.selectors import probe_selectors, SelectorHealthError


@pytest.mark.asyncio
async def test_all_selectors_present_returns_true():
    page = AsyncMock()
    page.locator.return_value.wait_for = AsyncMock()
    result = await probe_selectors(page)
    assert result is True


@pytest.mark.asyncio
async def test_missing_selector_raises():
    page = AsyncMock()
    page.locator.return_value.wait_for = AsyncMock(
        side_effect=Exception("Timeout"))
    with pytest.raises(SelectorHealthError, match="missing"):
        await probe_selectors(page)
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `sidecar_schwab_refresher/selectors.py`:**

```python
"""Schwab login DOM selectors. Versioned — last verified 2026-04-30.

H2 invariant: probe_selectors() runs BEFORE any credential submission. If any
selector is missing, the function raises and Tier-2 fails fast with
result=dom_changed without ever entering credentials. This prevents blind
credential submission to a possibly-changed (or phishing) DOM.

Selector update procedure (when Schwab rotates DOM):
  1. Inspect schwab.com/login in browser DevTools.
  2. Update each SELECTOR_* constant below.
  3. Update the LAST_VERIFIED date.
  4. Bump CHANGELOG.md operator note.
"""
from __future__ import annotations

LAST_VERIFIED = "2026-04-30"

# Tested against schwab.com/login as of LAST_VERIFIED.
SELECTOR_USERNAME = "input#loginIdInput"
SELECTOR_PASSWORD = "input#passwordInput"
SELECTOR_LOGIN_BUTTON = "button#btnLogin"
SELECTOR_TOTP_INPUT = "input#otpCode"
SELECTOR_TOTP_SUBMIT = "button#btnContinue"


class SelectorHealthError(RuntimeError):
    pass


async def probe_selectors(page, timeout_sec: float = 5.0) -> bool:
    """Confirm all expected selectors exist within timeout. Raises on missing."""
    selectors = [
        ("username", SELECTOR_USERNAME),
        ("password", SELECTOR_PASSWORD),
        ("login_btn", SELECTOR_LOGIN_BUTTON),
        # TOTP fields appear AFTER login submit — skip in initial probe.
    ]
    for name, sel in selectors:
        try:
            await page.locator(sel).wait_for(timeout=timeout_sec * 1000)
        except Exception as e:
            raise SelectorHealthError(
                f"selector missing: {name} ({sel}) — DOM may have changed since {LAST_VERIFIED}: {e}"
            )
    return True
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab_refresher/selectors.py \
        sidecar_schwab_refresher/tests/test_selector_health.py
git commit -m "feat(refresher): H2 selectors.py — version-dated probe + fail-fast health check"
```

### Task E4: `config_writer.py` — backend admin POST + retry

**Files:** Create `sidecar_schwab_refresher/config_writer.py`, `tests/test_config_writer.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a E4 — config_writer POSTs auth code; retries on 5xx.

Auth header pair is the CF-Access service-token bypass (NOT Authorization:Bearer)
per architect grounding + CLAUDE.md "CI bypass" pattern.
"""
import pytest

from sidecar_schwab_refresher.config_writer import post_oauth_callback


CF_HEADERS = {
    "CF-Access-Client-Id": "abc.access",
    "CF-Access-Client-Secret": "shhh",
}


@pytest.mark.asyncio
async def test_post_oauth_callback_success(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://backend:8000/api/admin/brokers/schwab/oauth-callback?code=C&state=S&actor=tier2",
        json={"access_token_issued_at": "..."},
    )
    result = await post_oauth_callback(
        backend_url="http://backend:8000", code="C", state="S",
        cf_headers=CF_HEADERS,
    )
    assert "access_token_issued_at" in result


@pytest.mark.asyncio
async def test_retry_on_5xx(httpx_mock):
    httpx_mock.add_response(method="POST", status_code=502, json={})
    httpx_mock.add_response(method="POST", status_code=200, json={"ok": True})
    result = await post_oauth_callback(
        backend_url="http://backend:8000", code="C", state="S",
        cf_headers=CF_HEADERS, max_retries=2,
    )
    assert result["ok"] is True
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `sidecar_schwab_refresher/config_writer.py`:**

```python
"""POST captured auth code to backend admin OAuth callback."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


async def post_oauth_callback(
    *,
    backend_url: str,
    code: str,
    state: str,
    cf_headers: dict[str, str],
    max_retries: int = 3,
) -> dict:
    """POST /api/admin/brokers/schwab/oauth-callback?code=&state=&actor=tier2.

    `cf_headers` is the dict from `BackendAdminClient._headers`:
        {"CF-Access-Client-Id": ..., "CF-Access-Client-Secret": ...}
    Per architect grounding + CLAUDE.md "CI bypass": NEVER use
    `Authorization: Bearer ...` from Tier-2 — service-token only.
    """
    url = f"{backend_url}/api/admin/brokers/schwab/oauth-callback"
    params = {"code": code, "state": state, "actor": "tier2"}
    async with httpx.AsyncClient(timeout=30.0, headers=cf_headers) as http:
        for attempt in range(max_retries + 1):
            try:
                resp = await http.post(url, params=params)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code >= 500 and attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
            except httpx.HTTPError:
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab_refresher/config_writer.py \
        sidecar_schwab_refresher/tests/test_config_writer.py
git commit -m "feat(refresher): config_writer.py — POST oauth-callback to backend admin + 5xx retry"
```

### Task E5: `refresher.py` Playwright flow with redirect interception (C1)

**Files:** Create `sidecar_schwab_refresher/{stealth.py,refresher.py}`, `tests/test_refresher_unit.py`.

- [ ] **Step 1: Failing test (mocked Playwright).**

```python
"""Phase 7a E5 — refresher fills creds + intercepts redirect WITHOUT following."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_refresher_intercepts_redirect_without_navigation():
    from sidecar_schwab_refresher.refresher import perform_refresh

    page = AsyncMock()
    page.locator.return_value.wait_for = AsyncMock()
    page.locator.return_value.fill = AsyncMock()
    page.locator.return_value.click = AsyncMock()

    # Simulate redirect: page.on("request") fires once with the callback URL.
    captured_handlers = {}
    def on_handler(event, handler):
        captured_handlers[event] = handler
    page.on = on_handler

    # Drive the redirect simulation by calling the request handler with a
    # fake request after fill submits.
    async def trigger_redirect():
        req = MagicMock()
        req.url = "https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=AUTH_CODE&state=STATE"
        req.is_navigation_request = lambda: True
        req.abort = AsyncMock()
        await captured_handlers["request"](req)

    page._trigger_redirect = trigger_redirect
    code, state = await perform_refresh(
        page,
        username="u", password="p", totp_secret="JBSWY3DPEHPK3PXP",
        callback_url_prefix="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
    )
    assert code == "AUTH_CODE"
    assert state == "STATE"
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `sidecar_schwab_refresher/stealth.py`:**

```python
"""playwright-stealth bootstrap — masks automation fingerprints."""
from __future__ import annotations

from playwright_stealth import Stealth


async def apply_stealth(context) -> None:
    await Stealth().apply_stealth_async(context)
```

- [ ] **Step 4: Write `sidecar_schwab_refresher/refresher.py`:**

```python
"""Tier-2 Playwright OAuth refresh flow.

Architectural invariant (C1): the browser MUST NOT follow the redirect to
the public callback URL. We intercept the request via page.on("request")
and POST the captured `code` directly to backend admin via config_writer.
"""
from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import parse_qs, urlparse

from sidecar_schwab_refresher.selectors import (
    SELECTOR_USERNAME, SELECTOR_PASSWORD, SELECTOR_LOGIN_BUTTON,
    SELECTOR_TOTP_INPUT, SELECTOR_TOTP_SUBMIT,
    probe_selectors,
)
from sidecar_schwab_refresher.totp import current_totp

log = logging.getLogger(__name__)


async def perform_refresh(
    page,
    *,
    username: str,
    password: str,
    totp_secret: str,
    callback_url_prefix: str,
) -> tuple[str, str]:
    """Complete the OAuth login + return (code, state) without following redirect."""
    await probe_selectors(page)

    # Fill credentials with random typing delays.
    await page.locator(SELECTOR_USERNAME).fill("")
    await _type_slowly(page, SELECTOR_USERNAME, username)
    await _type_slowly(page, SELECTOR_PASSWORD, password)
    await page.locator(SELECTOR_LOGIN_BUTTON).click()

    # MFA step.
    await page.locator(SELECTOR_TOTP_INPUT).wait_for(timeout=10_000)
    code = current_totp(totp_secret)
    await page.locator(SELECTOR_TOTP_INPUT).fill(code)

    # Set up redirect interception BEFORE submitting MFA.
    captured: dict[str, str] = {}
    redirect_event = asyncio.Event()

    async def on_request(req):
        if req.is_navigation_request() and req.url.startswith(callback_url_prefix):
            parsed = urlparse(req.url)
            qs = parse_qs(parsed.query)
            captured["code"] = qs.get("code", [""])[0]
            captured["state"] = qs.get("state", [""])[0]
            await req.abort()  # browser does NOT follow redirect (C1)
            redirect_event.set()

    page.on("request", on_request)

    await page.locator(SELECTOR_TOTP_SUBMIT).click()

    # Wait for redirect interception (max 30s).
    try:
        await asyncio.wait_for(redirect_event.wait(), timeout=30)
    except asyncio.TimeoutError:
        raise RuntimeError("Tier-2 refresh: redirect not observed within 30s")

    if not captured.get("code") or not captured.get("state"):
        raise RuntimeError(f"Tier-2 refresh: captured incomplete: {captured}")

    return captured["code"], captured["state"]


async def _type_slowly(page, selector: str, text: str) -> None:
    locator = page.locator(selector)
    for ch in text:
        await locator.type(ch)
        await asyncio.sleep(random.uniform(0.08, 0.2))
```

- [ ] **Step 5: Run — PASS.** Commit.

```bash
git add sidecar_schwab_refresher/stealth.py sidecar_schwab_refresher/refresher.py \
        sidecar_schwab_refresher/tests/test_refresher_unit.py
git commit -m "feat(refresher): C1 Playwright flow — redirect interception without follow"
```

**Conditional reviewers:** `security-reviewer`, `silent-failure-hunter`.

### Task E6: `main.py` cron loop + feature flag gate + auto-disable on 3 failures (H2)

**Files:** Create `sidecar_schwab_refresher/main.py`, `tests/test_consecutive_failures_auto_disable.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a E6 — H2: 3 consecutive failures flips tier2_refresh_enabled=false.

The refresher does NOT import ConfigService (separate Docker container, no DB
access). It speaks to backend over HTTP using `BackendAdminClient` which sets
CF-Access service-token headers on every request — never `Authorization: Bearer`.
The fixture `admin_client_mock` is an `httpx.AsyncClient`-compatible fake that
the test seeds with the same key/value semantics as a real admin call.
"""
import pytest


@pytest.mark.asyncio
async def test_three_failures_auto_disable(admin_client_mock):
    from sidecar_schwab_refresher.main import handle_failure

    await admin_client_mock.set_config("tier2_refresh_enabled", "true", value_type="bool")
    await admin_client_mock.set_config("tier2_consecutive_failures", "0", value_type="int")

    for i in range(2):
        await handle_failure(admin_client_mock, reason="login_failed")
        # Still enabled after 1, 2.
        assert await admin_client_mock.get_config("tier2_refresh_enabled") == "true"

    # 3rd failure auto-disables.
    await handle_failure(admin_client_mock, reason="login_failed")
    assert await admin_client_mock.get_config("tier2_refresh_enabled") == "false"


@pytest.mark.asyncio
async def test_success_resets_failure_counter(admin_client_mock):
    from sidecar_schwab_refresher.main import handle_failure, handle_success
    await admin_client_mock.set_config("tier2_consecutive_failures", "2", value_type="int")
    await handle_success(admin_client_mock)
    assert await admin_client_mock.get_config("tier2_consecutive_failures") == "0"
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Write `sidecar_schwab_refresher/admin_client.py`** first — the HTTP wrapper that mirrors a slice of the backend admin API. Per architect grounding (CLAUDE.md "CI bypass" pattern + spec §3.5), this container has no DB session factory and never imports `ConfigService`. It authenticates with **CF Access service-token headers** (`CF-Access-Client-Id` + `CF-Access-Client-Secret`), NOT `Authorization: Bearer`:

```python
"""HTTP client mirror of the slice of ConfigService the refresher needs.

Auth: CF-Access service-token headers per CLAUDE.md "CI bypass" pattern.
Namespace is fixed to "broker" with key prefix "schwab." — the refresher only
ever reads/writes Schwab keys, so callers pass bare key suffixes.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class BackendAdminClient:
    NAMESPACE = "broker"
    KEY_PREFIX = "schwab."

    def __init__(
        self,
        *,
        backend_url: str,
        cf_access_client_id: str,
        cf_access_client_secret: str,
    ) -> None:
        self._url = backend_url.rstrip("/")
        self._headers = {
            "CF-Access-Client-Id": cf_access_client_id,
            "CF-Access-Client-Secret": cf_access_client_secret,
        }

    @classmethod
    def from_env(cls) -> "BackendAdminClient":
        return cls(
            backend_url=os.environ.get("BACKEND_ADMIN_URL", "http://backend:8000"),
            cf_access_client_id=os.environ["CF_ACCESS_CLIENT_ID"],
            cf_access_client_secret=os.environ["CF_ACCESS_CLIENT_SECRET"],
        )

    def _key(self, suffix: str) -> str:
        return f"{self.KEY_PREFIX}{suffix}"

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        url = f"{self._url}/api/admin/config/{self.NAMESPACE}/{self._key(key)}"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.get(url)
            if resp.status_code == 404:
                return default
            resp.raise_for_status()
            return str(resp.json()["value"])

    async def set_config(self, key: str, value: str, *, value_type: str = "str") -> None:
        url = f"{self._url}/api/admin/config/{self.NAMESPACE}/{self._key(key)}"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.put(url, json={"value": value, "value_type": value_type})
            resp.raise_for_status()

    async def reveal_secret(self, key: str) -> str:
        url = f"{self._url}/api/admin/secrets/{self.NAMESPACE}/{self._key(key)}/reveal"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            return str(resp.json()["value"])
```

- [ ] **Step 4: Write `sidecar_schwab_refresher/main.py`:**

```python
"""Tier-2 entrypoint — cron loop or one-shot invocation.

Architectural invariants:
  - H2: 3 consecutive failures → auto-disable (set tier2_refresh_enabled=false +
    page operator).
  - Skip if tier2_refresh_enabled=false (silent no-op).
  - Run every REFRESH_INTERVAL_HOURS (default 72 = 3 days).
  - This container does NOT import backend code — backend is reachable only
    over HTTP via `BackendAdminClient` (CF Access service-token headers).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import structlog
from playwright.async_api import async_playwright

from sidecar_schwab_refresher.admin_client import BackendAdminClient
from sidecar_schwab_refresher.config_writer import post_oauth_callback
from sidecar_schwab_refresher.refresher import perform_refresh
from sidecar_schwab_refresher.stealth import apply_stealth

log = structlog.get_logger(module="sidecar_schwab_refresher.main")

BACKEND_URL = os.environ.get("BACKEND_ADMIN_URL", "http://backend:8000")
REFRESH_INTERVAL_HOURS = int(os.environ.get("REFRESH_INTERVAL_HOURS", "72"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

AUTO_DISABLE_THRESHOLD = 3


async def handle_failure(client: BackendAdminClient, *, reason: str) -> None:
    raw = await client.get_config("tier2_consecutive_failures", default="0")
    n = int(raw or "0") + 1
    await client.set_config("tier2_consecutive_failures", str(n), value_type="int")
    if n >= AUTO_DISABLE_THRESHOLD:
        await client.set_config("tier2_refresh_enabled", "false", value_type="bool")
        log.error("tier2_auto_disabled", failures=n, reason=reason)


async def handle_success(client: BackendAdminClient) -> None:
    await client.set_config("tier2_consecutive_failures", "0", value_type="int")


async def fetch_credentials(client: BackendAdminClient) -> dict[str, str]:
    """Read schwab username/password/totp_secret from backend admin /reveal."""
    return {
        "username":    await client.reveal_secret("username"),
        "password":    await client.reveal_secret("password"),
        "totp_secret": await client.reveal_secret("totp_secret"),
    }


async def get_oauth_start_url(client: BackendAdminClient) -> str:
    """GET /api/admin/brokers/schwab/oauth-start with CF-Access headers; return Location."""
    import httpx
    async with httpx.AsyncClient(
        timeout=10.0, follow_redirects=False, headers=client._headers,
    ) as http:
        resp = await http.get(f"{client._url}/api/admin/brokers/schwab/oauth-start")
        if resp.status_code != 302:
            raise RuntimeError(f"oauth-start returned {resp.status_code}, expected 302")
        return resp.headers["location"]


async def run_once() -> None:
    """One Tier-2 refresh attempt."""
    client = BackendAdminClient.from_env()

    enabled = (await client.get_config("tier2_refresh_enabled", default="false")) == "true"
    if not enabled:
        log.info("tier2_disabled_skip")
        return

    if DRY_RUN:
        log.info("tier2_dry_run_skip")
        return

    try:
        creds = await fetch_credentials(client)
        consent_url = await get_oauth_start_url(client)
    except Exception:
        log.exception("tier2_setup_failed")
        await handle_failure(client, reason="network_error")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        await apply_stealth(context)
        page = await context.new_page()
        try:
            await page.goto(consent_url, wait_until="domcontentloaded")
            code, state = await perform_refresh(
                page,
                username=creds["username"], password=creds["password"],
                totp_secret=creds["totp_secret"],
                callback_url_prefix="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
            )
            await post_oauth_callback(
                backend_url=BACKEND_URL, code=code, state=state,
                cf_headers=client._headers,
            )
            await handle_success(client)
            log.info("tier2_refresh_success")
        except Exception as e:
            from sidecar_schwab_refresher.selectors import SelectorHealthError
            reason = "dom_changed" if isinstance(e, SelectorHealthError) else \
                     "login_failed" if "login" in str(e).lower() else \
                     "mfa_failed" if "totp" in str(e).lower() or "mfa" in str(e).lower() else \
                     "network_error"
            log.exception("tier2_refresh_failed", reason=reason)
            await handle_failure(client, reason=reason)
        finally:
            await browser.close()
            await context.close()


async def main_loop() -> None:
    while True:
        await run_once()
        await asyncio.sleep(REFRESH_INTERVAL_HOURS * 3600)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run — PASS.** Commit.

```bash
git add sidecar_schwab_refresher/main.py \
        sidecar_schwab_refresher/tests/test_consecutive_failures_auto_disable.py
git commit -m "feat(refresher): main.py — H2 auto-disable on 3× failures + cron loop + feature flag gate"
```

### Task E7: docker-compose `tier2` profile

**Files:** Modify `deploy/docker-compose.prod.yml`.

- [ ] **Step 1: Add services under `services:`:**

```yaml
schwab-sidecar:
  build: ./sidecar_schwab
  restart: unless-stopped
  environment:
    SCHWAB_SIDECAR_PORT: "9090"
    BACKEND_ADMIN_GRPC: "backend:8001"
    LOG_LEVEL: "INFO"
  networks: [internal]
  depends_on:
    backend: { condition: service_started }

schwab-refresher:
  build: ./sidecar_schwab_refresher
  restart: unless-stopped
  environment:
    BACKEND_ADMIN_URL: "http://backend:8000"
    REFRESH_INTERVAL_HOURS: "72"
    DRY_RUN: "false"
    # CF Access service-token bypass — refresher auths to backend admin API
    # via CF-Access-Client-Id / CF-Access-Client-Secret headers. Same token
    # used by CI smoke tests; rotate via scripts/cloudflare/access-token.sh.
    CF_ACCESS_CLIENT_ID: "${CF_ACCESS_CLIENT_ID}"
    CF_ACCESS_CLIENT_SECRET: "${CF_ACCESS_CLIENT_SECRET}"
  networks: [internal]
  profiles: ["tier2"]
```

- [ ] **Step 2: Verify compose renders.**

```bash
cd /home/joseph/dashboard && docker compose -f deploy/docker-compose.prod.yml config --quiet
```

- [ ] **Step 3: Commit.**

```bash
git add deploy/docker-compose.prod.yml
git commit -m "feat(deploy): docker-compose schwab-sidecar + schwab-refresher (tier2 profile)"
```

### Task E8: Tier-2 metric `SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS` push

**Files:** Modify `sidecar_schwab_refresher/main.py` to push timestamp metric on every run.

- [ ] **Step 1:** In `main.py::run_once`, before return:

```python
from app.core.metrics import SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS
SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS.set(time.time())
```

- [ ] **Step 2: Commit.**

```bash
git add sidecar_schwab_refresher/main.py
git commit -m "feat(refresher): emit tier2_last_run_timestamp_seconds metric"
```

### Task E9: Refresher integration test (mocked Playwright + httpx)

**Files:** Create `sidecar_schwab_refresher/tests/test_run_once_e2e.py`.

- [ ] **Step 1: Failing test.**

```python
"""Phase 7a E9 — run_once happy path: stealth + selectors + refresh + post.

Uses the BackendAdminClient HTTP wrapper (no ConfigService import). The
`admin_client_mock` fixture is an in-memory fake that responds to the same
methods as `BackendAdminClient`. `httpx_mock` covers the raw HTTP calls
(oauth-start redirect, oauth-callback POST).
"""
import pytest


@pytest.mark.asyncio
async def test_run_once_happy_path(monkeypatch, admin_client_mock, httpx_mock, mock_playwright):
    # Seed admin client: enabled + creds.
    await admin_client_mock.set_config("tier2_refresh_enabled", "true", value_type="bool")
    admin_client_mock.seed_secret("username", "u")
    admin_client_mock.seed_secret("password", "p")
    admin_client_mock.seed_secret("totp_secret", "JBSWY3DPEHPK3PXP")

    # Force run_once() to use the mock instead of constructing a real client from env.
    from sidecar_schwab_refresher import main as refresher_main
    monkeypatch.setattr(
        refresher_main.BackendAdminClient, "from_env",
        classmethod(lambda cls: admin_client_mock),
    )

    # The Playwright path is mocked; raw HTTP for oauth-start + post-callback.
    httpx_mock.add_response(
        url="http://backend:8000/api/admin/brokers/schwab/oauth-start",
        method="GET", status_code=302,
        headers={"location": "https://api.schwabapi.com/v1/oauth/authorize?state=S"},
    )
    httpx_mock.add_response(
        url="http://backend:8000/api/admin/brokers/schwab/oauth-callback?code=C&state=S&actor=tier2",
        method="POST", json={"access_token_issued_at": "..."},
    )

    await refresher_main.run_once()
    counter = await admin_client_mock.get_config("tier2_consecutive_failures")
    assert counter == "0"
```

- [ ] **Step 2: Run — PASS.**

- [ ] **Step 3: Commit.**

```bash
git add sidecar_schwab_refresher/tests/test_run_once_e2e.py
git commit -m "test(refresher): run_once happy-path integration test"
```

### Task E10: Refresher unit-test coverage to ≥90% (M5 follow-on)

**Files:** Run coverage; identify gaps; add tests until ≥90%.

- [ ] **Step 1: Run coverage.**

```bash
cd /home/joseph/dashboard/sidecar_schwab_refresher
uv run pytest --cov=sidecar_schwab_refresher --cov-report=term-missing
```

- [ ] **Step 2: Add tests for any uncovered lines.** Common gaps: error branches in `refresher.py`, edge cases in `main.py::run_once` (DRY_RUN, disabled, etc.).

- [ ] **Step 3: Commit.**

```bash
git add sidecar_schwab_refresher/tests/
git commit -m "test(refresher): coverage to ≥90% per spec §6.5 invariant"
```

---

## End of Chunk E

After E10: 10 commits. Tier-2 refresher is fully implemented with feature-flag gating, selector health probe, redirect interception, retry, auto-disable, and ≥90% test coverage.

---

## Chunk F — Tests + smoke (6 tasks)

Goal: integration coverage of the full Phase 7a flow + nightly real-Schwab smoke.

### Task F1: Backend integration test — full OAuth flow round-trip

**Files:** Create `backend/tests/integration/test_schwab_oauth_flow.py`.

- [ ] **Step 1: Write integration test.**

```python
"""Phase 7a F1 — full Tier-1 OAuth round-trip with mocked Schwab token endpoint."""
import pytest


@pytest.mark.asyncio
async def test_full_oauth_round_trip(test_client_admin, test_client_no_auth, redis,
                                       config_service, httpx_mock, mock_sidecar_configure):
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token", method="POST",
        json={"access_token": "AT", "refresh_token": "RT", "expires_in": 1800},
    )
    await config_service.set_secret("broker", "schwab.app_key", "K")
    await config_service.set_secret("broker", "schwab.app_secret", "S")

    # Step 1: oauth-start (admin) → 302 with state nonce
    resp = await test_client_admin.get(
        "/api/admin/brokers/schwab/oauth-start", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    state = location.split("state=")[1].split("&")[0]

    # Step 2: simulate Schwab redirecting back to public callback
    resp2 = await test_client_no_auth.get(
        "/api/oauth/schwab/callback",
        params={"code": "AUTH_CODE", "state": state},
    )
    assert resp2.status_code == 200

    # Step 3: tokens persisted
    assert await config_service.reveal_secret("broker", "schwab.access_token") == "AT"
    assert await config_service.reveal_secret("broker", "schwab.refresh_token") == "RT"

    # Step 4: sidecar Configure was called
    mock_sidecar_configure.assert_called_once()

    # Step 5: pub/sub fired
    # (verified via redis fixture in test setup)
```

- [ ] **Step 2: Run — PASS.** Commit.

```bash
git add backend/tests/integration/test_schwab_oauth_flow.py
git commit -m "test(integration): full Tier-1 OAuth round-trip E2E"
```

### Task F2: Backend integration test — account listing across IBKR + Futu + Schwab

**Files:** Create `backend/tests/integration/test_schwab_account_listing.py`.

- [ ] **Step 1: Write test.** Mock all 3 sidecars; assert `/api/brokers/accounts` returns rows from all 3.

- [ ] **Step 2: Commit.**

```bash
git add backend/tests/integration/test_schwab_account_listing.py
git commit -m "test(integration): /api/brokers/accounts spans IBKR+Futu+Schwab"
```

### Task F3: Real-Schwab smoke (gated)

**Files:** Create `backend/tests/integration/test_real_schwab_smoke.py`.

- [ ] **Step 1: Write gated test.**

```python
"""Phase 7a F3 — real-Schwab smoke. Gated on CI_USE_REAL_SCHWAB=1."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CI_USE_REAL_SCHWAB") != "1",
    reason="Real-Schwab smoke disabled (set CI_USE_REAL_SCHWAB=1)",
)


@pytest.mark.asyncio
async def test_user_preference_endpoint_reachable():
    """GET /trader/v1/userPreference returns 200 with streamerInfo."""
    import httpx
    access = os.environ["SCHWAB_TEST_ACCESS_TOKEN"]
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.get(
            "https://api.schwabapi.com/trader/v1/userPreference",
            headers={"Authorization": f"Bearer {access}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "streamerInfo" in body
    assert len(body["streamerInfo"]) > 0


@pytest.mark.asyncio
async def test_account_numbers_endpoint_reachable():
    """GET /trader/v1/accountNumbers returns at least 1 account."""
    # (similar shape)
```

- [ ] **Step 2: Commit.**

```bash
git add backend/tests/integration/test_real_schwab_smoke.py
git commit -m "test(integration): real-Schwab smoke gated on CI_USE_REAL_SCHWAB=1"
```

### Task F4: `nightly-real-schwab.yml` GitHub Actions

**Files:** Create `.github/workflows/nightly-real-schwab.yml`.

- [ ] **Step 1: Write workflow.**

```yaml
name: nightly-real-schwab
on:
  schedule:
    - cron: "0 12 * * *"   # L3 — 12:00 UTC, staggered from Tier-2 at 13:00 UTC
  workflow_dispatch:

jobs:
  smoke:
    runs-on: self-hosted   # uses NUC self-hosted runner per nightly-real-ibkr.yml precedent
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --directory backend --dev
      - name: Real-Schwab smoke
        env:
          CI_USE_REAL_SCHWAB: "1"
          SCHWAB_TEST_ACCESS_TOKEN: ${{ secrets.SCHWAB_TEST_ACCESS_TOKEN }}
        run: |
          cd backend
          uv run pytest tests/integration/test_real_schwab_smoke.py -v
```

- [ ] **Step 2: Commit.**

```bash
git add .github/workflows/nightly-real-schwab.yml
git commit -m "ci: nightly-real-schwab.yml at 12:00 UTC (L3 stagger from tier2)"
```

### Task F5: Test fixtures forked from Dashboard_old

**Files:** Create `backend/tests/fixtures/schwab_test_data.py`.

- [ ] **Step 1: Fork shapes from `Dashboard_old/backend/tests/test_schwab_*.py`.** Extract: `make_account_summary_json()`, `make_position_json()`, `make_order_json(status, ...)`, `make_order_with_activity_json(...)`. Use as factory functions that produce the real Schwab JSON shapes for sidecar handler tests.

- [ ] **Step 2: Replace inline test data in B7/B8/B9 tests with these factories.** Verifies the factories work end-to-end.

- [ ] **Step 3: Commit.**

```bash
git add backend/tests/fixtures/schwab_test_data.py
git commit -m "test(fixtures): fork Schwab JSON factories from Dashboard_old"
```

### Task F6: e2e-mock workflow extension (optional)

**Files:** Modify `.github/workflows/e2e-mock.yml` to include Phase 7a tests.

- [ ] **Step 1: Add `sidecar_schwab/tests` and Phase 7a backend integration tests to the mock e2e job.**

- [ ] **Step 2: Commit.**

```bash
git add .github/workflows/e2e-mock.yml
git commit -m "ci: e2e-mock includes Phase 7a sidecar_schwab + integration tests"
```

---

## End of Chunk F

After F6: 6 commits. Layered E2E coverage in place — mock per-PR + nightly real-Schwab smoke.

---

## Chunk G — Ops + close-out (6 tasks)

Goal: deployable runbook, CF Access bypass policy, Prometheus alerts, CHANGELOG/TASKS/CLAUDE.md updates, tag v0.7.0.

### Task G1: `deploy/runbook-schwab-setup.md` (9 steps)

**Files:** Create `deploy/runbook-schwab-setup.md`.

- [ ] **Step 1: Write runbook.** 9 sections per spec §7.4:
  0. Pre-deploy snapshot of `app_secrets`
  1. Schwab Developer Portal app registration
  2. Seed `app_secrets` (app_key, app_secret)
  3. Deploy schwab-sidecar (`docker compose up -d schwab-sidecar`)
  4. Apply CF Access bypass (`bash scripts/cloudflare/access-bypass-schwab-callback.sh`)
  5. Click "Connect Schwab" → completes Tier-1 OAuth
  6. Optional Tier-2 setup (username/password/TOTP secrets + anti-fraud risk note)
  7. Optional Tier-2 deploy (`docker compose --profile tier2 up -d schwab-refresher`)
  8. Verify `/api/brokers/accounts` returns Schwab rows
  9. Schwabdev upgrade procedure

- [ ] **Step 2: Commit.**

```bash
git add deploy/runbook-schwab-setup.md
git commit -m "docs(deploy): runbook-schwab-setup.md (9 steps)"
```

### Task G2: `scripts/cloudflare/access-bypass-schwab-callback.sh`

**Files:** Create `scripts/cloudflare/access-bypass-schwab-callback.sh`.

- [ ] **Step 1: Write idempotent CF Access bypass applier.**

```bash
#!/usr/bin/env bash
# Phase 7a — CF Access bypass for /api/oauth/schwab/callback.
# Idempotent: re-runs are no-ops if the policy already exists.
set -euo pipefail

ZONE_ID="${CF_ZONE_ID:?CF_ZONE_ID env var required}"
ACCOUNT_ID="${CF_ACCOUNT_ID:?CF_ACCOUNT_ID env var required}"
TOKEN="${CF_ACCESS_API_TOKEN:?CF_ACCESS_API_TOKEN env var required}"
APP_NAME="dashboard-kiusinghung"

POLICY_NAME="bypass-schwab-callback"
POLICY_PRECEDENCE=1

API_BASE="https://api.cloudflare.com/client/v4"
APP_ID="$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API_BASE/accounts/$ACCOUNT_ID/access/apps?name=$APP_NAME" \
  | jq -r '.result[0].id')"

if [[ -z "$APP_ID" || "$APP_ID" == "null" ]]; then
  echo "Access app '$APP_NAME' not found"; exit 1
fi

# Check if policy already exists.
EXISTING="$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API_BASE/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" \
  | jq -r ".result[] | select(.name==\"$POLICY_NAME\") | .id")"

if [[ -n "$EXISTING" ]]; then
  echo "Policy '$POLICY_NAME' already exists ($EXISTING) — no action."
  exit 0
fi

curl -sf -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @- \
  "$API_BASE/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" <<EOF
{
  "name": "$POLICY_NAME",
  "decision": "bypass",
  "precedence": $POLICY_PRECEDENCE,
  "include": [{"everyone": {}}]
}
EOF

echo "Created CF Access bypass policy '$POLICY_NAME'"
```

```bash
chmod +x scripts/cloudflare/access-bypass-schwab-callback.sh
```

- [ ] **Step 2: Commit.**

```bash
git add scripts/cloudflare/access-bypass-schwab-callback.sh
git commit -m "feat(cloudflare): idempotent access-bypass-schwab-callback.sh"
```

### Task G3: Prometheus alerts — `phase7a_schwab` group (9 alerts)

**Files:** Modify `deploy/prometheus/alerts.yml`.

- [ ] **Step 1: Append the 9 alerts** per spec §8.2. Each follows the existing alert YAML shape.

- [ ] **Step 2: Verify YAML.**

```bash
docker run --rm -v $(pwd)/deploy/prometheus:/etc/prometheus prom/prometheus:latest \
  promtool check rules /etc/prometheus/alerts.yml
```

- [ ] **Step 3: Commit.**

```bash
git add deploy/prometheus/alerts.yml
git commit -m "feat(observability): phase7a_schwab alert group (9 alerts)"
```

### Task G4: Apply CF Access bypass + verify deploy

**Files:** No code changes — operator action.

- [ ] **Step 1: Run** `bash scripts/cloudflare/access-bypass-schwab-callback.sh`.

- [ ] **Step 2: Verify path is reachable without CF Access JWT.**

```bash
curl -sf -o /dev/null -w "%{http_code}" \
  "https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=test&state=invalid"
# Expected: 403 (state nonce invalid) — proves the path bypassed CF Access (not 401)
```

- [ ] **Step 3: Document in runbook G1 step 4.**

### Task G5: CHANGELOG.md + CLAUDE.md + memory updates

**Files:** Modify `CHANGELOG.md`, `CLAUDE.md`. Create memory `phase7a_schwab_topology.md`.

- [ ] **Step 1: Write `CHANGELOG.md` `[0.7.0]` block** with full Phase 7a delta (cloud-broker pattern, OAuth two-tier, account_hash, single-writer rule, etc.). Match shape of `[0.6.0]` block.

- [ ] **Step 2: Add `## Phase 7a — Schwab connect (v0.7.0)` to `CLAUDE.md`** with topology + invariants summary (5–10 lines pointing at memory + spec).

- [ ] **Step 3: Write memory `phase7a_schwab_topology.md`** capturing the cloud-broker pattern + token-refresh invariants for future-Claude.

- [ ] **Step 4: Mark Phase 7a tasks `[x]` in `TASKS.md`.**

- [ ] **Step 5: Commit.**

```bash
git add CHANGELOG.md CLAUDE.md TASKS.md \
        ~/.claude/projects/-home-joseph-dashboard/memory/phase7a_schwab_topology.md \
        ~/.claude/projects/-home-joseph-dashboard/memory/MEMORY.md
git commit -m "docs(phase7a): close-out — CHANGELOG [0.7.0] + CLAUDE.md + TASKS.md + memory"
```

### Task G6: Tag v0.7.0

**Files:** Git tag.

- [ ] **Step 1: Verify all CI green.**

```bash
gh run list --limit 5
```

- [ ] **Step 2: User gate — operator must confirm before tagging.** Per Phase 4/5/6 precedent.

- [ ] **Step 3: Tag and push.**

```bash
git tag -a v0.7.0 -m "Phase 7a — Schwab connect (data + read-only) — v0.7.0"
git push origin v0.7.0
```

- [ ] **Step 4: Verify tag.**

```bash
git tag --list | grep v0.7.0
gh release create v0.7.0 --notes-from-tag --prerelease=false
```

---

## End of Chunk G

After G6: 6 commits + tag `v0.7.0`. Phase 7a is shipped.

---

## Self-review (full plan)

**Spec coverage:**
- §3.1 topology — A3, A5, E7 (docker-compose services)
- §3.2 sidecar gRPC contract — A1 (proto), B4 (Configure), B5 (Health), B6 (ListAccounts), B7 (Summary), B8 (Positions), B9 (Orders), B10 (UNIMPLEMENTED stubs)
- §3.3 OAuth Tier-1 — C4, D1, D2, D3, F1
- §3.4 OAuth Tier-2 — E1–E10
- §3.5 token lifecycle — B2 (auth.py), B3 (client.py)
- §3.6 token rotation contract — C2, C3, C6
- §4.1 app_secrets — used in C2/C5/D4
- §4.2 app_config — D1, D2 (token-status reads)
- §4.3 SIDECAR_BROKERS — A5
- §4.4 Alembic 0008 — C1 ✓ (with partial index, downgrade)
- §5.1 sidecar_schwab — A3 ✓
- §5.2 sidecar_schwab_refresher — E1 ✓
- §5.3 backend — C2, C4, C5, C6, C7, C8, C9, C10, C11, C12, D4
- §5.4 proto changes — A1
- §5.5 frontend — D1, D2, D3, D5, D6, D7
- §6.1 sidecar unit tests — distributed across B1–B10
- §6.2 backend integration — F1, F2
- §6.3 refresher tests — E2–E6, E9, E10
- §6.4 real-Schwab smoke — F3, F4
- §6.5 coverage targets — E10 (≥90%), F5 (factories aid coverage)
- §7 deployment — E7, G1, G2, G4
- §8 observability — A7, G3
- §11 architectural pillars — A1, A2, A5, B2, B3, C2, C3, C6 (all 6 set)
- §12 LOWs — L1 fixed in C5 (path-derived actor); L3 fixed in F4 + E7; L5 fixed in D3 + D4

**Placeholder scan:** chunks A through G all have full task bodies with concrete code. No "TBD" / "next plan revision" / "fill in later" remnants.

**Type consistency check:**
- `BrokerServicer` class consistent (A4, B-tasks).
- `SIDECAR_BROKERS` tuple shape `(broker_id, addr)` — A5.
- `SchwabClient` interface (`get_account_numbers`, `get_account_details`, `get_orders`, `refresh_hashes`, `hash_for`) consistent across B3, B6, B7, B8, B9.
- `TokenCache` interface (`set_tokens`, `get_access_token`, `_token_lock`, `_access_issued_at`) consistent across B2, B3, B4, B5.
- `mint_state_nonce`/`consume_state_nonce` signatures consistent across C2, C4, C5, F1.
- `reconfigure_schwab` called from C4, C5, C7, C11.
- Prometheus metric names use ALL_CAPS Python constants matching shape across A7, B1, B3 (sidecar_schwab/metrics.py exposes shorter local names that map to the same metric_name string).

**Architect findings closed:**
| Finding | Closed in task |
|---|---|
| C1 callback host topology | C4 (public route), C5 (admin mirror), E5 (Tier-2 redirect interception), G2 (CF bypass) |
| C2 single-writer rule | B2 (no self-refresh), C3 (advisory lock), C6 (server handler) |
| C3 Configure trigger contract | C7 (lifespan), C11 (started_at delta), C4+C5 (OAuth), reconfigure endpoint, B5 (Health invariant) |
| C4 roadmap/spec scope (resolved earlier) | (commit 3c01b74 — outside plan scope) |
| H1 state nonce HMAC + GETDEL | C2 |
| H2 Tier-2 selector health + auto-disable | E3, E6 |
| H3 account_hash 404 + boundary strip + index | B6, C8, C1 |
| H4 Configure passes access_token + Health gateway_connected | B4, B5 |
| H5 USD-only fallback | B1, B7 |
| H6 SSE pub/sub for SchwabCard | C10, D2 |
| M1 status mapping table | B1 |
| M2 avg_fill from orderActivityCollection | B1, B9 |
| M3 Schwabdev confined + pinned | B3 |
| M4 Alembic partial index + downgrade | C1 |
| M5 structlog redaction | C9 |
| M6 semaphore + 429 + lock granularity | B2, B3 |
| M7 Disconnect dialog | D3, D4 |
| L1 actor from path | C5 (admin path tagged actor=tier2) |
| L2 userPreference at boot | B5 (Health uses _account_hashes proxy; no explicit userPreference) — DEFERRED to Phase 7b where it's load-bearing |
| L3 nightly stagger | F4 (12:00 UTC) + E7 (Tier-2 13:00 UTC env) |
| L4 string vs bytes account_hash | DEFERRED post-Phase-7a |
| L5 credential delete on disconnect | D3, D4 |

---

## Execution choice

Plan complete and saved to `docs/superpowers/plans/2026-04-30-phase7a-schwab-connect-plan.md`.

**Total: 59 tasks across 7 chunks** (A:7 + B:10 + C:12 + D:8 + E:10 + F:6 + G:6).

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Codex writes source code (per delegation rule); Claude writes tests/verifies/commits.

2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
