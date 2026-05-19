# Trading Dashboard — Project Constitution

Self-hosted multi-broker, multi-account trading dashboard. Stocks, forex, commodities, indexes, bonds, ETFs, futures, crypto, CFD, options, derivatives. FE+BE on IONOS VPS behind Cloudflare; broker gateways + PG-18 with timescaledb on NUC15PRO over WireGuard; heavy AI box (RTX 4080 + 64 GB) for large Ollama models on demand. NUC15PRO for lighter Ollama models always on.

## Stack

| Layer | Tech |
|---|---|
| BE | Python 3.14 · FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · asyncpg |
| FE | React 19 · Vite 7 · TS 6.0 strict · Tailwind v4 (`@theme`) · shadcn/ui · Zustand · Storybook 10 · klinecharts |
| Test | Vitest 4 + RTL 16 (FE); pytest 9 + pytest-asyncio + httpx (BE); Playwright (Phase 5+) |
| Infra | Redis 7 · PostgreSQL 18 native on Windows NUC · Docker Compose (WSL) · Cloudflare Tunnel + nginx · CF Access + Google IdP |
| Broker SDKs | `ib_async` · `futu-api` · `schwabdev` (read-only) · `alpaca-py` |
| AI | Ollama — 7-8B on NUC, 14-70B on heavy box (WoL) |
| Pkg | pnpm (FE) + uv (BE); Node 24 LTS via Corepack |
| Lint | ruff + mypy --strict (BE); ESLint 9 flat + `eslint-plugin-boundaries` + `jsx-a11y` + Stylelint (FE) |

Versioning: latest stable at scaffold time; pin via lockfiles only.

## Subdirectory rules (load only what you need)

- Backend conventions, Alembic, test commands, BE-specific invariants → `backend/CLAUDE.md`
- Frontend conventions, component layers, FE runtime rules → `frontend/CLAUDE.md`
- Phase workflow, subagent routing, shipped-phase index → `docs/CLAUDE.md`

## Cross-cutting invariants (always apply)

- **Boundary stripping:** `AccountResponse` to FE = `id, broker_id, alias, mode, currency_base, display_order` only. Never `gateway_label`/`account_number`. `account_id` UUID is the only FE handle; `AccountService._resolve_account` is the single chokepoint.
- **Race-free soft-delete:** `BrokerDiscoverer` only soft-deletes rows whose `last_seen_via = ANY(:healthy_labels)` THIS tick. All-unhealthy → empty predicate → zero deletions.
- **Maintenance:** `app/services/ibkr_maintenance.py` is source of truth. Backend returns `503 + Retry-After` during reset; watchdog skips weekend reset.
- **NUC ops:** `deploy/nuc/` (PowerShell launchers + watchdog + Pester). `provision-and-publish.ps1` rotates mTLS; `revoke-cert.ps1` revokes; `renew-sidecar-mtls.ps1` rolls one at a time.
- **Schema changes** → Alembic migration only, never raw model edits.
- **Never modify `brokers/base.py`** without updating every concrete adapter.

## Security (always apply)

- Never log API keys/tokens/passwords — structlog redacts via processor in `app/core/logging.py`.
- Broker creds in `app_secrets` (Fernet). Never in git.
- Postgres reachable only via WG (VPS) and LAN/WSL (NUC). Never public.
- FE never sees broker creds.
- Trade-execution endpoints require confirmation nonce (CSRF).
- Never commit `.env`/`*.key`/`secrets/*`.

## Configuration & topology

Runtime settings in DB (`app_config` + `app_secrets` Fernet-encrypted), not `.env`. Don't add new `.env` keys beyond bootstrap. Edit at runtime via `POST /api/admin/config`/`/secrets`. See `docs/CONFIG.md`.

WG topology + Postgres connectivity + Windows-side paths: `docs/NETWORK.md`. Key invariants: NUC = dev host (WSL2 at `/home/joseph/dashboard`); PG at `10.10.0.2:5432` over WG; VPS prod at `/home/trader/trading-dashboard`; SSH via `ssh -p 2222 trader@88.208.197.219`.

## Roadmap & goals

Phase 7 → v1.0.0 locked in `docs/ROADMAP.md`. End-state: every IBKR/Futu/Schwab/Alpaca asset class + relevant order types, multi-source streaming quotes, charting, AI alerts/scanner, autonomous self-refining bots (param-tuning + LLM-in-loop ceiling — no raw RL), UK CGT (S104 + SA108), PWA mobile.

**Non-goals:** native RN app (PWA covers); raw RL bots (overfit); paper sim (use broker paper); multi-tenant.

## When Claude makes changes

- Run tests after edits: see `backend/CLAUDE.md` for BE test command, `frontend/CLAUDE.md` for FE.
- Regenerate types when API schemas change: `scripts/gen-types.sh`.
- GitHub is canonical repo. Update CLAUDE.md/CHANGELOG.md/TASKS.md every phase close; commit.
- Prefer editing existing files. Use `/frontend-design` skill for FE design.
