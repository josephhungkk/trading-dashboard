# Tasks

## Phase 0 — Repo scaffold & local-dev loop  *(in progress)*
- [ ] Initialize git + gh remote (private, proprietary) + conventional-commits pre-commit
- [ ] Backend: uv project, FastAPI /health, structlog + redaction stub, Alembic init, tests, Dockerfile
- [ ] Frontend: Vite + React 19 + TS strict + Tailwind v4 + shadcn init + Button primitive
- [ ] Storybook 9 configured, Button has stories + tests
- [ ] Design tokens: spacing/typography/colors/radii/motion (rem only)
- [ ] Lint stack: Stylelint (no-px), ESLint (boundaries), pre-commit, commitlint
- [ ] docker-compose.yml: redis + backend + frontend (Postgres runs natively on Windows)
- [ ] .env.example with all bootstrap vars documented
- [ ] GitHub Actions CI: backend + frontend jobs, both green
- [ ] Docs: CLAUDE.md (updated), TASKS.md, CHANGELOG.md, README.md
- [ ] First PR merged; tag v0.0.1

## Phase 1 — VPS infra skeleton  *(next)*
## Phase 2 — Auth + DB-backed config service (app_config, app_secrets)
## Phase 3 — Frontend shell (mocks)
## Phase 4 — IBKR adapter (read-only, BrokerAdapter base lands here)
## Phase 5 — Trade execution (IBKR)
## Phase 6 — Futu adapter + CJK font polish
## Phase 7 — Alerts + Telegram + AI router (Ollama light + heavy-box WoL)
## Phase 8 — Schwab adapter
## Phase 9 — Bots service
