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

## Phase 7a — Schwab connect (data + read-only) ✅ shipped v0.7.0 (2026-05-04)

`sidecar_schwab/` on NUC at 18006 (PyInstaller-frozen, mTLS over WG). OAuth + manual re-auth UI for the 7-day refresh-token wall + opt-in Tier-2 Playwright auto-refresher (`sidecar_schwab_refresher/` separate cron container). `Configure` RPC, `ListManagedAccounts`, `GetAccountSummary`, `GetPositions`, `GetOrders` (read-only). `account_hash` column on `broker_accounts` (Alembic 0008; boundary-stripped before reaching FE). Trade execution + StreamQuotes return UNIMPLEMENTED (deferred to Phase 7b/8).

Single-writer rule (C2) enforced via `service BackendCallback` proto + PG advisory lock. HMAC-signed state nonce + Redis `GETDEL` atomic-consume for OAuth replay defense (H1). 11 metrics + 9 alerts (`phase7a_schwab` group). Operator runbook at `deploy/runbook-schwab-setup.md`. CF Access bypass for the public OAuth callback at `scripts/cloudflare/access-bypass-schwab-callback.sh`.

Spec: [`docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md`](docs/superpowers/specs/2026-04-30-phase7a-schwab-connect-design.md). Architect-reviewed (3 CRIT + 6 HIGH + 7 MED + 5 LOW; CRIT+HIGH+MED applied inline). Phase memory: `phase7a_schwab_topology.md`.

## Phase 7b — Streaming quote engine + IBKR/Futu/Schwab/Coinbase/OANDA sources

Subscription registry (refcounted), Redis quote bus `quote.<source>.<canonical_id>`, frontend WebSocket gateway with conflation (4–10/s), `instruments` + `symbol_aliases` schema (Alembic 0009), stale detection. IBKR + Futu + Schwab streamers wired in one phase. Coinbase WS + OANDA practice WS as additional sources (data-only prep for Phase 15). Quote-source-router with config-driven priority. **Saves IBKR data fees from v0.7.1.**

Inherits from prior deferrals:
- [ ] **On-demand quote subscribe for preview** (deferred from 5c) — falls out of the registry pattern.
- [ ] **Periodic BASE-tag refresh for accounts added mid-run** (deferred from 5b.1).

### Phase 7b.1 progress  *(complete — v0.7.1 · 2026-05-05)*
- [x] A1 — proto: `StreamQuotes` RPC + `SymbolRef` + `QuoteMessage` (`df8502a`)
- [x] A2 — Alembic 0009 `instruments` + `symbol_aliases` (`e1c8f51`)
- [x] A3 — SQLAlchemy ORM models (`392f7b8`)
- [x] A4 — `InstrumentResolver` race-safe upsert (`811e4e7` + reviewer fixes `4062b1b`)
- [ ] A5 — **deferred to Phase 7b.1.5** (see below). Plan assumed `positions.symbol` + `positions.exchange` + `watchlist_entries`; actual schema (Alembic 0005) has neither + `watchlist_entries` table doesn't exist. Resolver works fine without seed — first quote tick creates the row lazily.
- [x] B1–B5 — QuoteEngine core: `canonical_id` helpers + `SubscriptionRegistry` + `SourceRouter` + `SidecarStream` + `QuoteEngine` with INV-Q-1..4
- [x] C1–C3 — Schwab streamer + handler + token-rotation reconnect (CRIT-2)
- [x] D1 — Futu HK Lv1 streamer + Subscribe/Unsubscribe/Resync ops
- [x] E1 — IBKR streamer × 4 with LSE GBp guard (canonical-id-derived; SMART-routing-safe)
- [x] F1 — `WSConflator` per-WS focused-10Hz/background-4Hz
- [x] F2 — `/ws/quotes` MessagePack endpoint with CF JWT auth (HIGH-2), msgpack bounds, slow-client wait_for (HIGH-3)
- [ ] F3 — cardinality load test (gated off CI; reuse harness from B5/F2 unit tests when needed)
- [x] G1 — `RealQuotesService` + `connectWs` + `Quote.isStale`/`staleSinceMs` markers
- [x] G2 — `useFocusedSymbol` hook + Trade ticket integration (`bd99dfd`)
- [x] G3 — Playwright E2E for `/ws/quotes` upgrade + frame receipt (`49962b3`)
- [x] H1 — operator runbook `deploy/runbook-quote-coverage.md` (Schwab `$`-symbology day-1 verification template)
- [x] H2 — operator runbook `deploy/runbook-ibkr-data-subs.md` (cancel/keep/subscribe matrix template)
- [x] H3 — operator runbook `deploy/runbook-quote-streaming-ops.md` (debugging + adding-source guide)
- [x] H4 — close-out: CHANGELOG `[0.7.1]` + CLAUDE.md phase-shipped + memory `phase7b1_shipped.md` + tag `v0.7.1`

LOW deferrals carried forward to Phase 7b.2:
- yfinance + Coinbase + OANDA streamers (`sidecar_market_data/`)
- Source enum entries 4-13 wired by demand

## Phase 7b.1.5 — Instruments seed + admin alias endpoint  *(complete — 2026-05-05)*

Boot helper `seed_instruments_from_positions(session_factory)` + admin endpoint `POST /api/admin/instruments` for operators to manually create aliases when the lazy creation flow surfaces `op:"err", code:"NO_INSTRUMENT"`. Shipped with `d42142c`.

- [x] Alembic 0010: `positions.symbol`/`primary_exchange`/`canonical_id` (all NULLABLE — backfill is operator action, not migration concern); `watchlist_entries` table.
- [x] `WatchlistEntry` ORM model.
- [x] `BrokerDiscoverer._upsert_positions` populates the 3 new columns + emits `quote_position_canonical_resolved_total{broker_id}` / `quote_position_canonical_unresolved_total{broker_id, reason}` (`no_country` / `no_symbol` / `no_exchange`).
- [x] `seed_instruments_from_positions` helper iterates `positions ⋈ broker_accounts`, calls `InstrumentResolver.from_legacy()`; bumps `quote_seed_skipped_total{reason}` for null returns.
- [x] `POST /api/admin/instruments` (Pydantic v2 + `require_admin_jwt`) — canonical_id regex, asset_class enum, currency, ≥1 alias.
- [x] Lifespan wires the seed AFTER ConfigService start, BEFORE BrokerRegistry build; best-effort with try/except.
- [x] Tests: integration seed (3-row 1-pass-2-skip), API 201/400/401, unit upsert-canonical column write (skip on no DB).
- [ ] Backfill of existing prod `positions.symbol`/`primary_exchange` — operator action via re-discovery round (BASE-tag pattern, Phase 5b.1).

## Phase 7c — Alpaca adapter (data + read-only + crypto/options-ready)  *(complete — v0.7.3 · 2026-05-05)*

`sidecar_alpaca/` (own gRPC sidecar, same proto) — in-cluster Docker on `td-net`, port 9091 live + 9092 paper, no mTLS to sidecar (peer trust = docker bridge). API-key auth via app_secrets with forward-compat `<account_label>` schema (MED-2). Free-tier Alpaca data: 30-symbol cap per WS endpoint; backend soft cap at 25 (CRIT-1).

- [x] A1 — `BrokerId` Literal `+ "alpaca"` (`0924a08`).
- [x] A2 — `app_config.broker_gateway_dial` config table + `resolve_dial` helper (`1b688da`).
- [x] B1 — `sidecar_alpaca/` skeleton: Dockerfile, pyproject (alpaca-py), main.py, config.py, auth.py, metrics.py, handlers.py UNIMPLEMENTED stubs (`819fd03`).
- [x] C1 — AlpacaClient + normalize.py + read-RPC handler wiring (`ece6ff9`).
- [x] C2 — Per-mode Configure routing + `alpaca_mode_mismatch_total{label}` HIGH-5 cross-mode probe (`69ef187`).
- [x] C3 — `account_id` boundary-strip regression test — chokepoint already covers HIGH-2 via existing `_ACCOUNT_BOUNDARY_STRIP_FIELDS` (`52827aa`).
- [x] D1 — IEX equity streamer with supervisor + per-task isolation (HIGH-1) + Subscribe-vs-Resync (CRIT-2) + sidecar-side hard cap 30 (CRIT-1 layer 2) (`39881a2`).
- [x] E1 — Crypto v1beta3 streamer sibling + canonical↔pair mapping (`25ae9e9`).
- [x] F1 — Backend `SubscriptionRegistry` per-source soft cap 25 + widened `quote_subscription_cap_rejected_total` labels {cap_kind,source,asset_class} (CRIT-1 layer 1) (`d5d94f1`).
- [x] F2 — Subscribe-rejection drift detection — streamer emits drift sentinel via `QuoteMessage.raw_payload`; backend's `SidecarStream.decrement_for_source` recovers from ghost subs (HIGH-6) (`258ecbd`).
- [x] G1 — `app/services/config_defaults.py` + per-key router fallback merge (HIGH-3) (`ef515e1`).
- [x] G2 — Frontend BrokerId Literal + ACCOUNTS / BROKERS fixture extension + AccountPicker test count update (`bb49543`).
- [x] G3 — SourceRouter 4-case integration test (MED-1) (`88e999f`).
- [x] H1 + H2 — `docker-compose.prod.yml` services + `deploy/runbook-alpaca-setup.md` (`c58eb3d`).
- [x] H3 — full lint + mypy --strict clean across `backend/app/` + `sidecar_alpaca/`; 14 new tests (7 backend + 7 sidecar).
- [x] H4 — close-out: CHANGELOG `[0.7.3]` + CLAUDE.md phase-shipped pointer + memory `phase7c_alpaca_topology.md` + tag `v0.7.3`.

Spec: [`docs/superpowers/specs/2026-05-05-phase7c-alpaca-adapter-design.md`](docs/superpowers/specs/2026-05-05-phase7c-alpaca-adapter-design.md). Architect-reviewed (2 CRIT + 6 HIGH + 7 MED + 4 LOW; CRIT+HIGH+MED applied inline). Plan: [`docs/superpowers/plans/2026-05-05-phase7c-alpaca-adapter-plan.md`](docs/superpowers/plans/2026-05-05-phase7c-alpaca-adapter-plan.md).

## v0.7.4 hotfix release — 2026-05-05

Post-deploy bring-up fixes after v0.7.3. 7 commits, no new features.
See `CHANGELOG.md [0.7.4]` for details. Headline:
schwab + alpaca in-cluster sidecars now reachable; schwab OAuth
re-authorize button works end-to-end (URL must match `schwabdev`
shape: only `client_id` + `redirect_uri`, no state, no `response_type`).

## Phase 8 — Schwab trade + order-type expansion + Futu Modify/Bracket + Alpaca trade

Phase 8 split into 8a (Schwab single-leg + capability foundation),
8b (order-type expansion + Futu modify/bracket), 8c (Alpaca trade).

### Phase 8a — Capability foundation + Schwab single-leg trade write-path  *(rc1 tagged 2026-05-06; v0.8.0 gated on E3 + A5 + F)*

- [x] **A1** Proto: OrderType+TimeInForce extended; `parent_broker_order_id` on ModifyOrderResponse (HIGH-3)
- [x] **A2** Pydantic Literals match proto (`app/brokers/base.py`)
- [x] **A3** Alembic 0011: `order_types`+`time_in_force`+`broker_order_capability` (200-row seed; ibkr=16/futu=4/schwab=0/alpaca=0)
- [x] **A4** ORM models for capability tables
- [ ] **A5** Flip schwab column from 0 supported to 50 supported  *(deferred — gated on E3 PASS)*
- [x] **B1** OrderCapabilityService (60s LRU + Redis pubsub bust + 5 metrics)
- [x] **B2** GET /api/brokers/{id}/capabilities
- [x] **B3** POST /api/admin/order-capabilities (PUT-semantics + CSRF + code-set guard)
- [x] **B4** Capability gate in orders_service (CRIT-3 sequence)
- [x] **B5** OrderEventConsumer dedup (CRIT-2 backend half)
- [x] **C1** SchwabClient REST wrappers (place/cancel/replace/get_orders_since/get_order + ensure_fresh_token)
- [x] **C2** Order normalizers (11 statuses + replaced kind + inferred fill) — also fixes A1 prefix-strip cascade
- [x] **C3** PlaceOrder live (SIM + replay cache + REST + error map)
- [x] **C4** CancelOrder + ModifyOrder live (parent_order_id link)
- [x] **C5** OrderEvent stream + SearchContracts (5m cache)
- [x] **D1** Redis-backed OrderStateCache (CRIT-2 sidecar half)
- [x] **D2** Adaptive OrderPoller (2s/30s, 429 backoff, hash rotation)
- [x] **D3** SimRegistry (synthetic place/cancel/modify events)
- [x] **D4** PollerSupervisor + facades  *(Configure-time wiring + SIDECAR_REDIS_URL deferred to deploy ticket)*
- [x] **E1** FakeBrokerServicer extended for schwab + alpaca + new ModifyOrder shape
- [ ] **E2** Full E2E place/cancel/modify chain tests  *(deferred — needs Schwab fake-server conftest fixture; gate behavior unit-tested at B4)*
- [ ] **E3** C0 empirical hard gate (script ready; HUMAN-INVOKED with paper creds during market hours)
- [ ] **E4** Nightly + weekly real-Schwab CI workflows + real_broker test scaffolds
- [ ] **F1-F4** Frontend `useBrokerCapabilities` hook + TradeTicketModal + Storybook + OpenAPI lock  *(blocked on A5+E3)*
- [ ] **G1-G2** Metrics + alerts.yml extensions for capability + poller + place/cancel/modify
- [ ] **G3** Phase 8a runbook (operator playbook)
- [ ] **G4** Close-out v0.8.0 (CHANGELOG + tag) once A5+E3+F all green

### Phase 8b — Order-type expansion + Futu Modify/Bracket  *(brainstorm pending)*

STOP_LIMIT, TRAIL/TRAIL_LIMIT, IOC/FOK/GTD, OCO non-bracket, MOC/MOO/LOC/LOO across IBKR + Futu + Schwab. Futu Modify + Bracket (deferred from Phase 6).

### Phase 8c — Alpaca trade  *(brainstorm pending)*

Alpaca `PlaceOrder` (US equity + crypto). Two-layer 30-symbol cap from Phase 7c carries forward.

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
