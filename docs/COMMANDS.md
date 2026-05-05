# Common commands

Moved out of CLAUDE.md (token-budget hygiene). Claude pulls this on demand.

```bash
# Compose stack (NUC)
docker compose up -d
docker compose logs -f backend
docker compose exec backend alembic upgrade head
docker compose exec backend pytest

# Dev servers
cd frontend && pnpm dev          # FE hot reload
cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
cd frontend && pnpm storybook

# Lint
cd frontend && pnpm lint && pnpm stylelint && pnpm typecheck
cd backend && uv run ruff check . && uv run mypy app/

# Migrations
docker compose exec backend alembic revision --autogenerate -m "msg"
docker compose exec backend alembic upgrade head

# Deploy (manual; GH Actions auto-deploys on push-to-main)
./scripts/deploy.sh

# Health-check probes
# Dev bypass (NUC, over WG)
curl -sf http://10.10.0.1/health

# CI bypass (anywhere, service token)
curl -sf https://dashboard.kiusinghung.com/health \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```
