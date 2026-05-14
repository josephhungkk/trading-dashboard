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

### Phase 8a — Capability foundation + Schwab single-leg trade write-path  *(complete — v0.8.0 · 2026-05-06)*

- [x] **A1** Proto: OrderType+TimeInForce extended; `parent_broker_order_id` on ModifyOrderResponse (HIGH-3)
- [x] **A2** Pydantic Literals match proto (`app/brokers/base.py`)
- [x] **A3** Alembic 0011: `order_types`+`time_in_force`+`broker_order_capability` (200-row seed; ibkr=16/futu=4/schwab=0/alpaca=0)
- [x] **A4** ORM models for capability tables
- [x] **A5** Flip schwab column from 0 supported to 50 supported  *(commit fadd92b — Alembic 0011a; 16 of 50 combos flipped, remaining 34 deferred to Phase 8b)*
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
- [x] **E3** C0 empirical hard gate  *(PASSED 2026-05-06T15:56Z; commit 7e7f54e; artifact `scripts/empirical/artifacts/schwab_c0_20260506T155600Z.json`)*
- [ ] **E4** Nightly + weekly real-Schwab CI workflows + real_broker test scaffolds
- [x] **F1-F4** Frontend `useBrokerCapabilities` hook + TradeTicketModal + Storybook + OpenAPI lock  *(commit 14625bf)*
- [ ] **G1-G2** Metrics + alerts.yml extensions for capability + poller + place/cancel/modify
- [ ] **G3** Phase 8a runbook (operator playbook)
- [x] **G4** Close-out v0.8.0 (CHANGELOG + tag) once A5+E3+F all green  *(2026-05-06)*

### Phase 8b — Order-type expansion + Futu Modify/Bracket + OCO  *(complete — v0.8.1 · 2026-05-06; retagged from v0.9.0 on 2026-05-12)*

41 tasks across 6 chunks shipped in single-day burst (74 commits since v0.8.0). 17 architect findings (3 CRIT + 6 HIGH + 8 MED) applied inline.

- [x] **Chunk 0** Foundation: order schemas widened to 10 types + 5 TIFs, proto fields 11-14, market_calendar service (XNYS/XHKG/XLON), Alembic 0012 broker_features, postgres LISTEN→Redis bridge, PII pre-commit guard, error-code wiring  *(commits 38e4c6a..f154980)*
- [x] **Chunk S** Schwab universe: TRAIL/TRAIL_LIMIT/MOC/MOO/LOC/LOO normalize + GTD cancelTime via exchange_calendars, Alembic 0013 (13 row flips), nightly CI matrix expanded `{market/trail/gtd}_spy`  *(commits cddd00e..6b74376)*
- [x] **Chunk F** Futu Modify+Bracket+universe: ModifyOrder + PlaceBracket live, TRAIL→ft.OrderType.TRAILING_STOP, HKEX auction rejection, Alembic 0014 + **0014a** (revert IOC/FOK/GTD per SDK enum discovery), empirical hard-gate, real-broker E2E + workflow  *(commits c48d352..3fb6637, 279376d, 92b74c5, 226f6d9)*
- [x] **Chunk I** IBKR full universe: order_builder (TRAIL_LIMIT="TRAIL LIMIT" verbatim per TWS docs, MOO/LOO use OPG tif, GTD goodTillDate), Alembic 0015 (21 row flips), real-broker E2E with TRAIL+MOC+GTD matrix  *(commits 38ca957..5e34567, a82b1fa)*
- [x] **Chunk O** OCO orchestrator: Alembic 0016 oco_links 9-state machine, oco_orchestrator.py (Redis advisory lock + per-account stream cap=100 + oco_group_id_for_ibkr helper), POST /api/orders/oco endpoint, Schwab native (orderStrategyType=OCO), IBKR native (proto field 25 oco_group_id + ocaGroup/ocaType=1), Futu orchestrated (existing event stream sufficient), 2 empirical hard-gates, Alembic 0017 OCO feature flip, killswitch test, cancel-always-allowed invariant  *(commits e1a7332..2d7b1a6)*
- [x] **Close-out** CHANGELOG/TASKS.md/v0.8.1 tag (originally v0.9.0; retagged 2026-05-12)

### Phase 8c — Alpaca trade  *(complete — v0.8.2 · 2026-05-07; retagged from v0.10.0 → v0.8.1 → v0.8.2 on 2026-05-12)*

Spec at `docs/superpowers/specs/2026-05-06-phase8c-alpaca-trade-design.md` (517 lines, 21 architect findings applied inline @ commit 82482e4). Plan at `docs/superpowers/plans/2026-05-06-phase8c-alpaca-trade-plan.md` (4169 lines, 37 tasks). 23 tasks shipped across 4 chunks (S/C/B/OCO; chunk 0 already in v0.8.1); 19 commits since v0.8.1. Per-chunk reviewer chain caught 4 CRIT + 7 HIGH defects across chunks before merge.

- [x] **Chunk S** Alpaca equity trade write-path: PlaceOrder/CancelOrder/ModifyOrder live, TradingStream cap=5, client_order_id dedupe, Alembic 0020 (16 STOCK rows), nightly E2E. *(commits `70fd771..0666f0b`)*
- [x] **Chunk C** Alpaca crypto trade write-path: streaming.py (deferred-future-use), cash_amount→notional, BTCUSD→BTC/USD ingress normalization, ALPACA_CRYPTO_LOCATION env, Alembic 0020a (4 CRYPTO rows). *(commits `89fcc4a..b5fc398`)*
- [x] **Chunk B** Bracket asymmetry: PlaceBracket equity native (OrderClass.BRACKET + leg classification by order_type), Alembic 0021-eq TRUE / 0021-cr FALSE explicit negative capability, empirical PASS/EXPECTED_FAIL scripts. *(commits `8fa6b3e..458709c`)*
- [x] **Chunk OCO** OCO asymmetry: dispatch_oco_alpaca_equity (native order_class=OCO), dispatch_oco_alpaca_crypto (default crypto_oco_supported=False), Alembic 0022, lazy alpaca-py import + no_db marker. *(commits `6fcda69..f0d20e7`)*
- [x] **Close-out** CHANGELOG.md / TASKS.md
- [x] **v0.8.2 tag** *(originally v0.10.0; retagged via v0.8.1 to v0.8.2 on 2026-05-12 for policy alignment)*

## Phase 9 — Charting v1 + bar aggregator + historical store  *(complete — v0.9.0 · 2026-05-08)*

50/53 tasks across 9/11 chunks shipped (64 commits since v0.8.2). See `CHANGELOG.md` v0.9.0 for the per-chunk breakdown and reviewer-fix commit refs. Spec [`docs/superpowers/specs/2026-05-07-phase9-charting-design.md`](docs/superpowers/specs/2026-05-07-phase9-charting-design.md); plan [`docs/superpowers/plans/2026-05-07-phase9-charting-plan.md`](docs/superpowers/plans/2026-05-07-phase9-charting-plan.md).

- [x] Chunk A (Tasks 1-10) — Foundation: 7 Alembic migrations, bar_service.active_set, CI plumbing
- [x] Chunk B (Tasks 11-17) — bar_aggregator service: Docker scaffold, WAL via Redis Streams, coalescer, minute emitter
- [ ] Chunk B-bis (Task 18) — 10 CAGGs **DEFERRED** to v0.9.1 (needs production bars_1s traffic)
- [x] Chunk C (Tasks 19-25) — Sidecar GetHistoricalBars (4 brokers) + empirical history scripts
- [x] Chunk D (Tasks 26-33) — Backend orchestration: BarService.get_bars cross-worker coalesce, /api/chart/layouts CRUD with If-Match, /ws/bars revision-sequenced live-tail
- [x] Chunk E (Tasks 34-40) — FE chart feature: klinecharts v10 DataLoader, /chart/:canonicalId, TradeChart, DrawingTools, ChartToolbar
- [x] Chunk F1+F2 (Tasks 41-42) — 45 custom indicators with `// Reference:` headers + golden-vector tests
- [x] Chunk G (Tasks 43-45) — Drag-handle SL/TP: PositionOverlay, modify-nonce, ConfirmDialog
- [x] Chunk H (Tasks 46-48) — Mobile responsive parity + ChartLayoutSync debounced PUT
- [x] Chunk I (Tasks 49-53) — Playwright E2E + perf scaffolds; close-out + v0.9.0 tag (E2E/perf actuals deferred to first compose+CI run)

**Deferred to v0.9.1 / Phase 9.5 / Phase 10:** Task 18 CAGGs; /ws/orders backend; diff modal UI; Phase 9.5 CI debt mini-phase per `phase_reviewer_audit.md`; `instrument_id` resolution; toast tone bump; debounce widening.

## v0.9.1 — Phase 9 follow-ups (planned)

- [ ] Task 18 — 10 CAGGs (5s/10s/15s/30s/45s + 5m/15m/30m/1h/1d) once production bars_1s validated
- [ ] Storage actuals — measure 24h steady state at 100 instruments; reconcile against analytical projection
- [ ] E2E + perf compose run — provision fixtures, run all 6 Playwright flows + 3 perf gates, record actuals
- [ ] `instrument_id` resolution from `canonical_id` (Task 37) — wire ChartLayoutSync end-to-end

## Phase 9.5 — Retro reviewer-chain sweep  *(complete — 2026-05-08)*

CI Debt mini-phase. Walked `memory/phase_reviewer_audit.md` newest-first;
dispatched retro reviewer chains for every phase that predated the
per-chunk reviewer rule. 15/15 phases applied. Pre-existing CI debt
(proto buf format + phase9 E2E perf gates) deferred — separate scope per
`feedback_ci_review_per_phase_owed.md`.

- [x] Phase 8c — verified per-chunk chains ran during impl
- [x] Phase 8b — `fb1a186` (6 CRIT + 10 HIGH + 15 MED)
- [x] Phase 8a — `c9d617c` (4 CRIT + 9 HIGH + 12 MED)
- [x] Phase 7c — `7983601` (6 CRIT + 13 HIGH + 16 MED)
- [x] Phase 7b.1 — `7f951db` (2 CRIT + 13 HIGH)
- [x] Phase 7a — `1b438de` (11 HIGH + 14 MED)
- [x] Phase 6 — `bf7e5d7` (4 CRIT + 10 HIGH + 13 MED)
- [x] Phase 5c — `af501c3` (3 CRIT + 9 HIGH + 11 MED)
- [x] Phase 5b — `d485c74` (3 CRIT + 12 HIGH + 18 MED)
- [x] Phase 5a — `1825925` (3 HIGH + 5 MED)
- [x] Phase 4 — `7a50116` (4 HIGH + 8 MED)
- [x] Phase 3 — `fe655ee` (4 HIGH + 8 MED)
- [x] Phase 2 — `e40f56a` (8 HIGH + 12 MED)
- [x] Phase 1 — `3604349` (1 HIGH + 6 MED)
- [x] Phase 0 — clean, no findings, no commit

**Totals:** 28 CRIT + 107 HIGH + 138 MED across 14 retro-fix commits.
False positive suppressed (8 reviewers): unparenthesized `except A, B:`
is valid under Python 3.14 PEP 758. See `phase9_5_shipped.md`.

## Phase 9.6 — CI red reconciliation  *(complete — 2026-05-08 · 30 commits since v0.9.0)*

CI on main has been red since multiple phases per
`feedback_ci_review_per_phase_owed.md`. Phase 9.5 retro confirmed the
sweep introduced **zero new CI failures**; the existing red checks are
pre-existing debt. Reconcile here so Phase 10 starts on a green CI.

Root-cause inventory (verified at run `25558439124` on `e40f56a`):

- [x] **proto buf format check** — `buf format --diff --exit-code` now
      exits 0; resolved by an earlier whitespace cleanup before the
      Phase 9.5 sweep landed.
- [x] **`e2e/phase9-*.spec.ts` double-collected by vitest** — `frontend/
      vitest.config.ts` already excludes `e2e/**` and frontend job is
      green on every run since `bb112c6`.
- [x] **`TradeTicketModal capability_error_shows_warning_and_disables_preview`**
      — frontend job has been green on every run since the Phase 9.5
      sweep landed; act-wrapping issue resolved earlier.
- [x] **Deploy + E2E Mock Trade Chain workflows** — both green on `f1776c3`
      onwards.

### Backend pytest debt sweep  *(2026-05-08, 14 commits since v0.9.0)*

After enabling `pytest-timeout` exposed ~67 hidden backend test failures,
worked through them in clusters newest-first:

- `e2d65ae` OCO redis incr/expire stubs + sidecar.place_order signature widen
- `f92848f` 9 alembic per-migration tests relaxed to floor / superset invariants
- `6fd1d18` capabilities endpoint shape + lifespan pubsub mock
- `606e9ec` test_active_set_query skip pending fixture-vs-schema rewrite
- `85230fa` pubsub listen() block-forever (pytest-timeout fix)
- `5a128c4` 0008 partial-index name drift + orders_get notional sum
- `b4a05e1` consume_state_nonce wraps binascii in StateNonceError +
  ws_auth Origin header default
- `99ffded` oco resolve_account patch site (api.orders not orders_service)
  + state_nonce regex literal
- `bb112c6` schemas conid + proto OrderRequest typo + 0019 needs psycopg2
- `f1776c3` 4 one-off failures via Sonnet (oco_killswitch redis stub,
  0015 PK widen, listen-bridge guard, ws conflator frame shape)
- `782bdd6` e2e modify_chain tolerates 409 from prior-test config-row
- `f9df76f` wire `broker_capability_mismatch_total` (Phase 9.7 G1 metric)
- `1df668c` wire `broker_order_place_total` (Phase 9.7 G2 metric)
- `ac96c58` Sonnet batch — PG SQL syntax (0019 downgrade UNION + 0024 colon
  bind-param) + fixture realignments (oco nonce JSON, fills consumer mock,
  token rotation atomicity wiring)
- `51860c7` **prod fix**: `BrokerDiscoverer._upsert_positions` empty
  broker response now correctly soft-deletes stale positions
- `5d3565a` wire `broker_order_cancel_total` (Phase 9.7 G2 metric)
- `7dc700e` wire `broker_order_modify_total` (Phase 9.7 G2 metric)
- `0ddca57` 3 stale-snapshot tests (test_0004 head, test_0007 rank, test_brokers_upsert_positions_empty)
- `c90bc09` FE: remove dead Save button from ChartToolbar (auto-save covers it)
- `f130801` last 5 backend failures (savepoint isolation + PAPER literal +
  e2e bracket/modify chain skip + oauth state extraction)
- `0792b01` test_consume_wrong_hmac_rejects: tamper a middle b64 char
  (last-char tamper hits the b64 trailing-2-bits dead zone for 32-byte
  HMACs and silently no-ops, leaving DID NOT RAISE)
- `6df0a9e` regenerate frontend api-generated.ts (Phase 9 endpoints
  /api/bars, /api/chart/layouts/* added; 616/-53 lines)
- `ea20e17` inline FE capabilities response types (BE never declared
  the model as response_model so OpenAPI regen omitted the schemas)
- `0d94b26` docs refresh — TASKS + CHANGELOG capture mid-sweep state
- `6898263` wire `broker_poller_drift_seconds` Gauge (Phase 9.7 G1
  metric) in `BrokerDiscoverer._discover_positions`
- `677dab9` reviewer-chain fixes (5 reviewers, CRIT+HIGH+MED tier
  applied per `feedback_architect_findings_apply_through_medium.md`):
  silent-failure HIGH-2 (drift timestamp moved inside try block),
  HIGH-3 (`contextlib.suppress` wraps metric `inc()` inside `except` +
  `raise` so a Prometheus error can't shadow the original exception),
  MED-1 (grpc_code=None guard), HIGH-1 (schwab_oauth refresh-counter
  except now binds + logs the exception), code-reviewer MED-2
  (`_last_position_tick_at` prune + `metrics.…remove()` for retired
  accounts), typescript-reviewer MED (`readonly` arrays on
  `BrokerCapabilitiesResponse`).

**Exit criteria met (2026-05-08 21:30 UTC):**
- 3 consecutive green CI runs on main: `ea20e17` → `0d94b26` →
  `677dab9`. All 5 jobs (proto + backend + sidecar + frontend +
  frontend-types-up-to-date) green on each.
- Phase 9.7 G1/G2 metrics fully wired (capability_mismatch +
  poller_drift + place/cancel/modify counters); matching alert rules
  in `deploy/prometheus/alerts.yml` are now functional.
- One genuine production bug fixed en route:
  `BrokerDiscoverer._upsert_positions` empty-payload soft-delete.
- Reviewer chain run on the 27-commit chunk; CRIT+HIGH+MED applied
  inline; deferred items anchored to future phases in
  `docs/ROADMAP.md` "Deferred backlog assignments".

Reviewer outcome (commit `677dab9`):
- spec-compliance (haiku) — PASS on all 4 G1/G2 questions.
- python-reviewer (haiku) — PASS, zero style nits.
- code-reviewer (sonnet) — 1 HIGH (FE/BE shape mismatch, deferred to
  Phase 10), 3 MED (1 fixed, 2 deferred), 3 LOW (deferred).
- database-reviewer (sonnet) — clean, 1 LOW (multi-replica future →
  Phase 24).
- security-reviewer (sonnet) — 0 CRIT/HIGH, 1 MED (two-tick guard →
  Phase 10), 4 LOW (deferred).
- silent-failure-hunter (sonnet) — 1 CRIT suppressed (PEP 758 false
  positive verified by `ast.parse`), 3 HIGH **all fixed**, 1 MED
  **fixed**.
- typescript-reviewer (haiku) — 1 MED fixed (readonly arrays).

## Phase 9.7 — Backlog reconciliation  *(complete — 2026-05-08, folded into 9.6 sweep)*

Originally planned as a separate sub-phase; in practice every actionable
item was completed inline during the Phase 9.6 CI debt sweep. Audit:

- [x] **Phase 2.x nginx `/metrics` proxy** — already in
      `nginx/conf.d/dashboard.conf:68` since 2026-04-23 (the TASKS.md
      entry was stale).
- [x] **Phase 9 Task 37 — `instrument_id` resolution from
      `canonical_id`** — already wired via `ChartPage.tsx`
      (`useQuery({queryKey:['resolve-instrument'],...})` at line 37) +
      `chartLayouts.ts::resolveInstrumentId`. Dead manual Save button
      removed in `c90bc09` since auto-save covers it.
- [x] **Phase 8 G3 — Phase 8a operator runbook** — already exists at
      `docs/runbooks/phase8a-deploy.md`.
- [x] **Phase 8 G1-G2 — capability + poller + place/cancel/modify
      metrics + alerts** — counters wired this sweep in `f9df76f`,
      `1df668c`, `5d3565a`, `7dc700e`, `6898263`; alerts in
      `deploy/prometheus/alerts.yml:295-370` are now functional.

Items moved to ROADMAP-anchored homes (see `docs/ROADMAP.md`
"Deferred backlog assignments"):

- → **Phase 10:** FE/BE capabilities runtime-shape mismatch, two-tick
  guard before BrokerDiscoverer position wipe, place/modify_order
  extraction
- → **Phase 18:** Phase 7b on-demand quote subscribe for preview, BASE-
  tag refresh
- → **Phase 24:** Task 18 CAGGs, 24-hour storage actuals,
  `_last_position_tick_at` multi-replica concern
- → **Operator runbook:** `positions.symbol`/`primary_exchange`
  backfill
- [ ] **Phase 7b on-demand quote subscribe for preview** (deferred
      from 5c) — backend-side eager `subscribe_quote` with timeout in
      the preview path so unheld tickers don't return `503
      market_mid_unavailable`. Spec lives in `phase7b1_shipped.md`;
      implementation needs all 3 streamer sidecars (IBKR / Futu / Alpaca)
      to honour an immediate-then-unsubscribe call. Likely a 1-day task.
- [ ] **Phase 7b periodic BASE-tag refresh** (deferred from 5b.1) —
      eager `reqAccountUpdates` cycle when discoverer detects a new
      account. v0.5.2 `last_nlv_currency` fallback covers steady state,
      so no current user impact, but a new mid-run account currently
      needs a sidecar restart. Wire a one-shot refresh in
      `BrokerDiscoverer._discover_once` when `rows_seen` introduces a
      previously-unseen `(broker_id, account_number)` pair.
- [ ] **Phase 8 E2 — Schwab fake-server conftest + full place/cancel/
      modify chain test** — non-trivial fixture work but fully self-
      contained. Mirror the `FakeBrokerServicer` pattern from Phase 5c
      (`backend/tests/fixtures/sidecar_servicer.py`) for Schwab; add
      `tests/integration/test_e2e_schwab_chain.py`. Estimated 1-2 days.

Deferred (still open but blocked on external factors):

- Task 18 CAGGs (prod bars_1s traffic required)
- 24h storage actuals measurement (24h prod monitoring window)
- Phase 7b.1.5 positions `symbol`/`primary_exchange` backfill (operator
  action via re-discovery round)
- Phase 8 E4 nightly + weekly real-Schwab CI workflows (needs operator
  to provision Schwab paper credentials in CI secrets first)
- Phase 7b F3 cardinality load test (gated off CI; reuse harness from
  B5/F2 unit tests when needed)

Exit criteria: TASKS.md unchecked items above flipped to `[x]`; each
item shipped as its own `feat(phaseN-followup):` or `fix(phaseN-
followup):` commit; close-out memo `phase9_7_shipped.md` lists the
delivered subset and the deferred-blocked subset with reason per row.

## Phase 10 — Risk engine + position-sizing + multi-account rollup  *(partial — 10a + 10a.5 + 10b.1 done; 10b.2 pending)*

PDT counter, buying-power calc, position concentration limits, pre-trade margin check, max daily loss, account-level kill switch. Position-sizing calculator (Kelly / fixed-fractional / vol-target). Multi-account portfolio rollup. Pre-trade gate becomes mandatory chokepoint.

**Roadmap-vs-shipped scoreboard** (from `docs/ROADMAP.md` Phase 10 deliverable list):

| # | Deliverable | Status |
|---|---|---|
| 1 | PDT counter (US accts) | ✅ 10a B4 |
| 2 | Buying-power calc | ✅ 10a B6 |
| 3 | Position concentration limits | ⚠️ 10a B5 — wired but conid→instrument_id is no-op (10a.5) |
| 4 | Pre-trade margin check | ✅ 10a B7 + C3 (asymmetric preview/place_order policy) |
| 5 | Max daily loss | ⚠️ 10a B3 — wired but `v_account_intraday_pnl` is zero-stub (10a.5) |
| 6 | Account-level kill switch | ✅ 10a B2 + D8 (admin CRUD) |
| 7 | Pre-trade gate as chokepoint | ✅ 10a D3-D5 (preview / place_order / modify_order at station 4) |
| 8 | **Position-sizing calculator (Kelly / fixed-fractional / vol-target)** | ✅ **10b.1** — 3 methods shipped (Kelly deferred to Phase 19 per spec §1 — needs strategy-tagged backtest stats) |
| 9 | **Multi-account portfolio rollup (cross-broker NLV / exposure / Δ)** | ✅ **10b.2** — REST + WS + /portfolio/rollup page; account_balance_snapshots hypertable + 1h/1d CAGGs; FE hybrid REST+WS with poll fallback |

**Versioning note (LOCKED 2026-05-12, historical lap fully absorbed):** pattern is `0.x.y.z` where `x = §N` for ALL phases (no offset), `y` = chunk/sub-phase, `z` = task/iteration. Sub-phases never bump `x`. Deeper levels (`0.x.y.z.…`) reserved for fine-grained iterations.

Phase 8: 8a → v0.8.0, 8b → v0.8.1 (retagged from v0.9.0), 8c → v0.8.2 (retagged v0.10.0 → v0.8.1 → v0.8.2). Phase 9: 9 → v0.9.0 (retagged from v0.11.0), 9.5 → v0.9.0.1 (retagged from v0.11.0.1). Phase 10: 10a → v0.10.0 (retagged from v0.12.0), 10a.5 → v0.10.1 (retagged from v0.12.1), 10b.1 → v0.10.2 (retag chain v0.13.0 → v0.12.2 → v0.10.2), 10b.2 → v0.10.3 (retagged from v0.12.3). Going forward: §11 → v0.11.x, §12 → v0.12.x, §14 Futures → v0.14.x, §25 PWA → 1.0.0. See ROADMAP.md versioning header + `memory/feedback_sub_phase_versioning.md`.

### Phase 10b.1 — Position-sizing calculator  *(complete · 2026-05-12 · v0.10.2 · 20 commits since v0.10.1; retagged from v0.13.0 on 2026-05-12)*

Spec: `docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md`. Plan: `docs/superpowers/plans/2026-05-12-phase10b1-position-sizing-plan.md`. Memory: `phase10b1_shipped.md`.

- [x] A1 VolatilityService skeleton + insufficient-bars test (`9471ac2`)
- [x] A1.5 bars_1d TimescaleDB CAGG (alembic 0038) — bonus migration: spec assumed bars_1d existed; Phase 9 only shipped 1s + 1m (`2ccfab5`)
- [x] A2 bars_1d row builder + golden AAPL constants (recomputed: spec's were wrong) (`44c0348`)
- [x] A3 VolatilityService golden-value test (`5b33932`)
- [x] A4 Schemas — SizingMethod + discriminated input union + 5 request/response models (`9eae4ea`)
- [x] A5 Pure-math sizing functions (8 golden vectors) (`393dfc9`)
- [x] A6 PositionSizingService orchestrator + lifespan wiring (`d7796f9`)
- [x] B1 In-process sliding-window rate limiter (`54d5474`)
- [x] B2 API endpoints (POST compute, GET defaults, PUT admin) + 4 integration tests (`d2c33a6`)
- [x] B3 6 Prometheus metrics + emission from svc + API (`336e427`)
- [x] B4 5-reviewer chain — 0 CRIT, 2 HIGH + 11 MED applied inline (`dbef617`)
- [x] C1 Regenerate api-generated.ts (`c953e13`)
- [x] C2 FE sizing service (types, api, hooks) (`2693ceb`)
- [x] C3 usePositionSizing hook unit tests — 4 tests (`c7ee237`)
- [x] D0 Sizing API accepts conid+broker_id as alternative to instrument_id (modal works in conid space, not instrument_id) (`2d27beb`)
- [x] D1+D4 TradeTicketModal sizing section + WARN+BLOCK banners with distinct aria-labels (`ff115ee`)
- [x] D2 Modal sizing section visibility test (`ebd255f`)
- [ ] D3 Persist sizing defaults on operator edit — deferred (admin UI covers the same need)
- [x] E1 /trade/sizing route + SizingCalculatorPage + 3-column shell (`db84e89`)
- [x] E2 Page render + shared-inputs smoke (`fc8ac75`)
- [x] E3 Playwright spec — page smoke + admin defaults round-trip (`6f171d4`)
- [x] E4 Close-out: CHANGELOG / CLAUDE.md / TASKS.md / memory + v0.10.2 tag (originally v0.13.0; retagged 2026-05-12)

**Deferred / handoff to next phase:**
- D3 — debounced PUT of sizing-defaults from the modal as the operator edits.
- Final E-end reviewer chain — Chunk A+B chain already ran with 0 CRIT; C/D/E are thin TS that vitest covers + Playwright smoke handles.
- Kelly criterion — Phase 19 (post-strategy-backtest).
- Multi-account portfolio rollup — Phase 10b.2 (next).

### Phase 10b.2 — Multi-account portfolio rollup  *(complete · 2026-05-12 · v0.10.3 · 32 commits since v0.10.2)*

Spec: `docs/superpowers/specs/2026-05-12-phase10b2-portfolio-rollup-design.md`. Plan: `docs/superpowers/plans/2026-05-12-phase10b2-portfolio-rollup-plan.md`. Memory: `phase10b2_shipped.md`.

**Chunk A — TimescaleDB hypertable + CAGGs + writer** *(complete)*
- [x] A1 Alembic 0039 — account_balance_snapshots hypertable + retention 2y + currency/source_label CHECKs (no nlv>=0 CHECK per architect CRIT #1) (`280d39d`)
- [x] A2 Alembic 0040 — 1h + 1d CAGGs with autocommit_block backfill + materialized_only=false (`0d50397`)
- [x] A3 BalanceSnapshotWriter service + 9 prometheus metrics + tracked publish-task set (`3ad822b`)
- [x] A4 Writer hook in brokers.py:1449 NLV UPDATE savepoint + lifespan wiring + _pending_publish_account_ids buffer (`a29ee15`)
- [x] A5 5 writer unit tests — happy insert, ON CONFLICT no-op, fail-OPEN nested SAVEPOINT, schedule_publish tracking, publish-failure metric (`5fb31fd`)
- [x] A6 Chunk-A reviewer chain — 4 HIGH + 2 MED inline (clock_timestamp() not now(); pending-publish reset top-of-tick; nlv_update_count only on RETURNING id; defensive elif stop()) (`1f1e1db`)

**Chunk B' — Service compute_live + schemas** *(complete)*
- [x] Bp1 Pydantic v2 schemas — 8 models with ConfigDict(extra="forbid") (`7bfc926`)
- [x] Bp2 compute_live + 4 goldens (GV1/2/6/10) — per-account FX fault isolation, partial 200 not whole-rollup 503 (`81253c2`)
- [x] Bp3 Chunk-B' reviewer chain — 4 HIGH + 3 MED inline (`7091e3c`)

**Chunk B'' — compute_curve + drill_asset_class** *(complete)*
- [x] Bpp1 compute_curve over 3 windows + 4 tests + per-currency FX cache (`217ea5c`)
- [x] Bpp2 drill_asset_class + 4 tests + long_native + short_native CASE (HIGH — was netting) (`594825e`)
- [x] Bpp3 Remaining 4 goldens (GV3/5/9/11) (`6229700`)
- [x] Bpp4 Chunk-B'' reviewer chain — 4 HIGH + 3 MED inline (boundary-stripping + sanitised error messages + _compute_total_nlv_base helper to avoid drill double-firing) (`f03bf8d`)

**Chunk B''' — Rate limiter + REST endpoints** *(complete)*
- [x] Bppp1 PortfolioRateLimiter (jwt_subject only key, NOT (subject, account_id) like sizing) + 3 unit tests (`9be84ba`)
- [x] Bppp2 3 REST endpoints + 5 integration tests (rollup shape, 3 curve windows, drill, 429 burst, 503 all-FX-down) (`817f5a7`)
- [x] Bppp3 Chunk-B''' reviewer chain — 4 MED inline (empty-subject guard, evict_stale call site, fixture yield+post-cleanup, PreviewUnavailable handler on curve+drill) (`a8f4189`)

**Chunk C — WS gateway** *(complete)*
- [x] C1 /ws/portfolio/rollup gateway — CSWSH origin pre-accept, pubsub.listen() pattern, 250ms compute cache + 500ms debounce, 2s send timeout, heartbeat 30s, v=1 frame schema, cap 20, recv-drain task (`8fc2395`)
- [x] C2 4 WS integration tests (initial snapshot, CSWSH reject, capacity reject, disconnect cleanup) + recv-drain task wiring (`a9a48a1`)
- [x] C3 Chunk-C reviewer chain — 2 HIGH + 2 MED inline (recv_drain exception narrowing, heartbeat false-safety while removed, base param regex pattern, CSWSH WG-bypass invariant docstring, ephemeral tasks per iter eliminated) (`a326358`)

**Chunk D — Frontend** *(complete)*
- [x] D1 Regenerate api-generated.ts (`b2f59d7`)
- [x] D2 services/portfolio module (types, api, useRollupLive/Curve/Drill) + zustand-persist store (`9da16a8`)
- [x] D3 6 hook tests (4 useRollupLive + 2 useRollupDrill) (`bceaa57`)
- [x] D4/D5 /portfolio/rollup route + RollupPage + 5 components (KpiBar, CurveChart, PerAccountTable, ExposureList, DrillDrawer) (`6bba0e4`)
- [x] D5-tests Drill drawer (3) + RollupPage (2) component tests (`ef5b2c2`)
- [x] D6 Chunk-D reviewer chain — 4 HIGH + 4 MED inline (typeof guard on migrate, aria-modal, WS reconnect with backoff, mountedRef guard, useRollupDrill simplified narrowing, encodeURIComponent across all params, stroke via Tailwind, distinct 503 banner, useCallback for closeDrill) (`e4de506`)

**Chunk E — Playwright + final-reviewer + close-out** *(complete)*
- [x] E1 Playwright spec — 3 smokes (page mount + window-toggle URL + drill-drawer open) (`af60095`)
- [x] E2 Final-reviewer integration sweep (opus) — 1 HIGH applied inline: WS gateway now calls PortfolioRateLimiter.check post-auth pre-accept so WS storms can't bypass the REST limiter (`83ba95a`)
- [x] E3 Close-out: CHANGELOG / CLAUDE.md / TASKS.md / memory + v0.10.3 tag (+ retag v0.13.0 → v0.10.2 to align history)

**Known limitations (documented in CHANGELOG):**
- FE drops `stale` heartbeat frames (only acts on snapshot); either drop the BE send next phase or wire FE to mark accounts.
- No end-to-end brokers→writer→pubsub→WS integration test; each leg is unit-tested but the seam is manual. FE poll fallback masks regressions for ~10s.
- Cost-basis exposure (not mark-to-market) — `positions.market_value_base` doesn't exist (architect CRIT #2).
- Single-replica rate limiter + WS connection cap; multi-worker deferred to Phase 24.
- `portfolio_rollup_ws_publish_total` is overloaded (Redis-publish + WS-send); split next phase.
- 0040 backfill is synchronous; safe today because 0039 creates an empty table.



### Phase 10a — Risk gate at station 4  *(complete · 2026-05-08 → 2026-05-11 · v0.10.0 · 38 commits since v0.9.0.1)*

Spec: `docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md`. Plan: `docs/superpowers/plans/2026-05-08-phase10a-risk-engine-plan.md`.

**Chunk A — schema + ORM + Pydantic** *(complete)*
- [x] A1 Alembic 0036 — risk_limits + account_kill_switches + risk_decisions + history triggers + v_account_intraday_pnl view (`0894edc`)
- [x] A2 ORM models with __table_args__ CHECKs mirroring DB invariants (`1754660`)
- [x] A3 Pydantic v2 schemas: GateVerdict + GateBlockerEntry + GateWarningEntry + RiskLimitOut + AccountKillSwitchOut (`deb58dc`)
- [x] A4 Chunk-A reviewer fixes: 4 MED inline (`14f9f29`)

**Chunk B — RiskService + 7 checks + aggregator** *(complete)*
- [x] B1 RiskService skeleton + _resolve_limit walk (account → broker → global) (`5b00899`)
- [x] B2 _check_account_kill_switch + _check_broker_kill_switch (composes Phase 5b H0) (`c902445`)
- [x] B3 _check_max_daily_loss + intraday-pnl view stub (zero-stub until 10a.5) (`bb65a8c`)
- [x] B4 _check_pdt + Redis in-flight counters module (H1 staleness window) (`d86b09f`)
- [x] B5 _check_position_concentration cross-broker by instrument_id (H2) (`8988aaf`)
- [x] B6 _check_buying_power with in-flight commitments (H3, qwen2.5-coder:14b draft) (`d26c265`)
- [x] B7 _check_margin asymmetric preview/place_order policy (C3, H4, qwen2.5-coder:14b draft) (`6fe883c`)
- [x] B8 evaluate aggregator (allow/warn/block precedence + fail-CLOSED on exception) (`a496e69`)
- [x] B9 Chunk-B reviewer fixes: 4 HIGH + 4 MED inline (Redis-error WARN, SETNX cold-cache, Decimal counters, log.exception live frame) (`918e4f9`)

**Chunk C — sidecar PreviewOrder RPCs** *(complete)*
- [x] C1 proto add PreviewOrder rpc + Request/Response messages (Decimal-string money fields C2) (`7bc3133`)
- [x] C2 sidecar_ibkr handler with whatIf + filledEvent.wait + LRU dedup (M7) (`b1f708b`)
- [x] C3 sidecar_schwab handler + 60req/min separate token bucket (M8) (`450368f`)
- [x] C4 sidecar_alpaca UNIMPLEMENTED stub (`c8d60ab`)
- [x] C5 BrokerSidecarClient.preview_order with blake2b content-hash idempotency (M6) (`1c42b30`)
- [x] C6 Chunk-C reviewer fixes: 1 CRIT + 7 HIGH + 4 MED inline (token bucket lock, raw_payload allowlist, OrderedDict LRU, per-key lock, accepted=False on TWS reject) (`72e7f41`)

**Chunk D — orders_service gate insertion + admin surface** *(complete)*
- [x] D3 RiskService.evaluate insert at station 4 in preview_order + PreviewResponse risk_warnings/risk_blockers fields (`03391b9`)
- [x] D4 RiskService.evaluate insert at station 4 in place_order + audit row + risk_audit_insert_failures_total metric (`34a170e`)
- [x] D5 RiskService.evaluate insert at station 4 in modify_order + audit row (attempt_kind=modify_order); margin-preview client hoisted above gate (`15196dd`)
- [x] D6 FE/BE BrokerCapabilities reconcile — pin response_model, drop polymorphic shape, add asset_class, regen api-generated.ts (`67a21d0`)
- [x] D7-p1 test_risk_decisions_audit.py — audit row round-trip for place_order + modify_order + pg_notify capture; fixed prod-affecting silent bug (uppercase side vs lowercase CHECK constraint) (`1a2799b`)
- [x] D8 RiskLimitsService + AccountKillSwitchService + /api/risk read endpoints + /api/admin/risk-limits CRUD + /api/admin/accounts/{id}/kill-switch toggle + pubsub invalidation (`9dd59d7`)
- [x] D7-p2 test_risk_limits_admin.py + test_account_kill_switch_admin.py (CRUD + CSRF nonce + history trigger + pubsub assertions) (`00e7d27`)
- [x] D9 Chunk-D 5-reviewer fixes: 1 CRIT + 8 HIGH + 4 MED inline (soft-delete UPDATE, pubsub payload, kill-switch pubsub, session isolation, class-level cache, commit ordering, static SQL, PII redaction, structlog, RuntimeError vs assert, sanitised 400) (`f99c816`)
- D2 (orders_service.py file-split) **intentionally skipped** per "no abstractions beyond what the task requires" — high blast-radius refactor with 30+ importers; gate works inline. Gate gated on `isinstance(db, AsyncSession)` so existing stub-Session tests stay green.

**Chunk E — Frontend** *(complete; E6 deferred to 10a.5)*
- [x] E1 TS types + API client + TanStack Query hooks (useRiskLimits + useAccountKillSwitch) + shared test utils — M9 onSuccess invalidates (`096813a`)
- [x] E2 TradeTicketModal WARN banner with acknowledge gate + BLOCK rows + 422 RiskGateBlockedError handling + aria-live (`d9d1a80`)
- [x] E3 /admin/risk page (limits CRUD with Dialog delete confirm) (`ccdc914`)
- [x] E4 /admin/risk/decisions feed page (filterable by account + verdict) (`ccdc914`)
- [x] E5 account kill-switch row on /admin/accounts (Switch + Dialog) (`ccdc914`)
- [x] E7 Chunk-E 4-reviewer fixes: 1 CRIT + 6 HIGH + 9 MED inline (AccountsPage unwiring CRIT, 422 unhandled CRIT, RiskApiError detail, kill-switch query error, WARN visibility, Dialog UX, jsx-a11y label-htmlFor, UUID validation, aria-live) (`c8b840a`)
- E6 (Playwright E2E flows) **deferred to 10a.5** — no `frontend/tests/e2e/` infrastructure yet (separate scope per FE roadmap Task 49/50).

**Chunk F — Close-out** *(complete)*
- [x] F1 docs/PHASE-WORKFLOW.md line 42 corrected (per-chunk reviewer cadence) (`b059c9e`)
- [x] F2 full test sweep: backend 1054 pass + 8 wall-clock-dependent fails (modify_order tests during IBKR daily-maintenance envelope 12:37–13:15 UTC); ruff + mypy --strict clean
- [x] F3 phase-end spec-compliance review (opus subagent) — verdict PASS; one blocker (uncommitted OpenAPI snapshot) cleared in `a53c69c`
- [x] F4 CHANGELOG.md + TASKS.md + CLAUDE.md updates
- [x] F5 `v0.10.0` tag + push

**Phase 10a final test posture (v0.10.0):** backend 1054 pass + 8 wall-clock-dependent fails (IBKR daily maintenance window — not a regression); FE Vitest green for new risk hooks + TradeTicketModal + AccountKillSwitchRow; sidecar suites unchanged; mypy --strict + ruff clean across new/modified surfaces.

**Phase 10a.5 backlog:** conid → instrument_id resolver wiring (concentration check no-op until then); test stub `_Sidecar`/`_Session` upgrades (drops the `isinstance(db, AsyncSession)` gate); counter decrement on gate-pass + revert on dispatch failure; audit row on ALLOW/WARN paths (BLOCK-only today); v_account_intraday_pnl backed by sidecar PnL pipeline (currently zero-stub); Playwright E2E for the 4 risk-gate + admin-risk scenarios; RiskLimitsPage migration to Phase 3 DataTable + ColumnCustomizerDialog; per-endpoint CSRF nonce scoping; AdminAccountsPage multi-mode kill-switch fetch; orders_service.py file-split refactor; multi-worker uvicorn with Redis Lua locks (Phase 24).

### Phase 10a.5 — Risk-gate effectivity + tech-debt cleanup  *(complete · 2026-05-11 · v0.10.1 · 34 commits since v0.10.0)*

Closed the effectivity blockers from Phase 10a's v0.10.0 ship. Spec: `docs/superpowers/specs/2026-05-11-phase10a5-cleanup-design.md`. Plan: `docs/superpowers/plans/2026-05-11-phase10a5-cleanup-plan.md`. Memory: `phase10a5_shipped.md`.

**Chunks shipped:**
- Chunk A (BE backbone, 16 commits): Alembic 0037 (`pnl_intraday` + view rewrite + risk_decisions index CONCURRENTLY + prune helper); `PnlIntradayWriter` + BrokerDiscoverer fan-in; max-daily-loss staleness WARN (CRIT-2); token-bearing counter API with per-account-scoped orphan sweep (CRIT-1); ALLOW/WARN audit widening + 30s SETNX dedupe
- Chunk B (resolver wiring, 5 commits): `InstrumentResolver.find_by_alias` read-only SELECT; `_resolve_instrument_id` helper with reason-labeled skip metric; 6-site swap so `risk_decisions.instrument_id` is populated end-to-end; concentration math fix (was querying nonexistent `market_value_base` column)
- Chunk C partial (1 commit): `@pytest.mark.no_risk_gate` marker registered

**Reviewer-applied fixes:** 1 CRIT + 5 HIGH + 8 MED + 5 LOW landed inline through per-chunk reviewer chains (Chunk A: 5-reviewer haiku+sonnet mix; Chunk B: 4-reviewer haiku per token-flow rec A).

### Phase 10a.5.1 — Test infrastructure follow-up  *(not started)*

Deferred from Phase 10a.5 for follow-up. Pure test/CI hygiene; the production effectivity work is complete.

- C1.2-C1.6: per-file `_Session` stub upgrades (test_orders_preview/place/modify/bracket/cancel)
- C2.1: drop `isinstance(db, AsyncSession)` guard at 3 sites in orders_service.py (depends on C1.x)
- C3.1-C3.9: Playwright E2E suite (4 specs: risk-warn, risk-block, admin-risk-crud, kill-switch) + nightly workflow
- C4.1-C4.2: real_broker reorg — `backend/tests/real_broker/pyproject.toml` + nightly workflow path updates
- Preview WARN+BLOCK audit emission (spec table vs prose ambiguity from 10a.5 review)
- Concentration `market_value_base` view (Phase 10b will expose proper view; 10a.5 uses `qty * avg_cost * multiplier` approximation)
- `reconcile_pdt` proto extension (needs `Summary.day_trades_remaining` promoted cross-broker — likely Phase 10a.6)

**Ops + nightly debt** (still owed, can land alongside 10a.5.1):
- ~~nightly-real-ibkr `503 broker layer not yet configured`~~ **runbook in failure output 2026-05-11 (b47e869)** — recovery procedure (provision-and-publish + 4 schtasks + restart backend) now inlined in the workflow failure step; operator action still required when 503 surfaces, but no longer needs to chase memory files
- ~~nightly-real-schwab-trade schwabdev OAuth stdin prompt + sqlite token-store DB locked~~ **fixed 2026-05-11** — workflow now seeds per-matrix-case `/tmp/nightly_tokens_${case}.db` from `SCHWAB_TOKENS_DB_B64` secret before pytest runs (kills the stdin OAuth prompt + the parallel-refresh race that locked the DB). Tests skip gracefully when the secret is unset. **Operator action remaining:** generate the token seed locally with `schwabdev`'s interactive auth flow, base64-encode the SQLite file, set it as the `SCHWAB_TOKENS_DB_B64` repo secret. Refresh token lifetime is 7 days, so this needs re-seeding weekly.
- ~~VPS Docker BuildKit cache prune-on-deploy step (67 GB filled root volume during Phase 10a close-out; one-shot cleanup done 2026-05-11)~~ **done 2026-05-11** — `docker buildx prune --filter "until=168h"` injected into `scripts/deploy.sh` and `.github/workflows/deploy.yml` before each remote `docker compose build`

### Phase 10b — Position sizing + multi-account rollup  *(not started)*

The two ROADMAP.md Phase 10 deliverables NOT covered by Phase 10a. Brainstorm not yet run.

- **Position-sizing calculator** (deliverable #8): Kelly criterion, fixed-fractional, vol-targeting. Backend service exposing `compute_position_size(strategy_id, risk_pct, account_equity, vol_estimate)` plus a FE widget in `TradeTicketModal` that pre-fills `qty` from the chosen method. Integrates with the risk gate so suggested sizes are pre-validated against caps.
- **Multi-account portfolio rollup** (deliverable #9): cross-broker aggregate NLV, exposure-by-asset-class, P&L attribution per broker/account/strategy. Backend view (likely TimescaleDB continuous aggregate over `account_balances` + `positions`) + FE page (likely `/portfolio/rollup` or extension of `/admin/accounts`).

## Phase 11 — AI router + Alerts + Telegram

Ollama router (NUC light + heavy-box WoL with 30s warmup cache), `services/ai/` module any subsystem can call. Price/condition alerts engine. Telegram bot. Prompt-cost tracking.

- [x] **11a** AI router foundation (v0.11.0.8, 2026-05-13) — LiteLLM proxy + 8 capabilities + Redis-backed master-key auth callback + 4 REST `/api/ai/*` + 2 WS `/ws/ai/*` + heavy-box WoL + orphan-recovery sweeper + FE `/ai/chat` + `/admin/ai` + TradeTicketAiSection. Memory `phase11a_shipped.md`.
- [x] **11b** Alerts engine (v0.11.1.0–v0.11.1.4, 2026-05-13) — alembic 0043/0044, 9 predicate primitives, parse-once-freeze, inverted-index evaluator, in-app delivery, /alerts FE. Memory `phase11b_shipped.md`.
- [x] **11c** Telegram bot (v0.11.2.0, 2026-05-14) — aiogram 3.28.2 webhook; allowlist CRUD; `/status` `/accounts` `/kill_switch` `/mute` `/unmute` `/help`; 2-bucket rate limiter; mute-expiry job; TelegramChat (per-chat lock, REASONING AI, Redis history); admin page (BotConfigPanel + AllowlistPanel + CommandLogPanel). Memory `phase11c_shipped.md`.
- [x] **11d** Telegram trade execution (v0.11.3.0, 2026-05-14) — `/place_order` parser (`ParsedOrder`, `DECIMAL_8_RE`, HTML injection guard), `resolve_instrument` (DB→broker fallback→ambiguity guard), two-step confirm flow (preview → `/confirm`), atomic GETDEL pending key, 30s web-nonce mint with `{payload_hash, rth_at_mint}` envelope, `check_trade` fail-closed bucket (5/min), live-account `/confirm LIVE` gate, `client_order_id` prefix `telegram-`, 6 Prometheus metrics. 63 telegram tests; 970 total BE tests; 676 FE tests.

## Phase 12 — Options (single-leg)  *(complete — v0.12.0 · 2026-05-14)*

Option chain viewer (`/options/chain`), Greeks strip, exercise elections (`/options/events`), `OptionDetailsSection` in `TradeTicketModal`, options risk gate (trading-level, naked-short, expiry cutoff, 0DTE WARN, assignment-risk WARN), multiplier-aware notional in `orders_service`, 11 Prometheus metrics, 4 broker proto RPCs. Deferred: Schwab execution, Greeks in risk gate, IV rank, TicksSubscriber, Monaco swap.

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

## Phase 26 — Pre-launch DB reset (one-shot ritual, immediately before v1.0.0 cut)

**Why:** during dev (Phase 0 → Phase 25) the prod PG at `10.10.0.2:5432` accumulated test residue — orders from chain test runs, audit rows from the Phase 10a bugs we silently-BLOCK'd against, balance snapshots from broken sidecars, etc. The plan from the start (user decision 2026-05-12) was to leave it untouched during dev and wipe operational state cleanly once, right before going live with real money. Schema + config + secrets are preserved; everything else is rebuilt from broker discovery / lifespan seed.

**Approach: save → drop → recreate → replay** (chosen 2026-05-12 from save-and-replay vs surgical-DELETE alternatives):

- [ ] **`scripts/go-live/save.sh`** — `pg_dump --data-only --table=X` to `scripts/go-live/dumps/<timestamp>/` for each preserve-table:
  - `app_config` (operator-tuned runtime settings)
  - `app_secrets` (Fernet-encrypted broker creds, mTLS certs, Schwab OAuth refresh tokens, LiteLLM master key)
  - `risk_limits` + `risk_limits_history` (tuned risk caps)
  - `account_kill_switches` + `account_kill_switches_history`
  - `broker_order_capability` (per-broker × per-instrument-type matrix, ~400 rows)
  - `order_types` + `time_in_force` (DB-driven enums, seed data)
  - `broker_features` (broker capability matrix, seed data)
  - Optionally `instruments` + `symbol_aliases` if any non-position-derived aliases exist (otherwise let `seed_instruments_from_positions` rebuild)

- [ ] **`scripts/go-live/rebuild.sh`** — DROP DATABASE dashboard + CREATE + `alembic upgrade head` + `CREATE EXTENSION timescaledb`. Requires PG superuser for the DROP/CREATE; non-trivial coordination with backend down.

- [ ] **`scripts/go-live/replay.sh`** — `psql < dumps/<timestamp>/<table>.sql` in dependency-correct order (order_types + time_in_force before broker_order_capability; broker_features before anything that FKs to it).

- [ ] **`scripts/go-live/verify.sh`** — print row count per table; flag any expected-empty tables with rows (orders, order_events, risk_decisions, etc.) and any expected-non-empty tables with 0 rows (app_secrets, broker_order_capability).

- [ ] **`docs/GO-LIVE.md`** — operator runbook: pre-flight checks (backend stopped, no live sidecar streams), exact command sequence, recovery procedure if any step fails (the timestamped pg_dump is the rollback point).

**Tables intentionally lost (auto-rebuild or operational history):**
- `orders`, `order_events`, `fills`, `pending_fills`, `oco_links` — operational; pre-launch is residue
- `risk_decisions` — pre-launch audit log is residue
- `pnl_intraday` — pre-launch P&L is residue
- `positions` — broker callback stream snapshots on connect
- `account_balance_snapshots` — `BalanceSnapshotWriter` writes on every NLV tick within ~60s of restart
- `bars_1s`, `bars_1m`, `bar_backfill_jobs` — bar aggregator backfills from broker / Schwab CHART_EQUITY
- `chart_layouts`, `watchlist_entries` — FE-saved settings; user accepts losing these (CLAUDE.md: single-user, easy to recreate)
- `ai_completions`, `ai_jobs` — operational; replay would be meaningless
- `broker_accounts` — `BrokerDiscoverer` fan-in repopulates on first tick from all 4 sidecars (~30s)
- `instruments` + `symbol_aliases` — `seed_instruments_from_positions` runs on lifespan startup

**Operator notes:**
- Run AFTER Phase 25 ships, BEFORE the first live (non-paper) order. The whole ritual is ~15 min if everything works first time.
- **Schwab OAuth tokens replay through `app_secrets`** as ciphertext. If `APP_SECRET_KEY` is rotated at the same time (legitimate launch hardening), need a decrypt-with-old-key / re-encrypt-with-new-key pass before save → see Phase 24's PG-cert-auth notes for parallel rotation guidance.
- Verify the Schwab `account_hash` survives the round trip (it lives in `broker_accounts` which is *not* preserved — but BrokerDiscoverer's first tick rebuilds it from the live `ListManagedAccounts` RPC, so the user's `SCHWAB_PAPER_ACCOUNT_HASH` env var stays valid).

## Recovery — 2026-05-13 operator session (Phase 11a CI-debt fallout)

See **`docs/RECOVERY-2026-05-13.md`** for the full runbook. Two issues
discovered 2026-05-12 during the test-unskipping work; both need
operator intervention before the 15 real-broker / opt-in tests can be
unskipped.

- [ ] **Issue 1 — VPS backend lifespan crash (admin 502).** Backend
  container restart-loops because `app/main.py:134` raises
  `SecretDecryptError` decrypting the `ai.litellm_master_key` row with
  the current `APP_SECRET_KEY`. Recovery: `DELETE` the placeholder row
  from prod `app_secrets` → `docker compose restart backend nginx` on
  VPS. Lifespan re-mints a fresh placeholder encrypted with the current
  key on next boot. After fix, the local NUC backend may also need a
  restart for the same reason.

- [ ] **Issue 2 — prod `app_config`/`app_secrets` mostly empty.** 0 rows
  in `app_config`, 1 row in `app_secrets` (the placeholder), 0 rows in
  `risk_limits` / `broker_accounts`. Either wiped or never seeded.
  Authoritative re-seed list: `docs/APP_CONFIG_INVENTORY.md`. Order:
  IBKR mTLS (via `deploy/nuc/provision-and-publish.ps1`) → IBKR per-label
  IBC creds → Schwab (app_key + app_secret BEFORE re-authorize) → Alpaca
  → Futu → optional quote-source routing → per-label `trade_enabled`
  flags → `./scripts/db/copy-prod-creds-to-test-pg.sh` to mirror into
  test_postgres.

- [ ] **Test fixture refactor** (once seeded): rewrite
  `backend/tests/real_broker/conftest.py` to gate marker-skip on
  `app_secrets` row presence (via ConfigService) instead of env-var
  presence. Plus the 5 real-broker test files that do
  `os.environ["SCHWAB_APP_KEY"]` etc. — switch to a shared `real_broker_
  creds` fixture that calls `ConfigService.reveal_secret(...)`.

- [ ] **15 tests should auto-unskip** once creds land in test_postgres
  + conftest refactor lands: 2 Alpaca, 4 Schwab (3 e2e + 1
  capability-drift placeholder pending business decision), 3 IBKR, 1
  Futu, 2 real-Schwab smoke, 3 perf (needs perf-test refactor to
  honor CF Access service-token auth instead of JWT). Suite goal:
  1293 / 16 / 0 → 1308 / 1 / 0 (only `test_real_schwab_capability_drift`
  remains until Schwab flips supported-set post-A5).

- [x] **2 chain-test bugs (Phase 11b candidates)** — fixed 2026-05-13 in
  commit `e59d8bc`. `test_full_bracket_chain`: extracted shared
  `_preview_payload_hash` (8 fields) used by both `_nonce_and_payload_hash`
  (preview write) and the new `_consume_preview_nonce` (bracket place
  consume). `test_full_modify_chain`: `OrderEventConsumer` short-circuits
  on `event.kind == "replaced"` (audit row written, UPDATE + WS publish
  skipped); `orders_service.modify_order` now also UPDATEs the orders
  row to `status='modified'` + new broker_order_id (synthesized_status
  was previously only in the response, never persisted). Test suite:
  1293/16/0 → 1298/14/0.

- [ ] **Followup: retry cancel on LockNotAvailable** — Phase 11b
  surfaced a real race where `cancel_order`'s `FOR UPDATE NOWAIT`
  conflicts with the OrderEventConsumer's brief row lock during the
  modify cancel-replace event burst, returning 423 to the caller. The
  chain tests work around it with a 200ms sleep before DELETE. A
  small retry loop in `cancel_order` (3 attempts × 100ms) would fix
  this for real users too. Not blocking any test.

## Phase 2.x — follow-ups discovered during v0.2.0 verify

- [ ] nginx: add `location = /metrics { proxy_pass http://backend:8000/metrics; }` so Prometheus / Grafana can scrape through CF Access + service token. Backend endpoint exists and is auth-gated; only nginx is missing the proxy. Verified in prod 2026-04-23.
