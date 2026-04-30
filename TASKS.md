# Tasks

## Phase 0 — Repo scaffold & local-dev loop  *(complete — v0.0.1 · 2026-04-21)*
- [x] Initialize git + gh remote (private, proprietary) + conventional-commits pre-commit
- [x] Backend: uv project, FastAPI /health, structlog + redaction stub, Alembic init, tests, Dockerfile
- [x] Frontend: Vite + React 19 + TS strict + Tailwind v4 + shadcn init + Button primitive
- [x] Storybook 10 configured, Button has stories + tests *(bumped from plan's 9 per latest-stable policy)*
- [x] Design tokens: spacing/typography/colors/radii/motion (rem only)
- [x] Lint stack: Stylelint (no-px), ESLint (boundaries), pre-commit, commitlint
- [x] docker-compose.yml: redis + backend + frontend (Postgres runs natively on Windows)
- [x] .env.example with all bootstrap vars documented
- [x] GitHub Actions CI: backend + frontend jobs, both green
- [x] Docs: CLAUDE.md, TASKS.md, CHANGELOG.md, README.md
- [x] First PR merged (`#1`); tag `v0.0.1`

## Phase 1 — VPS cutover & security hardening  *(complete — v0.1.0 · 2026-04-22)*
- [x] Cloudflare automation scripts (scripts/cloudflare/*.sh, 10+1 helpers)
- [x] VPS install scripts (deploy/vps/install-prep.sh + install-enable.sh + friends)
- [x] nginx config ported from Dashboard_old, certbot stripped
- [x] docker-compose.prod.yml with dual-bound nginx + tmpfs + pinned digests
- [x] Playwright smoke test (tests/e2e/smoke.spec.ts)
- [x] GitHub Actions deploy.yml + CI audit steps
- [x] gitleaks pre-commit hook
- [x] Real scripts/deploy.sh (replace Phase 0 stub)
- [x] Cutover executed: old stack down, trading DB dropped, new stack live
- [x] IONOS firewall reduced to 2 ports, direct-IP bypass confirmed closed
- [x] Playwright smoke test passes via CF Access service token
- [x] v0.1.0 tagged and pushed

## Phase 2 — Auth + DB-backed config service (app_config, app_secrets)  *(complete — v0.2.0 · 2026-04-23)*
- [x] Pydantic Settings: +4 bootstrap keys (`APP_SECRET_KEY_PREV`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`, `TRUSTED_DEV_NETS`)
- [x] Fernet + HKDF-SHA256 key derivation, MultiFernet for rotation (`app.core.crypto`)
- [x] CFAccessVerifier + `require_admin_jwt` dep with prometheus result labels + dev-bypass double-gate
- [x] Alembic migration 0001: `app_config` + `app_secrets` with CHECK constraints (value exclusivity + value_type enum)
- [x] ConfigCache: in-memory TTL + Redis pub/sub invalidation with backoff reconnect
- [x] ConfigService: typed CRUD + encrypted secrets + typed accessors that raise ConfigTypeError
- [x] Admin router `/api/admin/{config,secrets}` with idempotent DELETE + 422 on body↔URL mismatch
- [x] Reveal endpoint with `Cache-Control: no-store`, audit log, `admin_secret_reveal_total` metric
- [x] `/metrics` endpoint gated by admin auth
- [x] main.py lifespan wires ConfigService singleton + Redis listener tasks
- [x] backend entrypoint.sh runs `alembic upgrade head` before uvicorn
- [x] Test coverage 85 tests (crypto 6 · cf_access 17 · cache 8 · service 20 · admin api 22 · admin auth 6 · metrics 1 · migration 4 · models 2)
- [x] CI: redis:7-alpine service + opt-in real-redis pub/sub fidelity test
- [x] Playwright smoke extended with admin config + secret reveal round-trips
- [x] v0.2.0 tagged

## Phase 3 — Frontend shell (mocks)  *(complete — v0.3.0 · 2026-04-24)*

- [x] Chunk A — Foundations: dependencies, Tailwind @theme tokens, Noto fonts + langForMarket, Vite config (Tasks 1-4)
- [x] Chunk B — Router: TanStack Router bootstrap + 11 route stubs (Tasks 5-6)
- [x] Chunk C — Services: types, fixtures, accounts/positions/orders services, refcounted Quotes ticker, lazy registry (Tasks 7-11)
- [x] Chunk D — Stores: global stores, scoped factory + phantom types, registry + useActiveStores, ESLint boundary rule (Tasks 12-15)
- [x] Chunk E — Primitives × 16: Input/NumericCell, Checkbox/Radio/Switch, Select, Dialog/Popover/Tooltip, DropdownMenu/Tabs, Icon/Badge/Avatar, Toast, ErrorBoundary (Tasks 16-23)
- [x] Chunk F — Patterns × 11: EmptyState, ResizablePanelFrame, ModeToggle + ConfirmDialog, AccountPicker, ConnectedDropdown, QuoteFeedDropdown, DataTable + MobileCardRow, ColumnCustomizerDialog, CommandPalette, BottomTabBar, CollapsibleDrawer (Tasks 24-33)
- [x] Chunk G — Layout × 4: Topbar, LeftPanel + RightPanel, AppShell single-subtree (Tasks 34-36)
- [x] Chunk H — Features × 8: Overview + AccountSummary, Orders + compact, Positions + compact, Watchlist + Compact + ticking hook, Admin (Page + Config + Secrets), Settings, Trade + Alerts stubs (Tasks 37-42)
- [x] Chunk I — Tests: Playwright frontend smoke × 5, DataTable 500×30 stress story with frame-budget observer (Tasks 43-44)
- [x] Chunk J — Close-out: docs + pre-flight sweep + tag (Tasks 45-48)

### Delegation rule (active from 2026-04-24)

**Coding only** is delegated to **Codex** (`codex:rescue` → `codex:codex-rescue` subagent). Codex writes/edits source files (components, hooks, services, stores, routes, feature pages). Codex does **not** author tests, stories, or make commits.

Claude Code keeps:
- Reading plans/specs + drafting Codex prompts
- Writing tests + Storybook stories
- Running typecheck/lint/test verification
- Staging + committing (conventional commits, commitlint, gitleaks)
- Updating TASKS.md / CHANGELOG.md / CLAUDE.md

- Rationale: user-initiated 2026-04-24 after `codex login`; narrowed 2026-04-24 ("just coding, not tests and commits").
- Scope: remainder of Phase 3 (Tasks 39-48) + Phases 4-9 unless user says otherwise.
- Override: user can say "use Frontend Developer" / "use general-purpose" / "do it yourself" to route around Codex per-task.


## Phase 4 — IBKR adapter (read-only, BrokerAdapter base lands here)  *(complete — v0.4.0 · 2026-04-26)*
- [x] Chunk A — Prerequisites + scaffold (verify-wg-windows.ps1 §0 gate, proto contract, buf wiring, codegen)
- [x] Chunk B — Sidecar core (entrypoint, handlers, normalize, pnl_cache, probe.py)
- [x] Chunk C — Sidecar packaging (PyInstaller --onedir build, golden-trace recording)
- [x] Chunk D — mTLS provisioning (provision-sidecar-mtls.ps1, provision-and-publish.ps1, revoke-cert.ps1, RUNBOOK-mtls-recovery.md)
- [x] Chunk E — NUC ops glue (BrokerWatchdog/Tray/DailyRestart, sidecar probe + tray dots, register-ibkr-sidecar.ps1, Launch-IBKRSidecar.vbs, gsudo + admin trampolines, Pester suite)
- [x] Chunk F — Backend service layer (Alembic 0002, brokers.py registry+client, ibkr_maintenance.py, AccountService, lifespan wiring, tzdata Dockerfile layer)
- [x] Chunk G — REST routes (`/api/accounts` list+patch, `/{id}/{summary,positions,orders}` with 503+Retry-After error envelope, OpenAPI smoke)
- [x] Chunk H — Frontend wiring (decimal.ts safeParseDecimal + custom ESLint rule, MaintenanceError/SidecarUnreachableError, listAccounts/Positions/Orders behind VITE_USE_MOCKS, useFleetHealth + degraded pill, Storybook mocks-pinned)
- [x] Chunk I — Tests + smoke (in-process gRPC discover-loop e2e, Playwright Phase 4 smoke × 4, nightly-real-ibkr.yml + self-hosted runner runbook, CI proto + sidecar jobs)
- [x] Chunk J — Close-out (CHANGELOG/TASKS/CLAUDE.md updates, pre-flight gates, USER GATE for push + tag v0.4.0)

## Phase 5a — NLV caching + currency + 4.x cleanups  *(complete — v0.5.0 · 2026-04-27)*

- [x] Chunk A — Schema + helper extraction (Alembic 0003 NUMERIC(20,8)+CHECK regex, `compute_broker_maintenance` helper, `_classify_sidecar_failure` refactor)
- [x] Chunk B — Backend wire shape (`AccountResponse.nlv*`, `AccountListResponse.broker_maintenance`, `_format_nlv` Decimal helper, OpenAPI contract test rename)
- [x] Chunk C — Discoverer fan-out (`asyncio.Lock`, `gather + wait_for(10)`, skip-write predicate, savepoint per row + sqlstate==22003 overflow, resurrect-clears-NLV)
- [x] Chunk D — Sidecar concurrency (`test_concurrent_summaries_do_not_interfere` 22 parallel calls)
- [x] Chunk E — Frontend mapper (`AccountResponse` TS types, `BrokerMaintenance`, currency fallback chain, `useFleetMaintenance` Zustand, `fetchAccountsAndSyncMaintenance` hook)
- [x] Chunk F — AccountPicker UI (`nlvCellState` 4-variant helper, React.memo row, 6 unit tests)
- [x] Chunk G — `sidecar/tests/test_real_ibkr_smoke.py` (6 read-only tests vs paper 4002)
- [x] Chunk H — Close-out (CHANGELOG ✓, TASKS ✓, CLAUDE.md ✓, tag v0.5.0)

## Phase 5b — Trade execution (IBKR)  *(complete — v0.5.1 + v0.5.2 hardening · 2026-04-28)*

Order place/cancel/status for IBKR. `OrderEvent` stream subscription is a separate background task per sidecar (one persistent gRPC server-streaming RPC per gateway), NOT extended off `_discover_once` (R14 architectural note from 5a spec). End-to-end verified on prod via paper canary (BARC + VOD on isa-paper).

- [x] Chunk A — Foundation (Alembic 0004 orders + order_events; proto add PlaceOrder/CancelOrder/OrderEvent/SearchContracts; gen-types.sh; BrokerSidecarClient extension; shared mock fixtures)
- [x] Chunk B — Sidecar handlers (PlaceOrder + simulator, CancelOrder, OrderEvent stream, SearchContracts caching + 5/sec rate limit, real-IBKR smoke gated on `CI_USE_REAL_IBKR=1`)
- [x] Chunk C — Pydantic + ORM models + per-account trade policy keys
- [x] Chunk D — 8 backend endpoints (preview, place, list, detail, policy, cancel, contract search, SSE) + OpenAPI snapshot lock
- [x] Chunk E — `BrokerOrderEventConsumer` + `PendingSubmitWatchdog` + reconnect-and-resync (R9 startup gap closed)
- [x] Chunk F — Frontend services + Zustand store + `useOrdersList` / `useOrdersStream` hooks
- [x] Chunk G — `ContractSearchInput` + `TradeTicketModal` + `OrdersPage` extension + Trade entry-points (AccountPicker + positions row)
- [x] Chunk H — Prometheus metrics + alerts.yml + docker-compose.prod single-worker + nginx SSE + clean_tables fixture + lifespan integration
- [x] H4 close-out — CHANGELOG ✓, TASKS ✓, CLAUDE.md ✓, tag v0.5.1 ✓
- [x] v0.5.2 hardening — 13 post-tag hotfixes (contract resolver, positions guard, currency_base fallback, trade-policy key shape, streaming-deadline) + first end-to-end paper canary validated on prod ✓
- [x] v0.5.3 — Phase 5b.1 canary hotfix pack — Alembic 0005 positions table + discoverer fan-out, SIM cancel echo via synthetic `ib.orderStatusEvent.emit`, layered E2E tests (`e2e-mock.yml` per-PR + `nightly-real-ibkr.yml` `e2e-trade` job), Prometheus alerts. BASE-tag startup round skipped per empirical pre-flight failure; v0.5.2 `last_nlv_currency` fallback remains canonical workaround.

## Phase 5c — Advanced order types  *(complete — v0.5.4 · 2026-04-29)*

Modify, bracket orders, fills history. Builds on 5b's place/cancel + the consumer/watchdog infra. 14 architect-review findings (2 CRIT + 4 HIGH + 5 MED + 3 LOW) all resolved inline.

- [x] Chunk A — Schema + proto (Alembic 0006 `modified` enum value; 0007 `order_status_rank()` SQL function + `parent_order_id` + `oca_group` + `fills` + `pending_fills` tables; proto: `ModifyOrder`, `PlaceBracket`, `exec_id`+`kind` on `OrderEventMessage`)
- [x] Chunk B — Sidecar handlers (`ModifyOrder`, `PlaceBracket`, `exec_id`/`kind` emission, `commissionReport` subscription, contract tests)
- [x] Chunk C — Backend service + endpoints (`modify_order` with replay cache, `place_bracket` two-phase commit, `list_fills` cursor pagination + `list_orders` date-range; PUT `/api/orders/{id}` + POST `/api/orders/bracket` + GET `/api/fills` + 18 unit tests + OpenAPI snapshot lock `test_openapi_schema_lock_phase5c` + frontend types regen)
- [x] Chunk D — Consumer fills + status-rank + cascade (`order_status_rank` predicate, `pending_fills` buffer + 30s sweeper, `commission_buffer` + `commission_report` event, `broker_bracket_cancel_cascade_seconds` histogram, 6 unit tests)
- [x] Chunk E — E2E + workflow (`FakeBrokerServicer` ModifyOrder + PlaceBracket + cascade-aware CancelOrder; `test_e2e_modify_chain.py` + `test_e2e_bracket_chain.py`; `test_real_ibkr_e2e_modify.py` + `test_real_ibkr_e2e_bracket.py` stubs)
- [x] Chunk F — Frontend (`TradeTicketModal` mode prop with field-disable map; `useFillsHistory` cursor hook; `FillsTable` pattern with date grouping + sticky header; `OrdersPage` Modify button on non-terminal rows; `/orders/$id/fills` route)
- [x] Chunk G — Alerts + close-out (`BrokerBracketCascadeLag` + `BrokerPendingFillsBacklog` + `CommissionBufferOverflow` alerts; CHANGELOG ✓; TASKS ✓; CLAUDE.md ✓; memory `phase5c_shipped.md`; tag v0.5.4)

### Open scope deferred from 5c (carries to v0.5.6+)

- [x] **`AccountResponse.position_count`** — shipped v0.5.6 (`ad9e23a`). LEFT JOIN positions count in `list_accounts`; field default 0 for accounts with no positions row.
- [x] **Brief 502 flash after backend restart** — `scripts/restart-backend.sh` (commit `11cda91`) bundles `docker compose restart backend` with `nginx -s reload` so manual restarts don't 502. Use that instead of bare `docker compose restart backend`.
- [x] **OrderEvent stream observability** — `BrokerOrderEventStreamDown` (page, `consumer_alive == 0` for 2m) + `BrokerOrderEventStreamFlapping` (warning, >10 reconnects/10m for 5m) added to `alerts.yml` `phase5b_orders` group. Both backed by metrics that already existed (`consumer_alive` Gauge, `broker_order_stream_reconnects_total` Counter). Lifecycle logs already in v0.5.5.
- [ ] **Multi-worker uvicorn** → Phase 9. Single-worker still load-bearing for the in-memory replay cache + commission buffer.

**Deferred to Phase 7 (after Schwab) — bundled with quote-subscribe rework:**

These three are the same shape of problem (sidecar only subscribes at startup; mid-run additions never get a subscription) and want the same fix pattern (on-demand subscribe with timeout). Designing once across IBKR + Futu + Schwab is cheaper than three one-offs.

- [ ] **On-demand quote subscribe for preview** — `_get_market_mid()` reads `mkt:mid:<conid>` from Redis only; sidecar populates this only for held positions. New tickers (e.g. AAPL when no AAPL position is held) → preview returns `503 market_mid_unavailable`. SGLN/VWRP work (held). Fix: eager `reqMktData` on contract-pick in `ContractSearchInput`, or backend-side one-shot subscribe with timeout in preview path.
- [ ] **Periodic BASE-tag refresh for accounts added mid-run** — eager `reqAccountUpdates` cycle when discoverer detects a new account. v0.5.2 `last_nlv_currency` fallback covers steady state, so no immediate user impact, but a new mid-run account never gets its base tag without a sidecar restart.

### v0.5.5 hotfix bundle shipped (2026-04-29) — end-to-end SIM canary debug pass

~14 commits between v0.5.4 (`5a86448`) and v0.5.5 close, all via the per-commit review chain.

- [x] **Topbar wires features-layer AccountPicker** so the Trade button surfaces in the picker dropdown (was bare pattern before).
- [x] **`/api/contracts/search` URL fix** in `services/orders.ts` (was hitting `/api/contracts`, returning 404).
- [x] **`OrderResponse.conid` exposed on the wire** + `list_orders`/`get_order_by_id` SELECT projections so the modify modal can pre-fill the contract.
- [x] **Modify nonce hashes 8 fields** (matching preview mint), not 3 — `_consume_nonce` now recomputes the same hash preview produced. Test helper updated.
- [x] **`OrderStatusEnum` + `_normalize_status` + `_synthesize_resync` aliases include `'modified'`** so the wire reads back; consumer accepts the new status.
- [x] **Sidecar SIM modify echo handler** (mirrors 5b.1 SIM cancel echo). Plus `--no-simulator` CLI flag (default still simulator-only for safety).
- [x] **Backend INVALID_ARGUMENT/NOT_FOUND from sidecar → 422 `broker_modify_rejected`** (was 500 with raw stack trace). `BrokerSidecarUnavailable` carries `grpc_code` + `grpc_details`.
- [x] **CRITICAL: SIM dispatch via per-account `_order_event_queues`** instead of `ib.orderStatusEvent.emit()`. Diagnostic instrumentation showed `emit()` doesn't trigger externally-registered listeners under ib_async's eventkit (cross-loop / IB-callback-only). Sidecar now puts the synthetic message directly into matching gRPC stream queues. This was the root cause of the 5b.1 SIM cancel echo flakiness AND the 5c modify smoke gap.
- [x] **`OrderEvent` subscribe/emit/queue lifecycle logging** in sidecar handler + backend consumer to diagnose the propagation gap above.
- [x] **`applyEvent` keys by `event.order_id` (not audit `event.id`)** — was creating orphan store entries; now updates the right row.
- [x] **`ACTIVE_STATUSES` includes `'modified'`** — modified rows stay visible in the Active list.
- [x] **Modify route updates `orders.qty/limit_price/stop_price/tif/notional` in-place** so UI reflects the new values, not just status. HIGH-3 audit-only-write split preserved for `status` (consumer-owned).
- [x] **OrdersPage refetches after modify (modal close + onSuccess) and after cancel (immediate + 750ms double-refetch)** so UI updates without manual page refresh.
- [x] **TradeTicketModal `handleSubmit` awaits a fresh preview** before constructing the body, eliminating the debounce → submit race that produced `payload_mismatch`.
- [x] **`broker_order_modify_duration_ms` Histogram + `broker_fills_write_failed_total{reason}` Counter** — instrumented. `BrokerOrderModifyP99HighWarning` + `BrokerFillsWriteFailures` alerts re-enabled in `alerts.yml`.
- [x] **`ContractSearchInput` STK-first ranking** — `rankContracts` partitions STK/STOCK to the top of the search dropdown.
- [x] **Verified `get_order_by_id` SELECT projection** — `OrderResponse` schema doesn't expose `parent_order_id`/`oca_group` (those exist in DB but aren't on the wire); the projection is correctly minimal. Earlier note was wrong.

## Phase 6 — Futu HK adapter + JP kanji font polish  *(complete — v0.6.0 · 2026-04-30)*

Read+place+cancel for HK stocks/ETFs/warrants/CBBC via FutuOpenD. New `sidecar_futu/`
PyInstaller process at `10.10.0.2:18005` with `Configure` RPC for app_secrets-driven
creds. Modify/Bracket return UNIMPLEMENTED (Phase 7 pickup). 12 architect-review
findings (5H + 6M + 8L) + 9 SDK-mismatch defects caught during execution all
applied inline.

- [x] Chunk A — Proto + wiring shells (Configure RPC, Health.broker_id/started_at, AssetClass.CBBC, SIDECAR_BROKERS map, alerts, runbook)
- [x] Chunk B — Sidecar core (`sidecar_futu/` package, Health/Configure handlers, `_init_loop` with RSA tempfile + SysConfig.set_init_rsa_file + unlock_trade fix, mTLS server hardening port from `sidecar/tls.py`, PyInstaller build script with UTF-8 BOM/CRLF)
- [x] Chunk C — Sidecar read+trade (GetAccountSummary, GetPositions+BWRT->CBBC mapping, GetOrders+16-state status table, SearchContracts via OpenQuoteContext column-remap, PlaceOrder with TIF+aux_price, CancelOrder via modify_order(CANCEL), SIM mode, OrderEvent stream with threadsafe `loop.call_soon_threadsafe` dispatch + H5 drop-pre-subscribe, Modify/Bracket UNIMPLEMENTED, IBKR Configure no-op)
- [x] Chunk D — Backend service updates (BrokerRegistry H4 cross-check, BrokerConfigurer + H2 reconfigure on started_at delta, `POST /api/admin/brokers/{label}/reconfigure`, contracts/search `?broker=` Pydantic Literal with schwab->503)
- [x] Chunk E — Tests (FakeBrokerServicer broker-agnostic, futu_test_data.py HK fixtures, test_reconfigure_cycle.py, sidecar_futu real-grpc contract test)
- [x] Chunk F — Frontend (`frontend/public/fonts/README.md` pyftsubset runbook, `[lang|="ja"]` selector + Noto Sans JP two-face split in global.css, CJKText.stories.tsx visual diff, `searchContracts` broker option-bag, TradeTicketModal disables STOP for warrants/CBBC with useEffect auto-revert)
- [x] Chunk G — Ops (`build-windows-futu.ps1` + `restart-futu-sidecar.ps1` UTF-8 BOM/CRLF; runbook-futu-setup.md 9 sections)

### Plan defects caught during execution

- C1/C2/C3/C5/C6: hardcoded `TrdEnv.REAL` -> `_accounts_trd_env` cache populated by list_accounts.
- C2: `BOND->CBBC` map wrong (Chinese docstring confirms WARRANT covers CBBC; `BWRT` is canonical Bull/Bear-Warrant).
- C3: plan missed 5 SDK OrderStatus values (CANCELLING_ALL/PART, SUBMIT_FAILED, FILL_CANCELLED, TIMEOUT).
- C4: `OpenQuoteContext.get_stock_basicinfo` returns `name`/`stock_type` not `stock_name`/`security_type`; needs query-layer remap.
- C5: missed `time_in_force` and `aux_price` parameters; plan only handled LIMIT/MARKET.
- C7: proto field `broker_event_at` doesn't exist (actual: `event_at`).
- C8: `q.put_nowait` from futu callback thread not threadsafe; switched to `loop.call_soon_threadsafe`.
- B4: SDK kwarg is `password_md5` (plan: `unlock_password_md5`); RSA priv key needs file-path via `SysConfig.set_init_rsa_file(<tempfile>)`, plain string in dataclass not consumed by SDK; `is_encrypt=True` mandatory.
- B6: barebones `tls.py` insufficient — ported hardened `sidecar/tls.py` (TLS 1.3, CRL hot-reload, cert/key matching pair, file-perm guard).

### Reviewer-applied findings (CRIT+HIGH+MEDIUM through F)

- B3: silent-failure in validate (narrowed try/except), FutuCreds repr leak (`repr=False`), concurrent Configure race (`asyncio.Lock`).
- B4: ctx.close try-guard, RSA path captured by value (closure race), atexit cleanup, _connect typing, no `assert` in prod paths.
- B5: metric name aligned to A6 alert (`broker_normalize_unknown_total{label,field}`), discriminated-union return type for `account_from_futu_row`.
- B6: assert_key_file_permissions before read_bytes, ACL runbook note (Windows ACL provisioning).

### Deferred to operator / Phase 7

- F1 binary regen (operator runs pyftsubset pipeline documented in `frontend/public/fonts/README.md`).
- G2 Defender exclusion + scheduled task registration (operator-only, runbook step 8 + 9).
- G3 deploy verification (USER GATE).
- D5 mechanical test parametrization for futu label (low value; existing tests are IBKR-specific by design).
- E3 e2e_futu_chain HTTP test (asset-class routing covered by C2 4 unit + C5+C6 6 unit + D4 4 HTTP).
- ModifyOrder + PlaceBracket on Futu sidecar (UNIMPLEMENTED stubs ship; Phase 7 implements).

> **Phases 7 → 25 are locked in [`docs/ROADMAP.md`](docs/ROADMAP.md).** The stubs below carry only the headline + open follow-ups inherited from prior phases. Each phase gets its full chunk breakdown when its own brainstorm runs.

## Phase 7a — Schwab connect (data + read-only)

`sidecar_schwab/` running on the **VPS** as a docker-compose service (cloud-broker pattern — no NUC, no PyInstaller, no mTLS). OAuth + manual re-auth UI for the 7-day refresh-token wall + opt-in Tier-2 Playwright auto-refresher (feature-flagged). `Configure` RPC, `ListAccounts`, `GetAccountSummary`, `GetPositions`, `GetOrders` (last 7 days, read-only). `account_hash` column on `broker_accounts` (Alembic 0008 — Schwab privacy layer; NULL for non-Schwab brokers). Trade execution + StreamQuotes return UNIMPLEMENTED.

Spec: [`docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md`](docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md). Architect-reviewed (3 CRIT + 6 HIGH + 7 MED + 5 LOW; CRIT+HIGH+MED applied inline).

## Phase 7b — Streaming quote engine + IBKR/Futu/Schwab/Coinbase/OANDA sources

Subscription registry (refcounted), Redis quote bus `quote.<source>.<canonical_id>`, frontend WebSocket gateway with conflation (4–10/s), `instruments` + `symbol_aliases` schema (Alembic 0009), stale detection. IBKR + Futu + Schwab streamers wired in one phase. Coinbase WS + OANDA practice WS as additional sources (data-only prep for Phase 15). Quote-source-router with config-driven priority. **Saves IBKR data fees from v0.7.1.**

Inherits from prior deferrals:
- [ ] **On-demand quote subscribe for preview** (deferred from 5c) — falls out of the registry pattern.
- [ ] **Periodic BASE-tag refresh for accounts added mid-run** (deferred from 5b.1).

## Phase 8 — Schwab trade + order-type expansion + Futu Modify/Bracket

Schwab `PlaceOrder`/`CancelOrder`/`ModifyOrder`/`OrderEvent`. STOP_LIMIT, TRAIL/TRAIL_LIMIT, IOC/FOK/GTD, OCO non-bracket, MOC/MOO/LOC/LOO across IBKR + Futu + Schwab. Futu Modify + Bracket (deferred from Phase 6).

## Phase 9 — Charting v1 + bar aggregator + historical store

TimescaleDB hypertable on PG-18, klineschart integration, 1s/1m/5m/15m/1h/1d bars, drag-handle stop/TP edit, historical backfill from broker APIs (Schwab CHART_EQUITY → free 1m US bars).

## Phase 10 — Risk engine + position-sizing + multi-account rollup

PDT counter, buying-power calc, position concentration limits, pre-trade margin check, max daily loss, account-level kill switch. Position-sizing calculator (Kelly / fixed-fractional / vol-target). Multi-account portfolio rollup. Pre-trade gate becomes mandatory chokepoint.

## Phase 11 — AI router + Alerts + Telegram

Ollama router (NUC light + heavy-box WoL with 30s warmup cache), `services/ai/` module any subsystem can call. Price/condition alerts engine. Telegram bot. Prompt-cost tracking.

## Phase 12 — Options (single-leg)

Option chain viewer, strike/expiry pickers, on-demand strike-window subscribe, Greeks display, exercise/assign events. Polymorphic contract via JSONB `contract_details`.

## Phase 13 — Multi-leg option combos

Spread / straddle / strangle / collar / butterfly / condor / iron-condor ticket. Net-debit/credit preview. Schwab `complexOrderStrategyType` + IBKR combo legs.

## Phase 14 — Futures

CME on IBKR + Schwab; HKFE (HSI/HHI) on Futu. Contract-month roll UI. Settlement events. Tick-size/multiplier per contract.

## Phase 15 — Forex + Crypto

IBKR IDEALPRO FX. IBKR Paxos crypto. Coinbase WS as free crypto data source (data-only). 24/7 maintenance handling. Decimal qty (not integer).

## Phase 16 — Bonds + Mutual Funds + CFD

CUSIP search, accrued-interest, T+2. Mutual-fund EOD NAV ordering. CFD on IBKR (ex-US jurisdictions only).

## Phase 17 — IBKR algos

Adaptive, TWAP, VWAP, Arrival, Iceberg / Hidden / Reserve. Algo parameter UI.

## Phase 18 — Universe scanner + News/filings + Earnings-event handling

Rule-based scanner (RSI / breakout / volume / mcap / fundamentals) + LLM commentary. Schwab `SCREENER_EQUITY` feed. SEC EDGAR (US) + RNS (HK) filings ingest. Earnings calendar with auto-flat / auto-pause hooks.

## Phase 19 — Backtesting harness

Replay historical bars through strategy code. PnL / drawdown / Sharpe / MAR report. Walk-forward. Monte Carlo.

## Phase 20 — Bot engine v1 (rule-based)

Strategy plugin model (Python files). Bot lifecycle (create/start/stop/version). Per-bot risk caps. Paper-mode-by-default. Bot worker is a separate Docker service.

## Phase 21 — Bot engine v2 (LLM-in-loop)

LLM-as-analyst on bot decisions. Parameter-tuning loop with human approval. Shadow-mode strategy promotion. Per-bot perf-attribution.

## Phase 22 — Bot engine v3 (autonomous, self-refining)

Multi-bot orchestration. Nightly retrain. LLM-driven strategy generation with guardrails. Auto-promotion rules. **No raw RL.**

## Phase 23 — UK CGT awareness + per-bot attribution + cgt-calc handoff

Real-time Section 104 pool tracker (mirrors `fills` table on every fill). Same-day + 30-day "bed-and-breakfast" matcher running continuously. Pre-trade gate (Phase 10) adds "would trigger 30-day b&b matching" warning. Live £3,000 annual allowance gauge in UI. New "Tax" page: Section 104 positions per security, 30-day-window alerts, allowance gauge, per-bot / per-strategy / per-asset / per-broker PnL breakdown. Year-end export pipeline emits RAW-CSV format consumable by [`KapJI/capital-gains-calculator`](https://github.com/KapJI/capital-gains-calculator). Optional admin-page subprocess invocation of `cgt-calc` for in-place PDF. Crypto / options / futures tracked locally with "not handled by cgt-calc — calculate manually for HMRC" flag.

**Contingency:** if cgt-calc proves unfit at Phase 23 start (current bug investigation pending; tracked as a side task independent of the roadmap), scope expands to include an in-house Section 104 calculation engine — back to the 23a + 23b shape from the previous draft.

## Phase 24 — Infra hardening

- [ ] PG client-cert auth over WireGuard — drop the plaintext `DATABASE_URL` password.
  - Edit `pg_hba.conf` on NUC: `hostssl dashboard trader 10.10.0.0/24 cert clientcert=verify-full`
  - Generate + distribute `secrets/postgres-client.{key,crt}` to VPS (600, `trader:trader`)
  - Shrink `DATABASE_URL` to `postgresql+asyncpg://trader@10.10.0.2/dashboard?ssl=require`
  - Context: user asked 2026-04-23; Phase 2 left `.env` password plaintext because `DATABASE_URL` is bootstrap. File-perms + WG isolation are current protection; cert auth eliminates the secret entirely.
- [ ] Multi-worker uvicorn — Redis-backed nonce / replay / commission stores so single-worker constraint drops.
- [ ] ClickHouse for tick history if TimescaleDB outgrows.

## Phase 25 — PWA mobile + v1.0 ship

Service worker. Install-to-home-screen. FCM / Web Push notifications. Mobile-only chart UX. Offline order queue. Biometric lock via WebAuthn. **Tag v1.0.0.**

## Phase 2.x — follow-ups discovered during v0.2.0 verify

- [ ] nginx: add `location = /metrics { proxy_pass http://backend:8000/metrics; }` so Prometheus / Grafana can scrape through CF Access + service token. Backend endpoint exists and is auth-gated; only nginx is missing the proxy. Verified in prod 2026-04-23.
