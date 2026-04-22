# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- Smoke-test PR confirming the CI loop works on `pull_request: branches: [main]`.

### Planned for [0.1.0]
- Full VPS cutover: Dashboard_old torn down, new rebuild deployed at dashboard.kiusinghung.com.
- Cloudflare Tunnel replacing public 80/443 ports + certbot.
- CF Access Google-login gate with 2-email allowlist + service-token CI bypass.
- Multi-layer hardening: IONOS firewall + UFW + fail2ban + nginx security headers + CF WAF + gitleaks.
- `trading` DB dropped; `dashboard` DB is sole survivor.

## [0.0.1] — 2026-04-21
### Added
- Initial repo scaffold: FastAPI backend, React 19 frontend, local docker-compose stack (Redis only; Postgres native on Windows).
- Component architecture: design-tokens → primitives → patterns → layout → features, enforced by ESLint boundaries.
- Tailwind v4 + shadcn/ui; Stylelint blocks `px` and `em` site-wide.
- Storybook 9 with seed `Button` primitive.
- Lint stack: ruff, mypy, ESLint (boundaries + a11y + hooks), Stylelint, pre-commit, commitlint.
- GitHub Actions CI: parallel backend + frontend jobs.
- Docs: CLAUDE.md constitution, TASKS.md roadmap, this changelog.
