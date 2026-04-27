# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
