# Phase 11 — AI Router + Alerts + Telegram — Design

**Status:** brainstorm complete, ARCHITECT-REVIEW applied (4 CRIT, 10 HIGH, 12 MED inline; 7 LOW documented), awaiting user review.
**Date:** 2026-05-12.
**ROADMAP §:** 11 (headline: "Ollama router (NUC light + heavy-box WoL with 30s warmup cache), `services/ai/` module any subsystem can call. Price/condition alerts engine. Telegram bot (notifications + admin commands). Prompt-cost tracking.").

**Versioning:**
- 11a → v0.11.0 — AI router foundation (chunks: A0 spike, A1 proxy+migrations, A.5 auth-callback, A2 WoL+installs, B core, C endpoints, D frontend)
- 11b → v0.11.1 — Alerts engine
- 11c → v0.11.2 — Telegram bot (outbound + inbound non-trade)
- 11d → v0.11.3 — Telegram trade execution (highest blast-radius surface, split out)

Per `memory/feedback_sub_phase_versioning.md` — `0.x.y.z` with `x = §N` for all phases.

**Architect-review-applied summary:** CRIT-1 secret-flow spike before commit; CRIT-2 evaluator inverted index + bounded queue; CRIT-3 parser hard-LOCAL_ONLY; CRIT-4 nonce binding extended to `(chat_id, from_user_id, account_id, symbol, side, qty_bucket, nonce_source)`; HIGH-1 capability pubsub invalidation; HIGH-2 cost-ledger fire-and-forget batched; HIGH-3 WoL TCP→model-ready probe + circuit breaker; HIGH-4 batch_complete + tools-param ABC reservations; HIGH-5 LiteLLM auth-callback over Redis (11a-A.5); HIGH-6 alert_fires retention + PII split; HIGH-7 allowlist moves to app_config; HIGH-8 split orphan threshold per phase; HIGH-9 limiter per-subject + router semaphore per capability; HIGH-10 dry-run resolution awareness.

---

## 1. Architecture & topology

```
┌────────────────────────────────────────────────────────────────────────┐
│  VPS (IONOS, behind Cloudflare)                                        │
│                                                                        │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │ frontend │   │ backend  │   │   redis      │   │   litellm      │  │
│  │ (Vite)   │◄─►│ FastAPI  │◄─►│              │   │   proxy :4000  │  │
│  └──────────┘   └────┬─────┘   └──────────────┘   └────┬───────────┘  │
│                      │ docker network                  │              │
│                      ▼                                 │              │
│  ┌─────────────────────────────────────────┐           │              │
│  │   services/ai/    (router + ledger)     │           │              │
│  │   services/alerts/(rules + evaluator)   │───────────┘              │
│  │   services/telegram/ (bot + cmds)       │                          │
│  └─────────────────────────────────────────┘                          │
└──────────────┬─────────────────────────────────────────┬───────────────┘
               │ WireGuard 10.10.0.0/24                  │ public internet
               ▼                                         ▼
┌──────────────────────────────────┐         ┌─────────────────────────┐
│  NUC15PRO (10.10.0.2, 32GB RAM)  │         │  Cloud LLM providers    │
│                                  │         │                         │
│  • PostgreSQL 18 native          │         │  • xAI Grok             │
│  • broker sidecars (Win tasks)   │         │  • Google Gemini        │
│  • Ollama-light :11434 (7B-8B,   │         │  • Anthropic Claude     │
│    always-on Windows service)    │         │  • OpenAI               │
└──────────────────────────────────┘         │  (all optional; keys    │
                                             │   in app_secrets)       │
┌──────────────────────────────────┐         └─────────────────────────┘
│  Heavy AI box (10.10.0.3)        │
│                                  │                ▲
│  • Ollama-heavy :11434 (13B-70B, │                │ WoL trigger
│    headless service)             │◄───────────────┘  services/ai/wol.py
│  • llama.cpp :11435 (dev-only,   │
│    retire post-Phase-11)         │
│  • Suspends after 15min idle     │
└──────────────────────────────────┘
```

### Three-tier latency model

1. **Always-warm cloud** (~50-200ms network + provider) — Grok / Gemini / Claude / OpenAI when LOCAL_ONLY is false.
2. **Always-warm NUC** (~1-3s) — 7B-8B local, LOCAL_ONLY safe. Always-on Windows service (pattern matches PG-18 + broker sidecars).
3. **WoL heavy-box** (~30s cold, ~3s warm) — 13B-70B local, LOCAL_ONLY safe. Wakes on magic packet; sleeps after 15min idle.

### Ollama-as-service install (Phase 11 ops tasks)

- **NUC:** Ollama Windows installer, all-users service, `OLLAMA_HOST=0.0.0.0:11434`. Pull `qwen2.5:7b` + `llama3.2:8b` defaults. Survives reboots without login.
- **Heavy box:** Ollama systemd (Linux) or Windows Service (Windows), runs as service account, `OLLAMA_HOST=0.0.0.0:11434`. Starts at boot before login. Pulls `qwen2.5:32b` + `llama3.3:70b` defaults. `OLLAMA_KEEP_ALIVE=5m` for auto-unload. Suspend-after-15min daemon puts box back to sleep.
- **Shared-machine caveat (heavy box):** other users may contend for GPU. Ollama OOM surfaces as `gpu_contended` in cost ledger; router falls back to NUC-light or cloud per capability map.

### Module boundaries

```
backend/app/
  services/
    ai/        — pure module, no knowledge of alerts or telegram
    alerts/    — consumes services/ai/ for parse + reasoning; delivery via dispatcher
    telegram/  — one of multiple delivery channels (siblings: in_app, email)
```

`services/ai/` is the **only** module that talks to LiteLLM. Phase 18 scanner, Phase 21 bot-engine, and any future consumer import `AICompletionClient` and route through `services/ai/`.

---

## 2. Sub-phase decomposition

### 11a — v0.11.0 — AI router foundation (split per MED-1 into 7 chunks)

**11a-A0 — Day-0 secret-flow spike (CRIT-1 gate, blocks 11a-A1+)**
- Stand up LiteLLM locally with the planned lightweight image.
- Ship `tests/spike/test_per_request_provider_key.py` asserting each of `{ollama-nuc, ollama-heavy, xai, gemini, anthropic, openai}` accepts a request-body `api_key` and forwards it to the provider correctly.
- If any provider fails: that provider falls back to **Option A** (LiteLLM holds the key, rotation via lifespan re-render + `docker compose up -d litellm`) — documented per-provider in the capability map. Option C remains for the rest.
- Exit criteria: green spike test + per-provider routing-mode table written into `deploy/litellm/secret_routing.md`.

**11a-A1 — LiteLLM proxy + migrations + capability map**
- Add `litellm:` service to `docker-compose.yml` on VPS. Lightweight image `ghcr.io/berriai/litellm:v1.x.x` (not `-database` — we have our own cost ledger).
- Mount `deploy/litellm/config.yaml` (committed, model-list-only, no secrets).
- `app_secrets`: `ai.litellm_master_key`; `ai_provider.{ollama-nuc,ollama-heavy,xai,gemini,anthropic,openai}.api_key`. Ollama "keys" are placeholders since Ollama itself doesn't require auth — they're there to keep the per-request key plumbing uniform.
- **Provider-key flow (Option C — backend signs requests, LiteLLM config has no secrets, per-provider per spike outcome):**
  - `services/ai/secrets.py` exposes `get_provider_key(provider)` with 60s TTL cache + pubsub-invalidation listener on `app_config:invalidate:ai_provider_keys`.
  - Every BE→LiteLLM request carries `{"api_key": <provider_key>}` in body (for providers that passed the 11a-A0 spike); LiteLLM forwards as provider auth.
  - Key rotation: `PUT /api/admin/secrets/ai-provider/{name}` → pubsub invalidate → next request uses new key. No container restart.
- `telegram.chat_id_hash_salt` (HMAC salt for Prometheus chat_id label hashing — LOW-equivalent of MED-10).
- Alembic 0041 (`ai_completions` hypertable) + 0042 (`ai_jobs`).
- Capability map: `app_config` namespace `ai_router` with default JSON mapping capability → ordered model list with fallback chain. Each entry tagged `{provider, model, secret_routing: "request_body" | "litellm_config"}` per 11a-A0 outcome.

**11a-A.5 — LiteLLM master-key auth-callback (HIGH-5)**
- Replace LiteLLM env-var master-key model with a Python HTTP auth callback (`litellm.proxy.auth.custom_auth`).
- New service: `services/ai/litellm_auth_callback.py` — small FastAPI endpoint at `POST /internal/litellm/verify` that reads the current master key from Redis (`ai:litellm_master_key`) and validates the incoming header.
- Master-key rotation flow: `PUT /api/admin/secrets/ai/litellm_master_key` → write to Redis → next LiteLLM request hits the auth-callback which sees the new key. **Zero-restart rotation.**
- Bootstrap: BE lifespan writes current master-key to Redis on startup (idempotent).

**11a-A2 — WoL primitive + Ollama service installs**
- Ollama service installs: `deploy/nuc/install-ollama.ps1` + `deploy/heavybox/install-ollama.sh` (or `.ps1`).
- Heavy-box `idle-suspend.service` (systemd timer; suspends after 15min of no `:11434` connection).
- **WoL primitive: `services/ai/wol.py`** sends magic packet to heavy-box MAC.
  - Readiness probe = `GET /api/tags` on `:11434` → check target model present in response (HIGH-3); TCP-open alone is insufficient because Ollama accepts connections during model load.
  - Idempotent via `asyncio.Event` (single-process; documented multi-replica caveat — Phase 24).
  - **Circuit breaker (HIGH-3):** 3 wake failures in 10min → mark heavy-box `unavailable` for 5min; new jobs auto-fall-back to capability map's next entry instead of retrying.
  - Split metrics: `ai_router_wol_wake_latency_seconds` (TCP open) + `ai_router_wol_warm_to_ready_seconds` (model loaded).
- WoL packet path verification (open question §10): NUC bridges WG↔LAN; if VPS→heavy-box magic packet doesn't traverse, NUC-side WoL helper script + a `WOL_HELPER_URL` env var the proxy hits.
- 11 + 1 = **12 Prometheus metrics** under `ai_router_*` (see §6, updated count).

**11a-B — `services/ai/` core**
- `AICompletionClient` ABC (HIGH-4 — forward-compat for Phase 18/21):
  - `complete(req: CompletionRequest) -> CompletionResult` — single-shot
  - `stream(req: CompletionRequest) -> AsyncIterator[Chunk]` — token streaming
  - `batch_complete(reqs: list[CompletionRequest]) -> AsyncIterator[CompletionResult]` — Phase 18 scanner fan-out hook (v0.11.0 impl = serial loop; ABC shape locked now)
  - `submit_job(req) -> job_id`, `get_job(job_id) -> JobStatus`, `cancel_job(job_id) -> None`
  - `CompletionRequest` schema includes `tools: list[ToolDef] | None = None` (v0.11.0 server-side rejects non-None with `501 tool_calling_not_yet_supported`; Phase 21 lights up).
- `LiteLLMClient` concrete impl via httpx-async to `http://litellm:4000`.
- `AICapability` StrEnum: `LOCAL_ONLY`, `LONG_CONTEXT`, `REALTIME_SENTIMENT`, `STRUCTURED_OUTPUT`, `BULK_CHEAP`, `REASONING`, `NUMERICAL`, `CODING`.
- `resolve_models(capability)` consults capability map; removes cloud entries when LOCAL_ONLY; removes entries whose provider key is missing.
- **Cost ledger (HIGH-2 — fire-and-forget batched):**
  - `cost_ledger.record(...)` enqueues to bounded asyncio.Queue (max 10K, drop-oldest).
  - Background worker batches every 1s or 100 rows via `executemany`.
  - Counters: `ai_cost_ledger_drops_total`, `ai_cost_ledger_insert_failures_total`.
  - **Fail-OPEN** per Phase 10a audit pattern — ledger failures must never fail the AI call.
  - Records failure cases as well (`outcome="failed"` + error_class).
- `jobs.py` async-job store; pubsub channel `ai:job:{id}` for WS push.
- **Rate-limit (HIGH-9 — two-tier):**
  - Subject limiter: 30/s per `jwt_subject` (covers all capabilities). Sliding window with `evict_stale`.
  - Capability semaphore (router-side, in-flight bound): `STRUCTURED_OUTPUT` max 4, `LOCAL_ONLY` max 6, `REASONING` max 2, others max 8. Protects local Ollama from herd.
  - Both fed through the shared `services/common/rate_limiter.py::SlidingWindowRateLimiter[K]` (MED-3 — extracts existing Phase 10b.1 / 10b.2 limiters into a generic; net code reduction).
- **Fallback policy:** router-level fallback iteration (not LiteLLM's native `fallbacks`), so the cost ledger sees every attempt.

**11a-C — REST + WS endpoints**
- **WS envelope (MED-4):** Extract `services/common/ws_envelope.py::make_ws_endpoint(handler, cap=N, heartbeat_s=30)` capturing the Phase 10b.2 canonical pattern: CSWSH origin check pre-accept, `pubsub.listen()`, 250ms compute cache + 500ms debounce, 2s send timeout (overridable per endpoint for chat streaming which may need longer), heartbeat 30s, `v=1` frame schema, connection cap, recv-drain task surfacing `WebSocketDisconnect`. All four new WS endpoints adopt it; deviations documented per endpoint.
- `POST /api/ai/complete` (JWT) — synchronous for warm-route capabilities.
- `POST /api/ai/jobs` (JWT) — async for cold-start capabilities; returns 202 + `{job_id}`.
- `GET /api/ai/jobs/{id}` (JWT) — poll status.
- `DELETE /api/ai/jobs/{id}` (JWT) — cooperative cancel (flag set; inference aborts at next check).
- `WS /ws/ai/chat` (JWT) — streaming chat via envelope; per-conn 1 active stream + 5 turns/min; send timeout 10s (chat-streaming override).
- `WS /ws/ai/jobs/{id}` (JWT) — push job state changes via envelope; closes on terminal state.
- **LOCAL_ONLY enforcement at API boundary AND in `resolve_models()`** (defence-in-depth): if request capability is LOCAL_ONLY and `resolve_models()` returns empty (no local providers available), return 503 `local_models_unavailable`. Boundary check rejects malformed requests; `resolve_models()` removes cloud entries; if both miss, the LiteLLM upstream is hard-coded to reject cloud routes for any request tagged `LOCAL_ONLY=true` via the auth callback (third layer).
- **Async-job orphan recovery (HIGH-8 — split threshold):**
  - `warming` state cutoff = 90s (WoL + readiness should always complete within)
  - `inferring` state cutoff = 10min (70B prompts need room)
  - Per-state-transition timestamps: `warming_started_at`, `inferring_started_at` stored on the row
  - Counter `ai_jobs_orphan_recovered_total{phase}` where `phase` ∈ {warming, inferring}
- Response carries `fallback_chain: [{from, reason, to}]` metadata when fallback exercised (MED-8); FE renders "Used local fallback (heavy box busy)" badge.

**11a-D — Frontend**
- New route `/ai/chat`: streaming chat UI with zustand-persist conversation history; model picker (capability-tagged); cost-this-conversation display; "Used local fallback" badge per MED-8.
- `services/ai/` FE module: `api.ts`, `useChatStream.ts` (WS hybrid), `useTradeContext.ts` (one-shot), `useAiJob.ts` (poll + WS).
- TradeTicketModal: "AI context" collapsible section between symbol header and sizing; `STRUCTURED_OUTPUT` call returns `{summary, recent_signals[], risk_flags[]}`. Failure-mode: section shows "AI context unavailable" instead of blocking the ticket.
- Admin page `/admin/ai`: capability map editor (drag-reorder providers per capability), provider-key CRUD with CSRF `consume_confirmation_nonce` (MED-5 — mandatory on any secret/channel/trading-rule mutation), cost-ledger view (last 24h), heavy-box state indicator (awake/sleeping/cold-warming/circuit-broken).

**11a tests (MED-11 — re-baselined upward):** ~50 backend + ~10 FE + 2 Playwright.

---

### 11b — v0.11.1 — Alerts engine (3 chunks)

**11b-A — Rule schema + parser**
- Alembic 0043 (`alerts` + `alert_fires` + `alert_fire_context`) + 0044 (`alert_capabilities` registry).
  - `alerts` adds `dormancy_reason TEXT` (MED-6) — values like `"awaiting_capability:news_feed"`, `"user_disabled"`, `"eval_error_threshold"`, `"awaiting_user_confirm"`.
  - `alert_fires` retention = 1y (matches `ai_completions`); stores **only** symbolic verdict + fire_context_id reference (HIGH-6 PII split). NO NLV/cash-balance values in `alert_fires`.
  - `alert_fire_context` separate table, retention 90d, holds evaluated_values JSONB; queryable from fire UI on demand.
- Predicate primitives (9): `price_threshold`, `pct_change_window`, `ma_cross`, `volume_spike`, `order_event`, `ai_signal`, `unknown` (MED-12 — for parser-uncertain leaves the user must disambiguate), `composite_and`, `composite_or`. JSONSchema validator.
- `requires_capabilities` shape (LOW-3): array of `{capability: str, params: dict}` JSON objects (not colon-joined strings) — easier to query/migrate.
- **Parser (CRIT-3 — hard-LOCAL_ONLY, non-overridable):**
  - `services/alerts/parser.py` calls `services/ai/` with capability `STRUCTURED_OUTPUT` AND `force_local_only=True` — flag hard-coded, NOT user-toggleable, NOT overridable via admin map.
  - Parser request schema strips user portfolio context to symbols-only (no NLV, no cost basis, no account_ids).
  - If local 7B JSON-mode fails twice, path is "show validation error to user, ask them to simplify rule," NEVER fall back to cloud.
- **Parser outcome paths (MED-12 — disambiguated):**
  - (a) JSON-schema validation fails on first attempt → retry once with schema-error system message
  - (b) Second attempt still fails → return `parse_failed: true` (NO fallback to cloud)
  - (c) JSON-schema-valid but predicate references unknown symbol / impossible threshold → `parse_uncertain: true, predicate: <parsed-but-suspect>, suggestions: [...]` — user can edit the `unknown` leaves manually
- `requires_capabilities` extraction: parser inspects predicate tree, lists capabilities referenced. Evaluator skips rule if any required capability is unavailable.
- **5 mitigations (locked):**
  1. Parse-once-freeze: AI runs ONCE at create-time, emits structured predicate JSON, stored. Runtime evaluator never re-parses.
  2. User confirmation step: create-rule UI shows "AI understood: ..." with Confirm/Edit/Reject. `POST /api/alerts/{id}/confirm` requires CSRF `consume_confirmation_nonce` (MED-5).
  3. JSON-schema-constrained AI output: AI fills slots in a fixed primitive list; cannot invent primitives. JSON-mode + JSON-Schema validation second-gate.
  4. **Dry-run replay (HIGH-10 — resolution-aware):** against `bars_1m` + `bars_1d` for last 24h/7d/30d. Output schema includes `replay_resolution: "1m" | "1d" | "insufficient"`. If predicate's time-window < bar resolution → `replay_resolution: "insufficient"` with UI banner; user must check an "I understand backtest is unreliable" checkbox before Confirm activates.
  5. Soft-conditions fail-closed: rules whose `requires_capabilities` aren't available evaluate to FALSE; surface in UI as "dormant — needs capability X."

**11b-B — Evaluator + dry-run + delivery (CRIT-2 — performance-bounded)**
- **Evaluator inverted index (CRIT-2):**
  - Maintain `symbol_to_rule_ids: dict[str, set[int]]` rebuilt on rule INSERT/UPDATE/DELETE.
  - Per-tick complexity: `O(rules_for_this_symbol)` not `O(all_rules)`.
  - **Push-based snapshot rebuild (MED-9):** pubsub channel `app_config:invalidate:alerts` fires on every rule mutation; evaluator listener rebuilds snapshot. Same pattern as Phase 10a `app_config:invalidate:risk_limits`.
- **Bounded queue (CRIT-2):**
  - Evaluator consumes from bounded asyncio.Queue (max 1000) downstream of pubsub fanout. Drop-oldest on overflow.
  - Counter `alerts_evaluator_queue_dropped_total`.
  - Watchdog Histogram `alerts_evaluator_tick_duration_seconds`; WARN log when p99 > 50ms.
- Debounce 500ms per (rule, symbol) — fire-throttle, distinct from eval-throttle.
- **Capability registry push-invalidation (HIGH-1):**
  - Pubsub channel `app_config:invalidate:alert_capabilities` fires on capability flip; evaluator rebuilds capability snapshot.
  - On flip-to-false: matching active rules transition `active → dormant` (reason `"awaiting_capability:<name>"`) in the same SAVEPOINT'd transaction.
  - On flip-to-true (MED-6 — registration handshake): dormant rules whose `dormancy_reason` matches stay DORMANT; one-time UI notification "N rules can now activate — review and enable?" → user clicks Enable per-rule. Capability flip should never silently re-activate rules.
  - If pubsub delivery fails: capability is treated as unavailable for ~60s cache TTL (fail-CLOSED for the soft-conditions mitigation).
- `dry_run.py` replays predicate against bars hypertable / CAGGs; returns resolution-aware output per HIGH-10.
- `delivery.py` dispatcher with `InAppChannel` (Redis pubsub → FE), `EmailChannel` (SMTP stub if no email primitive yet), `TelegramChannel` (stub at 11b, wired at 11c).
- Per-rule fail-isolation: catch + log + metric; 10 consecutive eval errors → auto-disable (reason `"eval_error_threshold"`) + push user notification.
- REST: `POST /api/alerts` (create with NL, returns parsed+suggestions+dry_run); `POST /api/alerts/{id}/confirm` (activate, CSRF nonce required); `GET /api/alerts` (list); `DELETE /api/alerts/{id}` (soft-delete); `POST /api/alerts/dry-run` (one-shot).
- WS: `WS /ws/alerts/feed` for in-app delivery push (via `ws_envelope` per MED-4).

**11b-C — Frontend**
- Replace `AlertsStubPage` with real `AlertsPage` at `/alerts`.
- List view: active/dormant/disabled tabs; fire-count + last-fired-at columns.
- `CreateAlertModal`: NL textbox → AI parse → confirmation card with parsed predicate + dry-run replay → Confirm/Edit/Reject.
- Per-rule detail page at `/alerts/$alertId`: predicate visualizer, fire history, dry-run-replay re-run button, edit/disable buttons.
- Top-bar bell-icon dropdown showing recent fires (WS-pushed).

**11b tests (MED-11 re-baseline):** ~45 backend (8 predicate primitives × 3 golden vectors + evaluator perf + dry-run + delivery + 5 endpoints + capability handshake) + ~8 FE + 2 Playwright.

---

### 11c — v0.11.2 — Telegram bot, outbound + inbound non-trade (3 chunks)

**11c-A — Bot core + outbound alerts**
- **`app_secrets`** holds only `telegram.bot_token` and `telegram.chat_id_hash_salt` (HMAC salt for Prometheus labels).
- **`app_config` namespace `telegram_allowlist`** (HIGH-7 — allowlist is authorization data, not a secret) holds the chat_id allowlist as plain JSON array. Every mutation audit-logged via existing admin pattern.
- `bot.py`: python-telegram-bot async client, long-poll (webhook deferred — long-poll avoids CF Access exception). Document long-poll trade-off in CHANGELOG; revisit at Phase 14 (LOW-4).
- Wire `TelegramChannel` in `services/alerts/delivery.py`: format alert text + chart-snapshot URL → `/alerts/{id}` on dashboard.
- Onboarding admin page `/admin/telegram`: token CRUD (CSRF nonce) + allowlist CRUD (CSRF nonce) + test-message button.

**11c-B — Inbound commands + admin surface** (MED-2 — `/kill_switch` deserves same scrutiny as 11d; dispatch security-reviewer after this chunk specifically)
- `commands.py`: command router; chat_id allowlist check + audit-log on every inbound.
- Commands: `/status`, `/accounts`, `/kill_switch <broker>`, `/mute <alert_id> [duration]`, `/unmute`, `/help`.
- `command_log.py` writes to `telegram_command_log` (Alembic 0045, hypertable).
- `chat_id_hash` in Prometheus labels uses HMAC with `telegram.chat_id_hash_salt` (MED-10) — operator can reverse for debugging by hashing a known chat_id. Cross-deploy hash stability deliberately sacrificed.
- structlog redaction processor extended to apply the same hash to chat_id in logs.

**11c-C — Inbound chat → AI router**
- Free-form messages (non-command) route to `services/ai/` chat surface.
- Per-chat conversation persisted in Redis with 24h TTL; isolated per chat_id.

**11c tests (MED-11 re-baseline):** ~25 backend + ~3 FE + 1 Playwright.

---

### 11d — v0.11.3 — Telegram trade execution (1 chunk, highest blast-radius)

- `execute.py`: parses `/place_order <symbol> <BUY|SELL> <qty> [@<limit>]`; builds `OrderRequest`; runs `preview_order`; formats reply with risk warnings/blockers; ALLOW gets inline-keyboard Confirm button carrying the confirmation nonce; BLOCK gets no Confirm button.
- **Extended nonce binding (CRIT-4):**
  - Nonces bound to **tuple**: `(chat_id, from_user_id, account_id, symbol_normalized, side, qty_bucket, nonce_source)`.
  - `from_user_id` is `CallbackQuery.from_user.id` (NOT `chat_id` — they diverge in group chats).
  - `qty_bucket` allows ±5% adjustment but blocks size-substitution attacks (substituting qty=10 for qty=10000 fails).
  - `nonce_source` enum {`web`, `telegram`} stored on mint. `place_order` rejects nonces whose `nonce_source` doesn't match the current request transport — defence against leaked-nonce-reused-on-REST attack.
  - Telegram-source nonces are **single-shot** — any second use (even with correct binding) rejected.
  - TTL 5min. All rejections audit-logged with reason.
- Per-chat trade rate limit: 5 orders/min (independent of API limiter), keyed on `(chat_id, from_user_id)`.
- Uses existing risk-gate + orders pipeline — no new BE paths.
- **Reviewer chain on this chunk** must include `security-reviewer` + `silent-failure-hunter` + `database-reviewer` (audit-log INSERT path) per LOW-7.

**11d tests (MED-11 re-baseline):** ~25 backend (nonce-binding tuple matrix + transport-mismatch reject + qty-bucket boundary + audit-log + parse error edges + per-chat rate limit + risk-gate WARN/BLOCK formatting); manual e2e with real test bot.

---

## 3. Data flow & error handling

### Flow A — Synchronous AI completion (warm-route)

NUC Ollama is hot. Router resolves capability, fetches provider key from cache, sends to LiteLLM, records cost ledger entry, returns. Failure modes:

| Failure | Response |
|---|---|
| NUC Ollama 503 | Router walks fallback chain; raises `LocalModelsUnavailable` if all LOCAL_ONLY models exhausted |
| LiteLLM container down | httpx retry with 200ms backoff × 2; then `AIProxyUnavailable` |
| JSON-schema validation fails | One retry with schema error in system message; else `StructuredOutputFailed` with raw text |
| Predicate post-validation fails (unresolvable symbol) | Returns `{parse_uncertain: true, suggestions}` — does NOT activate the rule |
| Rate limit | HTTP 429 with `Retry-After` |
| GPU contention | Record `gpu_contended_total{host}`; retry once after 500ms; else fall back |
| Timeout >30s | Raise `AITimeout`; record metric |

### Flow B — Async job (cold-start heavy box)

Returns 202 + job_id immediately. Background task: set `warming` → WoL wake → poll readiness → set `inferring` → call LiteLLM → set `completed`. Pubsub `ai:job:{id}` pushes state changes to WS subscribers. FE shows "warming, ~30s" with progress indication.

Failure modes:

| Failure | Response |
|---|---|
| WoL packet sent but no response in 60s | `failed: wol_timeout` |
| GPU OOM on heavy box | `failed: gpu_contended`; "Heavy AI box is busy" UI |
| Inference fails | `failed: <reason>` |
| BE crashes mid-job | Lifespan startup scans `WHERE status IN ('warming','inferring') AND started_at < now() - interval '5 min'` → marks `be_restart` |
| FE WS disconnects | Job continues; on reconnect FE `GET /api/ai/jobs/{id}` and resumes |
| User `DELETE /api/ai/jobs/{id}` | Cooperative cancel via flag |

### Flow C — Alert evaluation tick

Quote arrives → evaluator looks up active rules for symbol → per-rule match check → debounce → dispatch via channels. Failure modes:

| Failure | Response |
|---|---|
| Bars hypertable unavailable | Skip rule's tick; metric `evaluator_data_unavailable_total{check_type}`; rule flips `degraded` after 5 consecutive failures |
| Required capability becomes unavailable mid-life | Rule auto-flips active → dormant; FE banner explains |
| One channel delivery fails | Other channels still fire; failed channel retries with backoff × 3 then permanently failed for this fire (alert is in `alert_fires` table) |
| Predicate eval throws (bug in primitive) | Per-rule fail-isolation; 10 consecutive errors → auto-disable + notify user |
| Evaluator coroutine crashes | Lifespan supervisor restarts; in-flight evaluations lost (acceptable; next tick re-evaluates) |

### Flow D — Telegram inbound trade execution (11d)

`/place_order` → allowlist + rate-limit check → parse → preview_order → format reply with inline keyboard (Confirm button carries nonce) → user taps → place_order. Failure modes:

| Failure | Response |
|---|---|
| chat_id not in allowlist | "Unauthorized"; audit-log |
| Rate limit (5/min per chat_id) | "Too many orders" reply |
| Parse error | Usage hint reply |
| Risk gate WARN | Reply shows reasons; Confirm button rendered (acknowledge) |
| Risk gate BLOCK | Reply shows reasons; NO Confirm button |
| Nonce expired / chat_id mismatch | Reply rejects; audit-log |
| place_order rejected by broker | Reply surfaces broker reason |
| BE down on Confirm tap | Telegram retries internally × 3; user re-issues |

---

## 4. Testing strategy

- **Coverage target:** 80%+ per project default.
- **Per-chunk reviewer chains:** spec-compliance + python-reviewer + typescript-reviewer (FE) + code-reviewer + security-reviewer (mandatory on 11c-A, 11c-B `/kill_switch`, 11c-C, 11d) + database-reviewer (11a-A1, 11b-A, 11d audit-log) + silent-failure-hunter (evaluator, delivery, 11d preview→confirm path).
- **ARCHITECT-REVIEW once after this spec applied inline (this revision).**
- **Phase 11 total (MED-11 re-baseline): ~145 backend + ~21 FE + 5 Playwright smokes.**
  - 11a: ~50 backend + ~10 FE + 2 Playwright
  - 11b: ~45 backend + ~8 FE + 2 Playwright
  - 11c: ~25 backend + ~3 FE + 1 Playwright
  - 11d: ~25 backend (no Playwright; manual e2e with real test bot)

Required new fixtures:
- `ai_provider_mock` — LiteLLM mock per `(model, capability)`, supports streaming
- `ollama_unavailable` — toggle
- `wol_mock` — deterministic delay including TCP-open-but-model-not-ready
- `quote_bus_inject` — synthetic quotes for evaluator tests
- `telegram_bot_test_mode` — python-telegram-bot test mode
- `nonce_tuple_factory` — generates valid + invalid nonces for CRIT-4 binding tests
- `litellm_auth_callback_redis` — fake Redis for HIGH-5 master-key rotation tests
- `alert_capabilities_pubsub` — fake pubsub for HIGH-1 capability flip tests

---

## 5. Known limitations & deferred items

1. No tool-calling / function-calling at v0.11.0. Lands when Phase 18/21 needs it.
2. No conversation memory beyond Redis 24h TTL. Long-term + RAG deferred to Phase 21+.
3. No embeddings / vector DB.
4. No multi-modal (image input) at v0.11.0.
5. Heavy-box install runbook is manual; ops doc only.
6. LiteLLM master-key rotation requires `docker compose up -d litellm` (not zero-downtime). Acceptable for single-user.
7. Single-replica AI router rate limiter; multi-worker deferred to Phase 24.
8. No FE for alert-rule predicate direct-JSON editing at v0.11.1; only NL re-parse.
9. Cost-ledger retention 1y; no per-user budget enforcement at v0.11.x (visibility only).
10. `telegram_command_log` append-only; no purge policy.

---

## 6. File structure, migrations & metrics

### File structure

```
backend/app/api/{ai,ws_ai,alerts,ws_alerts,admin_ai,admin_telegram}.py
backend/app/services/ai/{router,capabilities,cost_ledger,wol,jobs,secrets,config_gen,rate_limiter,exceptions}.py
backend/app/services/alerts/{rules,parser,evaluator,dry_run,delivery,rate_limiter}.py
backend/app/services/alerts/channels/{in_app,email,telegram}.py
backend/app/services/telegram/{bot,allowlist,commands,chat,execute,command_log}.py
backend/alembic/versions/{0041…0045}_phase11*.py
frontend/src/routes/ai/chat.tsx
frontend/src/routes/admin/{ai,telegram}.tsx
frontend/src/routes/alerts.tsx (replace stub)
frontend/src/routes/alerts.$alertId.tsx
frontend/src/features/{ai,alerts,admin}/*.tsx
frontend/src/services/{ai,alerts}/*.ts
frontend/src/stores/global/{ai,alerts}.ts
deploy/litellm/{config.yaml,Dockerfile?}
deploy/nuc/install-ollama.ps1
deploy/heavybox/{install-ollama.sh,idle-suspend.service}
docker-compose.yml (modify: add litellm: service)
```

### Migration list

| File | Adds |
|---|---|
| 0041 | `ai_completions` hypertable (chunk 7d, retention 1y; **compress after 90d** per LOW-5) |
| 0042 | `ai_jobs` table (NOT hypertable per LOW-6) + index on `(status, started_at)` + columns `warming_started_at`, `inferring_started_at` per HIGH-8 |
| 0043 | `alerts` (with `dormancy_reason` per MED-6) + `alert_fires` (hypertable, retention 1y, symbolic-verdict-only per HIGH-6) + `alert_fire_context` (retention 90d, holds evaluated_values JSONB) |
| 0044 | `alert_capabilities` registry (seeded with `news_feed=false`, `filings_feed=false`, `earnings_calendar=false`) |
| 0045 | `telegram_command_log` hypertable (chunk 7d, retention 1y) |

### LiteLLM master-key Redis bootstrap

- Key: `ai:litellm_master_key` (Redis STRING)
- Set on BE lifespan startup from `app_secrets.ai.litellm_master_key`
- Rotated atomically via `PUT /api/admin/secrets/ai/litellm_master_key`
- Read by `services/ai/litellm_auth_callback.py` on every LiteLLM auth request

### Prometheus metrics (30 new series after architect-review)

**ai_router_*** (15): `completions_total{provider,model,capability,outcome}`, `latency_seconds{provider,capability}` *(MED-7 — dropped `model` label on Histograms to bound cardinality)*, `tokens_prompt_total{provider,model}`, `tokens_completion_total{provider,model}`, `wol_wake_total`, `wol_wake_latency_seconds` *(TCP open)*, `wol_warm_to_ready_seconds` *(model loaded — HIGH-3)*, `wol_wake_failures_total`, `wol_circuit_breaker_state{host}` *(HIGH-3 — 0/1/2 = closed/half-open/open)*, `gpu_contended_total{host}`, `jobs_in_flight` (Gauge), `jobs_orphan_recovered_total{phase}` *(HIGH-8 — phase ∈ {warming, inferring})*, `proxy_unavailable_total`, `rate_limited_total{capability}`, `fallback_chain_total{from_provider,to_provider,reason}` *(MED-8)*.

**ai_cost_ledger_*** (2 — HIGH-2): `drops_total`, `insert_failures_total`.

**alerts_*** (10): `evaluator_ticks_total`, `evaluator_tick_duration_seconds` *(Histogram — CRIT-2 watchdog)*, `evaluator_queue_dropped_total` *(CRIT-2)*, `evaluator_eval_errors_total{rule_id_bucket}` *(cardinality-bucketed)*, `evaluator_data_unavailable_total{check_type}`, `fires_total{rule_id_bucket,status}`, `delivery_total{channel,outcome}`, `delivery_failures_total{channel}`, `capability_unavailable_total{capability}`, `active_rules` (Gauge).

**telegram_*** (7): `messages_inbound_total{chat_id_hash,outcome}`, `messages_outbound_total{outcome}`, `commands_total{command,outcome}`, `unauthorized_attempts_total`, `rate_limited_total{kind}` (kind ∈ {command, execute}), `bot_api_errors_total{error_class}`, `active_conversations` (Gauge).

`chat_id_hash` and `rule_id_bucket` use HMAC-with-deploy-salt (MED-10) — operator-reversible during debugging.

---

## 7. Cross-cutting load-bearing decisions

1. **LiteLLM proxy on VPS in existing docker-compose.** Lightweight image. No `-database` variant.
2. **Option C secret flow** (validated per-provider via 11a-A0 spike per CRIT-1): BE signs requests with per-provider key; LiteLLM config has no secrets; rotation via pubsub-invalidated 60s TTL cache; no container restart for provider-key rotation. Per-provider Option-A fallback documented in `deploy/litellm/secret_routing.md` if any provider can't accept request-body keys.
3. **LiteLLM master-key auth-callback** (HIGH-5): Redis-backed key validation via `litellm.proxy.auth.custom_auth`; zero-restart rotation. Lives in 11a-A.5 chunk.
4. **Router-level fallback iteration**, not LiteLLM-native, so cost ledger sees every attempt.
5. **LOCAL_ONLY defence-in-depth** (three layers): API boundary check; `resolve_models()` filter; LiteLLM auth-callback rejects cloud routes for `LOCAL_ONLY=true` requests.
6. **All AI calls record to ledger** including failures. Ledger writes are fire-and-forget batched (HIGH-2), fail-OPEN per Phase 10a audit pattern.
7. **Free-form NL alert rules with all 5 mitigations.** AI in create path only, never in eval path. **Parser is hard-LOCAL_ONLY** (CRIT-3) — non-overridable.
8. **`requires_capabilities` array** is the forward-compat hook for Phase 18/19/etc. Shape: `[{capability, params}]` (LOW-3). Registry table `alert_capabilities`. Capability flip-to-true does NOT auto-activate dormant rules (MED-6) — UI notification + per-rule user opt-in.
9. **Telegram trade execution as separate sub-phase (11d).** Smallest reviewable surface for highest blast radius.
10. **Extended nonce binding** (CRIT-4): `(chat_id, from_user_id, account_id, symbol, side, qty_bucket, nonce_source)`; transport-type-bound (`telegram` vs `web`); telegram-source nonces single-shot.
11. **Cooperative job cancellation** (not preemptive) — inference checks a flag at safe points.
12. **Inverted-index evaluator + bounded queue** (CRIT-2): `symbol → rule_ids` map; per-tick `O(rules_for_symbol)`; bounded asyncio.Queue (max 1000, drop-oldest); push-based snapshot rebuild via pubsub.
13. **Push-based capability invalidation** (HIGH-1): `app_config:invalidate:alert_capabilities` pubsub; fail-CLOSED on pubsub failure.
14. **Two-tier rate limiting** (HIGH-9): per-subject sliding window (30/s) + per-capability router-side semaphore. Shared `SlidingWindowRateLimiter[K]` generic extracted (MED-3) — refactor lands in 11a-B.
15. **Shared WS envelope** (MED-4): `services/common/ws_envelope.py::make_ws_endpoint(...)` captures Phase 10b.2 canonical pattern; all four new WS endpoints adopt it.
16. **CSRF nonce on secret/channel/trading-rule mutations** (MED-5): `consume_confirmation_nonce` on `/admin/ai`, `/admin/telegram`, `POST /api/alerts/{id}/confirm`.
17. **Allowlist in `app_config`, not `app_secrets`** (HIGH-7): authorization data isn't a secret.

---

## 8. Versioning

| Sub-phase | Tag | Window |
|---|---|---|
| 11a | v0.11.0 | first y bump for Phase 11 |
| 11b | v0.11.1 | |
| 11c | v0.11.2 | |
| 11d | v0.11.3 | |
| 11.5 (CI debt, if any) | v0.11.x.y | per pattern |
| Phase 12 (options single-leg) starts | v0.12.0 | |

Per `feedback_sub_phase_versioning.md`: `0.x.y.z` with `x = §N` for ALL phases.

---

## 9. Out-of-scope for Phase 11

- Tool-calling / function-calling (ABC slot reserved per HIGH-4; rejected with 501 at v0.11.0)
- RAG / vector DB / embeddings
- Multi-modal (image, audio)
- Cross-user / multi-tenant AI surfaces
- Per-user / per-consumer budget enforcement
- AI-driven trade decisions (Phase 21+ bot engine)
- Telegram webhook mode (long-poll only at v0.11.x; LOW-4 documents trade-off, revisit Phase 14)
- Direct-JSON alert predicate editor (NL-only at v0.11.1)

## 11. LOW findings disposition

- **LOW-1** — `qwen2.5:7b` vs `llama3.2:8b` benchmark: APPLIED. Default parser pin is `qwen2.5:7b`; document benchmark in `memory/phase11_ollama_model_choice.md` post-ship.
- **LOW-2** — exception naming: APPLIED. `LocalModelsUnavailableError`, `AIProxyUnavailableError`, `StructuredOutputFailedError`, `AITimeoutError` (Error suffix consistent with `RiskGateBlockedError`).
- **LOW-3** — `requires_capabilities` value shape: APPLIED inline (§7 #8).
- **LOW-4** — long-poll vs webhook deferral: DOCUMENTED in §9; revisit Phase 14.
- **LOW-5** — cost-ledger 90d compression: APPLIED in migration 0041 (§6 table).
- **LOW-6** — `ai_jobs` not hypertable: APPLIED in migration 0042 (§6 table).
- **LOW-7** — 11d reviewer chain additions: APPLIED in §2 11d.

---

## 10. Open questions resolved by ARCHITECT-REVIEW + outstanding items

**Resolved:**
- ✅ LiteLLM `api_key` per-request flow → 11a-A0 spike validates per-provider; per-provider Option-A fallback documented (CRIT-1).
- ✅ JSON-mode reliability on 7B Ollama → parser stays hard-LOCAL_ONLY; failure path is "show validation error," NEVER fall back to cloud (CRIT-3); parser benchmarks `qwen2.5:7b` first (LOW-1).
- ✅ Shared heavy-box GPU contention → falls back per capability map, user-visible "Used local fallback" badge (MED-8).
- ✅ Telegram allowlist scope → moves to `app_config` (HIGH-7); chat_id remains sufficient for single-user dashboard.
- ✅ Predicate evaluator data-race → push-based pubsub snapshot rebuild (MED-9 / HIGH-1).
- ✅ Cost-ledger cardinality → `model` dropped from latency Histograms (MED-7); ledger writes fire-and-forget (HIGH-2).
- ✅ Async-job orphan recovery threshold → split per-phase, 90s warming / 10min inferring (HIGH-8).
- ✅ WS pattern drift → shared `ws_envelope` (MED-4).
- ✅ Telegram nonce binding gaps → extended tuple binding (CRIT-4).
- ✅ Capability flip auto-activation → opt-in via UI notification (MED-6).

**Outstanding (resolved during 11a-A2 implementation):**
- **WoL packet path** (NUC bridges WG ↔ LAN): if VPS→heavy-box magic packet doesn't traverse the two-hop path, NUC-side WoL helper script + `WOL_HELPER_URL` env var on the proxy. Tested live during 11a-A2; decision documented in commit.
