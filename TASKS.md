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

- [ ] **`AccountResponse.position_count`** — still deferred from 5b.1 architect-review HIGH-3; out of 5c scope per user choice "Family A only".
- [ ] **Periodic BASE-tag refresh for accounts added mid-run** — out of 5c scope; v0.5.2 `last_nlv_currency` fallback covers steady state.
- [ ] **Multi-worker uvicorn** (Phase 9) — single-worker still load-bearing for the in-memory replay cache + commission buffer.
- [ ] **On-demand quote subscribe for preview** — `_get_market_mid()` reads `mkt:mid:<conid>` from Redis only; sidecar populates this only for held positions. New tickers (e.g. AAPL when no AAPL position is held) → preview returns `503 market_mid_unavailable`. SGLN/VWRP work (held). Fix: eager `reqMktData` on contract-pick in `ContractSearchInput`, or backend-side one-shot subscribe with timeout in preview path. Substantial — focused v0.5.6+ feature.
- [ ] **Brief 502 flash after backend restart** — nginx caches the backend container IP; after manual `docker compose restart backend` (without `deploy.sh`), `/api/*` 502s for ~1-2s until nginx re-resolves. `deploy.sh` already does the reload; manual restart doesn't. Add a wrapper script or alias.
- [ ] **OrderEvent stream observability — partial:** v0.5.5 added `orderevent_subscribed`/`orderevent_unsubscribed`/`orderevent_emit_queued` in sidecar + `stream_subscribed`/`stream_closed` in backend consumer. `broker_order_events_received_total` exists. Remaining: dashboard panel + alert on stream-down.

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

## Phase 6 — Futu adapter + CJK font polish

- [ ] JP kanji routing: split JP @font-face into its own `font-family: "Noto Sans JP"` and select via `:lang(ja)` (or use `font-language-override: "JAN"`). Currently the TC face owns U+4E00-9FFF and precedes the JP face in source order, so Japanese kanji render from TC glyphs. Cosmetic at the Phase 3 ~10-char whitelist scale (forms coincide) but becomes user-visible once real JP tickers ship. Context: flagged by code-quality review during Phase 3 Task 3 (commit bbe97b9), 2026-04-24.
## Phase 7 — Alerts + Telegram + AI router (Ollama light + heavy-box WoL)
## Phase 8 — Schwab adapter
## Phase 2.x — follow-ups discovered during v0.2.0 verify

- [ ] nginx: add `location = /metrics { proxy_pass http://backend:8000/metrics; }` so Prometheus / Grafana can scrape through CF Access + service token. Backend endpoint exists and is auth-gated; only nginx is missing the proxy. Verified in prod 2026-04-23.

## Phase 9 — Bots service + security hardening

- [ ] PG client-cert auth over WireGuard — drop the plaintext `DATABASE_URL` password.
  - Edit `pg_hba.conf` on NUC: `hostssl dashboard trader 10.10.0.0/24 cert clientcert=verify-full`
  - Generate + distribute `secrets/postgres-client.{key,crt}` to VPS (600, `trader:trader`)
  - Shrink `DATABASE_URL` to `postgresql+asyncpg://trader@10.10.0.2/dashboard?ssl=require`
  - Context: user asked 2026-04-23; Phase 2 left `.env` password plaintext because `DATABASE_URL` is bootstrap. File-perms + WG isolation are current protection; cert auth eliminates the secret entirely.
