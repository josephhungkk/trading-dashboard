# Phase 11b — Alerts Engine — Design Spec

**Status:** brainstormed 2026-05-13; **ARCHITECT-REVIEW applied 2026-05-13** (1 CRIT + 8 HIGH + 8 MED inline; 5 LOW deferred).
**Tag target:** v0.11.1.0 (chunk A) through v0.11.1.3 (chunk D); patch bump within umbrella `v0.11.x` per sub-phase versioning convention.
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
| Predicate primitives | 9: price_threshold, pct_change_window, ma_cross, volume_spike, order_event, ai_signal, unknown, composite_and, composite_or | **10** — adds `news_event` (parser-aware, registry-dormant via `app_config[alert_capabilities/news_feed=false]`) | Forward-compat hook for Phase 18; `ensure_seeded` populates `news_feed=false` so the registry path is open |
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

## 3. Schema (alembic 0043 only — 0044 dropped per HIGH-7)

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

-- GIN indexes for JSONB queries (added during migration; cheap now, hard to retrofit later)
CREATE INDEX idx_alerts_predicate_gin ON alerts USING GIN (predicate_json jsonb_path_ops)
  WHERE status IN ('active', 'dormant');
-- Enables: SELECT * FROM alerts WHERE predicate_json @? '$.**.symbol ? (@ == "AAPL")'
-- jsonb_path_ops is smaller than the default operator class.

CREATE INDEX idx_alerts_requires_capabilities_gin ON alerts USING GIN (requires_capabilities)
  WHERE status IN ('active', 'dormant');
-- Enables fast "find all rules requiring capability X" on flip-to-true/false.

-- bars_1m AFTER INSERT trigger for evaluator LISTEN/NOTIFY
CREATE OR REPLACE FUNCTION notify_bars_1m_insert() RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_notify(
    'bars_1m_insert',
    json_build_object('inst_id', NEW.instrument_id, 'ts', extract(epoch from NEW.bucket_start))::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_bars_1m_notify AFTER INSERT ON bars_1m
  FOR EACH ROW EXECUTE FUNCTION notify_bars_1m_insert();
-- bars_1m is NOT compressed (verified against alembic 0024); trigger fires cleanly on every row.
-- Re-checked by every alembic migration that touches bars_1m so chunk-management ops don't drop it.

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

### 0044 — (no separate table)

**Capability registry is single-source via `app_config[alert_capabilities]`** — same pattern as 11a's `app_config[ai_router/capability_map]`. No parallel SQL table; the original 0044 design was reviewed out (HIGH-7) because two stores create a split-brain risk that the umbrella's two-store rotation pattern would have to solve unnecessarily.

Seed-if-missing on lifespan startup (`app/services/alerts/capabilities.py::ensure_seeded`):
```json
{
  "news_feed":         {"available": false, "description": "Phase 18 news ingest"},
  "filings_feed":      {"available": false, "description": "Phase 18 SEC filings ingest"},
  "earnings_calendar": {"available": false, "description": "Phase 18 earnings calendar"}
}
```

So the alembic 0044 slot is effectively unused for 11b; the migration list collapses to **only 0043**.

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

Default: a single async task subscribes to PostgreSQL `LISTEN bars_1m_insert` (added by alembic 0043 as a NOTIFY trigger on `bars_1m`). Trigger payload is `json_build_object('inst_id', NEW.instrument_id, 'ts', extract(epoch from NEW.bucket_start))::text` — small (well under the 8000-byte NOTIFY limit). Full row read by evaluator on demand from `bars_1m`. `bars_1m` is NOT compressed (verified against alembic 0024) so AFTER INSERT triggers fire cleanly on every row including chunk-creation INSERTs. The trigger is explicitly checked at every alembic migration that touches `bars_1m` so chunk-management ops don't drop it.

**Polling fallback:** `app_config[alerts/eval_data_source] = 'listen' | 'poll'` (default `listen`). Operator can flip to `poll` (5s `SELECT ... FROM bars_1m WHERE bucket_start > $last`) if NOTIFY ever becomes a bottleneck under load. Counter `alerts_evaluator_listen_lag_seconds` (Histogram) makes the flip-decision visible.

Opt-in: rules with `tick_subscribed=true` additionally enqueue from `ticks_subscriber.py`, which subscribes to the **internal Redis pubsub bus `quote.<source>.<canonical_id>`** (Phase 7b.1's in-cluster fanout layer — NOT the FE-facing `/ws/quotes` MessagePack gateway). Symbols are resolved via `InstrumentResolver.find_by_alias` (same chokepoint Phase 10b.1 position-sizing uses for conid resolution). Subscriptions register through `services/quotes/subscription_manager.register_internal_subscriber(name='alerts', symbols=[...])` so Phase 7b.1's global subscription cap (5000) accounting holds. On bus disconnect or symbol-resolution failure, falls back to bars_1m alone (bounded retry); after 3 retries, rule transitions `active → dormant` with `dormancy_reason='quote_engine_down'`.

### Inverted index

In-memory: `dict[str, set[int]]` mapping `symbol → {alert_id, ...}`. Rebuilt on:

- Lifespan startup (SELECT all `active` alerts — covered by `idx_alerts_active_by_subject` partial index from §3).
- Redis pubsub message on channel `app_config:invalidate:alerts` — sets `_rebuild_pending = True` and wakes a debounced rebuild task; the task waits 250ms then performs ONE rebuild, **coalescing any further pubsub messages that arrived during the wait** (matches Phase 10b.2 portfolio WS "250ms compute cache + 500ms debounce" pattern). Counter `alerts_evaluator_snapshot_rebuilds_total` + `alerts_evaluator_snapshot_rebuild_coalesced_total`.
- Capability flip on channel `app_config:invalidate:alert_capabilities` (same coalescing).

Per tick: `O(len(symbol_to_rule_ids[symbol]))`, not `O(all_rules)`.

### Throttling (producer-side debounce — applied BEFORE queue insertion)

Per `(rule_id, symbol)` 500ms debounce applied **at producer side** by `bars_1m_listener` and `ticks_subscriber`. Producers maintain `dict[tuple[int,str], float]` of last-enqueued timestamps; if `now - last < 0.5`, the event is dropped at source with counter `alerts_evaluator_debounced_total{source}` and does NOT consume queue capacity. This prevents a hot symbol with many opt-in tick rules from starving evaluations on other symbols.

**Eviction:** Debounce dict is swept every 60s by an `asyncio.create_task` loop owned by the evaluator. Any `(rule_id, symbol)` whose timestamp is older than `max(window_seconds * 10, 60s)` is dropped. Counter `alerts_evaluator_debounce_evicted_total`. Sweep loop cancelled on `stop()` alongside the main worker. Pattern matches the `evict_stale` calls in `SlidingWindowRateLimiter[K]`, `AIRouterRateLimiter`, and Phase 10b.2's `PortfolioRateLimiter`.

### Bounded queue

`asyncio.Queue(maxsize=1000)` between the debounce-passed producer output and the evaluator worker. Drop-oldest on overflow via `try: q.put_nowait(...); except QueueFull: q.get_nowait(); q.put_nowait(...); counter.inc()`. Counter: `alerts_evaluator_queue_dropped_total`. Drop-oldest applies only on genuine downstream-consumer stalls, not on hot-symbol bursts (those are already gated by the producer-side debounce above).

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
| Webhook | `WebhookChannel` | Real. POST to user-configured URL with SSRF validation (see below). HMAC-SHA256 signed header `X-Alerts-Signature`. 5s timeout per attempt; per-fire timeout budget hard-capped at 30s. 3 retries with exponential backoff (1s, 3s, 9s). 4xx = no retry; 5xx + timeout = retry. Per-webhook in-flight `asyncio.Semaphore(4)`; excess deliveries return `DeliveryOutcome.throttled` + counter `alerts_delivery_throttled_total{channel='webhook'}` (back-pressure for user's downstream system, NOT enqueued). |
| Telegram | `TelegramChannel` | **Stub.** Logs info, returns `DeliveryOutcome.channel_unavailable`. Wired at 11c. |

### Webhook config

`webhook_configs` lives in `app_config` namespace `alerts/webhooks` as JSON:
```json
[
  {"id": "wh_pushover_1", "url": "https://...", "secret_ref": "alerts.webhook.<id>.secret"}
]
```
Secret value lives in `app_secrets` Fernet-encrypted under `alerts.webhook.<id>.secret`. HMAC computed over the JSON-serialised fire payload.

### Webhook URL validation (SSRF defence — CRIT)

Backend has direct WG/LAN reach to PG (`10.10.0.2:5432`), broker sidecars (`10.10.0.x`), heavy box (`10.10.0.3:11434`), and docker-internal LiteLLM (`litellm:4000`). A user-configured webhook URL pointing at any of those becomes an arbitrary internal HTTP client. CLAUDE.md security: "Postgres reachable only via WG / never public" — the webhook channel must not route around that boundary.

`services/alerts/channels/webhook.py::_validate_url(url)` is called BEFORE every `httpx.post` (including each retry, for DNS-rebinding defence):

- **Scheme:** must be `https://` only — reject `http`, `file`, `gopher`, `ftp`, `data`, anything else.
- **Hostname:** reject `localhost`, `*.local`, `*.internal`, `*.svc.cluster.local`, and any literal IP that resolves to private/loopback/link-local/reserved/multicast ranges per `ipaddress.ip_address(...).is_private | is_loopback | is_link_local | is_reserved | is_multicast`.
- **DNS resolve:** call `socket.getaddrinfo(host, None)` and reject if ANY resolved address fails the IP check above. Re-resolve on every retry (defence against DNS-rebinding between attempts).
- **Port:** reject ports `<1024` except `443`.
- **Failure:** raise `WebhookUrlRejected(reason)`; channel returns `DeliveryOutcome.failed` with `error_code: 'webhook_url_invalid'`; counter `alerts_webhook_url_rejected_total{reason}` increments with `reason ∈ {scheme, hostname, private_ip, port, dns_rebinding}`.

### Fail-isolation

Per-channel `try/except → counter → return DeliveryOutcome.failed`. One channel's failure never blocks another's. Outcomes recorded in `alert_fires.delivery_outcomes` JSONB.

## 9. REST endpoints (`app/api/alerts.py` — 7 total)

All gated on `require_jwt`. All use shared `_guarded_alerts_call` helper for rate-limit + CSRF + 5-arm exception mapping (matches 11a's `_guarded_ai_call` pattern).

| Endpoint | Verb | Purpose | CSRF? | Rate-limited? | 404 defence? |
|---|---|---|---|---|---|
| `/api/alerts` | `POST` | Create from NL or from raw predicate_json; returns parse+dry_run | yes | 5/min per jwt_subject | n/a (no id yet) |
| `/api/alerts/{id}` | `GET` | Get one rule | no | no | **yes** (404 on unknown-id AND cross-jwt-subject) |
| `/api/alerts/{id}` | `PUT` | Edit predicate_json (skips parser; validates against schema) | yes | no | yes |
| `/api/alerts/{id}` | `DELETE` | Soft-delete | yes | no | yes |
| `/api/alerts/{id}/confirm` | `POST` | Flip status pending → active | yes | no | yes |
| `/api/alerts` | `GET` | List rules for jwt_subject | no | no | n/a |
| `/api/alerts/dry-run` | `POST` | One-shot replay against arbitrary predicate_json | no | 10/min | n/a |
| `/api/alerts/recent-fires` | `GET` | Last 50 fires across all user's rules (WS-reconnect backfill); accepts `?since=<ISO ts>&limit=<n≤200>` | no | no | n/a (scoped to jwt_subject) |

Net: 8 FastAPI operations across 6 URL paths.

### `_guarded_alerts_call` helper responsibilities

(a) JWT extraction + subject-scoping for 404 defence.
(b) CSRF nonce consumption on POST/PUT/DELETE/confirm via `consume_confirmation_nonce`.
(c) Rate-limit check via `SlidingWindowRateLimiter[K]` on POST `/alerts` (5/min) and POST `/alerts/dry-run` (10/min).
(d) 5-arm exception → HTTP mapping: `RuleNotFoundError → 404`, `RuleCrossSubjectError → 404 identical body`, `RateLimitExceededError → 429 Retry-After: 60`, `PredicateValidationError → 422 with schema_errors`, `ParserUnavailableError → 503`, fallthrough → 500 with `error_code: 'internal'`.

Helper does NOT inject LOCAL_ONLY assertion — that lives in `parser.py` only since other endpoints don't call AI.

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
- `services/alerts/useAlertsFeed.ts` — TanStack Query 10s poll for list + WS push via `setQueryData` invalidation. Bounded backoff `[500, 1500, 5000, 15000]` matching 11a's `useChatStream`. Same-origin WS URL guard. **On every WS (re)connect**, BEFORE opening the WS, issues `GET /api/alerts/recent-fires?since=<last_seen_at>&limit=50` to backfill any fires that landed during disconnect; merges into the bell store de-duped by `fire_id`. `last_seen_at` is persisted in `stores/global/alerts.ts` and updated on every fire received (push or backfill). This closes the silent-miss window when the FE is offline (laptop sleep, network drop) — a CRIT-class concern for an alerts product.
- `services/alerts/useDryRun.ts` — wraps `postDryRun` with TanStack Query mutation.

### Stores

- `stores/global/alerts.ts` — zustand-persist. Stores recent fires (capped 50 FIFO) for the bell dropdown when WS isn't connected, plus `last_seen_at: string | null` for reconnect-backfill. Migrate guard against corrupted localStorage matching 11a's `stores/global/ai.ts`.

## 12. Capability registry (single-source via `app_config[alert_capabilities]`)

**Single source of truth: `app_config[alert_capabilities]`.** No parallel SQL table (HIGH-7 single-source revision). Matches 11a's `app_config[ai_router/capability_map]` pattern.

- Lifespan `ensure_seeded` populates the namespace on first startup with `{news_feed, filings_feed, earnings_calendar}` all `available=false`.
- Admin endpoint `PUT /api/admin/alert-capabilities/{name}` flips the flag (CSRF + JWT-admin); writes via the standard `app_config` admin update path.
- Publishes `app_config:invalidate:alert_capabilities`.
- Evaluator subscribes; on flip-to-false, marks matching active rules dormant in same SAVEPOINT'd transaction (using the GIN index on `requires_capabilities` from §3); on flip-to-true, dormant rules stay dormant (UI notification per umbrella MED-6).
- On pubsub delivery failure: capability treated as **unavailable** for 60s cache TTL (fail-CLOSED for the soft-conditions mitigation).
- Evaluator caches the namespace with 60s TTL + pubsub invalidation — same pattern as `services/ai/secrets.py`.

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

### Log redaction (PII)

`original_nl` and `predicate_json` may contain user PII (NLV figures, account names, position sizes in free text). Evaluator/parser error logs MUST emit `alert_id` only, never the rule body — per CLAUDE.md security: "Never log API keys/tokens/passwords — structlog redacts via processor in `app/core/logging.py`." Add `original_nl`, `predicate_json`, and `evaluated_values` to the structlog redaction allowlist. Tests assert no log line contains `original_nl` content when an evaluator exception is raised.

## 14. Metrics (~14 new `alerts_*` series matching umbrella §6 + chunk-B additions)

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
| `alerts_evaluator_debounced_total` | Counter | `source` (∈ `{listen, ticks}`) |
| `alerts_evaluator_debounce_evicted_total` | Counter | — |
| `alerts_evaluator_snapshot_rebuilds_total` | Counter | — |
| `alerts_evaluator_snapshot_rebuild_coalesced_total` | Counter | — |
| `alerts_evaluator_listen_lag_seconds` | Histogram | — |
| `alerts_delivery_throttled_total` | Counter | `channel` |
| `alerts_webhook_url_rejected_total` | Counter | `reason` (∈ `{scheme, hostname, private_ip, port, dns_rebinding}`) |

Plus 2 WS counters: `alerts_ws_send_timeout_total`, `alerts_ws_active_connections` (Gauge).

`rule_id_bucket` is HMAC-with-deploy-salt per umbrella MED-10.

## 15. Testing strategy

### Backend (~45 tests baseline + per-primitive)

- **Per primitive (10 × ≥3 golden vectors = 30 tests):** `test_predicates.py` parametrized over `(input_state, predicate, expected_verdict)`.
- **Parser (6 tests):** mocked AI client returning canonical predicate / schema-invalid / unknown-leaf / second-try-fail / capability-missing. **Plus `test_parser_request_payload_strips_portfolio_context`** — asserts request body to `AICompletionClient` contains keys ⊆ `{system_prompt, user_text, symbols_user_currently_watches}` and contains NONE of the substrings `nlv`, `cost_basis`, `account_id`, `position`, `cash`, `currency`, `broker_id` even if the user's input mentions them. Uses 11a's `services/ai/test_doubles.py` fakes; mocked client captures requests via `__call__` MagicMock.
- **Evaluator (12 tests):** inverted-index rebuild on pubsub, **snapshot rebuild coalescing (10 rapid pubsubs → 1 rebuild within 250-500ms)**, bounded queue drop-oldest, **producer-side debounce gates 1500 single-symbol events down to 2 without consuming queue capacity for other symbols**, **debounce sweep drops stale entries after 60s**, per-rule fail-isolation 10-error auto-disable, capability flip dormancy, tick-subscription opt-in via internal Redis bus (not /ws/quotes), symbol resolution via InstrumentResolver, fallback to bars_1m on disconnect + dormant after 3 retries, **listen vs poll mode flip via app_config**, watchdog histogram.
- **Dry-run (4 tests):** bars_1d resolution, bars_1m resolution, insufficient resolution, truncated sample.
- **Delivery (9 tests):** InApp publish to Redis, Webhook HMAC + retry success, Webhook 4xx no-retry, Webhook 5xx exhausted retries, Telegram stub no-op, per-channel fail-isolation, **Webhook rejects RFC1918 / loopback / link-local / litellm hostname / DNS-rebind via second-resolve**, **Webhook semaphore back-pressure drops 5th concurrent attempt with throttled outcome**, **Webhook 30s per-fire timeout budget enforced**.
- **REST (14 tests):** POST happy path, POST parse_failed, POST rate-limited, GET 404 unknown, GET 404 cross-subject (existence-oracle), PUT schema-invalid, PUT cross-subject 404, DELETE cross-subject 404, confirm CSRF missing, confirm already-active 409, list scoped to subject, dry-run standalone, **recent-fires backfill returns correct since-window scoped to subject**, **recent-fires cross-subject returns 0 rows (no leak)**.
- **WS (3 tests):** envelope origin check, pubsub fanout to subscriber, recv-drain detects disconnect.
- **PII redaction (1 test):** evaluator error log lines do not contain `original_nl` substring even when `rule.evaluation` raises.

### Frontend (~10 tests)

- `useAlertsFeed` (4): poll fallback when WS down, WS push updates query data, reconnect backoff, **on reconnect calls `/api/alerts/recent-fires?since=<last_seen_at>` and merges results de-duped by `fire_id`**.
- `CreateAlertModal` (2): NL submit → parsed card render, parse_failed → editor opens.
- `PredicateJsonEditor` (2): schema validation inline, save calls PUT.
- `BellDropdown` (1): WS push appends to top.
- `AlertsPage` (2): tab filter, delete confirms.

### Playwright (2 test.fixme'd until docker-compose harness lands)

- Create-rule golden path (NL → parse → confirm → see in list).
- Parse-failed → JSON editor → save → see in list.

## 16. Versioning

- Chunks tag in order: chunk A → **v0.11.1.0**, chunk B → **v0.11.1.1**, chunk C → **v0.11.1.2**, chunk D → **v0.11.1.3** (or earliest patch if chunks collapse).
- Reviewer-fix batches inside a chunk re-use that chunk's z slot (e.g. v0.11.1.0 may absorb chunk-A reviewer fixes).
- Phase 11 umbrella close = 11d's last tag (e.g. `v0.11.3.N`); no additional bump on phase close.
- Phase 12 starts fresh at **v0.12.0** per ROADMAP §12.

Per `feedback_sub_phase_versioning.md`: `0.x.y.z` with `x = §N` for ALL phases.

## 17. Chunk decomposition (4 chunks, ~6-8 commits each)

### Chunk A — Schema + parser + predicates (v0.11.1.0)
- Alembic 0043 (tables + GIN indexes + bars_1m LISTEN/NOTIFY trigger). **No alembic 0044** — capability registry is `app_config`-only (HIGH-7).
- `services/alerts/capabilities.py::ensure_seeded` for app_config seed-if-missing.
- `services/alerts/predicates.py` + `predicates.schema.json`.
- `services/alerts/parser.py` (hard-LOCAL_ONLY + parse-once-freeze + portfolio-context-stripping).
- `services/alerts/rules.py` CRUD layer.
- 30 primitive tests + 6 parser tests + 6 rule tests.

### Chunk B — Evaluator + dry-run + tick-opt-in + retention (v0.11.1.1)
- `services/alerts/evaluator.py` (inverted index w/ coalesced rebuild + producer-side debounce w/ eviction + bounded queue + per-rule fail-isolation).
- `services/alerts/ticks_subscriber.py` (internal Redis bus subscription via `quote.<source>.<canonical_id>`; InstrumentResolver chokepoint; subscription_manager registration).
- `services/alerts/dry_run.py` (resolution-aware).
- `services/alerts/retention.py` (apscheduler 90d cleanup of `alert_fire_context`).
- 12 evaluator tests + 4 dry-run tests + 1 ticks_subscriber test + 1 retention test + 1 PII-redaction test.

### Chunk C — Delivery + WS + REST (v0.11.1.2)
- `services/alerts/delivery.py` + channels (InApp, Webhook with SSRF validation + semaphore + budget, Telegram-stub).
- `app/api/alerts.py` (8 operations / 6 paths + `_guarded_alerts_call` helper + recent-fires backfill endpoint).
- `app/api/ws_alerts.py` (via shared envelope).
- `services/alerts/rate_limiter.py`.
- 9 delivery tests + 14 REST tests + 3 WS tests.

### Chunk D — Frontend (v0.11.1.3)
- `services/alerts/` (types, api, useAlertsFeed with reconnect-backfill, useDryRun).
- `stores/global/alerts.ts` (zustand-persist with `last_seen_at`).
- `features/alerts/` (8 components).
- Routes `/alerts`, `/alerts/$alertId`.
- BellDropdown into top bar.
- 11 FE tests + 2 Playwright (fixme'd — see §18).

Each chunk closes with the 5-reviewer chain (haiku spec/python/typescript + sonnet code/security/silent-failure) per `feedback_review_per_chunk.md`. Findings applied through MED inline per `feedback_architect_findings_apply_through_medium.md`.

## 18. Known limitations (carried from umbrella + new)

1. Direct-JSON predicate editor IS exposed on (a) `parse_failed` outcome of POST `/api/alerts`, and (b) `/alerts/$alertId` "Edit Predicate" mode. It is NOT exposed as the primary create entry point — NL-first remains the primary path. Trade-off accepted per 11a's `useTradeContext` graceful-degrade precedent; partial deviation from umbrella line 471 documented and bounded.
2. No fire-history retention shortening below 90d for `alert_fire_context` — manual cleanup if disk pressure.
3. Tick-subscription is best-effort; internal Redis bus disconnect drops to bars_1m fallback after 3 retries (`dormancy_reason='quote_engine_down'`).
4. Single-replica evaluator; multi-worker deferred to Phase 24 (same as 11a rate-limiter, position-sizing limiter, portfolio rate-limiter).
5. No backfill replay for alerts created today over data older than 30d — dry-run window capped at 30d bars_1d / 24h bars_1m.
6. Webhook deliveries are validated for SSRF (CRIT-1 defence) but otherwise trusted; HMAC-signed; per-webhook semaphore + 30s budget bound the blast radius.
7. Bell dropdown shows ≤ 50 recent fires; deeper history requires `/alerts/$alertId` detail page. **WS reconnect backfill via `GET /api/alerts/recent-fires` closes the offline-window gap** (HIGH-5).
8. Playwright E2E tests for alerts (2 specs in `e2e/alerts.spec.ts`) are `test.fixme(true)`'d pending the docker-compose Playwright harness — same blocker as Phase 11a's 4 fixme'd specs. Until the harness lands, the create-NL → parse → confirm → list golden path is verified only via Vitest component tests + manual smoke. Tracked as Phase 9.5+ Playwright debt; do NOT extend 11b to ship the harness.
9. `ticks_subscriber` depends on Phase 7b.1's internal Redis bus (`quote.<source>.<canonical_id>` + `services/quotes/subscription_manager`); if Phase 7b.1 is reorganised in future, this boundary needs revisit.
10. **LOWs deferred** (5, per ARCHITECT-REVIEW): (a) bell-dropdown evaluated_values truncation post-launch if needed; (b) `dormancy_reason` left TEXT (no CHECK) — admin queries surface mistypes; (c) `alerts_capability_state_transitions_total` metric deferred until drift visibility becomes useful; (d) webhook secret rotation pattern inherited from 11a's two-store rotation when admin UI lands; (e) `consecutive_eval_errors` not reset on capability re-availability — consistent with the documented per-rule user-opt-in re-enable path.

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
- **Architect review:** applied inline 2026-05-13 — 1 CRIT (webhook SSRF) + 8 HIGH (versioning slot; producer-side debounce; debounce eviction; snapshot rebuild coalescing; WS reconnect fire-replay backfill; predicate_json + requires_capabilities GIN; capability registry single-source; ticks_subscriber internal-bus boundary) + 8 MED (NOTIFY safety + poll fallback; PII log redaction; `_guarded_alerts_call` table; parser portfolio-strip test; parse_failed editor wording; webhook timeout + concurrency cap; Playwright deferral documented). 5 LOW findings deferred with documented rationale (see §18 item 10).
