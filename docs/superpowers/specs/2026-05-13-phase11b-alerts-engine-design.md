# Phase 11b — Alerts Engine — Design Spec

**Status:** brainstormed 2026-05-13; **pending ARCHITECT-REVIEW**.
**Tag target:** v0.11.1 (one minor bump after Phase 11a's v0.11.0.8).
**Predecessor:** Phase 11a shipped 2026-05-13 (memory `phase11a_shipped.md`).
**Umbrella spec:** `docs/superpowers/specs/2026-05-12-phase11-ai-router-alerts-telegram-design.md` §2.11b (lines 173-227).

This document is a **delta-spec** layered over the umbrella. It revises 11b assumptions in light of what 11a actually shipped. Where this document is silent, the umbrella spec stands.

## 0. Goal

Ship a free-form natural-language alert system that users describe in plain English ("notify me when AAPL closes above 200 for 3 days running"), parsed once by a local 7B model into a structured predicate JSON, evaluated against live market data + broker events, and delivered to in-app + webhook channels. No cloud LLM in the parser path. No AI in the evaluation path. Forward-compat hooks for news/filings/earnings capabilities (Phase 18) and Telegram delivery (Phase 11c).

## 1. Decisions revised after shipping 11a

| Topic | Umbrella spec | This spec | Reason |
|---|---|---|---|
| Parser failure → cloud fallback | Hard-LOCAL_ONLY, no fallback ever | Same — hard-LOCAL_ONLY (CRIT-3 stands) | unchanged |
| parse_failed UX | "Show validation error, ask user to simplify" | Surface partial-parse JSON to a **manual predicate editor** in the FE | 11a's `useTradeContext` graceful-degrade pattern proved that surfacing partial state beats hard-blocking the user; line 471's "no direct-JSON editor at v0.11.1" is reversed for this narrow case (only opens on parse_failed, not as primary UI) |
| Predicate primitives | 9: price_threshold, pct_change_window, ma_cross, volume_spike, order_event, ai_signal, unknown, composite_and, composite_or | **10** — adds `news_event` (parser-aware, registry-dormant via `app_config[alert_capabilities/news_feed=false]`) | Forward-compat hook for Phase 18; alembic 0044 already seeds `news_feed=false` so the registry path is open |
| Price-data source | Implicit (spec didn't specify) | **bars_1m default + opt-in per-rule tick-subscription to Phase 7b.1 quote-engine WS** | Default decouples alerts from streaming-quotes operational state; opt-in addresses sub-minute stop-loss-style use cases without forcing every rule onto the WS |
| Evaluator runtime | "Bounded asyncio.Queue + inverted index" (single-replica implied) | **In-process FastAPI lifespan loop** matching 11a's `orphan_sweeper` pattern | Proven; single-replica is fine for the same Phase-24 reason as 11a |
| Delivery channels at 11b | InApp + Email (SMTP) + Telegram-stub | **InApp + Webhook + Telegram-stub** | We have no SMTP primitive in-repo; webhook covers Pushover/Slack/IFTTT/user-SMTP-gateway and is ~5 LoC against `httpx` |
| REST defences | CSRF on /confirm only | **11a-parity across the board**: 404-existence-oracle on `/alerts/{id}/*`, CSRF on POST+PUT+DELETE+/confirm, rate-limit on POST `/alerts` via existing `SlidingWindowRateLimiter[K]` | 11a learnings; consistent with `/api/ai/jobs/{id}` |
| REST endpoints | 5 (POST /alerts, POST /alerts/{id}/confirm, GET /alerts, DELETE /alerts/{id}, POST /alerts/dry-run) | **6** — add `PUT /api/alerts/{id}` for manual predicate edits | Required by the parse_failed JSON editor and for general post-create edits |
| Chunk decomposition | 3 (A schema+parser, B evaluator+dry-run+delivery, C FE) | **4** — A schema+parser, B evaluator+dry-run+tick-opt-in, C delivery+WS, D FE | Surface grew (news_event, JSON editor, PUT endpoint, webhook, tick-opt-in, bell dropdown); 4 chunks keeps each ~6-8 commits |

All other umbrella-spec decisions for 11b (5 mitigations, capability-registry pubsub, dormancy_reason, fire-context PII split, soft-conditions fail-closed, inverted-index evaluator with bounded queue + drop-oldest, dry-run resolution-aware replay, push-based capability invalidation, per-rule fail-isolation with 10-error auto-disable) stand unchanged.

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  backend (FastAPI lifespan)                                              │
│                                                                          │
│  ┌─────────────────────────────────┐    ┌─────────────────────────────┐  │
│  │  app/api/                       │    │  app/services/alerts/       │  │
│  │  alerts.py    (6 REST)          │◄──►│  rules.py    CRUD + 404     │  │
│  │  ws_alerts.py (1 WS via env.)   │    │  parser.py   LOCAL_ONLY     │  │
│  └─────────────────────────────────┘    │  predicates.py 10 primitives │  │
│                                          │  evaluator.py inverted idx  │  │
│  ┌─────────────────────────────────┐    │  ticks_subscriber.py opt-in │  │
│  │  app/services/ai/  (Phase 11a)  │◄───┤  dry_run.py  resolution-aware│  │
│  │  /api/ai/complete LOCAL_ONLY    │    │  delivery.py dispatcher     │  │
│  └─────────────────────────────────┘    │  channels/{in_app,webhook,  │  │
│                                          │            telegram-stub}.py │  │
│  ┌─────────────────────────────────┐    │  rate_limiter.py SLW[K]     │  │
│  │  app/services/common/           │◄───┤                              │  │
│  │  rate_limiter.SlidingWindow[K]  │    └─────────────────────────────┘  │
│  │  ws_envelope.make_ws_endpoint   │                                     │
│  └─────────────────────────────────┘                                     │
│                                                                          │
│  ┌─────────────────────────────────┐    ┌─────────────────────────────┐  │
│  │  Redis pubsub                   │    │  PostgreSQL                 │  │
│  │  app_config:invalidate:alerts   │    │  alerts (table)             │  │
│  │  app_config:invalidate:alert_   │    │  alert_fires (hypertable 1y)│  │
│  │    capabilities                 │    │  alert_fire_context (90d)   │  │
│  │  alerts:fire:{user_id}          │    │  alert_capabilities         │  │
│  │  bars_1m NOTIFY (or 5s poll)    │    │  bars_1m, bars_1d (Phase 9) │  │
│  └─────────────────────────────────┘    └─────────────────────────────┘  │
│                                                                          │
│  optional per-rule subscription:                                         │
│   Phase 7b.1 quote-engine WS  ──►  ticks_subscriber.py  ──►  evaluator   │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                  frontend (/alerts, /alerts/$alertId)
                  WS /ws/alerts/feed (via shared envelope)
```

### Module boundaries

- `services/alerts/parser.py` consumes `services/ai/` exactly through the public `AICompletionClient` ABC; no direct LiteLLM call.
- `services/alerts/evaluator.py` consumes `bars_1m` via the existing async PG pool. Tick-subscribed rules also consume Phase 7b.1's quote-WS via `ticks_subscriber.py`.
- `services/alerts/channels/` is the only place that talks to FE-WS / outbound HTTP / Telegram. Telegram channel is a stub at 11b (no-op + log info) and wires at 11c.
- `services/alerts/` never imports from `services/telegram/` or `services/quotes/` directly except through their public service interfaces.

## 3. Schema (alembic 0043 + 0044)

### 0043 — `alerts` + `alert_fires` + `alert_fire_context`

```sql
CREATE TABLE alerts (
  id              BIGSERIAL PRIMARY KEY,
  jwt_subject     TEXT NOT NULL,
  user_label      TEXT NOT NULL,            -- user-facing rule name
  original_nl     TEXT NOT NULL,            -- the input the user typed
  predicate_json  JSONB NOT NULL,           -- frozen parsed predicate tree
  requires_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                                            -- [{capability, params}, ...]
  parse_status    TEXT NOT NULL CHECK (parse_status IN ('ok','uncertain','manual','failed')),
  parse_metadata  JSONB,                    -- {suggestions, model, latency_ms}
  delivery_channels JSONB NOT NULL DEFAULT '["in_app"]'::jsonb,
                                            -- ["in_app", "webhook:<id>"]
  tick_subscribed BOOLEAN NOT NULL DEFAULT FALSE,
                                            -- opt-in real-time tick stream
  status          TEXT NOT NULL CHECK (status IN ('pending','active','dormant','disabled','deleted')),
  dormancy_reason TEXT,                     -- "awaiting_capability:news_feed" etc.
  consecutive_eval_errors INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  confirmed_at    TIMESTAMPTZ,
  deleted_at      TIMESTAMPTZ
);
CREATE INDEX idx_alerts_active_by_subject ON alerts (jwt_subject) WHERE status = 'active';
CREATE INDEX idx_alerts_status ON alerts (status);

CREATE TABLE alert_fires (
  id            BIGSERIAL,
  alert_id      BIGINT NOT NULL,
  jwt_subject   TEXT NOT NULL,
  fired_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  verdict       TEXT NOT NULL,              -- symbolic only: "true"/"false"
  fire_context_id BIGINT,                   -- FK -> alert_fire_context
  delivery_outcomes JSONB NOT NULL DEFAULT '{}'::jsonb,
                                            -- {"in_app":"sent","webhook":"failed"}
  PRIMARY KEY (id, fired_at)
);
SELECT create_hypertable('alert_fires', 'fired_at', chunk_time_interval => INTERVAL '7 days');
ALTER TABLE alert_fires SET (timescaledb.compress, timescaledb.compress_orderby = 'fired_at DESC');
SELECT add_compression_policy('alert_fires', INTERVAL '90 days');
SELECT add_retention_policy('alert_fires', INTERVAL '1 year');

CREATE TABLE alert_fire_context (
  id              BIGSERIAL PRIMARY KEY,
  alert_id        BIGINT NOT NULL,
  fired_at        TIMESTAMPTZ NOT NULL,
  evaluated_values JSONB NOT NULL,           -- may contain PII (NLV, positions)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_alert_fire_context_alert ON alert_fire_context (alert_id, fired_at DESC);
-- Retention 90d via apscheduler nightly job (see services/alerts/retention.py — chunk B);
-- deletes WHERE created_at < now() - interval '90 days'. Table stays small (10 fires/day × 1 user × 90d ≈ 900 rows).
```

### 0044 — `alert_capabilities`

```sql
CREATE TABLE alert_capabilities (
  name          TEXT PRIMARY KEY,
  available     BOOLEAN NOT NULL DEFAULT FALSE,
  description   TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO alert_capabilities (name, available, description) VALUES
  ('news_feed', FALSE, 'Phase 18 news ingest'),
  ('filings_feed', FALSE, 'Phase 18 SEC filings ingest'),
  ('earnings_calendar', FALSE, 'Phase 18 earnings calendar');
```

## 4. Predicate primitives (10)

| Primitive | Slots | Data source | Capability |
|---|---|---|---|
| `price_threshold` | `{symbol, op, value, lookback?}` | bars_1m or tick (opt-in) | none |
| `pct_change_window` | `{symbol, pct, window_seconds}` | bars_1m | none |
| `ma_cross` | `{symbol, fast_period, slow_period, direction}` | bars_1m + computed | none |
| `volume_spike` | `{symbol, multiple, vs_window}` | bars_1m | none |
| `order_event` | `{account_id?, broker_id?, event_type, symbol?}` | broker fill stream | none |
| `ai_signal` | `{prompt_template, capability, threshold}` | calls `/api/ai/complete` STRUCTURED_OUTPUT | none (depends on 11a router) |
| `news_event` | `{symbol?, source?, sentiment?}` | — | `news_feed` (dormant) |
| `unknown` | `{raw_text, suggestions[]}` | parser-uncertain leaf | n/a |
| `composite_and` | `{children[]}` | recursive | n/a |
| `composite_or` | `{children[]}` | recursive | n/a |

`news_event` is registry-dormant: parser may emit it, evaluator marks rule `status='dormant', dormancy_reason='awaiting_capability:news_feed'`. When Phase 18 flips the capability to `available=true`, dormant rules whose `dormancy_reason` matches stay dormant per umbrella MED-6 — user opts in per-rule via UI notification.

JSON-Schema validator (`predicates.schema.json`) is the second-gate after AI JSON-mode. Validation happens at create-time AND post-edit. Schema lives in `backend/app/services/alerts/predicates.schema.json` and is exported to FE for client-side validation in the predicate editor.

## 5. Parser (`services/alerts/parser.py`)

### Flow

1. Receive `original_nl` from `POST /api/alerts`.
2. Build system prompt that constrains output to the JSON schema (primitive names + slot shapes).
3. Strip user portfolio context: send `{symbols_user_currently_watches: [...]}` from `alerts WHERE jwt_subject=$1` only. No NLV, no cost basis, no account_ids, no positions.
4. Call `services/ai/router.complete(capability=STRUCTURED_OUTPUT, prompt=..., force_local_only=True, response_format='json')`.
5. Validate response against `predicates.schema.json`.
6. If valid AND no `unknown` leaves → return `{parse_status: 'ok', predicate_json, suggestions: []}`.
7. If valid AND any `unknown` leaves → return `{parse_status: 'uncertain', predicate_json, suggestions: [...]}` — user can edit unknowns in FE.
8. If schema validation fails → second attempt with schema-error system message included.
9. If second attempt fails → return `{parse_status: 'failed', partial_predicate: <best-effort>, error_message}`. FE opens manual predicate editor.

### Hard-LOCAL_ONLY enforcement (3-layer, matches 11a)

1. **API boundary:** `_guarded_alerts_call` helper in `alerts.py` asserts `force_local_only=True` before invoking parser.
2. **Parser:** explicitly passes `force_local_only=True` to AI client; never reads any override flag.
3. **AI router:** existing LiteLLM auth-callback rejects cloud routes for LOCAL_ONLY requests (already shipped in 11a).

### Caching

- No parse-result caching. Parse-once-freeze means each rule gets ONE parse at create-time; subsequent edits via the JSON editor bypass the parser entirely.

## 6. Evaluator (`services/alerts/evaluator.py`)

### Lifespan integration

```python
# app/main.py lifespan
alerts_evaluator = AlertsEvaluator(...)
await alerts_evaluator.start()
app.state.alerts_evaluator = alerts_evaluator
yield
await alerts_evaluator.stop()
```

Same pattern as `orphan_sweeper`, `ollama_health_watcher`, `BalanceSnapshotWriter`.

### Data sources

Default: a single async task subscribes to PostgreSQL `LISTEN bars_1m_insert` (added by alembic 0043 as a NOTIFY trigger on `bars_1m`). Trigger payload is `{symbol, ts}` only; full row read by evaluator on demand from `bars_1m`.

Opt-in: rules with `tick_subscribed=true` additionally enqueue from `ticks_subscriber.py`, which subscribes to Phase 7b.1 quote-engine WS for the union of symbols across all opt-in rules. On WS disconnect, falls back to bars_1m alone (bounded retry, then dormant with `dormancy_reason='quote_engine_down'`).

### Inverted index

In-memory: `dict[str, set[int]]` mapping `symbol → {alert_id, ...}`. Rebuilt on:

- Lifespan startup (SELECT all `active` alerts).
- Redis pubsub message on channel `app_config:invalidate:alerts` (fires on every CRUD mutation).
- Capability flip on channel `app_config:invalidate:alert_capabilities`.

Per tick: `O(len(symbol_to_rule_ids[symbol]))`, not `O(all_rules)`.

### Bounded queue

`asyncio.Queue(maxsize=1000)` between producer (bars_1m LISTEN or tick subscription) and consumer (evaluator worker). Drop-oldest on overflow via `try: q.put_nowait(...); except QueueFull: q.get_nowait(); q.put_nowait(...); counter.inc()`. Counter: `alerts_evaluator_queue_dropped_total`.

### Throttling

Per `(rule_id, symbol)` 500ms debounce in addition to the queue. Implemented as `dict[tuple[int,str], float]` mapping last-eval timestamp; evaluator skips if `now - last < 0.5`.

### Per-rule fail-isolation

`try: evaluate(rule, snapshot); except Exception as exc: log + counter + rule.consecutive_eval_errors += 1; if >= 10: rule.status='disabled', dormancy_reason='eval_error_threshold'`. Reset to 0 on successful eval.

### Watchdog

`alerts_evaluator_tick_duration_seconds` Histogram; structured `WARN` log when p99 > 50ms over 60s window.

## 7. Dry-run replay (`services/alerts/dry_run.py`)

Resolution-aware per umbrella HIGH-10:

- Predicate window ≥ 1 day → replay against `bars_1d` CAGG, last 30d.
- Predicate window 1m–24h → replay against `bars_1m`, last 24h.
- Predicate window < 1m → `replay_resolution: 'insufficient'`; UI requires checkbox "I understand backtest is unreliable" before Confirm activates.

Output: `{replay_resolution, fire_count, sample_fires: [{ts, evaluated_values_snippet}, ...]}`. `sample_fires` is bounded to 10 examples; truncates with `truncated: true` flag.

Replay is offered:

- Inline as part of `POST /api/alerts` (returns dry_run in same response — single round-trip for create-flow).
- Standalone via `POST /api/alerts/dry-run` (predicate JSON in body; no DB write) — used by the JSON editor's "test predicate" button.

## 8. Delivery dispatcher (`services/alerts/delivery.py`)

### Channel ABC

```python
class AlertChannel(ABC):
    name: ClassVar[str]
    @abstractmethod
    async def deliver(self, fire: AlertFire, config: dict) -> DeliveryOutcome: ...
```

`DeliveryOutcome` is `Enum {sent, failed, throttled, channel_unavailable}`.

### Channels at 11b

| Channel | Class | Status at 11b |
|---|---|---|
| InApp | `InAppChannel` | Real. Publishes to Redis `alerts:fire:{jwt_subject}`. FE WS `/ws/alerts/feed` re-broadcasts. |
| Webhook | `WebhookChannel` | Real. POST to user-configured URL. HMAC-SHA256 signed header `X-Alerts-Signature`. 5s timeout. 3 retries with exponential backoff (1s, 3s, 9s). 4xx = no retry; 5xx + timeout = retry. |
| Telegram | `TelegramChannel` | **Stub.** Logs info, returns `DeliveryOutcome.channel_unavailable`. Wired at 11c. |

### Webhook config

`webhook_configs` lives in `app_config` namespace `alerts/webhooks` as JSON:
```json
[
  {"id": "wh_pushover_1", "url": "https://...", "secret_ref": "alerts.webhook.<id>.secret"}
]
```
Secret value lives in `app_secrets` Fernet-encrypted under `alerts.webhook.<id>.secret`. HMAC computed over the JSON-serialised fire payload.

### Fail-isolation

Per-channel `try/except → counter → return DeliveryOutcome.failed`. One channel's failure never blocks another's. Outcomes recorded in `alert_fires.delivery_outcomes` JSONB.

## 9. REST endpoints (`app/api/alerts.py` — 6 total)

All gated on `require_jwt`. All use shared `_guarded_alerts_call` helper for LOCAL_ONLY assertion + rate-limit + CSRF + 5-arm exception mapping (matches 11a's `_guarded_ai_call` pattern).

| Endpoint | Verb | Purpose | CSRF? | Rate-limited? | 404 defence? |
|---|---|---|---|---|---|
| `/api/alerts` | `POST` | Create from NL or from raw predicate_json; returns parse+dry_run | yes | 5/min per jwt_subject | n/a (no id yet) |
| `/api/alerts/{id}` | `GET` | Get one rule | no | no | **yes** (404 on unknown-id AND cross-jwt-subject) |
| `/api/alerts/{id}` | `PUT` | Edit predicate_json (skips parser; validates against schema) | yes | no | yes |
| `/api/alerts/{id}` | `DELETE` | Soft-delete | yes | no | yes |
| `/api/alerts/{id}/confirm` | `POST` | Flip status pending → active | yes | no | yes |
| `/api/alerts` | `GET` | List rules for jwt_subject | no | no | n/a |
| `/api/alerts/dry-run` | `POST` | One-shot replay against arbitrary predicate_json | no | 10/min | n/a |

Net: 7 FastAPI operations across 5 URL paths (`/api/alerts`, `/api/alerts/{id}`, `/api/alerts/{id}/confirm`, `/api/alerts/dry-run`, plus the `/api/alerts` collection-GET).

### POST `/api/alerts` request shape

```json
{
  "user_label": "AAPL above 200",
  "original_nl": "tell me when AAPL closes above 200 for 3 days",
  "predicate_json": null,   // if set, bypasses parser; PUT-like create
  "delivery_channels": ["in_app"],
  "tick_subscribed": false
}
```

Response:
```json
{
  "id": 42,
  "parse_status": "ok",
  "predicate_json": {...},
  "requires_capabilities": [],
  "suggestions": [],
  "dry_run": {"replay_resolution": "1m", "fire_count": 7, "sample_fires": [...]},
  "status": "pending"
}
```

User reviews → calls `POST /api/alerts/42/confirm` with CSRF nonce → status flips to `active`.

### Rate-limiter wiring

`services/alerts/rate_limiter.py` imports `services/common/rate_limiter.SlidingWindowRateLimiter` and configures `{create_window_seconds: 60, create_max: 5, dry_run_window_seconds: 60, dry_run_max: 10}` per `jwt_subject`. No new generic.

## 10. WebSocket endpoint (`app/api/ws_alerts.py`)

Adopts `services/common/ws_envelope.make_ws_endpoint(...)` — same pattern as 11a's `ws_ai.py`.

Per umbrella MED-4:
- CSWSH origin check pre-accept; close 1008 on mismatch.
- v=1 frame schema.
- `pubsub.listen()` consumer for Redis channel `alerts:fire:{jwt_subject}`.
- Recv-drain task surfacing `WebSocketDisconnect`.
- Module connection cap 20.

Endpoint: `WS /ws/alerts/feed` (per-user, scoped by jwt_subject from query-param JWT). Frame shape:

```json
{"v": 1, "type": "fire", "alert_id": 42, "fired_at": "...", "verdict": "true",
 "evaluated_values": {...},  // surfaced for top-bar bell dropdown
 "user_label": "AAPL above 200"}
```

`evaluated_values` is the full snapshot from `alert_fire_context` (PII boundary stays server-side: alerts WS is per-user authenticated, same trust level as REST).

## 11. Frontend (`features/alerts/`)

### Routes

- `/alerts` → `AlertsPage.tsx` — list with active/dormant/disabled tabs.
- `/alerts/$alertId` → `AlertDetailPage.tsx` — predicate visualiser, fire history, dry-run re-run button, edit/disable.
- Top-bar bell-icon → `BellDropdown.tsx` — recent fires (WS push via `useAlertsFeed`).

### Components

- `CreateAlertModal.tsx` — NL textbox → AI parse → confirmation card showing parsed predicate + dry-run replay + suggestions. Confirm/Edit/Reject buttons.
- `ParseFailedEditor.tsx` — opens when `parse_status='failed'`. Monaco-editor JSON predicate with schema-driven IntelliSense (via `predicates.schema.json` exported from BE).
- `PredicateJsonEditor.tsx` — same editor reused for `/alerts/$alertId` "Edit Predicate" mode.
- `PredicateVisualiser.tsx` — read-only tree rendering of `predicate_json` (composite_and / composite_or as collapsible nodes; primitives as leaf rows).
- `DryRunPanel.tsx` — replay resolution banner, fire-count, sample fires table, "re-run" button, insufficient-resolution checkbox.
- `WebhookConfigPanel.tsx` — under `/admin/alerts/webhooks` (admin-only; CSRF nonce on save).

### Services

- `services/alerts/api.ts` — `postAlert`, `getAlert`, `putAlert`, `deleteAlert`, `confirmAlert`, `listAlerts`, `postDryRun`. Same-origin guard. CSRF via `services/admin/api.ts::mintCsrfNonce` for mutations.
- `services/alerts/types.ts` — re-exports from `api-generated.ts`; hand-curated `AlertWsFrame`.
- `services/alerts/useAlertsFeed.ts` — TanStack Query 10s poll for list + WS push via `setQueryData` invalidation. Bounded backoff `[500, 1500, 5000, 15000]` matching 11a's `useChatStream`. Same-origin WS URL guard.
- `services/alerts/useDryRun.ts` — wraps `postDryRun` with TanStack Query mutation.

### Stores

- `stores/global/alerts.ts` — zustand-persist. Stores recent fires (capped 50 FIFO) for the bell dropdown when WS isn't connected. Migrate guard against corrupted localStorage matching 11a's `stores/global/ai.ts`.

## 12. Capability registry (alembic 0044 + `app_config[alert_capabilities]`)

Single source of truth: `alert_capabilities` table. Mirror in `app_config` for fast in-memory read with pubsub invalidation:

- `app_config[alert_capabilities]` = `{news_feed: false, filings_feed: false, earnings_calendar: false}`.
- Admin endpoint `PUT /api/admin/alert-capabilities/{name}` flips the flag (CSRF + JWT-admin).
- Publishes `app_config:invalidate:alert_capabilities`.
- Evaluator subscribes; on flip-to-false, marks matching active rules dormant in same SAVEPOINT'd transaction; on flip-to-true, dormant rules stay dormant (UI notification per umbrella MED-6).
- On pubsub delivery failure: capability treated as **unavailable** for 60s cache TTL (fail-CLOSED for the soft-conditions mitigation).

## 13. Error handling (full taxonomy)

| Scenario | Status | Detail |
|---|---|---|
| POST /alerts rate-limited | 429 | `Retry-After: 60` |
| POST /alerts parse_failed | 200 | `{parse_status:'failed', partial_predicate, error_message}` |
| POST /alerts AI router down | 503 | `{error_code:'parser_unavailable'}`; user retries |
| GET/PUT/DELETE /alerts/{id} unknown-id OR cross-subject | 404 | identical body (existence-oracle defence) |
| /confirm on already-active rule | 409 | `{error_code:'already_active'}` |
| Predicate JSON-schema validation failure (PUT) | 422 | `{error_code:'invalid_predicate', schema_errors:[...]}` |
| CSRF nonce missing/expired | 401 | `{error_code:'csrf_required'}` |
| Capability unavailable on confirm | 200 | rule created with `status='dormant', dormancy_reason='awaiting_capability:<name>'` |
| Evaluator per-rule error | n/a (counter) | `alerts_evaluator_eval_errors_total`; 10 in a row → auto-disable |
| Bounded queue overflow | n/a (counter) | `alerts_evaluator_queue_dropped_total`; drop-oldest |
| Channel delivery failure | n/a (counter) | `alerts_delivery_failures_total{channel}`; isolated per-channel |
| WS connection cap exceeded | 1013 close | per envelope |
| WS origin mismatch | 1008 close | per envelope |
| WS frame send timeout | force-close | `alerts_ws_send_timeout_total` |

## 14. Metrics (10 new `alerts_*` series matching umbrella §6)

| Metric | Type | Labels |
|---|---|---|
| `alerts_evaluator_ticks_total` | Counter | — |
| `alerts_evaluator_tick_duration_seconds` | Histogram | — |
| `alerts_evaluator_queue_dropped_total` | Counter | — |
| `alerts_evaluator_eval_errors_total` | Counter | `rule_id_bucket` |
| `alerts_evaluator_data_unavailable_total` | Counter | `check_type` |
| `alerts_fires_total` | Counter | `rule_id_bucket`, `status` |
| `alerts_delivery_total` | Counter | `channel`, `outcome` |
| `alerts_delivery_failures_total` | Counter | `channel` |
| `alerts_capability_unavailable_total` | Counter | `capability` |
| `alerts_active_rules` | Gauge | — |

Plus 2 WS counters: `alerts_ws_send_timeout_total`, `alerts_ws_active_connections` (Gauge).

`rule_id_bucket` is HMAC-with-deploy-salt per umbrella MED-10.

## 15. Testing strategy

### Backend (~45 tests baseline + per-primitive)

- **Per primitive (10 × ≥3 golden vectors = 30 tests):** `test_predicates.py` parametrized over `(input_state, predicate, expected_verdict)`.
- **Parser (5 tests):** mocked AI client returning canonical predicate / schema-invalid / unknown-leaf / second-try-fail / capability-missing. Use 11a's `services/ai/test_doubles.py` fakes.
- **Evaluator (8 tests):** inverted-index rebuild on pubsub, bounded queue drop-oldest, 500ms debounce, per-rule fail-isolation 10-error auto-disable, capability flip dormancy, tick-subscription opt-in, bars_1m LISTEN integration, watchdog histogram.
- **Dry-run (4 tests):** bars_1d resolution, bars_1m resolution, insufficient resolution, truncated sample.
- **Delivery (6 tests):** InApp publish to Redis, Webhook HMAC + retry success, Webhook 4xx no-retry, Webhook 5xx exhausted retries, Telegram stub no-op, per-channel fail-isolation.
- **REST (12 tests):** POST happy path, POST parse_failed, POST rate-limited, GET 404 unknown, GET 404 cross-subject (existence-oracle), PUT schema-invalid, PUT cross-subject 404, DELETE cross-subject 404, confirm CSRF missing, confirm already-active 409, list scoped to subject, dry-run standalone.
- **WS (3 tests):** envelope origin check, pubsub fanout to subscriber, recv-drain detects disconnect.

### Frontend (~10 tests)

- `useAlertsFeed` (3): poll fallback when WS down, WS push updates query data, reconnect backoff.
- `CreateAlertModal` (2): NL submit → parsed card render, parse_failed → editor opens.
- `PredicateJsonEditor` (2): schema validation inline, save calls PUT.
- `BellDropdown` (1): WS push appends to top.
- `AlertsPage` (2): tab filter, delete confirms.

### Playwright (2 test.fixme'd until docker-compose harness lands)

- Create-rule golden path (NL → parse → confirm → see in list).
- Parse-failed → JSON editor → save → see in list.

## 16. Versioning

- Tag at chunk-D close: **v0.11.1**.
- Per-chunk patches if reviewer-fix batches needed: v0.11.1.1, v0.11.1.2, ...
- Phase 11 umbrella close (after 11d) bumps to v0.11.3.
- Phase 12 starts at v0.12.0.

Per `feedback_sub_phase_versioning.md`: `0.x.y.z` with `x = §N` for ALL phases.

## 17. Chunk decomposition (4 chunks, ~6-8 commits each)

### Chunk A — Schema + parser + predicates (v0.11.1.0)
- Alembic 0043 (tables + bars_1m LISTEN/NOTIFY trigger) + 0044.
- `services/alerts/predicates.py` + `predicates.schema.json`.
- `services/alerts/parser.py` (hard-LOCAL_ONLY + parse-once-freeze).
- `services/alerts/rules.py` CRUD layer.
- 30 primitive tests + 5 parser tests + 6 rule tests.

### Chunk B — Evaluator + dry-run + tick-opt-in + retention (v0.11.1.1)
- `services/alerts/evaluator.py` (inverted index + bounded queue + debounce + per-rule fail-isolation).
- `services/alerts/ticks_subscriber.py` (Phase 7b.1 WS subscription opt-in).
- `services/alerts/dry_run.py` (resolution-aware).
- `services/alerts/retention.py` (apscheduler 90d cleanup of `alert_fire_context`).
- 8 evaluator tests + 4 dry-run tests + 1 ticks_subscriber test + 1 retention test.

### Chunk C — Delivery + WS + REST (v0.11.1.2)
- `services/alerts/delivery.py` + channels (InApp, Webhook, Telegram-stub).
- `app/api/alerts.py` (6 endpoints + `_guarded_alerts_call` helper).
- `app/api/ws_alerts.py` (via shared envelope).
- `services/alerts/rate_limiter.py`.
- 6 delivery tests + 12 REST tests + 3 WS tests.

### Chunk D — Frontend (v0.11.1.3, may roll into v0.11.1)
- `services/alerts/` (types, api, useAlertsFeed, useDryRun).
- `stores/global/alerts.ts` (zustand-persist).
- `features/alerts/` (8 components).
- Routes `/alerts`, `/alerts/$alertId`.
- BellDropdown into top bar.
- 10 FE tests + 2 Playwright (fixme'd).

Each chunk closes with the 5-reviewer chain (haiku spec/python/typescript + sonnet code/security/silent-failure) per `feedback_review_per_chunk.md`. Findings applied through MED inline per `feedback_architect_findings_apply_through_medium.md`.

## 18. Known limitations (carried from umbrella + new)

1. No direct-JSON predicate editor in the primary create flow — only opens on `parse_failed` or edit (per spec line 471, mostly upheld).
2. No fire-history retention shortening below 90d for `alert_fire_context` — manual cleanup if disk pressure.
3. Tick-subscription is best-effort; quote-engine WS disconnect drops to bars_1m fallback (already documented as `dormancy_reason='quote_engine_down'`).
4. Single-replica evaluator; multi-worker deferred to Phase 24 (same as 11a rate-limiter, position-sizing limiter, portfolio rate-limiter).
5. No backfill replay for alerts created today over data older than 30d — dry-run window capped at 30d bars_1d / 24h bars_1m.
6. Webhook delivery has no per-channel rate-limit beyond the dispatcher's 3-retry budget; user-configured URL is trusted.
7. Bell dropdown shows ≤ 50 recent fires; deeper history requires `/alerts/$alertId` detail page.

## 19. Out of scope (Phase 11b)

- Telegram outbound delivery → 11c.
- Telegram inbound commands / trade execution → 11c / 11d.
- News / filings / earnings ingest → Phase 18 (capability registry slot reserved).
- Per-user budget enforcement → Phase 24+.
- Multi-tenant alerts surface → out of scope for the whole Phase-11 umbrella.

## 20. Cross-references

- **Umbrella spec:** `docs/superpowers/specs/2026-05-12-phase11-ai-router-alerts-telegram-design.md` (architect-reviewed commit 077324c).
- **Predecessor memory:** `phase11a_shipped.md` — load-bearing patterns reused here.
- **Patterns reused:** `feedback_review_per_chunk.md`, `feedback_reviewer_spec_inline.md`, `feedback_codex_routing_strict.md`, `codex_defaults.md`, `feedback_architect_findings_apply_through_medium.md`.
- **Architect review:** pending (will commit findings as `docs(phase11b): apply ARCHITECT-REVIEW findings inline` immediately after this spec lands).
