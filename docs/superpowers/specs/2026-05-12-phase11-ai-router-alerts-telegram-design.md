# Phase 11 — AI Router + Alerts + Telegram — Design

**Status:** brainstorm complete, awaiting ARCHITECT-REVIEW + user review.
**Date:** 2026-05-12.
**ROADMAP §:** 11 (headline: "Ollama router (NUC light + heavy-box WoL with 30s warmup cache), `services/ai/` module any subsystem can call. Price/condition alerts engine. Telegram bot (notifications + admin commands). Prompt-cost tracking.").

**Versioning:**
- 11a → v0.11.0 — AI router foundation
- 11b → v0.11.1 — Alerts engine
- 11c → v0.11.2 — Telegram bot (outbound + inbound non-trade)
- 11d → v0.11.3 — Telegram trade execution (highest blast-radius surface, split out)

Per `memory/feedback_sub_phase_versioning.md` — `0.x.y.z` with `x = §N` for all phases.

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

### 11a — v0.11.0 — AI router foundation (4 chunks)

**11a-A — Infrastructure**
- Add `litellm:` service to `docker-compose.yml` on VPS. Lightweight image `ghcr.io/berriai/litellm:v1.x.x` (not `-database` — we have our own cost ledger).
- Mount `deploy/litellm/config.yaml` (committed, model-list-only, no secrets).
- `app_secrets`: `ai.litellm_master_key`; `ai_provider.{ollama-nuc,ollama-heavy,xai,gemini,anthropic,openai}.api_key`. Ollama "keys" are placeholders since Ollama itself doesn't require auth — they're there to keep the per-request key plumbing uniform.
- **Provider-key flow (Option C — backend signs requests, LiteLLM config has no secrets):**
  - `services/ai/secrets.py` exposes `get_provider_key(provider)` with 60s TTL cache + pubsub-invalidation listener on `app_config:invalidate:ai_provider_keys`.
  - Every BE→LiteLLM request carries `{"api_key": <provider_key>}` in body; LiteLLM forwards as provider auth.
  - Key rotation: `PUT /api/admin/secrets/ai-provider/{name}` → pubsub invalidate → next request uses new key. No container restart.
  - LiteLLM master-key rotation: `PUT /api/admin/secrets/ai/litellm_master_key` → lifespan re-renders env var → `docker compose up -d litellm` (rare, manual restart acceptable).
- Ollama service installs: `deploy/nuc/install-ollama.ps1` + `deploy/heavybox/install-ollama.sh` (or `.ps1`).
- WoL primitive: `services/ai/wol.py` sends magic packet to heavy-box MAC, polls TCP :11434 up to 60s, idempotent via `asyncio.Event` (multiple concurrent callers share one probe).
- Alembic 0041 (`ai_completions` hypertable) + 0042 (`ai_jobs`).
- Capability map: `app_config` namespace `ai_router` with default JSON mapping capability → ordered model list with fallback chain.
- 11 Prometheus metrics under `ai_router_*` (see §6).

**11a-B — `services/ai/` core**
- `AICompletionClient` ABC: `complete()`, `stream()`, `submit_job()`, `get_job()`, `cancel_job()`.
- `LiteLLMClient` concrete impl via httpx-async to `http://litellm:4000`.
- `AICapability` StrEnum: `LOCAL_ONLY`, `LONG_CONTEXT`, `REALTIME_SENTIMENT`, `STRUCTURED_OUTPUT`, `BULK_CHEAP`, `REASONING`, `NUMERICAL`, `CODING`.
- `resolve_models(capability)` consults capability map; removes cloud entries when LOCAL_ONLY; removes entries whose provider key is missing.
- `cost_ledger.record(...)` INSERTs to `ai_completions` including failure cases.
- `jobs.py` async-job store; pubsub channel `ai:job:{id}` for WS push.
- Rate-limit: 10/s per `(jwt_subject, capability)` with `evict_stale` (mirrors `portfolio_rate_limiter` shape).
- **Fallback policy:** router-level fallback iteration (not LiteLLM's native `fallbacks`), so the cost ledger sees every attempt.

**11a-C — REST + WS endpoints**
- `POST /api/ai/complete` (JWT) — synchronous for warm-route capabilities.
- `POST /api/ai/jobs` (JWT) — async for cold-start capabilities; returns 202 + `{job_id}`.
- `GET /api/ai/jobs/{id}` (JWT) — poll status.
- `DELETE /api/ai/jobs/{id}` (JWT) — cooperative cancel (flag set; inference aborts at next check).
- `WS /ws/ai/chat` (JWT) — streaming chat; per-conn 1 active stream + 5 turns/min; CSWSH origin check pre-accept; recv-drain task surfaces `WebSocketDisconnect`.
- `WS /ws/ai/jobs/{id}` (JWT) — push job state changes; closes on terminal state.
- **LOCAL_ONLY enforcement at API boundary:** if request capability is LOCAL_ONLY and `resolve_models()` returns empty (no local providers available), return 503 `local_models_unavailable`.
- Origin check for both WS endpoints.

**11a-D — Frontend**
- New route `/ai/chat`: streaming chat UI with zustand-persist conversation history; model picker (capability-tagged); cost-this-conversation display.
- `services/ai/` FE module: `api.ts`, `useChatStream.ts` (WS hybrid), `useTradeContext.ts` (one-shot), `useAiJob.ts` (poll + WS).
- TradeTicketModal: "AI context" collapsible section between symbol header and sizing; `STRUCTURED_OUTPUT` call returns `{summary, recent_signals[], risk_flags[]}`. Failure-mode: section shows "AI context unavailable" instead of blocking the ticket.
- Admin page `/admin/ai`: capability map editor (drag-reorder providers per capability), provider-key CRUD (CSRF nonce), cost-ledger view (last 24h), heavy-box state indicator.

**11a tests:** ~37 backend + ~10 FE + 2 Playwright.

---

### 11b — v0.11.1 — Alerts engine (3 chunks)

**11b-A — Rule schema + parser**
- Alembic 0043 (`alerts` + `alert_fires`) + 0044 (`alert_capabilities` registry).
- Predicate primitives: `price_threshold`, `pct_change_window`, `ma_cross`, `volume_spike`, `order_event`, `ai_signal`, `composite_and`, `composite_or`. JSONSchema validator.
- `parser.py`: NL→predicate via `services/ai/` STRUCTURED_OUTPUT capability with predicate schema inlined in the system prompt. Low-confidence returns `{parse_uncertain: true, suggestions: [...]}`.
- `requires_capabilities` extraction: parser inspects predicate tree, lists capabilities referenced (e.g. `["news_feed", "ai_signal:realtime_sentiment"]`). Evaluator skips rule if any required capability is unavailable.
- **5 mitigations (locked):**
  1. Parse-once-freeze: AI runs ONCE at create-time, emits structured predicate JSON, stored. Runtime evaluator never re-parses.
  2. User confirmation step: create-rule UI shows "AI understood: ..." with Confirm/Edit/Reject.
  3. JSON-schema-constrained AI output: AI fills slots in a fixed primitive list; cannot invent primitives. JSON-mode + JSON-Schema validation second-gate.
  4. Dry-run replay against `bars_1m` + `bars_1d` for last 24h/7d/30d — shows would-have-fired count + timestamps before activation.
  5. Soft-conditions fail-closed: rules whose `requires_capabilities` aren't available evaluate to FALSE; surface in UI as "dormant — needs capability X."

**11b-B — Evaluator + dry-run + delivery**
- `evaluator.py` subscribes to quote bus (`quote.*.*`); per-tick checks active rules whose scope matches; debounce 500ms per (rule, symbol).
- `dry_run.py` replays predicate against bars hypertable / CAGGs.
- `delivery.py` dispatcher with `InAppChannel` (Redis pubsub → FE), `EmailChannel` (SMTP stub if no email primitive yet), `TelegramChannel` (stub at 11b, wired at 11c).
- Per-rule fail-isolation: catch + log + metric; 10 consecutive eval errors auto-disable.
- REST: `POST /api/alerts` (create with NL, returns parsed+suggestions+dry_run); `POST /api/alerts/{id}/confirm` (activate); `GET /api/alerts` (list); `DELETE /api/alerts/{id}` (soft-delete); `POST /api/alerts/dry-run` (one-shot).
- WS: `WS /ws/alerts/feed` for in-app delivery push.

**11b-C — Frontend**
- Replace `AlertsStubPage` with real `AlertsPage` at `/alerts`.
- List view: active/dormant/disabled tabs; fire-count + last-fired-at columns.
- `CreateAlertModal`: NL textbox → AI parse → confirmation card with parsed predicate + dry-run replay → Confirm/Edit/Reject.
- Per-rule detail page at `/alerts/$alertId`: predicate visualizer, fire history, dry-run-replay re-run button, edit/disable buttons.
- Top-bar bell-icon dropdown showing recent fires (WS-pushed).

**11b tests:** ~33 backend + ~8 FE + 2 Playwright.

---

### 11c — v0.11.2 — Telegram bot, outbound + inbound non-trade (3 chunks)

**11c-A — Bot core + outbound alerts**
- `app_secrets`: `telegram.bot_token`, `telegram.allowed_chat_ids` (allowlist).
- `bot.py`: python-telegram-bot async client, long-poll (webhook deferred — long-poll avoids CF Access exception).
- Wire `TelegramChannel` in `services/alerts/delivery.py`: format alert text + chart-snapshot URL → `/alerts/{id}` on dashboard.
- Onboarding admin page `/admin/telegram`: token + allowlist CRUD + test-message button.

**11c-B — Inbound commands + admin surface**
- `commands.py`: command router; chat_id allowlist check + audit-log on every inbound.
- Commands: `/status`, `/accounts`, `/kill_switch <broker>`, `/mute <alert_id> [duration]`, `/unmute`, `/help`.
- `command_log.py` writes to `telegram_command_log` (Alembic 0045, hypertable).

**11c-C — Inbound chat → AI router**
- Free-form messages (non-command) route to `services/ai/` chat surface.
- Per-chat conversation persisted in Redis with 24h TTL; isolated per chat_id.

**11c tests:** ~18 backend + ~3 FE + 1 Playwright.

---

### 11d — v0.11.3 — Telegram trade execution (1 chunk, highest blast-radius)

- `execute.py`: parses `/place_order <symbol> <BUY|SELL> <qty> [@<limit>]`; builds `OrderRequest`; runs `preview_order`; formats reply with risk warnings/blockers; ALLOW gets inline-keyboard Confirm button carrying the confirmation nonce; BLOCK gets no Confirm button.
- **Nonce binding:** confirmation nonces are bound to chat_id at preview time. Place rejects mismatched chat_id (audit-logged). TTL 5min. Used nonce rejected on replay.
- Per-chat trade rate limit: 5 orders/min (independent of API limiter).
- Uses existing risk-gate + orders pipeline — no new BE paths.

**11d tests:** ~16 backend; manual e2e with real test bot (no Playwright since not in FE).

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
- **Per-chunk reviewer chains:** spec-compliance + python-reviewer + typescript-reviewer (FE) + code-reviewer + security-reviewer (mandatory on 11c/11d) + database-reviewer (11a-A, 11b-A) + silent-failure-hunter (evaluator + delivery).
- **ARCHITECT-REVIEW once after this spec; CRIT+HIGH+MED inline before writing-plans.**
- **Phase 11 total:** ~104 tests + 5 Playwright smokes.

Required new fixtures:
- `ai_provider_mock` — LiteLLM mock per `(model, capability)`, supports streaming
- `ollama_unavailable` — toggle
- `wol_mock` — deterministic delay
- `quote_bus_inject` — synthetic quotes for evaluator tests
- `telegram_bot_test_mode` — python-telegram-bot test mode

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
| 0041 | `ai_completions` hypertable (chunk 7d, retention 1y) |
| 0042 | `ai_jobs` table + index on `(status, started_at)` |
| 0043 | `alerts` + `alert_fires` (alert_fires hypertable) |
| 0044 | `alert_capabilities` registry (seeded with `news_feed=false`, etc.) |
| 0045 | `telegram_command_log` hypertable (chunk 7d, retention 1y) |

### Prometheus metrics (26 new series)

**ai_router_*** (11): `completions_total{provider,model,capability,outcome}`, `latency_seconds{provider,model}`, `tokens_prompt_total{provider,model}`, `tokens_completion_total{provider,model}`, `wol_wake_total`, `wol_wake_latency_seconds`, `wol_wake_failures_total`, `gpu_contended_total{host}`, `jobs_in_flight` (Gauge), `proxy_unavailable_total`, `rate_limited_total{capability}`.

**alerts_*** (8): `evaluator_ticks_total`, `evaluator_eval_errors_total{rule_id}` (cardinality-bound), `evaluator_data_unavailable_total{check_type}`, `fires_total{rule_id,status}`, `delivery_total{channel,outcome}`, `delivery_failures_total{channel}`, `capability_unavailable_total{capability}`, `active_rules` (Gauge).

**telegram_*** (7): `messages_inbound_total{chat_id_hash,outcome}`, `messages_outbound_total{outcome}`, `commands_total{command,outcome}`, `unauthorized_attempts_total`, `rate_limited_total{kind}` (kind ∈ {command, execute}), `bot_api_errors_total{error_class}`, `active_conversations` (Gauge).

---

## 7. Cross-cutting load-bearing decisions

1. **LiteLLM proxy on VPS in existing docker-compose.** Lightweight image. No `-database` variant.
2. **Option C secret flow:** BE signs requests with per-provider key; LiteLLM config has no secrets; rotation via pubsub-invalidated 60s TTL cache; no container restart for provider-key rotation.
3. **Router-level fallback iteration**, not LiteLLM-native, so cost ledger sees every attempt.
4. **LOCAL_ONLY at API boundary**, not just in `resolve_models()` — defence in depth.
5. **All AI calls record to ledger** including failures (capacity planning).
6. **Free-form NL alert rules with all 5 mitigations.** AI in create path only, never in eval path.
7. **`requires_capabilities` array** is the forward-compat hook for Phase 18/19/etc. Registry table `alert_capabilities`; later phases UPDATE to flip rules active.
8. **Telegram trade execution as separate sub-phase (11d).** Smallest reviewable surface for highest blast radius.
9. **Nonces bound to chat_id at preview time.** Defence against any nonce-leak scenario.
10. **Cooperative job cancellation** (not preemptive) — inference checks a flag at safe points.

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

- Tool-calling / function-calling
- RAG / vector DB / embeddings
- Multi-modal (image, audio)
- Cross-user / multi-tenant AI surfaces
- Per-user / per-consumer budget enforcement
- AI-driven trade decisions (Phase 21+ bot engine)
- Telegram webhook mode (long-poll only at v0.11.x)
- Direct-JSON alert predicate editor (NL-only at v0.11.1)

---

## 10. Risks & open questions for ARCHITECT-REVIEW

- **LiteLLM `api_key` per-request flow:** verify the exact field name + provider compatibility for Ollama, xAI, Gemini, Anthropic, OpenAI in current LiteLLM version. Falsifies Option C if any provider can't accept request-body `api_key`.
- **JSON-mode reliability on 7B Ollama models:** parser needs JSON-schema-conformant output; verify `qwen2.5:7b` honors grammar constraints reliably. If unreliable, parser may need to route to heavier model (which conflicts with LOCAL_ONLY warm-path goal — parsing the user's positions is privacy-sensitive).
- **WoL packet path:** NUC bridges WG (10.10.0.0/24) ↔ LAN (192.168.50.0/24). Verify magic packet reaches heavy box from VPS via this two-hop path; may need a NUC-side WoL helper if not.
- **Shared heavy box GPU contention:** what's the right back-off / fallback policy when another user has the GPU? Currently: record metric + fall back; consider if we need user-visible warning.
- **Telegram allowlist vs admin role mapping:** is chat_id allowlist sufficient, or should we map chat_id → user_id and reuse the existing JWT role system? Single-user dashboard means it's currently 1:1; design for multi-user-ready or YAGNI?
- **Predicate evaluator data races:** if a rule is being edited while evaluator ticks it, what's the read-consistency model? Suggest: evaluator snapshots active rules every N seconds, edits flush snapshot.
- **Cost-ledger cardinality:** `model` label can grow if users add many providers. Bound to capability map entries (8-12 typical).
