# Phase 0 — Repo Scaffold & Local-Dev Loop — Design Spec

- **Status:** Design-approved 2026-04-21. Ready for implementation plan (`superpowers:writing-plans`).
- **Owner:** Joseph Hung (GitHub handle to be confirmed — assumed `josephhungkk`).
- **Parent roadmap:** See §11 for the Phase 1–9 sequence this unblocks.
- **Dev host:** The NUC15PRO. Claude Code runs in WSL2 on the NUC; `/mnt/c/dashboard` is the NUC's `C:\dashboard`. The NUC is simultaneously dev host, Postgres host, broker-gateway host, light-Ollama host, and ops-glue host. There is no separate Windows dev box.
- **Prod host:** The IONOS VPS 88.208.197.219 (Phase 1+ only; Phase 0 does not deploy there).
- **Relationship to live deployment:** Rebuild runs **parallel** to the existing Phase 1 deployment on the VPS + NUC. Phase 0 and Phase 1 do not touch live services. The new rebuild uses DB name `dashboard`; the live deployment uses DB `trading` — both coexist on the NUC's PG18. Cutover is a deliberate later phase, not scheduled here.

---

## 1. Scope & intent

Phase 0 produces the minimum repo and local-dev loop that everything else builds on. Its only user-visible output is a dev who can `git clone`, `cp .env.example .env`, `docker compose up -d`, and within a minute see:

- Backend `/health` responding with `{status: "ok", env: "dev", db: "ok"}`
- Frontend at `localhost:5173` showing "Backend: ok"
- Storybook at `localhost:6006` showing the seed `Button` primitive

Nothing real. No auth, no brokers, no features. Just the scaffolding, the conventions, and the lint gates that make future phases fast and safe.

### What Phase 0 does not ship

- No business features (brokers, portfolios, orders, alerts — those are Phases 2–9)
- No VPS-side infrastructure (Nginx, Let's Encrypt, WireGuard routing, rsync deploy — Phase 1)
- No Alembic migrations yet (empty `versions/` directory; first migration lands with `app_config` table in Phase 2)
- No real adapter code in `backend/app/brokers/` (empty `__init__.py`; the abstract base class lands with the first concrete adapter in Phase 4 to avoid speculative abstraction)
- No WebSocket handlers, Zustand stores, React Router, or shell layout — all deferred to Phase 3+
- No Noto fonts on disk (`public/fonts/` exists but empty; fonts land in Phase 3 when stock names are rendered)

---

## 2. Decisions locked in (from brainstorming)

| Area | Decision | Notes |
|---|---|---|
| **Repo** | `github.com/josephhungkk/trading-dashboard` · private · proprietary license | Confirm handle with `gh auth status` at impl time. Branch protection deferred to Phase 1. |
| **Python** | **3.14** (latest stable as of 2026-04-21). Constitution is updated from 3.12 to match. | Use `uv python pin 3.14` locally; Dockerfile base `python:3.14-slim`. |
| **Node** | **24 LTS** (latest LTS since 2025-10). | `.nvmrc` pinned; Dockerfile base `node:24-alpine`. |
| **Frontend framework** | **React 19 + Vite + TypeScript strict** | |
| **Styling** | **Tailwind v4** (CSS-first `@theme`) + **shadcn/ui** (copy-paste primitives) | All rem-based. `px` and `em` banned site-wide by Stylelint. |
| **Component architecture** | Five-layer: `design-tokens` → `primitives` → `patterns` → `layout` → `features`. One-way deps enforced by `eslint-plugin-boundaries`. | See §5 for rules. |
| **Workbench** | **Storybook 9** (latest stable at impl time; substitute if 10+ is out) | Stories for every primitive + pattern. Features are NOT storied. |
| **State** | Zustand (directory stub only in Phase 0) | Real stores land in Phase 3. |
| **Python tooling** | `uv` + `ruff` + `ruff format` + `mypy` + `pytest` + `pytest-asyncio` | No black, isort, flake8, pip, poetry, pyenv. |
| **Frontend testing** | Vitest + React Testing Library + `@testing-library/jest-dom`. Playwright deferred to Phase 5+. | |
| **Pre-commit** | `pre-commit` framework + `commitlint` (conventional commits enforced at commit-msg hook) | |
| **Package manager** | `pnpm` (via Corepack) | |
| **Postgres** | Runs **natively on Windows on the NUC15PRO** (not in Docker). **Dev and prod use the same PG18 server**, different DB names (`dashboard` new vs `trading` legacy). | Dev (Claude runs in WSL on the NUC): `host.docker.internal:5432` → NUC's PG18. Prod (VPS): `10.10.0.2:5432` → same NUC over WireGuard. |
| **Redis** | Runs in **Docker** both locally (compose) and in Phase 1 (on VPS). | Stateless cache + pub/sub; no native Windows install needed. |
| **CI** | GitHub Actions: two parallel jobs (`backend`, `frontend`) on every PR + push to `main`. No deploy step (that's Phase 1). | |
| **Phase 0 deployment** | Option B from brainstorming: working local Docker-Compose stack. No VPS work. | |
| **Versioning policy** | Latest stable at scaffold time; pin via lockfiles (`uv.lock`, `pnpm-lock.yaml`). No hand-pinned semver ranges. | See `~/.claude/projects/-mnt-c-dashboard/memory/feedback_latest_stable.md`. |

---

## 3. Repo structure

```
trading-dashboard/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .vscode/
│   └── settings.json
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── api/__init__.py
│   │   ├── brokers/__init__.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── db.py
│   │   │   ├── logging.py
│   │   │   └── deps.py
│   │   ├── models/__init__.py
│   │   ├── services/__init__.py
│   │   └── ws/__init__.py
│   ├── tests/
│   │   ├── conftest.py
│   │   └── test_health.py
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── uv.lock
│   └── Dockerfile
├── frontend/
│   ├── public/
│   │   ├── fonts/                    # empty — Phase 3
│   │   └── favicon.svg
│   ├── src/
│   │   ├── design-tokens/
│   │   │   ├── index.ts
│   │   │   ├── spacing.ts
│   │   │   ├── typography.ts
│   │   │   ├── colors.ts
│   │   │   ├── radii.ts
│   │   │   └── motion.ts
│   │   ├── components/
│   │   │   ├── primitives/
│   │   │   │   └── Button/
│   │   │   │       ├── Button.tsx
│   │   │   │       ├── Button.stories.tsx
│   │   │   │       ├── Button.test.tsx
│   │   │   │       └── index.ts
│   │   │   ├── patterns/             # empty — Phase 3
│   │   │   └── layout/               # empty — Phase 3
│   │   ├── features/                 # empty — Phase 3
│   │   ├── hooks/
│   │   ├── services/
│   │   │   ├── api.ts
│   │   │   ├── ws.ts                 # stub
│   │   │   └── lang.ts               # stub returning 'en'
│   │   ├── stores/                   # empty — Phase 3
│   │   ├── styles/
│   │   │   ├── global.css
│   │   │   └── tailwind.css
│   │   ├── App.tsx
│   │   ├── App.test.tsx
│   │   └── main.tsx
│   ├── .storybook/
│   │   ├── main.ts
│   │   └── preview.ts
│   ├── index.html
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── stylelint.config.mjs
│   ├── eslint.config.mjs
│   ├── vitest.config.ts
│   ├── components.json               # shadcn config
│   ├── package.json
│   ├── pnpm-lock.yaml
│   └── Dockerfile
├── migrations/                       # symlink → backend/alembic/versions (per constitution's directory layout)
├── bots/                             # reserved, empty — Phase 9
├── scripts/
│   ├── deploy.sh                     # stub (prints "Phase 1 will wire this up", exit 1)
│   ├── gen-types.sh                  # stub (OpenAPI → TS, Phase 2)
│   └── dev.sh                        # docker compose up + tail logs
├── docker-compose.yml
├── docker-compose.override.yml.example
├── nginx/                            # reserved, empty — Phase 1
├── deploy/
│   └── nuc/
│       └── README.md                 # placeholder pointing at existing ops glue
├── docs/
│   └── superpowers/
│       ├── specs/
│       │   └── 2026-04-21-phase0-scaffold-design.md   # this document
│       └── plans/                    # writing-plans output lands here
├── .env.example
├── .gitignore
├── .gitattributes
├── .pre-commit-config.yaml
├── commitlint.config.cjs
├── CLAUDE.md
├── TASKS.md
├── CHANGELOG.md
└── README.md
```

---

## 4. Backend skeleton

### 4.1 Stack

- Python 3.14
- FastAPI (latest stable)
- SQLAlchemy 2.x async (asyncpg driver)
- Alembic (initialized, no migrations yet)
- Pydantic v2 + pydantic-settings
- structlog
- uvicorn
- uv (package manager + venv)
- ruff (lint + format, replaces black/isort/flake8)
- mypy (strict mode on `app/`)
- pytest + pytest-asyncio + httpx

All pinned only by lockfile (`uv.lock`); `pyproject.toml` uses bare names with optional lower-bound minimums.

### 4.2 `app/main.py`

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.core.config import settings
from app.core.db import engine, SessionLocal
from app.core.logging import configure_logging

configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()

app = FastAPI(title="Trading Dashboard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
```

### 4.3 `app/core/config.py` — bootstrap-only

Per the constitution, `.env` holds only values needed before the DB is reachable. The DB-backed `ConfigService` (reading `app_config` / `app_secrets` tables) lands in Phase 2, not here.

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    env: str = Field(default="dev", alias="APP_ENV")
    secret_key: str = Field(alias="APP_SECRET_KEY")
    cors_origins: list[str] = Field(default_factory=list, alias="APP_CORS_ORIGINS")
    database_url: str = Field(alias="DATABASE_URL")
    postgres_pool_size: int = Field(default=5, alias="POSTGRES_POOL_SIZE")
    postgres_max_overflow: int = Field(default=10, alias="POSTGRES_MAX_OVERFLOW")
    redis_password: str = Field(alias="REDIS_PASSWORD")
    redis_url: str = Field(alias="REDIS_URL")

settings = Settings()
```

### 4.4 `app/core/db.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.postgres_pool_size,
    max_overflow=settings.postgres_max_overflow,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

### 4.5 `app/core/logging.py`

- structlog configured to emit JSON when `env != "dev"`, pretty-colored otherwise.
- Processor chain includes a **secret-redaction stub** that regex-masks anything matching `sk-…`, `Bearer …`, `api_key=…`. Redaction list expands in Phase 2 as broker/OAuth secrets are known.
- Timestamp, level, logger name, event — standard fields.

### 4.6 Alembic

- Initialized with the async template (`async_env.py` pattern from `alembic init -t async`).
- `env.py` reads `settings.database_url`.
- `versions/` empty. First migration (the `app_config` and `app_secrets` tables) lands in Phase 2.

### 4.7 Tests

`tests/conftest.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

`tests/test_health.py`:

```python
import pytest

@pytest.mark.asyncio
async def test_health_returns_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["env"] == "dev"
    assert body["db"] in {"ok", "unreachable"}
```

### 4.8 Dockerfile

```dockerfile
FROM python:3.14-slim AS base
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Frontend skeleton — component architecture

### 5.1 Five layers, one-way dependencies

| Layer | What lives here | May import from |
|---|---|---|
| `design-tokens/` | Rem scale, color vars, font stacks, radii, motion curves — the **only** place raw numbers live | — |
| `components/primitives/` | Unstyled accessible building blocks via shadcn/ui (Radix under the hood). No business logic. | tokens |
| `components/patterns/` | Trading-specific composites — dumb, props-only, reusable across features | tokens + primitives + patterns |
| `components/layout/` | Shell structure: `AppShell`, `Sidebar`, `BottomTabBar`, `SplitPane`, `Panel` | tokens + primitives + patterns + layout |
| `features/` | Data-aware route-level modules; own their Zustand slice, API calls, WS subscriptions | everything |

**Enforcement:** `eslint-plugin-boundaries` with element-type globs matching these folders. Any cross-layer violation breaks CI.

### 5.2 Design tokens (rem only)

```ts
// src/design-tokens/spacing.ts
export const space = {
  0: '0', px: '0.0625rem', 1: '0.25rem', 2: '0.5rem', 3: '0.75rem',
  4: '1rem', 6: '1.5rem', 8: '2rem', 12: '3rem', 16: '4rem', 24: '6rem',
} as const;
```

Parallel files for `typography.ts`, `colors.ts`, `radii.ts`, `motion.ts`. `index.ts` re-exports everything. Tailwind config consumes these via `theme.extend`.

### 5.3 Tailwind v4

- CSS-first config via `@theme` directive in `styles/global.css`.
- Small `tailwind.config.ts` for content paths + plugins only.
- Dark mode: `class` strategy; `ThemeProvider` component is stubbed but not mounted yet.

### 5.4 shadcn/ui seed — the `Button` primitive

- Run `pnpm dlx shadcn@latest init` → "New York" style → Tailwind v4 preset → writes `components.json` + base CSS vars. **Override the default `ui` alias** during init to point at `@/components/primitives` so generated components land in the primitives layer (not the default `src/components/ui/`). In `components.json`:
  ```json
  { "aliases": { "components": "@/components", "ui": "@/components/primitives", "utils": "@/lib/utils" } }
  ```
- Run `pnpm dlx shadcn@latest add button` → writes `src/components/primitives/button.tsx` using Radix Slot + CVA. Rename to the folder convention (`Button/Button.tsx`) by hand after generation — shadcn emits flat files by default; our layer convention is folder-per-component with `Button.tsx`, `Button.stories.tsx`, `Button.test.tsx`, `index.ts`.
- Add `Button.stories.tsx` with default / destructive / outline / ghost variants + disabled state.
- Add `Button.test.tsx`: renders children, click handler fires, `disabled` blocks click.
- Re-export from `src/components/primitives/Button/index.ts`.

This is the only component Phase 0 ships — it exists to prove the shadcn + Storybook + Vitest path works end-to-end.

### 5.5 Storybook

- `pnpm dlx storybook@latest init --type react-vite`.
- `.storybook/main.ts` enables addons: `@storybook/addon-essentials`, `@storybook/addon-a11y`, `@storybook/addon-vitest`.
- `.storybook/preview.ts` imports `src/styles/global.css`; sets viewports `mobile-375`, `tablet-768`, `desktop-1440`.
- CI runs `pnpm build-storybook` to catch broken stories; no Chromatic yet.

### 5.6 `services/api.ts`

```ts
const BASE = import.meta.env.VITE_API_URL ?? '';

export async function getHealth(): Promise<{status: string; env: string; db: string}> {
  const r = await fetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}
```

### 5.7 `App.tsx`

```tsx
import { useEffect, useState } from 'react';
import { getHealth } from './services/api';
import { Button } from './components/primitives/Button';

export default function App() {
  const [status, setStatus] = useState('…');
  useEffect(() => {
    getHealth().then(r => setStatus(r.status)).catch(() => setStatus('unreachable'));
  }, []);
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold">Trading Dashboard</h1>
        <p className="text-sm opacity-70 mt-2">Backend: {status}</p>
        <Button className="mt-4" onClick={() => location.reload()}>Recheck</Button>
      </div>
    </main>
  );
}
```

### 5.8 Dockerfile

```dockerfile
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM node:24-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable && pnpm build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/spa.conf /etc/nginx/conf.d/default.conf
```

`nginx/spa.conf` is a minimal SPA fallback (`try_files $uri /index.html;`). Phase 1 replaces this with the real reverse-proxy config on the VPS.

---

## 6. Lint & quality gates

### 6.1 Stylelint (`stylelint.config.mjs`)

```js
export default {
  extends: ['stylelint-config-standard', 'stylelint-config-clean-order'],
  customSyntax: 'postcss-html',
  rules: {
    'unit-disallowed-list': ['px', 'em'],
    'declaration-property-unit-allowed-list': {
      '/^(width|height|min-.+|max-.+|margin.*|padding.*|gap|top|right|bottom|left|font-size|line-height|border-radius|inset.*)$/':
        ['rem', '%', 'vh', 'vw', 'fr', 'auto'],
    },
  },
};
```

Runs on `.css` files and inside `<style>` blocks in TSX.

### 6.2 ESLint (`eslint.config.mjs`)

- Flat config.
- `typescript-eslint` strict preset.
- `eslint-plugin-react` + `eslint-plugin-react-hooks` + `eslint-plugin-jsx-a11y`.
- **`eslint-plugin-boundaries`** with element definitions:

```js
boundaries: {
  elements: [
    { type: 'tokens',     pattern: 'src/design-tokens/**' },
    { type: 'primitives', pattern: 'src/components/primitives/**' },
    { type: 'patterns',   pattern: 'src/components/patterns/**' },
    { type: 'layout',     pattern: 'src/components/layout/**' },
    { type: 'features',   pattern: 'src/features/**' },
  ],
},
rules: {
  'boundaries/element-types': ['error', {
    default: 'disallow',
    rules: [
      { from: 'primitives', allow: ['tokens'] },
      { from: 'patterns',   allow: ['tokens', 'primitives', 'patterns'] },
      { from: 'layout',     allow: ['tokens', 'primitives', 'patterns', 'layout'] },
      { from: 'features',   allow: ['tokens', 'primitives', 'patterns', 'layout', 'features'] },
    ],
  }],
}
```

- Also a custom rule blocking `style={{ ... : '...px' }}` literals as belt-and-suspenders against Stylelint missing inline objects.

### 6.3 Python (ruff + mypy)

- `[tool.ruff]` in `pyproject.toml`: enable `E`, `F`, `W`, `I`, `N`, `UP`, `B`, `A`, `C4`, `ASYNC`, `RUF`.
- `mypy`: `strict = true` on `app/`, loose on `tests/`.

### 6.4 pre-commit (`.pre-commit-config.yaml`)

Revs (`rev:`) are pinned to whatever `pre-commit autoupdate` resolves to at implementation time. Do not hand-pick these; run the command and commit the resulting `.pre-commit-config.yaml`.

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    # rev pinned by `pre-commit autoupdate` at impl time
    hooks: [{id: ruff, args: [--fix]}, {id: ruff-format}]
  - repo: https://github.com/pre-commit/mirrors-mypy
    # rev pinned by `pre-commit autoupdate` at impl time
    hooks: [{id: mypy, additional_dependencies: [pydantic, sqlalchemy]}]
  - repo: local
    hooks:
      - id: eslint
        name: eslint
        entry: bash -c 'cd frontend && pnpm lint --fix'
        language: system
        files: ^frontend/.*\.(ts|tsx|js|jsx)$
      - id: stylelint
        name: stylelint
        entry: bash -c 'cd frontend && pnpm stylelint --fix'
        language: system
        files: ^frontend/.*\.(css|tsx)$
      - id: commitlint
        name: commitlint
        stages: [commit-msg]
        entry: bash -c 'cd frontend && pnpm commitlint --edit "$1"'
        language: system
```

### 6.5 commitlint (`commitlint.config.cjs`)

```js
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [2, 'always', ['feat', 'fix', 'refactor', 'docs', 'test', 'chore', 'perf', 'ci']],
  },
};
```

---

## 7. Local-dev stack (`docker-compose.yml`)

```yaml
# Postgres intentionally absent — runs natively on Windows on the NUC15PRO.
# Dev (Claude/uvicorn on the NUC):         host.docker.internal:5432  (NUC's own PG18)
# Prod (uvicorn on IONOS VPS, Phase 1+):   10.10.0.2:5432             (same NUC, via WireGuard)
# See DATABASE_URL in .env.

services:
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 5s
      retries: 10

  backend:
    build: ./backend
    env_file: .env
    extra_hosts: ["host.docker.internal:host-gateway"]
    depends_on:
      redis: { condition: service_healthy }
    ports: ["8000:8000"]
    volumes: ["./backend/app:/app/app"]
    command: ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

  frontend:
    build: { context: ./frontend, target: build }
    command: ["pnpm", "dev", "--host", "0.0.0.0"]
    ports: ["5173:5173"]
    volumes:
      - "./frontend/src:/app/src"
      - "./frontend/public:/app/public"
    environment:
      VITE_API_URL: "http://localhost:8000"
```

`docker-compose.override.yml.example` documents WSL quirks (per existing feedback memory: Vite HMR can miss events across the WSL boundary — add `WATCHPACK_POLLING=true` or restart hint).

### 7.1 `.env.example`

```env
APP_ENV=dev
APP_SECRET_KEY=change-me-32-bytes-base64
APP_CORS_ORIGINS=http://localhost:5173

# Postgres runs NATIVELY on the NUC15PRO — never in Docker.
# Dev happens on the NUC itself (Claude in WSL2), so:
#   Dev:  host.docker.internal:5432  (NUC's own PG18 reached from WSL Docker)
#   Prod: 10.10.0.2:5432             (same NUC PG18 reached from VPS via WireGuard)
# New rebuild uses DB name "dashboard" to coexist with the legacy "trading" DB on the same server.
DATABASE_URL=postgresql+asyncpg://trader:change-me@host.docker.internal:5432/dashboard
POSTGRES_POOL_SIZE=5
POSTGRES_MAX_OVERFLOW=10

# Redis — runs in Docker (compose locally; Phase 1 will run it on VPS).
# NOTE: per memory, keep the password URL-safe (no special characters that need escaping).
REDIS_PASSWORD=change-me-url-safe
REDIS_URL=redis://:change-me-url-safe@redis:6379/0
```

### 7.2 Prerequisites (documented in README)

Dev runs on the **NUC15PRO** (same physical machine that hosts PG18, broker gateways, light Ollama, and ops glue per the constitution's network-topology table). Claude Code runs in WSL2 on the NUC; `/mnt/c/dashboard` is the NUC's own `C:\dashboard`. There is no separate Windows dev box.

The NUC must have (most of which are already in place for the live deployment):

- **WSL2 + Docker Desktop** installed and running.
- **PostgreSQL 18 installed natively on Windows** via the official installer at `C:\Program Files\PostgreSQL\18\` (already present — same PG18 that the live `trading` DB uses).
- A new database named **`dashboard`** owned by role `trader` with a known password. This is **separate from the live `trading` DB** so the rebuild can coexist without touching live data.
- `pg_hba.conf` allows connections from `host.docker.internal` (i.e., from WSL's Docker network) — typically `host all all 172.17.0.0/16 md5` or similar (likely already permitted since WG peers from the VPS already connect).
- `postgresql.conf` has `listen_addresses = '*'` (likely already set since the VPS reaches this Postgres via WG).

These are host-side prerequisites, **not** part of the repo scaffold. Phase 0 assumes the NUC is already configured the way the 2026-04-17 deployment left it, plus the new `dashboard` DB.

---

## 8. CI — GitHub Actions (`.github/workflows/ci.yml`)

Two parallel jobs triggered on every PR against `main` and every push to `main`.

### 8.1 `backend` job

```yaml
backend:
  runs-on: ubuntu-latest
  services:
    postgres:
      image: postgres:18-alpine
      env:
        POSTGRES_USER: trader
        POSTGRES_PASSWORD: ci
        POSTGRES_DB: dashboard
      ports: ["5432:5432"]
      options: >-
        --health-cmd "pg_isready -U trader"
        --health-interval 5s
        --health-retries 10
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v5
      with: { python-version: '3.14' }
    - working-directory: backend
      run: |
        uv sync --frozen
        uv run ruff check .
        uv run ruff format --check .
        uv run mypy app/
        uv run pytest --cov=app --cov-report=term-missing
      env:
        DATABASE_URL: postgresql+asyncpg://trader:ci@localhost:5432/dashboard
        APP_SECRET_KEY: ci-secret-key-32-chars-minimum-req
        APP_ENV: dev
        APP_CORS_ORIGINS: http://localhost:5173
        POSTGRES_POOL_SIZE: 2
        POSTGRES_MAX_OVERFLOW: 2
        REDIS_PASSWORD: ci
        REDIS_URL: redis://:ci@localhost:6379/0
    - if: github.ref == 'refs/heads/main'
      working-directory: backend
      run: docker build -t trading-dashboard-backend:ci .
```

Postgres is spun up as a GitHub Actions service (only place in the project where it runs in a container — CI-only, not dev, not prod).

### 8.2 `frontend` job

```yaml
frontend:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with: { node-version: '24', cache: 'pnpm' }
    - run: corepack enable
    - working-directory: frontend
      run: |
        pnpm install --frozen-lockfile
        pnpm lint
        pnpm stylelint
        pnpm typecheck
        pnpm test
        pnpm build
        pnpm build-storybook
    - if: github.ref == 'refs/heads/main'
      working-directory: frontend
      run: docker build -t trading-dashboard-frontend:ci .
```

### 8.3 Not in Phase 0 CI

- No deploy step (Phase 1 adds rsync-over-SSH to VPS).
- No e2e tests (Playwright deferred to Phase 5+).
- No Chromatic visual diff.
- No secret scanning (add before Phase 4 when real broker credentials exist).

---

## 9. Documentation baseline

### 9.1 `CLAUDE.md`

The full constitution from the user's input, with these edits:

- Python version: **3.14** (was 3.12).
- Add "Frontend stack" subsection under **Stack**: React 19, Vite, TypeScript strict, Tailwind v4, shadcn/ui, Storybook 9, Zustand, Vitest, RTL.
- Add "Component architecture" subsection: the five-layer model + the dependency matrix.
- Add "Lint gates" subsection: Stylelint `unit-disallowed-list`, ESLint `boundaries`.
- Add a "Versioning policy" one-liner: "Latest stable at scaffold time; pin via lockfiles, never hand-pinned semver."
- Everything else (network topology, broker paths, security rules, non-goals, etc.) stays verbatim.

### 9.2 `TASKS.md`

```md
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
```

### 9.3 `CHANGELOG.md`

```md
# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.0.1] — 2026-04-21
### Added
- Initial repo scaffold: FastAPI backend, React 19 frontend, local docker-compose stack (Redis only; Postgres native on Windows).
- Component architecture: design-tokens → primitives → patterns → layout → features, enforced by ESLint boundaries.
- Tailwind v4 + shadcn/ui; Stylelint blocks `px` and `em` site-wide.
- Storybook 9 with seed `Button` primitive.
- Lint stack: ruff, mypy, ESLint (boundaries + a11y + hooks), Stylelint, pre-commit, commitlint.
- GitHub Actions CI: parallel backend + frontend jobs.
- Docs: CLAUDE.md constitution, TASKS.md roadmap, this changelog.
```

### 9.4 `README.md`

```md
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
```

### 9.5 `.gitignore`

```
.env
.env.local
node_modules/
__pycache__/
*.pyc
.venv/
dist/
build/
storybook-static/
coverage/
.superpowers/
.vite/
.ruff_cache/
.mypy_cache/
.pytest_cache/
.DS_Store
```

### 9.6 `.gitattributes`

```
* text=auto eol=lf
*.ps1 text eol=crlf
pnpm-lock.yaml linguist-generated=true
uv.lock linguist-generated=true
```

### 9.7 Scripts

- `scripts/deploy.sh` — stub; prints "Phase 1 will wire this up" and exits 1.
- `scripts/gen-types.sh` — stub; real OpenAPI → TS pipeline lands in Phase 2.
- `scripts/dev.sh` — `docker compose up -d && docker compose logs -f backend frontend`.

---

## 10. Success criteria (Phase 0 "done" gate)

1. On the NUC (in WSL2), `git clone && cp .env.example .env && docker compose up -d` brings up a green stack against the NUC's native PG18, using the new `dashboard` DB (leaving the live `trading` DB untouched).
2. `curl http://localhost:8000/health` returns `{"status":"ok","env":"dev","db":"ok"}`.
3. `http://localhost:5173` renders "Backend: ok" (CORS + API client proven).
4. `cd frontend && pnpm storybook` opens Storybook 9 showing the `Button` primitive.
5. `pnpm lint && pnpm stylelint` exit 0. Adding `padding: 4px` anywhere fails the lint (Stylelint). Adding `import { Button } from '../../features/…'` inside a primitive fails the lint (boundaries).
6. `pnpm test` exits 0 (App + Button tests pass). `uv run pytest` exits 0 (health test passes).
7. GitHub Actions CI green on a PR against `main`.
8. Pre-commit hooks run on `git commit`; a non-conventional commit message is rejected by commitlint.
9. `docs/superpowers/specs/2026-04-21-phase0-scaffold-design.md` (this file) is committed.
10. `CLAUDE.md`, `TASKS.md`, `CHANGELOG.md`, `README.md` all present, committed, and referenced from each other where cross-links make sense.
11. Repo exists at `github.com/josephhungkk/trading-dashboard`, private, default branch `main`, tag `v0.0.1` on the scaffold commit.

---

## 11. Out of scope / deferred to later phases

Anchor for every "is this in Phase 0?" question during implementation:

| Item | Phase |
|---|---|
| VPS deployment, Nginx, Let's Encrypt DNS-01, WireGuard peer config | 1 |
| `rsync` deploy script with real target | 1 |
| `docker-compose.prod.yml` for VPS | 1 |
| Auth, JWT, sessions, admin role | 2 |
| `app_config` + `app_secrets` tables, `ConfigService`, Redis pub/sub invalidation | 2 |
| `/api/admin/config`, `/api/admin/secrets` endpoints + admin UI | 2 |
| OpenAPI → TypeScript generator (`scripts/gen-types.sh`) | 2 |
| Noto fonts in `public/fonts/`; `langForMarket()` returning real CJK lang codes | 3 |
| React Router, Zustand slices, WebSocket client | 3 |
| `AppShell`, `Sidebar`, `BottomTabBar`, `SplitPane`, `Panel` layout components | 3 |
| Patterns: `PriceCell`, `Ticker`, `PnLBadge`, `AccountSwitcher`, `OrderTicketForm`, `PositionRow` | 3 |
| klineschart integration | 3 |
| `BrokerAdapter` abstract base class (lands with first concrete adapter, not speculatively) | 4 |
| IBKR adapter (positions/quotes, WebSocket quotes, UK-pence + split handling) | 4 |
| Trade-execution endpoints, nonce/confirmation tokens, order ticket | 5 |
| Futu adapter, HK/CN market rendering | 6 |
| Alerts engine, Telegram bot, Ollama router + WoL magic packet | 7 |
| Schwab adapter (OAuth flow) | 8 |
| `bots/` container, first strategy | 9 |

---

## 12. Open questions / deltas from the constitution

Things that the act of specifying Phase 0 surfaced, which should be reflected back into `CLAUDE.md` when it lands:

1. **Python 3.12 → 3.14.** Constitution says 3.12; we've bumped to 3.14. Update the Stack section and the coding conventions heading.
2. **Frontend stack is now determined.** Constitution says "Not yet determined." Replace with the React 19 + Vite + TS + Tailwind v4 + shadcn + Storybook + Zustand stack.
3. **Component architecture is now determined.** Add the five-layer model + the import matrix as a new subsection.
4. **"Latest stable at scaffold time" is a stated policy.** Add a one-liner under Coding Conventions.
5. **Postgres does not run in Docker in any environment** — not dev, not prod. This is implicit in the constitution's network topology but worth restating under the "Infrastructure" notes so future contributors don't containerize it.
6. **Redis is always in Docker** — dev in compose, prod on VPS in compose. Add the counterpart statement.
7. **GitHub handle to confirm.** Memory assumes `josephhungkk` based on email prefix; confirm at impl time with `gh auth status`.
8. **Phase 0 touches no live services.** The 2026-04-17 deployment stays running; we do not migrate, seed, or re-deploy anything yet.
9. **"Dev machine" = NUC15PRO.** The constitution's project-paths section distinguishes "Dev machine (Windows)" from the VPS, which originally implied a separate Windows box. In reality dev happens on the NUC itself — Claude runs in WSL2 on the NUC; `C:\dashboard` is the NUC's own disk. The NUC is simultaneously: Postgres host, broker gateway host, light Ollama host, ops-glue host, and dev host. Rewrite the "Dev machine (Windows)" line to name the NUC explicitly.
10. **DB name separation.** Live deployment uses the `trading` DB. New rebuild uses `dashboard` on the same PG18 server. Document both in CLAUDE.md's infrastructure notes so future sessions don't confuse them.

---

## 13. Next step

After the user reviews this spec:

1. Resolve any changes requested.
2. Invoke `superpowers:writing-plans` to produce `docs/superpowers/plans/2026-04-21-phase0-scaffold-plan.md` — the step-by-step implementation plan that produces this scaffold.
3. Implementation work (not this session) executes the plan.

---

*End of spec.*
