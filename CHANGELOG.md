# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
