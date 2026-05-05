# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.7.4] — 2026-05-05

### Fixed — post-deploy hotfixes for v0.7.3

Schwab + Alpaca sidecars failed to come up cleanly after the v0.7.3
deploy. Cascade of 7 fixes; tagging as a patch so the running prod
state has a named release.

- **In-cluster sidecars couldn't be dialed.** `BrokerSidecarClient` always
  built a `secure_channel`, but Schwab + Alpaca bind insecure ports
  (peer trust = `td-net` docker bridge). Added `use_mtls` kwarg +
  `INSECURE_IN_CLUSTER_LABELS` frozenset; `build_broker_registry`
  passes `use_mtls=label not in INSECURE_IN_CLUSTER_LABELS`
  (`da4cdf9`).
- **Sidecar containers failed `python -m sidecar_<name>.main`.**
  `COPY . .` flattened the package into `/app/`. Fixed with
  `COPY . ./sidecar_<name>/` so the module path resolves under
  `PYTHONPATH=/app` (`b44cabc`).
- **Alpaca registry dialed `10.10.0.2:9091/9092`** (NUC, no listener)
  instead of `alpaca-sidecar-{live,paper}:9091/9092` (docker DNS).
  Added entries to `SIDECAR_HOSTS` (`67a030a`).
- **Schwab sidecar restart loop:** `from broker.v1 import broker_pb2`
  in auto-gen file failed at import time. Added
  `sys.path.insert(_GENERATED_ROOT)` to `sidecar_schwab/main.py`,
  matching the `sidecar_alpaca/handlers.py` workaround (`d8cabbb`).
- **Schwab OAuth callback returned 500 after token exchange succeeded.**
  Py2-style `except AttributeError, ImportError:` only caught the first
  type and bound `ImportError` to a local name; gRPC errors from
  `reconfigure_schwab` bubbled up. Tuple-catch + fail-soft handler
  ensures the user sees a success page even if the sidecar reconfigure
  blips (`eef9c51`).
- **Schwab UX bug — "contact customer support" on re-authorize button.**
  Three compounding issues: (1) `urllib.parse.quote(callback_url)`
  encoded `:` and `/` to `%3A`/`%2F`, breaking Schwab's strict
  byte-match on registered redirect_uri (`b70e601`); (2) registered
  redirect_uri at Schwab's developer portal didn't match backend
  callback path (operator action: portal updated to
  `/api/oauth/schwab/callback`); (3) Schwab's authorize endpoint rejects
  `state` and `response_type=code` parameters even though both are
  standard OAuth2 — empirically confirmed by 3-step retest cycle
  (`8cc3d07` → `4919084` → `545fbc0`). Final URL shape matches the
  `schwabdev` SDK: only `client_id` + `redirect_uri`. State CSRF
  protection waived; public callback logs the caveat and accepts a
  missing state. CSRF defense reduces to redirect_uri byte-match (`a946e5e`).
- **Frontend Docker build failed:** `pnpm-lock.yaml` out of date with
  `@msgpack/msgpack@^3.0.0` from Phase 7b.1 (`643a56a`).

## [0.7.3] — 2026-05-05

### Phase 7c — Alpaca adapter

- New `sidecar_alpaca/` Python package, in-cluster Docker on `td-net`,
  insecure-port 9091 (live) / 9092 (paper). API-key auth via app_secrets
  with forward-compat `<account_label>` schema (MED-2). SDK isolation:
  only `client.py` imports `alpaca-py` (M3).
- Two upstream WS connections per sidecar — IEX equity + crypto v1beta3
  — with per-task isolation supervisor (HIGH-1). Failure of one endpoint
  cannot cancel the other; both directions verified by
  `test_streamer_isolation.py`.
- Two-layer 30-symbol cap (CRIT-1): backend `SubscriptionRegistry` soft
  cap at 25 + sidecar `_iex_active`/`_crypto_active` hard cap at 30.
  `quote_subscription_cap_rejected_total` widens from 1 to 3 labels:
  `cap_kind`/`source`/`asset_class`. New `cap_kind=per_source` value.
- Subscribe vs Resync reconnect contract (CRIT-2): full WS reconnect on
  Subscribe (sidecar restart), diff-only on Resync (gRPC blip). No
  upstream reconnect storm on backend reconnect churn.
- Per-mode Configure routing (HIGH-5): paper sidecar never sees live
  creds; cross-mode probe fires `alpaca_mode_mismatch_total{label}`.
- New `app/services/config_defaults.py` + per-key merge in
  `SourceRouter._priority_list_for` (HIGH-3) — operator partial overrides
  preserve new defaults shipped in later phases.
- Source-router default: `crypto.US` primary → alpaca; `stock.US`/
  `etf.US` fallback after schwab.
- New `app_config.broker_gateway_dial` table (HIGH-4) — labeled-docker
  sidecar dial resolution. Schwab + IBKR dials NOT migrated this phase.
- `account_id` boundary strip (HIGH-2) — Alpaca's UUID rides proto
  field 5 (account_hash) which the existing M22 chokepoint at
  `services/brokers.py::_ACCOUNT_BOUNDARY_STRIP_FIELDS` already strips.
  Regression test asserts AccountResponse declares no broker-internal
  ID fields.
- Subscribe-rejection drift detection (HIGH-6): when Alpaca silently
  lowers their cap, streamer removes the rejected symbol locally AND
  emits a drift sentinel via `QuoteMessage.raw_payload` =
  `b'{"drift":"cap_exceeded"...}'`; backend's `SidecarStream` decrements
  `SubscriptionRegistry._per_source_refs[source]` to prevent ghost subs.
- 11 new metrics (`alpaca_*` family + extended
  `quote_subscription_cap_rejected_total`).
- 1 operator runbook: `deploy/runbook-alpaca-setup.md`.
- 14 new tests (7 backend + 7 sidecar) — all green; lint + mypy --strict
  clean across `backend/app/` and `sidecar_alpaca/`.
- Trade execution remains UNIMPLEMENTED — Phase 8 alongside Schwab.

### Phase 7b.1.5 — Instruments seed mini-phase (2026-05-05)

- Alembic 0010: `positions.symbol`/`primary_exchange`/`canonical_id` columns
  (NULLABLE) + `watchlist_entries` table.
- `WatchlistEntry` ORM model.
- `BrokerDiscoverer._upsert_positions` derives + writes `canonical_id` per
  position; emits `quote_position_canonical_resolved_total` /
  `quote_position_canonical_unresolved_total{reason}`.
- `seed_instruments_from_positions(session_factory)` lifespan helper +
  `quote_seed_skipped_total{reason}` counter.
- `POST /api/admin/instruments` admin endpoint with Pydantic v2 validation
  + `require_admin_jwt`.
- 3 test files (integration seed, API endpoint, unit upsert).

## [0.7.1] — 2026-05-05

### Phase 7b.1 — Streaming quote engine

- New bidirectional gRPC `StreamQuotes` RPC on `service Broker` — backend
  is gRPC client, sidecar is server; `Subscribe`/`Unsubscribe`/`Heartbeat`/
  `Resync` ops via `oneof` (CRIT-1, HIGH-1).
- New `instruments` + `symbol_aliases` schema (Alembic 0009) with
  race-safe `INSERT … ON CONFLICT DO NOTHING RETURNING` upsert + in-process
  `asyncio.Lock` guard (CRIT-3).
- `sidecar_schwab` ports `LEVELONE_EQUITIES` streamer with `$`-symbology
  for US cash indexes; proactive reconnect on token rotation, gap < 2s
  (CRIT-2).
- `sidecar_futu` exposes HK Lv1 quotes (stocks/ETFs/warrants/CBBC + HSI/
  HSCEI/HHI indexes) over `StreamQuotes`.
- `sidecar_ibkr` (×4) exposes STK + IND quotes with LSE GBp normalization;
  4 SidecarStream instances, gateway-quote-assignment via app_config map
  (MED-6).
- New backend `QuoteEngine` with `SubscriptionRegistry` (cap + rate-limit,
  HIGH-6), `SourceRouter` (config-driven priority + health window, HIGH-7),
  `InstrumentResolver`, `SidecarStream` (Subscribe-vs-Resync per HIGH-1),
  Redis bus `quote.<source>.<canonical_id>` with `publisher_worker_id`
  envelope for INV-Q-1 single-worker loopback suppression.
- Engine invariants `INV-Q-1..4`: Redis loopback suppression, M22 boundary
  strip (`raw_payload` + `source_meta`), staleness-not-reroute, token-
  rotation Event ordering.
- New `/ws/quotes` FastAPI WebSocket endpoint with MessagePack v=1 frames
  (op: `sub`/`unsub`/`focus`/`ping`/`ack`/`snap`/`q`/`stale`/`err`/`pong`),
  `WSConflator` per-connection focused-10Hz/background-4Hz, `asyncio
  .wait_for(send, timeout=2.0)` slow-client isolation (HIGH-3), CF Access
  JWT auth via `Cf-Access-Jwt-Assertion` header (HIGH-2), dev-bypass over
  WG.
- Frontend `RealQuotesService` replaces `MockQuotesService`; `useFocused
  Symbol` hook elevates one symbol per session to 10Hz on Trade ticket
  mount; reconnect with bounded `pendingFrames` (≤100, drop-oldest);
  fallback to mock after 3 failed reconnects with banner.
- 3 operator runbooks: `runbook-quote-coverage.md`,
  `runbook-ibkr-data-subs.md`, `runbook-quote-streaming-ops.md`.
- Source enum proto: 13 entries open-set, 3 wired in 7b.1
  (IBKR/Futu/Schwab); 10 designed-for, wired by demand
  (Coinbase/OANDA/yfinance in 7b.2; Finnhub Free in Phase 18; EODHD in
  Phase 9; Tradier conditional in Phase 12; Twelve Data/Alpaca/Polygon/
  Binance per asset-class phase).
- A5 (instruments seed) deferred to Phase 7b.1.5 — schema for `positions`
  / `watchlist_entries` doesn't carry `symbol`/`exchange` yet; resolver
  works lazily without seed.
- **Saves $192–960/yr in IBKR data fees** (cancel US bundles +
  expensive intl subs, replace with Schwab+Futu+yfinance).

## [0.7.0] — 2026-05-04

### Phase 7a — Schwab broker connect (read-only OAuth + two-tier auth)

- New `sidecar_schwab/` Python package (PyInstaller-frozen) at `10.10.0.2:18006`,
  label `"schwab"`, broker_id `"schwab"`. Reuses the gRPC `Broker` contract;
  `Configure` RPC ships `app_key`/`app_secret`/`access_token`/`refresh_token` from
  `app_secrets`. Read-only surfaces this phase: ListManagedAccounts,
  GetAccountSummary, GetPositions, GetOrders. Place/Cancel/Modify return UNIMPLEMENTED
  (deferred to Phase 7b).
- New `service BackendCallback` proto (`RequestTokenRefresh`) so the sidecar can ask
  the backend to mint a new access_token when its cached one expires. Single-writer
  rule enforced via PG advisory lock (C2 invariant) — backend is the only writer of
  `app_secrets.broker.schwab.refresh_token`.
- Two-tier auth:
  - **Tier-1 (manual)**: `POST /api/admin/brokers/schwab/oauth-start` returns the
    Schwab authorize URL with HMAC-SHA256-signed state nonce; public callback at
    `/api/oauth/schwab/callback` verifies signature, atomic-consumes nonce via Redis
    `GETDEL` (H1 — replay defense), exchanges code → tokens under advisory lock.
  - **Tier-2 (auto-refresh)**: separate `sidecar_schwab_refresher/` Playwright cron
    container (25-min interval), TOTP-driven login, redirect interception via
    page.route() (C1 — never follows the redirect), selector-health probe (H2),
    auto-disable after 3 consecutive failures.
- New `POST /api/admin/brokers/schwab/disconnect` admin endpoint deletes both tokens;
  `GET /api/admin/brokers/schwab/status` returns connection state + ages for the
  `SchwabCard` settings UI.
- `Account.account_hash` proto field 5 (Schwab PII-equivalent); boundary-stripped in
  `AccountService` so frontend never sees it. `Order.avg_fill_price_inferred` proto
  field 14 flags Schwab orders where `executionLeg.price` was missing and
  avg_fill was inferred from quantity × marketValue (M2).
- New SSE forwarder `/api/sse/config_stream` republishes `config:invalidate:<ns>`
  Redis pub/sub events to subscribed clients (Tier-2 refresher uses this to learn
  about token rotations the backend wrote).
- 11 new Prometheus metrics in `app/core/metrics.py`:
  `broker_configure_total`, `schwab_oauth_start_total`, `schwab_oauth_callback_total`,
  `schwab_access_token_age_seconds`, `schwab_refresh_token_age_hours`,
  `schwab_refresh_token_uses_per_24h`, `schwab_account_hash_refresh_total`,
  `schwab_http_requests_total`, `schwab_sidecar_token_drift_seconds`,
  `schwab_tier2_refresh_total`, `schwab_tier2_last_run_timestamp_seconds`.
- New `phase7a_schwab` Prometheus alert group (9 alerts):
  `SchwabAccessTokenStale` (>1500s), `SchwabRefreshTokenExpiringSoon` (>144h),
  `SchwabRefreshTokenFlapping` (>50/24h — H4 restart-flap detector),
  `SchwabSidecarTokenDriftHigh` (>60s — C3), `SchwabOAuthCallbackFailures`,
  `SchwabHttpErrorRateHigh`, `SchwabTier2Stalled`, `SchwabTier2FailureRateHigh`,
  `SchwabAccountHashRefreshChurn`.
- Operator runbook: `deploy/runbook-schwab-setup.md` (9 steps, snapshot → app
  registration → seed app_secrets → Tier-1 → optional Tier-2 → smoke).
- CF Access bypass: `scripts/cloudflare/access-bypass-schwab-callback.sh` (idempotent;
  the OAuth callback is publicly reachable but authenticated via HMAC state nonce).
- Alembic 0008 adds `account_hash TEXT` + partial index on `broker_accounts`.
- New `backend/tests/integration/test_token_rotation_atomicity.py` proves the
  single-writer rule end-to-end (concurrent refresh attempts → only one mints).
- Nightly real-Schwab smoke at `.github/workflows/nightly-real-schwab.yml`
  (12:00 UTC, gated on `CI_USE_REAL_SCHWAB=1` + service token).
- Schwabdev SDK 3.0.3 confined to `client.py` (M3 isolation); rest of the codebase
  never imports it. Fork inventory: `tokens_db` not `tokens_file`,
  `linked_accounts()` not `account_linked`, manual `_sync_tokens()` direct mutation
  to avoid `update_tokens()` minting unwanted refresh tokens.

## [0.6.0] — 2026-04-30

### Phase 6 — Futu HK adapter + JP kanji font polish

- New `sidecar_futu/` Python package (PyInstaller-frozen → `dist-staging-futu/futu-sidecar.exe`).
  Single Futu sidecar at `10.10.0.2:18005` (label `"futu"`, broker_id `"futu"`); shares the
  gRPC `Broker` contract with IBKR plus a new `Configure` RPC that ships unlock_pwd_md5 +
  RSA priv key from `app_secrets` so creds never live on disk.
- Read + place + cancel for HK stocks/ETFs/warrants/CBBC. Modify/Bracket return UNIMPLEMENTED
  (deferred to Phase 7).
- `Health.broker_id` + `Health.started_at` proto fields added; `BrokerRegistry`
  cross-checks broker_id against the `SIDECAR_BROKERS` map (architect H4) and
  re-Configures on sidecar restart via started_at delta (architect H2).
- `BrokerConfigurer` lifecycle in `broker_registry_factory.py` reads creds via
  `ConfigService` and fires the Configure RPC at startup + after every restart.
- New `POST /api/admin/brokers/{label}/reconfigure` admin endpoint for cred rotation.
- `?broker=ibkr|futu|schwab` Pydantic `Literal` on `/api/contracts/search`; `schwab`
  short-circuits to 503 with `Retry-After: 86400` (deferred to Phase 7).
- mTLS server hardening: ported `sidecar/tls.py` (TLS 1.3 enforcement, CRL hot-reload
  via `sys.exit(64)`, cert/key matching-pair validation, file-perm guards) into
  `sidecar_futu/tls.py`; key-perm check moved before any read for defense-in-depth.
- futu-api 10.04.6408 SDK gotchas captured: `unlock_trade(password_md5=...)`, RSA via
  `SysConfig.set_init_rsa_file(<tempfile>)` + `enable_proto_encrypt(True)`,
  `is_encrypt=True`, `BWRT → CBBC` mapping (Bull/Bear-Warrant), 16-state OrderStatus
  table including the 5 SDK values the plan missed (CANCELLING_ALL/PART, SUBMIT_FAILED,
  FILL_CANCELLED, TIMEOUT).
- JP kanji font split: new `Noto Sans JP` family with two `@font-face` declarations
  (kana ~50KB + kanji ~1-2MB unicode-range-gated); `[lang|="ja"]` selector triggers
  the JP-specific glyphs only when a Japanese ticker is rendered. Operator runs
  `frontend/public/fonts/README.md` pyftsubset pipeline to materialize the binaries.
- New Prometheus alerts: `BrokerLabelMismatch` (page severity, fires on
  `broker_registry_label_mismatch_total[5m] > 0`) and `BrokerFutuNormalizeUnknown`
  (warning, fires on `broker_normalize_unknown_total{label="futu"}[15m] > 5`).
- Frontend: `searchContracts` accepts `broker?: 'ibkr'|'futu'` via options object;
  `ContractSearchInput` plumbs the broker through. `TradeTicketModal` disables STOP
  order type for HK warrants/CBBC and auto-reverts to LIMIT.
- Deploy ops: `deploy/nuc/build-windows-futu.ps1` + `restart-futu-sidecar.ps1` +
  `runbook-futu-setup.md` (9-section operator procedure: install OpenD, generate
  1024-bit RSA, configure OpenD web UI, compute MD5, seed app_secrets, wipe plaintext,
  trigger Configure, Defender exclusion, mTLS provisioning).

## [0.5.7] — 2026-04-29

### Fixed — BrokerTray switched from WG dev-bypass to CF Access service token

The Windows tray hit `http://10.10.0.1/api/brokers/accounts` (WG dev-bypass)
which always returned 401 because `require_admin_jwt`'s dev-bypass is gated
on `APP_ENV=dev` (cf_access.py:89), and the VPS runs `APP_ENV=prod`. Result:
yellow "VPS unreachable" forever even after v0.5.6 added the endpoint.

- `BrokerTray.ps1` now hits `https://dashboard.kiusinghung.com/...` with
  `CF-Access-Client-Id` + `CF-Access-Client-Secret` headers (the same shape
  CI uses for `/health` probes). Backend's `require_admin_jwt` accepts the
  resulting service-token JWT (`kind=service_token`).
- New `Get-CFAccessHeaders` helper reads `CF_ACCESS_CLIENT_ID` /
  `CF_ACCESS_CLIENT_SECRET` env vars first (interactive runs), falls back
  to `C:\dashboard\secrets\cf-access-tray.env` (the Scheduled Task user
  context typically has neither env var).
- Operator setup: drop the two creds into `C:\dashboard\secrets\cf-access-tray.env`,
  then `restart-tray.ps1`. Template at `secrets/cf-access-tray.env.example`.
- Removed the dead `https://10.10.0.1` + cert-bypass override + Host rewrite.
  WG-side TLS termination on the VPS doesn't exist (nginx binds :80 only;
  TLS is terminated by Cloudflare Tunnel on the public path).
- `secrets/` directory tracked but contents gitignored except `*.example`.

No backend redeploy required — tray-side change only.

## [0.5.6] — 2026-04-29

### Phase 5 close-out — deferred items shipped

Closes everything from the "Open scope deferred from 5c" list except the items
explicitly re-homed to Phase 7 (quote/BASE-tag subscribe rework, bundled with
Schwab) and Phase 9 (multi-worker uvicorn).

- **`AccountResponse.position_count`** — closes the 5b.1 architect-review HIGH-3
  deferred item. `list_accounts` SQL gains a `LEFT JOIN positions` cnt subquery;
  field default `0` for accounts with no rows. `_AccountRow` dataclass extended,
  OpenAPI `OPTIONAL_ACCOUNT_FIELDS` set updated.
- **`scripts/restart-backend.sh`** — bundles `docker compose restart backend`
  with `nginx -s reload` so manual backend restarts don't 502 for ~1-2s while
  nginx re-resolves the new container IP. Use this instead of bare
  `docker compose restart backend`.
- **OrderEvent stream observability alerts** — `BrokerOrderEventStreamDown`
  (page, `consumer_alive == 0` for 2m) and `BrokerOrderEventStreamFlapping`
  (warning, >10 reconnects in 10m sustained for 5m) added to
  `alerts.yml::phase5b_orders` group. Both backed by metrics that already
  existed (`consumer_alive` Gauge, `broker_order_stream_reconnects_total`
  Counter); lifecycle logs were added in v0.5.5.
- **`GET /api/brokers/accounts`** — fills the missing endpoint that the Windows
  BrokerTray (`deploy/nuc/BrokerTray.ps1`) probes every few seconds. The route
  never existed; the tray fell into `ConnectFailure`/`Timeout` and showed
  yellow "VPS unreachable" forever. Returns one row per (broker, gateway_label,
  mode) sidecar with a `connected` flag derived from
  `BrokerRegistry.degraded_labels()`. Distinct from `/api/accounts` (which
  strips `gateway_label` per M22 boundary discipline) — this is an
  operator-internal surface gated by `require_admin_jwt`. New Pydantic models
  `BrokerSidecarStatus` + `BrokerSidecarStatusList`.

### CLAUDE.md slim-down

CLAUDE.md was 352 lines / 40K and starting to impact session-load latency.
Extracted Phase 5a/5b/5c shipping invariants (now pointed to memory),
phase workflow (now `docs/PHASE-WORKFLOW.md`), and Configuration Storage code
examples (now `docs/CONFIG.md`). Net: 244 lines / 20K.

### Notes

- v0.5.6 deploys without schema or proto changes — straight container redeploy.
- The two Phase 7 items (on-demand quote subscribe + periodic BASE-tag refresh)
  remain deferred. They share the same root pattern (sidecar only subscribes at
  startup; mid-run additions never get a subscription) and will be designed
  once across IBKR + Futu + Schwab.
- TASKS.md ordering swapped: Phase 7 is Schwab + market-data subscribe rework;
  Phase 8 is Alerts + Telegram + AI router.

## [0.5.5] — 2026-04-29

### Fixed — Phase 5c canary debug pass + SIM dispatch fix

End-to-end SIM canary debug session that uncovered a longstanding propagation bug from 5b.1 SIM cancel echo. ~14 commits, all via the per-commit review chain.

- **CRITICAL — SIM dispatch via per-account queue:** the sidecar's SIM cancel/modify echo paths were calling `ib.orderStatusEvent.emit(synthetic_trade)` to fan a synthetic event out to the OrderEvent gRPC stream's `_on_status` listener. Under `ib_async`'s eventkit, `emit()` doesn't trigger externally-registered listeners (cross-loop / IB-callback-only dispatch). The 5b.1 SIM cancel echo "worked" in tests because the mock servicer bypasses the real path. Real prod was silently dropping every SIM-echo. Fix: sidecar now maintains `self._order_event_queues: dict[str, list[asyncio.Queue]]` keyed by account_number; the OrderEvent handler registers its queue on subscribe, the SIM echo paths put the synthetic `OrderEventMessage` directly into all matching queues. Bypasses eventkit entirely. Diagnostic logging added: `orderevent_subscribed/_unsubscribed/_emit_queued` (sidecar) + `stream_subscribed/_closed` (backend consumer).
- **Modify nonce hash matched preview's** — `_consume_nonce` for the modify path was computing a 3-field hash (`account_id, qty, limit_price`) but the preview endpoint mints an 8-field hash. Every modify returned 422 `payload_mismatch`. `_consume_nonce` now recomputes the same 8-field hash by merging the order row's immutable fields with the request's mutable fields.
- **TradeTicketModal `handleSubmit` awaits a fresh preview** before constructing the body. Without this, a fast-typing user could click Modify before the 300ms debounced preview fires, sending the new body with a stale nonce → 422.
- **Sidecar SIM modify echo handler** — mirrors 5b.1 SIM cancel echo (was previously a hard `INVALID_ARGUMENT` rejection). Backend INVALID_ARGUMENT/NOT_FOUND now translates to HTTP 422 `broker_modify_rejected` (was 500 with raw stack). `BrokerSidecarUnavailable` exception now carries `grpc_code` + `grpc_details`.
- **Sidecar `--no-simulator` CLI flag** — opt out of simulator branch for real-IBKR placement; default still simulator-only for safety.
- **Wire shape:** `OrderResponse.conid` exposed on wire + `list_orders` and `get_order_by_id` SELECT projections include it. Modify modal pre-fills the contract correctly. `OrderStatusEnum` Literal includes `'modified'`. Consumer status alias maps include `'modified'`.
- **Frontend orders flow:**
  - Topbar mounts the features-layer AccountPicker (was bare pattern → no Trade button surfaced).
  - `services/orders.ts` corrected to call `/api/contracts/search` (was hitting `/api/contracts` → 404).
  - `applyEvent` in `stores/global/orders.ts` keys by `event.order_id` (not audit `event.id`) so SSE events update the right row instead of creating orphan entries.
  - `OrdersPage` `ACTIVE_STATUSES` includes `'modified'` so modified rows stay visible.
  - `OrdersPage` refetches after modify modal close + cancel (immediate + 750ms double-refetch) so the UI updates without manual page refresh.
  - `ContractSearchInput` STK-first ranking via `rankContracts` — search "AAPL" now surfaces the SMART/STK row above options/futures.
- **Modify route updates `orders.qty/limit_price/stop_price/tif/notional` in-place** so UI reflects new values, not just status. HIGH-3 audit-only-write split preserved for `status` (consumer-owned).
- **Observability completion:** `broker_order_modify_duration_ms` Histogram instrumented in modify route via `time.perf_counter()`. `broker_fills_write_failed_total{reason}` Counter incremented in `_record_fill` exception path. `BrokerOrderModifyP99HighWarning` + `BrokerFillsWriteFailures` alerts re-enabled in `alerts.yml` (the original plan §G1 alerts that were previously skipped because the metrics didn't exist).

### Notes

- 9 orphan SIM orders from the canary debug were marked `cancelled` via authorized DB UPDATE (audit trail in `order_events` preserved).
- Single-worker uvicorn assumption still load-bearing.

## [0.5.4] — 2026-04-29

### Added — Phase 5c: advanced order types

- **Modify orders** (`PUT /api/orders/{id}`) — full-payload modify with always-fresh-nonce policy. HTTP write touches only `order_events` (audit row); the consumer owns `orders.status` mutation (HIGH-3 audit-only-write split). 60s per-(order_id, nonce) replay-safety cache (HIGH-1). Child-order modify allowed even when parent partial (MED-1).
- **Bracket orders** (`POST /api/orders/bracket`) — entry + optional stop-loss + optional take-profit, atomic OCA group via two-phase commit (HIGH-2: parent-only INSERT, RPC, then children INSERT on success). `OrderBracketResponse.parent` is a thin placement-confirmation shape (`OrderBracketParent`, parallel to `OrderBracketLeg` for children). Cancel parent cascades to children via broker OCA semantics; sidecar mock servicer emits the cascade events for E2E coverage.
- **Fills history** (`GET /api/fills`) — cursor-paginated execution-level audit trail with date-range. New `fills` (`exec_id` UNIQUE) and `pending_fills` tables. CRIT-2 buffer pattern handles the execDetails-before-order-row race: per-event drain on order arrival + 30s `PendingFillsSweeper` for cross-path cases (e.g. order written by `reconcile_at_startup`, not by the consumer event path).
- **Date-range filter** on `GET /api/orders` (`?from=...&to=...`).
- **`modified` order status** in `order_status_enum` + `order_status_rank()` SQL function (CRIT-1: prevents backward transitions like `modified → submitted` via a `CASE` predicate that compares ranks before applying the new status).
- **Commission backfill** (MED-5): consumer handles `kind="commission_report"` events; if the matching `fills` row hasn't landed yet, the commission is held in a 5-min in-memory `_COMMISSION_BUFFER` keyed by `exec_id` and applied on next fill INSERT.
- **Cascade-lag metric** (HIGH-4): `broker_bracket_cancel_cascade_seconds` histogram observed in `_process_event` on child cancel — measures `broker_event_at − parent.cancel_requested_at`.
- **Frontend modify + bracket + fills surface:** `TradeTicketModal` gains a `mode: "place" | "modify" | "bracket"` prop with field-disable map and submit-endpoint routing; `useFillsHistory` cursor hook + `FillsTable` pattern (date-grouped, sticky header) + `/orders/$id/fills` route + Modify button on non-terminal `OrdersPage` rows.
- **Prometheus alerts:** `BrokerBracketCascadeLag` (p99 > 5s over 10m), `BrokerPendingFillsBacklog` (any rows > 5min), `CommissionBufferOverflow` (any > 1000-entry overflow over 15m).
- **OpenAPI snapshot lock** extended (`test_openapi_schema_lock_phase5c`) covering 7 new wire models: `OrderModifyRequest`, `OrderBracketRequest`, `OrderBracketResponse`, `OrderBracketParent`, `OrderBracketLeg`, `FillResponse`, `FillListResponse`. Frontend `api-generated.ts` regenerated.
- **Mock + real-IBKR E2E:** `test_e2e_modify_chain.py` and `test_e2e_bracket_chain.py` (`e2e-mock.yml`); `test_real_ibkr_e2e_modify.py` and `test_real_ibkr_e2e_bracket.py` stubs (`nightly-real-ibkr.yml`).

### Architecture-review findings applied (14 total)

- **2 CRITICAL:** `order_status_rank` predicate (D1); `pending_fills` buffer + sweeper (D2).
- **4 HIGH:** modify replay cache (C2); bracket two-phase commit (C3); audit-only HTTP write (C2/C5); cascade-lag metric (D4).
- **5 MEDIUM:** child-modify allowance (C2); bracket sequencing (C3); commission backfill (D3); replay paths (C2/C3); field-disable map for modify-vs-bracket (F1).
- **3 LOW:** documented inline in spec.

All resolved inline per the project rule "apply through MEDIUM" (memory `feedback_architect_findings_apply_through_medium.md`).

### Notes

- Single-worker uvicorn still load-bearing (the in-memory replay cache + commission buffer assume one process). Multi-worker is Phase 9.
- Codex hit usage quota partway through F1/E2-E5; Claude completed all blocked tasks per `feedback_codex_fallback.md`.

## [0.5.3] — 2026-04-28

### Fixed — Phase 5b.1 canary hotfix pack

All four 5b.1 work items shipped. The BASE-tag startup round was at one point believed broken (early pre-flight failure), but the failure traced to a script-side filter bug, not the IBKR API. Corrected pre-flight passed; C2 + C3 landed; the v0.5.2 `last_nlv_currency` fallback (`9910e3b`) remains as defence-in-depth.

- **`positions` table** (Alembic 0005) populated by `BrokerDiscoverer._discover_positions` per-account fan-out (mirrors Phase 5a NLV pattern: per-account `gather` + `return_exceptions=True`, savepoint-isolated upsert via `jsonb_to_recordset`, NULL-safe delta-delete via `NOT EXISTS`, sqlstate `22003` overflow → metric + skip, resurrect-from-soft-delete clears positions cache). `_position_qty` now reads real values; the defensive `to_regclass` guard from `b5a633d` is dropped.
- **SIM cancel echo:** sidecar `CancelOrder` recognizes `SIM-` prefix BEFORE int-parsing (latent ValueError fixed), pops new `_sim_orders` map registered at PlaceOrder time, synthesizes a Trade-like `SimpleNamespace`, and fires `ib.orderStatusEvent.emit(...)` so the existing per-subscriber OrderEvent fan-out emits a `cancelled` event for every connected backend consumer. Idempotent (re-cancelling missing SIM is a no-op). New metric `broker_sim_cancel_echo_total{label}`.
- **BASE-tag startup round:** sidecar runs sequential per-account `ib.client.reqAccountUpdates(True/False, account)` cycle BEFORE `reqAccountSummaryAsync()`. Populates `ib.accountValues()` with 876 per-currency rows (6 isa-paper accounts × ~146 rows). Backend extracts the account's base currency from the `NetLiquidation` row's `currency` field (IBKR reports NLV in the account base currency only). Sequential adds ~2.3s per account (~14s total for 6-account paper sidecar). The IBKR API permits only one active `reqAccountUpdates` subscription per connection, so the round MUST complete before `reqAccountSummary` opens. Empirical pre-flight (`sidecar/scripts/base_round_preflight.py` @ `97efe0f`) validated the full PASS path on paper gateway 4002 with the sidecar killed via `gsudo Stop-Process` (clientId=999 as master).
- **Layered E2E tests:** `e2e-mock.yml` runs the full preview→place→cancel chain on every push + PR (httpx ASGITransport + extended sidecar mock servicer + Postgres-18 + Redis-7 service containers). `nightly-real-ibkr.yml` cron moved to 12:00 UTC (clears all four IBKR maintenance windows by ≥6h) with new `workflow_dispatch.inputs.run_e2e` for manual runs and `CF_ACCESS_*` env wired through; the `@pytest.mark.real_ibkr`-gated test in `sidecar/tests/test_real_ibkr_e2e_trade.py` exercises the production HTTPS endpoint with a finally-revert of `trade_enabled`.
- **Prometheus alerts:** `BrokerDiscoverPositionsP99HighWarning` (fan-out p99 > 1000ms over 5m), `BrokerSimCancelEchoMismatch` (synthetic emit rate diverges from cancel HTTP 202 rate by >10% over 10m).

### Lesson — measure twice, cut once

The first pre-flight FAIL was a script-side filter bug (`v.tag == "BASE"`) — but per `ib_async/wrapper.py:527`, `BASE` is a **currency** meta-marker, never a tag. The base currency code is the `.currency` field of the `NetLiquidation` row. The second pre-flight (with the IBKR API docs handed in by the operator) caught it. Lesson saved to memory: empirical scripts should print all `(tag, currency)` shapes before applying a filter, especially when working against undocumented or sparsely-documented broker APIs.

### Open Phase 5c work surfaced for the next phase

- `AccountResponse.position_count` (deferred from 5b.1 spec on architect-review HIGH-3 — needs Pydantic + service SQL + OpenAPI snapshot regen + frontend types regen).
- Periodic BASE-tag refresh for new accounts added mid-run (Phase 5c R11) — only relevant if a future ib_async / IBKR API revision makes BASE reachable.
- Modify orders + brackets/OCO + fills history endpoint + multi-worker uvicorn.

## [0.5.2] — 2026-04-28

### Fixed — Phase 5b post-canary hardening

Thirteen hotfixes since v0.5.1; first end-to-end canary validated (BARC + VOD on isa-paper, place + cancel both 200/202, simulator-prefixed broker IDs).

**Order placement path (canary blockers):**
- `_resolve_contract` was using `search_contracts` (symbol-name autocomplete via `reqMatchingSymbols`); switched to `get_contract` (qualifyContractsAsync) — numeric conids now round-trip correctly. Without this every preview 404'd `contract_not_found`.
- `_position_qty` defends against missing `positions` table via `to_regclass` (Phase 5c work; mirrors `_position_count` guard). Without this every preview 500'd UndefinedTableError.
- `_resolve_account` falls back to `last_nlv_currency` when `currency_base` is empty. The sidecar can't subscribe `reqAccountUpdates` concurrently with `reqAccountSummary` on one connection — `currency_base` is permanently empty in production, so without the fallback every preview 503'd `fx_rate_unavailable USD:""`.
- Trade-policy keys moved from namespace=`broker.<label>` to namespace=`broker` with dotted key prefix `<label>.trade_enabled` etc. NAMESPACE_PATTERN forbids dots; the resolver was unreachable through the validated admin POST endpoint.
- `_stream_call` no longer passes the unary `deadline_seconds` to streaming gRPC RPCs — every OrderEvent subscription was being torn down with `DEADLINE_EXCEEDED` at exactly 5s in production thrash. Connection liveness now governed by gRPC keepalives.

**Test fixtures aligned:**
- `_Sidecar` mock gains `get_contract` in `test_orders_preview.py` + `test_orders_place.py`.
- `_Config` mocks accept the dotted policy key shape (`broker.<label>.<key>` → namespace="broker", key="<label>.<setting>").
- `OrderEvent` mock asserts no `timeout` kwarg (mirrors streaming-RPC contract change).

**Deploy + CI:**
- `docker-compose.prod.yml` uses absolute `/app/.venv/bin/uvicorn` path (bare `uvicorn` not on `$PATH` in the python:3.14-slim base — entrypoint `exec` was failing).
- `tests/migrations/test_0004.py` resolves `backend_dir` from `__file__` instead of hardcoding `/home/joseph/dashboard/backend` (broke CI runners at `/home/runner/work/...`).
- `ci.yml` `frontend-types-up-to-date` job now generates proto stubs + supplies pydantic Settings env vars before `dump_openapi`.
- ESLint ignores `frontend/src/services/api-generated.ts` — `--fix` was rewriting openapi-typescript output and dropping ~110 lines, breaking the CI drift gate every iteration.

### Open Phase 5c work surfaced this canary

- `positions` table never received an Alembic migration. Position-sanity defaults to `qty=0` until then.
- Sidecar `SIM-` simulator prefix doesn't echo cancel events through OrderEvent — cancels are recorded server-side (`cancel_requested` 202) but the orders table row stays at `submitted` until a real broker `cancelled` event arrives. Acceptable for paper canary; production-grade fills/cancels need real broker handlers wired.
- Sidecar `currency_base` permanently empty (BASE tag unreachable concurrent with reqAccountSummary). Possible 5c fix: dedicated short-lived `reqAccountUpdates` round per discovery tick, or use accountValues snapshot at startup before reqAccountSummary subscribes.

## [0.5.1] — 2026-04-28

### Added — Phase 5b: IBKR trade execution (write path)

- **Three new RPCs + one search RPC** — `proto/broker/v1/broker.proto` adds `PlaceOrder` (unary), `CancelOrder` (unary), `OrderEvent` (server-streaming), and `SearchContracts` (unary). Stubs regenerated for both backend and sidecar.
- **`orders` + `order_events` tables** (Alembic 0004) — UUIDv7 PKs (sortable client_order_id for idempotency), 8-state `order_status_enum` (`pending_submit/submitted/working/partial/filled/cancelled/rejected/expired`), terminal-status sticky transitions, NUMERIC(20,8) for qty/avg_fill_price, JSONB `raw_payload` for broker-specific fields.
- **8 backend HTTP endpoints** — `POST /api/orders/preview` (validate + nonce-mint), `POST /api/orders` (place, requires nonce), `GET /api/orders` + `GET /api/orders/{id}` (list + detail), `GET /api/orders/policy/{account_id}` (per-account caps + kill-switch), `DELETE /api/orders/{id}` (cancel with cooldown rollback), `GET /api/orders/events` (SSE with Last-Event-ID replay), `GET /api/contracts/search` (proxy to sidecar with caching + 5/sec rate limit).
- **`BrokerOrderEventConsumer`** — supervisor task spawns one child per `(gateway_label, account_number)` tailing the OrderEvent stream. Child INSERTs into `order_events`, UPSERTs `orders`, PUBLISHes onto Redis `orders:events:account:<id>` + `orders:events:fleet`. Reconnect-and-resync on stream death. Account add/remove churn drives child lifecycle without restarting siblings.
- **`PendingSubmitWatchdog`** — 30s scan loop reconciles orders stuck in `pending_submit > 60s` against the broker's live order list. Match → synthesize `OrderEventMessage` and route through `_process_event`. No match after 5 min → escalate to `rejected` with audit row. `reconcile_at_startup()` runs the same pass before consumer streams open (R9: closes the mid-order-bounce gap).
- **Per-account trade policy** — `app_config` keys `broker.<account>.trade_enabled`, `broker.<account>.daily_notional_cap`, `broker.<account>.max_notional_per_order`, `broker.kill_switch_enabled` (fleet-wide). Policy resolver enforced at preview + place; kill-switch returns 503 ahead of every other check.
- **Frontend trade execution UX** — `TradeTicketModal` (preview → confirm flow with debounced policy fetches), `ContractSearchInput` (debounced search w/ keyboard nav), extended `OrdersPage` with active orders + cancel + EventSource subscription, "Trade" entry-points on `AccountPicker` row + position rows. Zustand `useOrders` store with optimistic insert + reconcile via SSE.
- **`scripts/gen-types.sh`** — frontend types now generated from the backend OpenAPI snapshot (`backend/app/scripts/dump_openapi.py`), with `pnpm check:types-up-to-date` CI drift gate.
- **OpenAPI schema lock (D7)** — `tests/api/test_openapi_contract.py::test_openapi_schema_lock_phase5b` snapshots 5 named models (`PreviewResponse`, `OrderResponse`, `OrderListResponse`, `PolicyResponse`, `ContractSummary`) via `syrupy` so wire-shape changes can't sneak through unreviewed.
- **9 Prometheus metrics + 12 alert rules** — `broker_order_pending_submit_recovered_total`, `broker_order_pending_submit_orphan_total`, `sse_active_connections`, `sse_dropped_clients_total`, etc. Alerts cover preview latency, place failure rate, watchdog orphan rate, SSE backpressure.
- **Real-IBKR smoke gate (B6)** — `CI_USE_REAL_IBKR=1` env-gated workflow `real-ibkr.yml` for nightly + manual dispatch, exercises place/cancel/stream against a paper gateway.

### Changed
- **Cancel cooldown rollback** — sidecar 503 / network failure during `DELETE /api/orders/{id}` rolls back the in-memory cooldown so the operator can immediately retry (H2 fix).
- **Lifespan ordering** — consumer + watchdog start AFTER the broker registry succeeds; shutdown drains them BEFORE the registry closes, so in-flight events finish processing.
- **Single-worker uvicorn assertion** — `docker-compose.prod.yml` pins backend to `--workers 1` (Phase 5b only — multi-worker support is deferred to Phase 9). CI asserts the entrypoint can't drift.
- **nginx SSE config** — `proxy_buffering off`, `X-Accel-Buffering: no`, 65s read timeout, no compression for `/api/orders/events`.

### Fixed
- **Pytest 9 duplicate-conftest plugin error** — the rootdir conftest is now the only place `pytest_plugins` is declared. Shared `session` fixture moved to `tests.fixtures.db_session`.
- **Preview test maintenance flake** — fixture default-monkeypatches `compute_broker_maintenance` so the suite runs cleanly during the live IBKR daily reset window.
- **Sidecar test imports** — `from handlers import ...` (missing package prefix) replaced with `from sidecar.handlers import ...`. Probe `FakeChannel` mock now exposes `unary_stream` so `BrokerStub(channel)` works after the OrderEvent server-streaming RPC was added.

## [0.5.0] — 2026-04-27

### Added
- **Discoverer NLV fan-out** — every 30s, `_discover_once` issues one `GetAccountSummary` per discovered account via `asyncio.gather(*calls, return_exceptions=True)` with per-call `asyncio.wait_for(timeout=10)`. `asyncio.Lock` re-entrancy guard prevents tick overlap. `last_nlv` / `last_nlv_currency` / `last_nlv_at` columns populated on `broker_accounts` (Alembic 0003 — NUMERIC(20,8) + VARCHAR(3) CHECK regex `^[A-Z]{3}$` + TIMESTAMPTZ, all nullable).
- **`AccountResponse` wire fields** — `nlv` (decimal-as-string, fixed-point 8-fractional-digits), `nlv_currency` (ISO-3 with Pydantic regex constraint), `nlv_at` (UTC ISO-8601). All optional; null until discoverer first populates.
- **`AccountListResponse.broker_maintenance`** envelope `{active, window, until}` — single source of truth shared with `_classify_sidecar_failure` 503 path. Required field, populated from `compute_broker_maintenance(now)`.
- **AccountPicker per-row staleness rule** — `< 2 min normal · 2-30 min dim (opacity-60) · > 30 min '—' · null nlvAt 'no data yet'`. Maintenance-active suppresses the rule when `nlvAt` is non-null; null `nlvAt` always renders `—` even during maintenance (no synthesized $0.00).
- **`React.memo` on AccountPicker row** with custom comparator on `(id, nlv, nlvAt.getTime(), maintenance.active, maintenance.window, maintenance.until?.getTime())`.
- **`useFleetMaintenance` Zustand store** + `fetchAccountsAndSyncMaintenance(mode)` hook helper composing service + store (services layer stays pure per `eslint-plugin-boundaries`).
- **Prometheus metrics** — `broker_discover_nlv_update_duration_ms` histogram, `broker_discover_nlv_overflow_total` counter.
- **6 `@pytest.mark.real_ibkr` smoke tests** — read-only contract tests against paper gateway 4002 (`sidecar/tests/test_real_ibkr_smoke.py`): connect, managedAccounts, accountSummary currency, reqPositionsAsync, openTrades, 60s connection survival. Default CI run filters them out via `-m 'not real_ibkr'`; nightly cron picks them up.
- **`compute_broker_maintenance(now)` helper** in `app/services/ibkr_maintenance.py` — single-evaluation envelope with `max(secs, 1)` floor to ensure `until > now` whenever `active=true` (eliminates the boundary-second race surface).
- **`@model_validator(mode="after")` on `BrokerMaintenance`** — rejects inconsistent `active=true, window=null, until=null` constructions.
- **Per-row savepoint** — each NLV UPDATE wrapped in `session.begin_nested()` so a NUMERIC(20,8) overflow on one account leaves the outer transaction alive for the other 21. `sqlstate=='22003'` (locale-stable) detects the overflow.

### Changed
- **`_classify_sidecar_failure`** uses `compute_broker_maintenance(now)` shared helper instead of an inline weekend/daily cascade. Zero behavior change, eliminates boundary-second race; 503 envelope shape now mirrors the list-endpoint envelope exactly.
- **OpenAPI contract test renamed** `tests/api/test_openapi_phase4.py` → `tests/api/test_openapi_contract.py`. Strict-shape check replaced by "required ⊆ actual ⊆ required ∪ optional"; forbidden keys (`gateway_label`, `account_number`) still asserted absent.
- **`RealAccountsService.list`** returns `{ accounts, brokerMaintenance }` instead of side-effecting a global store; the publish step moved to `frontend/src/hooks/useAccountsList.ts`.

### Fixed
- **Empty-string currency** from sidecar fallback no longer corrupts the database — skip-write predicate (`_is_populated`) requires `len(currency) == 3 AND isascii AND isupper AND bool(value)` before any UPDATE.
- **Resurrect-from-soft-delete** clears `last_nlv*` columns via `ON CONFLICT DO UPDATE` CASE clauses — frontend no longer briefly displays week-old stale values when an account reappears.
- **`_format_nlv` defensive against malformed Decimal** — returns `None` for NaN/Infinity/InvalidOperation instead of 500ing the entire `/api/accounts` list endpoint.
- **`Retry-After` header restored** on maintenance 503 (regression from the Phase-4 inline cascade refactor).
- **Migration test fixture** uses outer-rollback + `s.begin_nested()` savepoints so success-path tests cannot commit phantom rows to prod (the SQLAlchemy 2.0 `s.begin()` auto-commit hazard, hit during A2 implementation; see `feedback_pytest_session_begin_commits.md`).

## [0.4.0] — 2026-04-26
### Added
- **gRPC sidecar contract** — `proto/broker/v1.proto` (`Broker` service: Health, ListManagedAccounts, GetAccountSummary, GetPositions, GetOrders, GetContract). Generated client stubs land in `backend/app/_generated/broker/v1/` and `sidecar/_generated/broker/v1/` via `buf generate`; both dirs gitignored. Frontend uses plain JSON wire shapes — no proto runtime in the browser bundle.
- **4 PyInstaller-frozen Python sidecars** (one per IBKR gateway: isa-live/isa-paper/normal-live/normal-paper) bound to NUC ports 18001-18004. Each sidecar wraps `ib_async`, exposes the proto contract over mTLS-secured gRPC, and self-throttles backoff during gateway-not-yet-up + IBKR maintenance windows. Read-only in v0.4.0 — trade execution lands in Phase 5.
- **mTLS over WireGuard** — `provision-sidecar-mtls.ps1` generates a self-signed CA + 4 server certs (CN=`sidecar-<label>`, SAN=`IP:10.10.0.2`) + 1 client cert (CN=`dashboard-backend`) in `C:\dashboard\secrets\` with restrictive ACLs. CRL at `C:\dashboard\secrets\crl.pem` is reloaded every 60s. `provision-and-publish.ps1` POSTs the client material to `/api/admin/secrets/broker/mtls.*` via a CF Access service token — end-to-end automated cert distribution.
- **`broker_accounts` table** (Alembic 0002) — natural unique key `(broker_id, account_number)`; soft-delete via `deleted_at`; per-row `gateway_label`, `currency_base`, `display_order`, `last_seen_via`/`last_seen_at` for the C1 race-free discover guarantee. `last_seen_via = ANY(:healthy_labels)` ensures zero soft-deletes when no sidecars are healthy.
- **`/api/accounts/*` REST routes** — gated by `require_admin_jwt` (CF Access). `GET /api/accounts` returns `AccountListResponse { accounts, degraded_sidecars }` with `gateway_label` and `account_number` boundary-stripped (M22). Detail routes `/{id}/{summary,positions,orders}` return proto-mapped JSON with the typed 503 envelope: `{error:"sidecar_unreachable", label}` (Retry-After 30) or `{error:"broker_maintenance", window:"weekend|daily", until}` (Retry-After computed from `seconds_until_window_ends`).
- **`AccountService` + `BrokerRegistry` + `BrokerDiscoverer`** — central chokepoint that translates the frontend's `account_id` UUID to `(gateway_label, account_number)`. Registry holds 4 `BrokerSidecarClient`s + a per-label health cache; discoverer polls each healthy sidecar's `ListManagedAccounts` every 30s and upserts rows. The H11 invariant (`Σ(qty × avg_cost) > 1.5 × NLV`) emits `avg_cost_unit_suspected_wrong{account}` to flag the UK pence trap.
- **`app/services/ibkr_maintenance.py`** — single source of truth for IBKR reset windows (NA/EU/APAC-1/APAC-2 daily + Fri 23:00 ET → Sat 03:00 ET weekend). Backend short-circuits to `503 + Retry-After` during reset; watchdog skips probes during weekend reset; tolerates daily-reset BAD reads.
- **NUC ops glue** ported + extended from `Dashboard_old/deploy/nuc/`: `BrokerWatchdog.ps1` with `Adapt-SidecarHealth` block (kills + relaunches stuck sidecars after 2 consecutive BAD outside reset windows); `BrokerTray.ps1` with 2 sidecar triangles (live filled, paper empty) + right-click restart actions; `register-ibkr-sidecar.ps1` registers 4 Scheduled Tasks (S4U, +30s after the matching gateway); `Probe-Sidecar.ps1` shells out to `probe-sidecar.exe` (PyInstaller-built) so PowerShell doesn't pull in the .NET gRPC runtime; `verify-wg-windows.ps1` is the §0 pre-flight gate; `revoke-cert.ps1` appends to the CRL; `renew-sidecar-mtls.ps1` rolls one sidecar at a time on the annual cadence.
- **gsudo + admin trampolines** — `install-gsudo.ps1` + `register-admin-helpers.ps1` install gsudo and register Scheduled Task trampolines for elevated operations (`kill-stuck-trays`, `setup-autologon`, `verify-bitlocker`).
- **Headless run** — Sysinternals Autologon-based unattended login (LSA Secrets) + BitLocker post-reboot verification. The 4 sidecars + 4 gateways + 2 trays start at boot regardless of user session, survive logoff, and resume after reboot.
- **`SidecarLib.ps1` + Pester suite** — extracted `Test-InResetWindow`, `Read-SidecarHealth`, `Read-SidecarPair` from the watchdog/tray duplication. 21 Pester tests cover the IBKR daily/weekend window logic + sidecar health-state-file parsing.
- **Frontend `safeParseDecimal()`** at `src/lib/decimal.ts` — `{display, precise, lossy}` returned per call so callers can choose the precise string for comparisons or the rounded number for chart axes; the `lossy` flag surfaces precision loss explicitly. Custom ESLint rule `local/no-unsafe-decimal-arithmetic` flags `Number(x.value | x.precise)` on Money-shaped objects so the chokepoint can't be bypassed.
- **`MaintenanceError` + `SidecarUnreachableError`** at `services/errors.ts`. `listAccounts()` / `listPositions(id)` / `listOrders(id)` flip behind `VITE_USE_MOCKS`; on 503 they parse the typed envelope and throw the matching error class. Storybook preview pins `VITE_USE_MOCKS=true` so stories never hit a real API.
- **`useFleetHealth` selector** + Zustand `fleet-health` store — `degraded_sidecars[]` populates the store; `ConnectedDropdown` renders a yellow "N broker(s) degraded" pill in the topbar when `ok === false`.
- **Nightly real-IBKR contract test** — `.github/workflows/nightly-real-ibkr.yml` cron at 06:00 UTC on a `[self-hosted, nuc]` runner runs `pytest -m real_ibkr` against paper Gateway 4002. Self-hosted runner provisioning documented in `deploy/nuc/RUNBOOK-self-hosted-runner.md`.
- **CI proto + sidecar jobs** — `proto` job (`buf lint` + `buf format --diff --exit-code`) and `sidecar` job (`buf generate` + `pytest -m 'not real_ibkr' --cov-fail-under=80`). Existing `backend` job depends on `proto` and runs `buf generate` before `uv sync`.

### Changed
- `BrokerSidecarUnavailable` carries an optional `label: str = ""` so the route layer can surface the gateway label on `503 sidecar_unreachable` envelopes without reaching across the AccountService boundary.
- `python:3.14-slim` Dockerfile gains a `tzdata` install layer — `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError` on the upstream image without it (M20).

### Tooling
- `proto/buf.yaml` + `proto/buf.gen.yaml` wire the proto-codegen pipeline. `backend/scripts/proto-gen.sh` and `sidecar/scripts/build-windows.ps1` (pure-PowerShell, no bash) keep dev + CI in sync across WSL and Windows.

## [0.3.0] — 2026-04-24
### Added
- TanStack Router file-based routing with 11 routes (`/`, `/overview`, `/orders`, `/positions`, `/watchlist`, `/watchlist/$id`, `/admin`, `/admin/config`, `/admin/secrets`, `/settings`, `/trade`, `/alerts`). `routeTree.gen.ts` is gitignored and regenerated via `pnpm tsr generate` (wired into the `typecheck` and `test` scripts).
- Tailwind v4 `@theme` design tokens (rem-only, OKLCH dark palette) + Noto Sans / Noto Sans CJK font subsets (TC, SC, HK, JP, KR) wired via `unicode-range` + `langForMarket(exchange)` helper.
- Scoped store factory with phantom types — `useActiveStores()` returns the live or paper bundle based on the current mode store; features must never import `@/stores/scoped/*` directly (enforced by an ESLint boundaries rule).
- Mocked services layer: `accounts`, `positions`, `orders`, `quotes` (refcounted lazy ticker via `requestAnimationFrame`), `watchlists`, `commands`, `connected`, `quote-feeds`, plus a lazy `getServices()` registry. Storybook decorators + tests call `setTickingEnabled(false)` to keep the ticker quiet.
- 16 primitives: `Button`, `Input` (with numeric variant + memoed `NumericCell`), `Checkbox`, `Radio`, `Switch`, `Select`, `Dialog`, `Popover`, `Tooltip`, `DropdownMenu`, `Tabs`, `Icon` (Lucide wrapper), `Badge`, `Avatar`, `Toast` + `useToast`, `ErrorBoundary`.
- 11 patterns: `EmptyState`, `ResizablePanelFrame`, `ModeToggle` + `ModeSwitchConfirmDialog`, `AccountPicker` (grouped by broker), `ConnectedDropdown` (per-broker gateway health), `QuoteFeedDropdown` (per-exchange feed status), `DataTable` (TanStack Table + virtualizer) + `MobileCardRow`, `ColumnCustomizerDialog` (30-col reorder), `CommandPalette` (cmdk + prefix routing + global Cmd+K), `BottomTabBar` (mobile-only), `CollapsibleDrawer` (mobile-only side drawer).
- 4 layout components: `Topbar` (mode + account + connected + nav + palette trigger), `LeftPanel` + `RightPanel` (nested vertical PanelGroups), `AppShell` (single subtree, Tailwind-responsive, hydrate-on-mode, sets `<body data-mode>`).
- 8 feature pages: `OverviewPage` + `AccountSummary`, `OrdersPage` + compact, `PositionsPage` + compact, `WatchlistPage` + `WatchlistCompact` + `useTickingQuotes` rAF-throttled hook, `AdminPage` (Tabs shell) + `AdminConfigPage` + `AdminSecretsPage` (CRUD via CF-Access-gated `/api/admin`), `SettingsPage` (density + sound localStorage + about), `TradeStubPage` + `AlertsStubPage`.
- Playwright frontend smoke × 5 (paper-default body attr, paper→live confirm + cancel, Cmd+K palette → `/orders`, watchlist customize-columns dialog open/apply, mobile BottomTabBar navigates to `/positions`).
- DataTable stress story: 500 rows × 30 NumericCell columns + `PerformanceObserver` warning on >16ms frames; play function asserts the virtualizer keeps rendered row count well under the data length.
- Test coverage: 218 vitest tests across 52 files (primitives + patterns + layout + services + stores + hook).

### Changed
- ESLint flat config gained an `eslint-plugin-boundaries` rule enforcing the 5-layer dependency direction (tokens → primitives → patterns → layout → features) plus a `no-restricted-imports` block stopping features from reaching into `@/stores/scoped/*` outside the registry.
- `.gitignore` switched from blanket `.claude/` ignore to a selective allowlist (`!.claude/settings.json`, `!.claude/hooks/**`) so team-wide settings ship in git.

### Tooling
- Project-scope `.claude/settings.json` lands with team-wide pnpm/uv/docker/gh permissions and one PostToolUse hook (`.claude/hooks/post-edit-reminder.sh`) that emits silent reminders on Alembic migrations, lockfiles, docker-compose, `BrokerAdapter` base, CLAUDE.md, TASKS.md, eslint config, `.env.example`.
- Codex (`codex@1.0.4`) plugin authorized for source-code authoring via `codex:rescue`. Claude Code retains tests, stories, verification, and commits per the delegation rule recorded in `TASKS.md` Phase 3 header.

## [0.2.0] — 2026-04-23
### Added
- `CFAccessVerifier` — RS256 JWT verification via PyJWKClient with kid-miss retry, team-domain/audience enforcement, identity extraction from `email` or `common_name` (covers Google login + CF Access service token).
- `require_admin_jwt` FastAPI dep — `Cf-Access-Jwt-Assertion` header → identity, with prometheus result labels (`ok`, `expired`, `bad_signature`, `bad_claims`, `no_identity`, `kid_miss`, `missing_header`, `dev_bypass`, `jwks_fetch_fail`). Hard-refuses dev bypass attempts in prod with 500 + critical log.
- Dev-bypass double-gate: `APP_ENV=dev` AND client IP in `TRUSTED_DEV_NETS` CIDR list (empty by default; prod-safe).
- `ConfigService` — typed DB-backed config + Fernet-encrypted secrets. Full CRUD (`get` / `set` / `delete` / `list`), typed accessors (`get_int` / `get_bool` / `get_json`) that raise `ConfigTypeError` on mismatch, `set_secret` / `get_secret_metadata` / `reveal_secret*` / `delete_secret` / `list_secrets`.
- `ConfigCache` — in-memory TTL cache + Redis pub/sub invalidation with exponential-backoff listener reconnect.
- Fernet key derivation via HKDF-SHA256 (`app.core.crypto.get_fernet`). MultiFernet when `APP_SECRET_KEY_PREV` set — PREV-key hits increment `fernet_prev_key_hits_total` for rotation observability.
- `app_config` + `app_secrets` tables via Alembic migration `0001` with CHECK constraints (value/value_json exclusivity, value_type enum).
- Admin router at `/api/admin/{config,secrets}` — POST/GET/PUT/DELETE + `POST /api/admin/secrets/{ns}/{key}/reveal` with `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Pragma: no-cache`. Idempotent DELETE, 422 on body↔URL ns/key mismatch, 409 on duplicate POST.
- `/metrics` endpoint gated by admin auth — prometheus exposition of 7 collectors (`cf_jwt_verification_total`, `config_ops_total`, `config_cache_size`, `redis_publish_fail_total`, `redis_subscribe_reconnect_total`, `fernet_prev_key_hits_total`, `admin_secret_reveal_total`).
- `main.py` lifespan — wires `ConfigService` singleton + spawns two Redis pub/sub listener tasks; tears down on shutdown.
- `backend/scripts/entrypoint.sh` — runs `alembic upgrade head` before uvicorn; `ENTRYPOINT`+`CMD` split so compose `command:` overrides still trigger the migration step.
- Test coverage: 85 tests — crypto (6), cf_access (17), config_cache (7 + 1 opt-in real-redis), config_service (20), admin_api (22), admin_auth (6), metrics (1), migration (4), models (2). Playwright smoke extended with admin config + secret reveal round-trips.
- CI: `redis:7-alpine` service sidecar enables the opt-in pub/sub fidelity test via `CI_USE_REAL_REDIS=1`.

### Changed
- Pydantic `Settings` grew 4 bootstrap keys: `APP_SECRET_KEY_PREV`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`, `TRUSTED_DEV_NETS`.
- `.env.example` documents the new keys with defaults that keep prod-safe.
- `CLAUDE.md` "Configuration Storage" updated — example usage now shows `get_config()` → `svc.get(...)` / `svc.reveal_secret(...)`; rotation guidance added.

### Security
- Secret plaintext is never cached locally; `_reveal_typed` round-trips to DB + Fernet on every call, metric-logged by actor kind.
- `AdminIdentity.__repr__` scrubs the `claims` dict to prevent JWT leakage via exception formatting or log lines.
- Dev bypass logs a WARNING on grant (client_ip visible) for auditability.

## [0.1.0] — 2026-04-22
### Added
- Cloudflare Tunnel (cloudflared on VPS) replaces public 80/443.
- Cloudflare Access with Google IdP + 2-email allowlist.
- CF Access service token bypass for CI smoke tests.
- WireGuard dev-bypass route to nginx (10.10.0.1:80).
- `scripts/cloudflare/` — 10 idempotent CF API driver scripts.
- `deploy/vps/` — install-prep + install-enable + sshd-hardening + UFW + fail2ban + cloudflared.service.
- `docker-compose.prod.yml` — dual-bound nginx, tmpfs, non-root users, resource limits, pinned digests.
- `tests/e2e/` — Playwright smoke test; runs in CI via deploy.yml.
- `.github/workflows/deploy.yml` — rsync + compose up + smoke on push-to-main.
- gitleaks pre-commit hook.
- `pnpm audit` + `pip-audit` CI steps (fail on high/critical).
- Real `scripts/deploy.sh` (replaced Phase 0 stub).
- Architect-review workflow codified in CLAUDE.md phase workflow.

### Changed
- Nginx kept as defense-in-depth (headers, rate limits, Host: strict-match); certbot + cert-reload watcher removed.
- IONOS firewall reduced to 2222/tcp + 51820/udp only (was 80, 443, 8443, 8447, 51820, 2222).
- SSH hardened: password auth off, `AllowUsers trader` only, `MaxAuthTries 3`, Port 2222.

### Removed
- Dashboard_old deployment at dashboard.kiusinghung.com (torn down during cutover).
- Let's Encrypt certbot container + cert-reload sentinel.
- `trading` DB on NUC PG18 (already dropped pre-cutover).
- Public 80/443 exposure on VPS.

## [0.0.1] — 2026-04-21
### Added
- Initial repo scaffold: FastAPI backend, React 19 frontend, local docker-compose stack (Redis only; Postgres native on Windows).
- Component architecture: design-tokens → primitives → patterns → layout → features, enforced by ESLint boundaries.
- Tailwind v4 + shadcn/ui; Stylelint blocks `px` and `em` site-wide.
- Storybook 9 with seed `Button` primitive.
- Lint stack: ruff, mypy, ESLint (boundaries + a11y + hooks), Stylelint, pre-commit, commitlint.
- GitHub Actions CI: parallel backend + frontend jobs.
- Docs: CLAUDE.md constitution, TASKS.md roadmap, this changelog.
