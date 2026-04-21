# Trading Dashboard

Self-hosted multi-broker trading dashboard. See `CLAUDE.md` for the canonical architecture, tech stack, and conventions. See `TASKS.md` for the roadmap.

**Proprietary — All rights reserved.**

## Prerequisites

Dev runs on the NUC15PRO (same machine as the native PG18 install, broker gateways, light Ollama, ops glue). Claude Code runs in WSL2 on the NUC.

- WSL2 + Docker Desktop on the NUC.
- PostgreSQL 18 installed natively on Windows (`C:\Program Files\PostgreSQL\18\`) — already present for the live deployment. Create a **new database `dashboard`** owned by role `trader` (separate from the live `trading` DB).
- `pg_hba.conf` allowing connections from WSL's Docker network (typically already permitted since the VPS connects via WG).
- `listen_addresses = '*'` in `postgresql.conf` (typically already set).
- Node 24 (via Corepack/pnpm) and Python 3.14 (via uv) only needed if running outside Docker.

## Local dev

    cp .env.example .env
    # edit DATABASE_URL password to match your trader role's password
    docker compose up -d

- Backend:   http://localhost:8000/health
- Frontend:  http://localhost:5173
- Storybook: cd frontend && pnpm storybook  →  http://localhost:6006
