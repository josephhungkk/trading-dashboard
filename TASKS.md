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

## Phase 5 — Trade execution (IBKR)  *(next)*
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
