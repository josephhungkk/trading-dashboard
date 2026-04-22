# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
