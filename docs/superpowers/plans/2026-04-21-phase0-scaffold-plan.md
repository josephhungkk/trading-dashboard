# Phase 0 — Repo Scaffold & Local-Dev Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold `trading-dashboard` as a greenfield monorepo with a working local Docker stack (Redis-containerized, Postgres-native-on-NUC), a green GitHub Actions CI, and the component-architecture lint gates that every subsequent phase will rely on.

**Architecture:** Monorepo with `backend/` (FastAPI + uv + async SQLAlchemy + Alembic) and `frontend/` (React 19 + Vite + Tailwind v4 + shadcn/ui + Storybook 9). Five-layer frontend architecture enforced by `eslint-plugin-boundaries`. Tailwind-only rem units enforced by Stylelint. Conventional commits enforced by commitlint at commit-msg hook. CI runs backend + frontend jobs in parallel.

**Tech Stack:** Python 3.14, Node 24 LTS, pnpm (via Corepack), uv, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, structlog, Vite, React 19, TypeScript strict, Tailwind v4, shadcn/ui, Storybook 9, Vitest, React Testing Library, ESLint flat config, Stylelint, ruff, mypy, pytest, pytest-asyncio, httpx.

**Authoritative spec:** `docs/superpowers/specs/2026-04-21-phase0-scaffold-design.md`. Re-read when in doubt.

**Environment constraints:**
- Dev host: the NUC15PRO. Claude runs in WSL2 on the NUC. `/mnt/c/dashboard` === NUC's `C:\dashboard`.
- Postgres 18 is native Windows on the NUC. A **new** DB named `dashboard` must exist (separate from the live `trading` DB).
- Do NOT deploy to the IONOS VPS in Phase 0. Do NOT touch the live `trading` DB, its admin token, or the live deployment at `dashboard.kiusinghung.com`.

**Prerequisites check before starting:**
1. `gh auth status` → confirm logged in; note the handle (assumed `josephhungkk`).
2. `psql -h localhost -U trader -d postgres -c '\l'` → confirm `dashboard` DB exists (or create it with `createdb -U trader dashboard`).
3. `docker version` → confirm Docker Desktop is running in WSL integration mode.
4. `pnpm --version` fails? Run `corepack enable` first.
5. `uv --version` fails? `curl -LsSf https://astral.sh/uv/install.sh | sh`.

---

## Task 1: Initialize the repo with git + baseline files

**Files:**
- Create: `/mnt/c/dashboard/.gitignore`
- Create: `/mnt/c/dashboard/.gitattributes`
- Create: `/mnt/c/dashboard/README.md`
- Create: `/mnt/c/dashboard/CHANGELOG.md`
- Create: `/mnt/c/dashboard/TASKS.md`

- [ ] **Step 1.1: Verify cwd and emptiness**

Run:
```bash
cd /mnt/c/dashboard
pwd
ls -la
```
Expected: only `.remember/` and `.superpowers/` and `docs/` (from this brainstorming). No `.git/`, no `package.json`.

- [ ] **Step 1.2: `git init`**

Run:
```bash
cd /mnt/c/dashboard
git init -b main
```
Expected: `Initialized empty Git repository in /mnt/c/dashboard/.git/`.

- [ ] **Step 1.3: Create `.gitignore`**

Write `/mnt/c/dashboard/.gitignore`:
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
.remember/
```

- [ ] **Step 1.4: Create `.gitattributes`**

Write `/mnt/c/dashboard/.gitattributes`:
```
* text=auto eol=lf
*.ps1 text eol=crlf
pnpm-lock.yaml linguist-generated=true
uv.lock linguist-generated=true
```

(The CRLF rule for `.ps1` is from memory `ps1_nuc_bom_crlf.md`: deploy/nuc PowerShell scripts need CRLF + BOM or Windows PowerShell 5.1 parse errors on em-dashes.)

- [ ] **Step 1.5: Create `README.md`**

Write `/mnt/c/dashboard/README.md`:
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

- [ ] **Step 1.6: Create `CHANGELOG.md`**

Write `/mnt/c/dashboard/CHANGELOG.md`:
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

- [ ] **Step 1.7: Create `TASKS.md`**

Write `/mnt/c/dashboard/TASKS.md`:
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

- [ ] **Step 1.8: First commit**

Run:
```bash
cd /mnt/c/dashboard
git add .gitignore .gitattributes README.md CHANGELOG.md TASKS.md
git commit -m "chore: initialize repo with baseline docs"
```
Expected: commit created. No pre-commit hooks yet — they land in Task 23.

---

## Task 2: Commit the design spec + this plan

**Files:**
- Add: `/mnt/c/dashboard/docs/superpowers/specs/2026-04-21-phase0-scaffold-design.md` (already on disk)
- Add: `/mnt/c/dashboard/docs/superpowers/plans/2026-04-21-phase0-scaffold-plan.md` (this file)

- [ ] **Step 2.1: Stage and commit**

Run:
```bash
cd /mnt/c/dashboard
git add docs/superpowers/specs/ docs/superpowers/plans/
git commit -m "docs: phase 0 design spec and implementation plan"
```
Expected: commit created.

---

## Task 3: Bootstrap the backend (uv project)

**Files:**
- Create: `/mnt/c/dashboard/backend/pyproject.toml`
- Create: `/mnt/c/dashboard/backend/.python-version`
- Create: `/mnt/c/dashboard/backend/uv.lock` (generated)

- [ ] **Step 3.1: Create `backend/` and `cd` in**

Run:
```bash
mkdir -p /mnt/c/dashboard/backend
cd /mnt/c/dashboard/backend
```

- [ ] **Step 3.2: Initialize uv project pinned to Python 3.14**

Run:
```bash
cd /mnt/c/dashboard/backend
uv python install 3.14
uv python pin 3.14
uv init --python 3.14 --no-readme .
```
Expected: `pyproject.toml`, `.python-version`, and a `hello.py` (we'll delete) created. If uv says Python 3.14 is not yet available, fall back to `uv python install --preview 3.14` or use 3.13 and flag back to the user.

- [ ] **Step 3.3: Delete uv's boilerplate**

Run:
```bash
cd /mnt/c/dashboard/backend
rm -f hello.py
```

- [ ] **Step 3.4: Add runtime dependencies**

Run:
```bash
cd /mnt/c/dashboard/backend
uv add fastapi 'sqlalchemy[asyncio]' asyncpg alembic pydantic pydantic-settings structlog 'uvicorn[standard]' httpx
```

- [ ] **Step 3.5: Add dev dependencies**

Run:
```bash
cd /mnt/c/dashboard/backend
uv add --dev ruff mypy pytest pytest-asyncio pytest-cov
```

- [ ] **Step 3.6: Append tool configs to `pyproject.toml`**

Open `/mnt/c/dashboard/backend/pyproject.toml`. Append (or merge, if uv put similar keys) the following blocks:
```toml
[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "ASYNC", "RUF"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.14"
strict = true
warn_unused_ignores = true
warn_return_any = true
no_implicit_reexport = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3.7: Sync + verify**

Run:
```bash
cd /mnt/c/dashboard/backend
uv sync
uv run ruff --version
uv run mypy --version
uv run pytest --version
```
Expected: each command prints a version.

- [ ] **Step 3.8: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add backend/pyproject.toml backend/uv.lock backend/.python-version
git commit -m "feat(backend): uv project pinned to python 3.14 with ruff/mypy/pytest config"
```

---

## Task 4: Backend core modules (config, db, logging, deps)

**Files:**
- Create: `/mnt/c/dashboard/backend/app/__init__.py`
- Create: `/mnt/c/dashboard/backend/app/core/__init__.py`
- Create: `/mnt/c/dashboard/backend/app/core/config.py`
- Create: `/mnt/c/dashboard/backend/app/core/db.py`
- Create: `/mnt/c/dashboard/backend/app/core/logging.py`
- Create: `/mnt/c/dashboard/backend/app/core/deps.py`

- [ ] **Step 4.1: Create empty `__init__.py` files**

Run:
```bash
cd /mnt/c/dashboard/backend
mkdir -p app/core
touch app/__init__.py app/core/__init__.py
```

- [ ] **Step 4.2: Write `app/core/config.py`**

Write `/mnt/c/dashboard/backend/app/core/config.py`:
```python
"""Bootstrap config — only values needed before the DB is reachable.

DB-backed ConfigService (app_config/app_secrets) lands in Phase 2.
"""
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


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4.3: Write `app/core/db.py`**

Write `/mnt/c/dashboard/backend/app/core/db.py`:
```python
"""Async SQLAlchemy engine + session factory."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.postgres_pool_size,
    max_overflow=settings.postgres_max_overflow,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

- [ ] **Step 4.4: Write `app/core/logging.py`**

Write `/mnt/c/dashboard/backend/app/core/logging.py`:
```python
"""structlog configuration with a stub secret-redaction processor.

Phase 2 will expand the redaction patterns to match real broker/OAuth secrets.
"""
import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import settings

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"),
    re.compile(r"api_key=[^&\s]+"),
]


def _redact_secrets(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            redacted = value
            for pattern in _SECRET_PATTERNS:
                redacted = pattern.sub("[REDACTED]", redacted)
            event_dict[key] = redacted
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_secrets,
    ]
    if settings.env == "dev":
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

- [ ] **Step 4.5: Write `app/core/deps.py`**

Write `/mnt/c/dashboard/backend/app/core/deps.py`:
```python
"""FastAPI dependency providers.

Phase 0 only ships the DB session provider. Auth and config-service deps
are added in Phase 2.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 4.6: Ruff-check**

Run:
```bash
cd /mnt/c/dashboard/backend
uv run ruff check app/
uv run ruff format app/
```
Expected: clean.

- [ ] **Step 4.7: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add backend/app/
git commit -m "feat(backend): core modules (config, db, logging, deps)"
```

---

## Task 5: Backend /health endpoint (TDD)

**Files:**
- Create: `/mnt/c/dashboard/backend/tests/__init__.py`
- Create: `/mnt/c/dashboard/backend/tests/conftest.py`
- Create: `/mnt/c/dashboard/backend/tests/test_health.py`
- Create: `/mnt/c/dashboard/backend/app/main.py`

- [ ] **Step 5.1: Create tests package**

Run:
```bash
mkdir -p /mnt/c/dashboard/backend/tests
touch /mnt/c/dashboard/backend/tests/__init__.py
```

- [ ] **Step 5.2: Write `tests/conftest.py`**

Write `/mnt/c/dashboard/backend/tests/conftest.py`:
```python
"""Pytest fixtures."""
import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

# Env vars set before importing app (pydantic-settings reads at import time).
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-chars-ok")
os.environ.setdefault("APP_CORS_ORIGINS", '["http://localhost:5173"]')
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://trader:ci@localhost:5432/dashboard",
)
os.environ.setdefault("POSTGRES_POOL_SIZE", "2")
os.environ.setdefault("POSTGRES_MAX_OVERFLOW", "2")
os.environ.setdefault("REDIS_PASSWORD", "ci")
os.environ.setdefault("REDIS_URL", "redis://:ci@localhost:6379/0")

from app.main import app  # noqa: E402


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

- [ ] **Step 5.3: Write the failing health test**

Write `/mnt/c/dashboard/backend/tests/test_health.py`:
```python
"""Smoke test for the /health endpoint."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["env"] == "dev"
    assert body["db"] in {"ok", "unreachable"}
```

- [ ] **Step 5.4: Run the test — confirm it fails (no `app.main` yet)**

Run:
```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_health.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 5.5: Write `app/main.py` to make the test pass**

Write `/mnt/c/dashboard/backend/app/main.py`:
```python
"""FastAPI app entrypoint."""
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.core.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
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
async def health() -> dict[str, str]:
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
```

- [ ] **Step 5.6: Run the test — confirm it passes**

Run:
```bash
cd /mnt/c/dashboard/backend
uv run pytest tests/test_health.py -v
```
Expected: PASS. Tolerates `db: unreachable` (no PG needed for this test).

- [ ] **Step 5.7: Full lint + type check**

Run:
```bash
cd /mnt/c/dashboard/backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app/
```
Expected: all clean.

- [ ] **Step 5.8: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add backend/app/main.py backend/tests/
git commit -m "feat(backend): /health endpoint with db reachability probe"
```

---

## Task 6: Backend empty placeholder packages

**Files:**
- Create: `/mnt/c/dashboard/backend/app/api/__init__.py`
- Create: `/mnt/c/dashboard/backend/app/brokers/__init__.py`
- Create: `/mnt/c/dashboard/backend/app/models/__init__.py`
- Create: `/mnt/c/dashboard/backend/app/services/__init__.py`
- Create: `/mnt/c/dashboard/backend/app/ws/__init__.py`

- [ ] **Step 6.1: Create the empty packages**

Run:
```bash
cd /mnt/c/dashboard/backend
mkdir -p app/api app/brokers app/models app/services app/ws
for d in api brokers models services ws; do
  printf '"""Placeholder package. Real code lands in later phases."""\n' > "app/$d/__init__.py"
done
```

- [ ] **Step 6.2: Lint clean**

Run:
```bash
cd /mnt/c/dashboard/backend
uv run ruff check app/
```
Expected: 0 errors.

- [ ] **Step 6.3: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add backend/app/api backend/app/brokers backend/app/models backend/app/services backend/app/ws
git commit -m "chore(backend): reserve empty packages (api, brokers, models, services, ws)"
```

---

## Task 7: Backend Alembic init (async, no migrations yet)

**Files:**
- Create: `/mnt/c/dashboard/backend/alembic.ini`
- Create: `/mnt/c/dashboard/backend/alembic/env.py`
- Create: `/mnt/c/dashboard/backend/alembic/script.py.mako`
- Create: `/mnt/c/dashboard/backend/alembic/versions/.gitkeep`

- [ ] **Step 7.1: Run `alembic init` with async template**

Run:
```bash
cd /mnt/c/dashboard/backend
uv run alembic init -t async alembic
```
Expected: creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/README`, `alembic/versions/`.

- [ ] **Step 7.2: Edit `alembic/env.py` to read from our settings**

Open `/mnt/c/dashboard/backend/alembic/env.py`:

1. Near the top, after the existing imports, add:
```python
from app.core.config import settings
```

2. Find the line `config = context.config` and immediately below add:
```python
config.set_main_option("sqlalchemy.url", settings.database_url)
```

3. Find `target_metadata = None` and leave it as `None`. Add a comment above it:
```python
# No ORM metadata yet — first models land in Phase 2 (app_config, app_secrets).
target_metadata = None
```

- [ ] **Step 7.3: Keep `versions/` in git and drop unneeded README**

Run:
```bash
cd /mnt/c/dashboard/backend
touch alembic/versions/.gitkeep
rm -f alembic/README
```

- [ ] **Step 7.4: Smoke: `alembic current` imports our settings**

Run (the PG target can be nonexistent — this is just proving the config loads):
```bash
cd /mnt/c/dashboard/backend
APP_ENV=dev \
APP_SECRET_KEY=scratch-secret-key-32-chars-or-more \
APP_CORS_ORIGINS='["http://localhost:5173"]' \
DATABASE_URL='postgresql+asyncpg://trader:CHANGE-ME@host.docker.internal:5432/dashboard' \
POSTGRES_POOL_SIZE=2 \
POSTGRES_MAX_OVERFLOW=2 \
REDIS_PASSWORD=scratch \
REDIS_URL='redis://:scratch@localhost:6379/0' \
uv run alembic current || true
```
Expected: either prints nothing (no current) or prints a connection error — neither blocks the commit. What matters is that the alembic command loads our config module without ImportError.

- [ ] **Step 7.5: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add backend/alembic.ini backend/alembic/
git commit -m "feat(backend): alembic async init, no migrations yet"
```

---

## Task 8: Backend Dockerfile

**Files:**
- Create: `/mnt/c/dashboard/backend/Dockerfile`
- Create: `/mnt/c/dashboard/backend/.dockerignore`

- [ ] **Step 8.1: Write `backend/.dockerignore`**

Write `/mnt/c/dashboard/backend/.dockerignore`:
```
.venv
__pycache__
*.pyc
.pytest_cache
.ruff_cache
.mypy_cache
tests
.env
.env.*
```

- [ ] **Step 8.2: Write `backend/Dockerfile`**

Write `/mnt/c/dashboard/backend/Dockerfile`:
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

- [ ] **Step 8.3: Build the image**

Run:
```bash
cd /mnt/c/dashboard/backend
docker build -t trading-dashboard-backend:phase0 .
```
Expected: image builds. If `python:3.14-slim` isn't published yet, fall back to `python:3.13-slim` and flag to the user.

- [ ] **Step 8.4: Smoke-run the image**

Run:
```bash
docker run --rm \
  -e APP_ENV=dev \
  -e APP_SECRET_KEY=docker-smoke-secret-32-chars-long \
  -e APP_CORS_ORIGINS='["http://localhost:5173"]' \
  -e DATABASE_URL='postgresql+asyncpg://nouser:nopass@nohost:5432/nodb' \
  -e POSTGRES_POOL_SIZE=2 \
  -e POSTGRES_MAX_OVERFLOW=2 \
  -e REDIS_PASSWORD=x \
  -e REDIS_URL='redis://:x@localhost:6379/0' \
  -p 8000:8000 \
  --name tdb-backend-smoke \
  -d trading-dashboard-backend:phase0
sleep 3
curl -sf http://localhost:8000/health | tee /tmp/health.json
docker stop tdb-backend-smoke
```
Expected: `{"status":"ok","env":"dev","db":"unreachable"}` (DB unreachable is expected — we pointed at a nonexistent host).

- [ ] **Step 8.5: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add backend/Dockerfile backend/.dockerignore
git commit -m "feat(backend): dockerfile with uv + uvicorn"
```

---

## Task 9: Bootstrap the frontend (Vite + React 19 + TS strict)

**Files:**
- Create: `/mnt/c/dashboard/frontend/package.json` (scaffolded)
- Create: `/mnt/c/dashboard/frontend/tsconfig.json`
- Create: `/mnt/c/dashboard/frontend/tsconfig.node.json`
- Create: `/mnt/c/dashboard/frontend/vite.config.ts`
- Create: `/mnt/c/dashboard/frontend/index.html`
- Create: `/mnt/c/dashboard/frontend/src/main.tsx` (scaffolded)
- Create: `/mnt/c/dashboard/frontend/src/App.tsx` (scaffolded, rewritten in Task 15)
- Create: `/mnt/c/dashboard/frontend/.nvmrc`

- [ ] **Step 9.1: Enable Corepack + prepare pnpm**

Run:
```bash
corepack enable
corepack prepare pnpm@latest --activate
pnpm --version
```
Expected: pnpm version printed.

- [ ] **Step 9.2: Scaffold via `pnpm create vite`**

Run:
```bash
cd /mnt/c/dashboard
pnpm create vite frontend --template react-ts
```
Expected: non-interactive scaffold into `frontend/` using the `react-ts` template.

- [ ] **Step 9.3: Pin Node version**

Write `/mnt/c/dashboard/frontend/.nvmrc`:
```
24
```

- [ ] **Step 9.4: Upgrade to React 19 + latest TS/Vite**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add react@latest react-dom@latest
pnpm add -D @types/react@latest @types/react-dom@latest typescript@latest vite@latest @vitejs/plugin-react@latest
pnpm install
```
Expected: `package.json` now lists React 19.x.

- [ ] **Step 9.5: Replace `tsconfig.json` with a strict config**

Write `/mnt/c/dashboard/frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src", "vite.config.ts", "vitest.config.ts"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 9.6: Ensure `tsconfig.node.json` is correct**

Write `/mnt/c/dashboard/frontend/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

- [ ] **Step 9.7: Update `vite.config.ts` with `@` alias**

Write `/mnt/c/dashboard/frontend/vite.config.ts`:
```ts
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: { host: '0.0.0.0', port: 5173 },
});
```

- [ ] **Step 9.8: Delete Vite's default CSS + assets we'll replace**

Run:
```bash
cd /mnt/c/dashboard/frontend
rm -f src/App.css src/index.css
rm -rf src/assets
```

- [ ] **Step 9.9: Remove dangling CSS imports**

Open `/mnt/c/dashboard/frontend/src/App.tsx`. Delete any `import './App.css'` line.
Open `/mnt/c/dashboard/frontend/src/main.tsx`. Delete any `import './index.css'` line. (We'll wire `global.css` in Task 10.)

- [ ] **Step 9.10: Typecheck**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm exec tsc --noEmit
```
Expected: clean.

- [ ] **Step 9.11: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/package.json frontend/pnpm-lock.yaml frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/index.html frontend/src/ frontend/public/ frontend/.nvmrc
git commit -m "feat(frontend): vite + react 19 + typescript strict scaffold"
```

---

## Task 10: Frontend — Tailwind v4 + design tokens

**Files:**
- Modify: `/mnt/c/dashboard/frontend/package.json`
- Create: `/mnt/c/dashboard/frontend/src/styles/global.css`
- Create: `/mnt/c/dashboard/frontend/src/styles/tailwind.css`
- Create: `/mnt/c/dashboard/frontend/src/design-tokens/index.ts`
- Create: `/mnt/c/dashboard/frontend/src/design-tokens/spacing.ts`
- Create: `/mnt/c/dashboard/frontend/src/design-tokens/typography.ts`
- Create: `/mnt/c/dashboard/frontend/src/design-tokens/colors.ts`
- Create: `/mnt/c/dashboard/frontend/src/design-tokens/radii.ts`
- Create: `/mnt/c/dashboard/frontend/src/design-tokens/motion.ts`
- Create: `/mnt/c/dashboard/frontend/tailwind.config.ts`
- Create: `/mnt/c/dashboard/frontend/postcss.config.mjs`

- [ ] **Step 10.1: Install Tailwind v4**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add -D tailwindcss@latest @tailwindcss/postcss@latest postcss autoprefixer
pnpm why tailwindcss
```
Verify the printed major version is `4.x`. If not, force `@next` or pin explicitly.

- [ ] **Step 10.2: Write design tokens — `spacing.ts`**

Write `/mnt/c/dashboard/frontend/src/design-tokens/spacing.ts`:
```ts
export const space = {
  0: '0',
  px: '0.0625rem',
  1: '0.25rem',
  2: '0.5rem',
  3: '0.75rem',
  4: '1rem',
  6: '1.5rem',
  8: '2rem',
  12: '3rem',
  16: '4rem',
  24: '6rem',
} as const;
```

- [ ] **Step 10.3: Write `typography.ts`**

Write `/mnt/c/dashboard/frontend/src/design-tokens/typography.ts`:
```ts
export const fontFamily = {
  sans: ['"Noto Sans"', 'system-ui', 'sans-serif'],
  mono: ['"Noto Sans Mono"', 'ui-monospace', 'monospace'],
} as const;

export const fontSize = {
  xs: '0.75rem',
  sm: '0.875rem',
  base: '1rem',
  lg: '1.125rem',
  xl: '1.25rem',
  '2xl': '1.5rem',
  '3xl': '1.875rem',
  '4xl': '2.25rem',
} as const;

export const lineHeight = {
  tight: '1.2',
  normal: '1.5',
  relaxed: '1.75',
} as const;
```

- [ ] **Step 10.4: Write `colors.ts`**

Write `/mnt/c/dashboard/frontend/src/design-tokens/colors.ts`:
```ts
export const colors = {
  bg: 'hsl(0 0% 100%)',
  fg: 'hsl(222 15% 12%)',
  muted: 'hsl(210 10% 45%)',
  primary: 'hsl(217 91% 60%)',
  'primary-fg': 'hsl(0 0% 100%)',
  border: 'hsl(220 13% 91%)',
  destructive: 'hsl(0 72% 51%)',
  'destructive-fg': 'hsl(0 0% 100%)',
} as const;
```

- [ ] **Step 10.5: Write `radii.ts`**

Write `/mnt/c/dashboard/frontend/src/design-tokens/radii.ts`:
```ts
export const radius = {
  none: '0',
  sm: '0.25rem',
  md: '0.5rem',
  lg: '0.75rem',
  full: '9999rem',
} as const;
```

- [ ] **Step 10.6: Write `motion.ts`**

Write `/mnt/c/dashboard/frontend/src/design-tokens/motion.ts`:
```ts
export const duration = {
  fast: '120ms',
  normal: '200ms',
  slow: '320ms',
} as const;

export const easing = {
  standard: 'cubic-bezier(0.2, 0, 0, 1)',
  emphasis: 'cubic-bezier(0.3, 0, 0.2, 1)',
} as const;
```

- [ ] **Step 10.7: Write `index.ts` re-exports**

Write `/mnt/c/dashboard/frontend/src/design-tokens/index.ts`:
```ts
export * from './spacing';
export * from './typography';
export * from './colors';
export * from './radii';
export * from './motion';
```

- [ ] **Step 10.8: Write `src/styles/tailwind.css`**

Write `/mnt/c/dashboard/frontend/src/styles/tailwind.css`:
```css
@import "tailwindcss";
```

- [ ] **Step 10.9: Write `src/styles/global.css`**

Write `/mnt/c/dashboard/frontend/src/styles/global.css`:
```css
@import "./tailwind.css";

@theme {
  --color-bg: hsl(0 0% 100%);
  --color-fg: hsl(222 15% 12%);
  --color-muted: hsl(210 10% 45%);
  --color-primary: hsl(217 91% 60%);
  --color-primary-fg: hsl(0 0% 100%);
  --color-border: hsl(220 13% 91%);
  --color-destructive: hsl(0 72% 51%);
  --color-destructive-fg: hsl(0 0% 100%);

  --spacing-0: 0;
  --spacing-px: 0.0625rem;
  --spacing-1: 0.25rem;
  --spacing-2: 0.5rem;
  --spacing-3: 0.75rem;
  --spacing-4: 1rem;
  --spacing-6: 1.5rem;
  --spacing-8: 2rem;
  --spacing-12: 3rem;
  --spacing-16: 4rem;
  --spacing-24: 6rem;

  --radius-sm: 0.25rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;

  --font-sans: "Noto Sans", system-ui, sans-serif;
  --font-mono: "Noto Sans Mono", ui-monospace, monospace;
}

:root { color-scheme: light; }
html.dark { color-scheme: dark; }

body {
  background: var(--color-bg);
  color: var(--color-fg);
  font-family: var(--font-sans);
}
```

- [ ] **Step 10.10: Write `tailwind.config.ts`**

Write `/mnt/c/dashboard/frontend/tailwind.config.ts`:
```ts
import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  plugins: [],
};

export default config;
```

- [ ] **Step 10.11: Write `postcss.config.mjs`**

Write `/mnt/c/dashboard/frontend/postcss.config.mjs`:
```js
export default {
  plugins: { '@tailwindcss/postcss': {} },
};
```

- [ ] **Step 10.12: Wire `global.css` into `main.tsx`**

Open `/mnt/c/dashboard/frontend/src/main.tsx`. Replace contents with:
```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/global.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('root element not found');

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 10.13: Smoke build**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm exec tsc --noEmit
pnpm exec vite build
```
Expected: both succeed.

- [ ] **Step 10.14: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/
git commit -m "feat(frontend): tailwind v4 + design tokens (rem-only)"
```

---

## Task 11: Frontend — shadcn/ui init + Button primitive

**Files:**
- Create: `/mnt/c/dashboard/frontend/components.json`
- Create: `/mnt/c/dashboard/frontend/src/lib/utils.ts`
- Create: `/mnt/c/dashboard/frontend/src/components/primitives/Button/Button.tsx`
- Create: `/mnt/c/dashboard/frontend/src/components/primitives/Button/index.ts`

- [ ] **Step 11.1: Run shadcn init**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm dlx shadcn@latest init
```
Answer prompts:
- Style: **New York**
- Base color: **Neutral**
- Tailwind config: `tailwind.config.ts`
- Global CSS: `src/styles/global.css`
- CSS variables: **Yes**
- Import alias components: `@/components`
- Import alias utils: `@/lib/utils`
- React Server Components: **No**

- [ ] **Step 11.2: Override `components.json` aliases so primitives land in our layer**

Write `/mnt/c/dashboard/frontend/components.json` (replace whatever shadcn generated):
```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/styles/global.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/primitives",
    "lib": "@/lib",
    "hooks": "@/hooks"
  }
}
```

- [ ] **Step 11.3: Add the Button primitive**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm dlx shadcn@latest add button
```
Expected: creates `src/components/primitives/button.tsx` (flat, lowercase). shadcn also ensures `src/lib/utils.ts` exists.

- [ ] **Step 11.4: Reshape to folder convention**

Run:
```bash
cd /mnt/c/dashboard/frontend
mkdir -p src/components/primitives/Button
mv src/components/primitives/button.tsx src/components/primitives/Button/Button.tsx
```

- [ ] **Step 11.5: Write `Button/index.ts`**

Write `/mnt/c/dashboard/frontend/src/components/primitives/Button/index.ts`:
```ts
export { Button, buttonVariants } from './Button';
export type { ButtonProps } from './Button';
```

- [ ] **Step 11.6: Ensure `ButtonProps` is exported**

Open `/mnt/c/dashboard/frontend/src/components/primitives/Button/Button.tsx`. Verify it exports a `ButtonProps` type. If shadcn's generated code doesn't, add it. If the whole file needs to be rewritten to match our tokens, use this canonical shape:
```tsx
import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-fg hover:bg-primary/90',
        destructive: 'bg-destructive text-destructive-fg hover:bg-destructive/90',
        outline: 'border border-border bg-bg hover:bg-muted/10',
        ghost: 'hover:bg-muted/10',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        lg: 'h-12 px-6',
      },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
```

- [ ] **Step 11.7: Ensure `@/lib/utils.ts` is correct**

Verify `/mnt/c/dashboard/frontend/src/lib/utils.ts` contains:
```ts
import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
```
If missing, create it. If `clsx` or `tailwind-merge` aren't in `package.json`: `pnpm add clsx tailwind-merge`.

- [ ] **Step 11.8: Typecheck**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm exec tsc --noEmit
```
Expected: clean.

- [ ] **Step 11.9: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/components.json frontend/src/lib frontend/src/components/primitives/
git commit -m "feat(frontend): shadcn/ui init with primitives alias + Button"
```

---

## Task 12: Frontend — Vitest setup + Button tests + story

**Files:**
- Create: `/mnt/c/dashboard/frontend/vitest.config.ts`
- Create: `/mnt/c/dashboard/frontend/src/test/setup.ts`
- Create: `/mnt/c/dashboard/frontend/src/components/primitives/Button/Button.test.tsx`
- Create: `/mnt/c/dashboard/frontend/src/components/primitives/Button/Button.stories.tsx`
- Modify: `/mnt/c/dashboard/frontend/package.json`

- [ ] **Step 12.1: Install Vitest + RTL**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add -D vitest @vitest/coverage-v8 jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

- [ ] **Step 12.2: Write `vitest.config.ts`**

Write `/mnt/c/dashboard/frontend/vitest.config.ts`:
```ts
import path from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    coverage: { reporter: ['text', 'html'] },
  },
});
```

- [ ] **Step 12.3: Write `src/test/setup.ts`**

Run:
```bash
mkdir -p /mnt/c/dashboard/frontend/src/test
```

Write `/mnt/c/dashboard/frontend/src/test/setup.ts`:
```ts
import '@testing-library/jest-dom/vitest';
```

- [ ] **Step 12.4: Add scripts to `package.json`**

Open `/mnt/c/dashboard/frontend/package.json`. Merge these into the `scripts` block (preserve existing keys):
```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit"
  }
}
```

- [ ] **Step 12.5: Write `Button.test.tsx`**

Write `/mnt/c/dashboard/frontend/src/components/primitives/Button/Button.test.tsx`:
```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button } from './Button';

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument();
  });

  it('fires onClick when enabled', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await user.click(screen.getByRole('button', { name: 'Go' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not fire onClick when disabled', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button onClick={onClick} disabled>Go</Button>);
    await user.click(screen.getByRole('button', { name: 'Go' }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 12.6: Run the tests — confirm they pass**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm test
```
Expected: 3/3 PASS.

- [ ] **Step 12.7: Write `Button.stories.tsx`**

Write `/mnt/c/dashboard/frontend/src/components/primitives/Button/Button.stories.tsx`:
```tsx
import type { Meta, StoryObj } from '@storybook/react-vite';
import { Button } from './Button';

const meta = {
  title: 'Primitives/Button',
  component: Button,
  tags: ['autodocs'],
  argTypes: {
    variant: {
      control: 'select',
      options: ['default', 'destructive', 'outline', 'ghost'],
    },
    size: { control: 'select', options: ['sm', 'md', 'lg'] },
    disabled: { control: 'boolean' },
  },
  args: { children: 'Button' },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Destructive: Story = { args: { variant: 'destructive' } };
export const Outline: Story = { args: { variant: 'outline' } };
export const Ghost: Story = { args: { variant: 'ghost' } };
export const Disabled: Story = { args: { disabled: true } };
export const Small: Story = { args: { size: 'sm' } };
export const Large: Story = { args: { size: 'lg' } };
```

(TypeScript may complain about `@storybook/react-vite` not being found until Task 14 installs it. That's expected; skip verifying this file's types until Task 14.)

- [ ] **Step 12.8: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/
git commit -m "test(frontend): vitest setup + Button unit tests + Storybook story"
```

---

## Task 13: Frontend services (api, ws stub, lang stub)

**Files:**
- Create: `/mnt/c/dashboard/frontend/src/services/api.ts`
- Create: `/mnt/c/dashboard/frontend/src/services/ws.ts`
- Create: `/mnt/c/dashboard/frontend/src/services/lang.ts`

- [ ] **Step 13.1: Create services directory**

Run:
```bash
mkdir -p /mnt/c/dashboard/frontend/src/services
```

- [ ] **Step 13.2: Write `services/api.ts`**

Write `/mnt/c/dashboard/frontend/src/services/api.ts`:
```ts
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '';

export interface HealthResponse {
  status: string;
  env: string;
  db: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const r = await fetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return (await r.json()) as HealthResponse;
}
```

- [ ] **Step 13.3: Write `services/ws.ts` stub**

Write `/mnt/c/dashboard/frontend/src/services/ws.ts`:
```ts
/**
 * WebSocket client stub. Real connection logic lands in Phase 4
 * when the first broker adapter starts streaming quotes.
 */
export function connectWs(): null {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.info('[ws] stub — real connection lands in Phase 4');
  }
  return null;
}
```

- [ ] **Step 13.4: Write `services/lang.ts` stub**

Write `/mnt/c/dashboard/frontend/src/services/lang.ts`:
```ts
/**
 * Map an exchange code to the correct Noto CJK variant lang tag.
 * Phase 3 populates the real mapping; Phase 0 returns 'en' for everything
 * since no stock names render yet.
 */
export function langForMarket(_exchange: string): string {
  return 'en';
}
```

- [ ] **Step 13.5: Typecheck**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm typecheck
```
Expected: clean.

- [ ] **Step 13.6: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/src/services/
git commit -m "feat(frontend): service stubs (api, ws, lang)"
```

---

## Task 14: Frontend — Storybook 9 setup

**Files:**
- Create: `/mnt/c/dashboard/frontend/.storybook/main.ts`
- Create: `/mnt/c/dashboard/frontend/.storybook/preview.ts`
- Modify: `/mnt/c/dashboard/frontend/package.json`

- [ ] **Step 14.1: Run Storybook init**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm dlx storybook@latest init --yes --type react-vite --package-manager pnpm
```
Expected: adds Storybook devDeps, creates `.storybook/`, adds `storybook` + `build-storybook` scripts.

- [ ] **Step 14.2: Overwrite `.storybook/main.ts`**

Write `/mnt/c/dashboard/frontend/.storybook/main.ts`:
```ts
import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  addons: [
    '@storybook/addon-essentials',
    '@storybook/addon-a11y',
    '@storybook/addon-vitest',
  ],
  framework: { name: '@storybook/react-vite', options: {} },
  docs: { autodocs: 'tag' },
  typescript: { reactDocgen: 'react-docgen-typescript' },
};

export default config;
```

- [ ] **Step 14.3: Install addons if not already present**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add -D @storybook/addon-essentials @storybook/addon-a11y @storybook/addon-vitest
```

- [ ] **Step 14.4: Overwrite `.storybook/preview.ts`**

Write `/mnt/c/dashboard/frontend/.storybook/preview.ts`:
```ts
import type { Preview } from '@storybook/react-vite';
import '../src/styles/global.css';

const preview: Preview = {
  parameters: {
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
    viewport: {
      viewports: {
        mobile: { name: 'Mobile', styles: { width: '375px', height: '667px' } },
        tablet: { name: 'Tablet', styles: { width: '768px', height: '1024px' } },
        desktop: { name: 'Desktop', styles: { width: '1440px', height: '900px' } },
      },
    },
    backgrounds: {
      default: 'light',
      values: [
        { name: 'light', value: 'hsl(0 0% 100%)' },
        { name: 'dark', value: 'hsl(222 15% 12%)' },
      ],
    },
  },
};

export default preview;
```

- [ ] **Step 14.5: Delete auto-generated example stories**

Run:
```bash
cd /mnt/c/dashboard/frontend
rm -rf src/stories
```

- [ ] **Step 14.6: Verify Storybook builds**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm build-storybook
```
Expected: builds `storybook-static/` cleanly.

- [ ] **Step 14.7: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/.storybook/ frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): storybook 9 with a11y + viewports"
```

---

## Task 15: Frontend — App.tsx wired to /health + App.test.tsx

**Files:**
- Modify: `/mnt/c/dashboard/frontend/src/App.tsx`
- Create: `/mnt/c/dashboard/frontend/src/App.test.tsx`
- Modify: `/mnt/c/dashboard/frontend/index.html`

- [ ] **Step 15.1: Write the failing App test**

Write `/mnt/c/dashboard/frontend/src/App.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import App from './App';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('App', () => {
  it('shows "Backend: ok" when /health returns ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: 'ok', env: 'dev', db: 'ok' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/Backend: ok/)).toBeInTheDocument();
    });
  });

  it('shows "Backend: unreachable" when fetch rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')));
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/Backend: unreachable/)).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 15.2: Run tests — App tests fail**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm test
```
Expected: 2 App tests FAIL (default Vite scaffold shows "Vite + React"); 3 Button tests still PASS.

- [ ] **Step 15.3: Rewrite `src/App.tsx`**

Write `/mnt/c/dashboard/frontend/src/App.tsx`:
```tsx
import { useEffect, useState } from 'react';
import { Button } from '@/components/primitives/Button';
import { getHealth } from '@/services/api';

export default function App(): JSX.Element {
  const [status, setStatus] = useState<string>('…');

  useEffect(() => {
    getHealth()
      .then((r) => setStatus(r.status))
      .catch(() => setStatus('unreachable'));
  }, []);

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold">Trading Dashboard</h1>
        <p className="mt-2 text-sm opacity-70">Backend: {status}</p>
        <Button className="mt-4" onClick={() => location.reload()}>
          Recheck
        </Button>
      </div>
    </main>
  );
}
```

- [ ] **Step 15.4: Update `index.html` title**

Open `/mnt/c/dashboard/frontend/index.html`. Change `<title>...</title>` to `<title>Trading Dashboard</title>`. Ensure the body only contains `<div id="root"></div>` and `<script type="module" src="/src/main.tsx"></script>`.

- [ ] **Step 15.5: Run tests — all pass**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm test
```
Expected: all 5 tests PASS.

- [ ] **Step 15.6: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/
git commit -m "feat(frontend): App smoke UI fetching /health"
```

---

## Task 16: Frontend — Stylelint no-px enforcement

**Files:**
- Create: `/mnt/c/dashboard/frontend/stylelint.config.mjs`
- Modify: `/mnt/c/dashboard/frontend/package.json`

- [ ] **Step 16.1: Install Stylelint + plugins**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add -D stylelint stylelint-config-standard stylelint-config-clean-order postcss-html
```

- [ ] **Step 16.2: Write `stylelint.config.mjs`**

Write `/mnt/c/dashboard/frontend/stylelint.config.mjs`:
```js
export default {
  extends: ['stylelint-config-standard', 'stylelint-config-clean-order'],
  overrides: [
    {
      files: ['**/*.tsx'],
      customSyntax: 'postcss-html',
    },
  ],
  rules: {
    'unit-disallowed-list': ['px', 'em'],
    'declaration-property-unit-allowed-list': {
      '/^(width|height|min-.+|max-.+|margin.*|padding.*|gap|top|right|bottom|left|font-size|line-height|border-radius|inset.*)$/':
        ['rem', '%', 'vh', 'vw', 'fr', 'auto'],
    },
    'at-rule-no-unknown': [true, { ignoreAtRules: ['theme', 'tailwind', 'apply', 'layer', 'config'] }],
    'custom-property-pattern': null,
  },
};
```

- [ ] **Step 16.3: Add `stylelint` script**

In `/mnt/c/dashboard/frontend/package.json`, merge into scripts:
```json
{
  "scripts": {
    "stylelint": "stylelint \"src/**/*.{css,tsx}\""
  }
}
```

- [ ] **Step 16.4: Verify lint passes on current CSS**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm stylelint
```
Expected: 0 errors.

- [ ] **Step 16.5: Verify the rule blocks `px`**

Run:
```bash
cd /mnt/c/dashboard/frontend
printf '\n.tmp-check { padding: 4px; }\n' >> src/styles/global.css
pnpm stylelint; EXIT=$?
sed -i '/.tmp-check/d' src/styles/global.css
if [ $EXIT -ne 0 ]; then echo "CONFIRMED: stylelint rejected px"; else echo "FAIL: stylelint did not reject px"; exit 1; fi
```
Expected: `CONFIRMED: stylelint rejected px`. If `FAIL`, stop and debug the config.

- [ ] **Step 16.6: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/stylelint.config.mjs frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): stylelint blocks px/em site-wide"
```

---

## Task 17: Frontend — ESLint flat config with boundaries

**Files:**
- Create: `/mnt/c/dashboard/frontend/eslint.config.mjs`
- Modify: `/mnt/c/dashboard/frontend/package.json`

- [ ] **Step 17.1: Install ESLint + plugins**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add -D eslint@latest typescript-eslint@latest eslint-plugin-react@latest eslint-plugin-react-hooks@latest eslint-plugin-jsx-a11y@latest eslint-plugin-boundaries@latest globals@latest @eslint/js@latest
```

- [ ] **Step 17.2: Write `eslint.config.mjs`**

Write `/mnt/c/dashboard/frontend/eslint.config.mjs`:
```js
import js from '@eslint/js';
import tsEslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import boundaries from 'eslint-plugin-boundaries';
import globals from 'globals';

export default tsEslint.config(
  { ignores: ['dist', 'storybook-static', 'coverage', 'node_modules', '**/*.d.ts'] },
  js.configs.recommended,
  ...tsEslint.configs.strict,
  ...tsEslint.configs.stylistic,
  {
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
      boundaries,
    },
    settings: {
      react: { version: 'detect' },
      'boundaries/elements': [
        { type: 'tokens',     pattern: 'src/design-tokens/**' },
        { type: 'primitives', pattern: 'src/components/primitives/**' },
        { type: 'patterns',   pattern: 'src/components/patterns/**' },
        { type: 'layout',     pattern: 'src/components/layout/**' },
        { type: 'features',   pattern: 'src/features/**' },
        { type: 'services',   pattern: 'src/services/**' },
        { type: 'stores',     pattern: 'src/stores/**' },
        { type: 'hooks',      pattern: 'src/hooks/**' },
        { type: 'lib',        pattern: 'src/lib/**' },
        { type: 'app',        pattern: 'src/{App,main,vite-env}*' },
      ],
    },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      'react/react-in-jsx-scope': 'off',
      'boundaries/element-types': [
        'error',
        {
          default: 'disallow',
          rules: [
            { from: 'tokens',     allow: [] },
            { from: 'primitives', allow: ['tokens', 'lib'] },
            { from: 'patterns',   allow: ['tokens', 'primitives', 'patterns', 'lib'] },
            { from: 'layout',     allow: ['tokens', 'primitives', 'patterns', 'layout', 'lib'] },
            { from: 'features',   allow: ['tokens', 'primitives', 'patterns', 'layout', 'features', 'services', 'stores', 'hooks', 'lib'] },
            { from: 'services',   allow: ['lib'] },
            { from: 'stores',     allow: ['services', 'lib'] },
            { from: 'hooks',      allow: ['services', 'stores', 'lib'] },
            { from: 'lib',        allow: ['lib'] },
            { from: 'app',        allow: ['tokens', 'primitives', 'patterns', 'layout', 'features', 'services', 'stores', 'hooks', 'lib'] },
          ],
        },
      ],
    },
  },
  {
    files: ['**/*.stories.tsx', '**/*.test.tsx'],
    rules: { 'boundaries/element-types': 'off' },
  },
);
```

- [ ] **Step 17.3: Add `lint` script**

In `/mnt/c/dashboard/frontend/package.json`, merge into scripts:
```json
{
  "scripts": {
    "lint": "eslint src"
  }
}
```

- [ ] **Step 17.4: Run eslint clean**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm lint
```
Expected: 0 errors.

- [ ] **Step 17.5: Verify boundaries rule blocks bad imports**

Run:
```bash
cd /mnt/c/dashboard/frontend
mkdir -p src/features/fake
cat > src/features/fake/Fake.tsx <<'EOF'
import { Button } from '@/components/primitives/Button';
export default function Fake() { return <Button>ok</Button>; }
EOF
pnpm lint; FEATURE_RESULT=$?
cat > src/components/primitives/Button/poison.ts <<'EOF'
import Fake from '@/features/fake/Fake';
export { Fake };
EOF
pnpm lint; POISON_RESULT=$?
rm -rf src/features/fake src/components/primitives/Button/poison.ts
if [ "$FEATURE_RESULT" -eq 0 ] && [ "$POISON_RESULT" -ne 0 ]; then
  echo "CONFIRMED: boundaries rule works"
else
  echo "FAIL: boundaries rule misconfigured (feature→primitive=$FEATURE_RESULT, primitive→feature=$POISON_RESULT)"
  exit 1
fi
```
Expected: `CONFIRMED: boundaries rule works`. If `FAIL`, fix `eslint.config.mjs` before committing.

- [ ] **Step 17.6: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/eslint.config.mjs frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): eslint flat config with layer boundaries"
```

---

## Task 18: Frontend Dockerfile + nginx SPA stub

**Files:**
- Create: `/mnt/c/dashboard/frontend/Dockerfile`
- Create: `/mnt/c/dashboard/frontend/.dockerignore`
- Create: `/mnt/c/dashboard/nginx/spa.conf`

- [ ] **Step 18.1: Write `frontend/.dockerignore`**

Write `/mnt/c/dashboard/frontend/.dockerignore`:
```
node_modules
dist
storybook-static
coverage
.vite
.env
.env.*
```

- [ ] **Step 18.2: Write `nginx/spa.conf`**

Run:
```bash
mkdir -p /mnt/c/dashboard/nginx
```

Write `/mnt/c/dashboard/nginx/spa.conf`:
```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location ~* \.(?:css|js|svg|woff2?)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

- [ ] **Step 18.3: Write `frontend/Dockerfile`**

Write `/mnt/c/dashboard/frontend/Dockerfile`:
```dockerfile
# Phase 0: produces a static-file nginx image. Phase 0 docker-compose runs `pnpm dev`
# instead of this nginx image for local dev. Phase 1 wires nginx + LE certs on the VPS.
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
```

- [ ] **Step 18.4: Build the image**

Run:
```bash
cd /mnt/c/dashboard/frontend
docker build -t trading-dashboard-frontend:phase0 .
```
Expected: image builds.

- [ ] **Step 18.5: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add frontend/Dockerfile frontend/.dockerignore nginx/spa.conf
git commit -m "feat(frontend): multi-stage Dockerfile + nginx SPA config stub"
```

---

## Task 19: `docker-compose.yml` + `.env.example`

**Files:**
- Create: `/mnt/c/dashboard/docker-compose.yml`
- Create: `/mnt/c/dashboard/docker-compose.override.yml.example`
- Create: `/mnt/c/dashboard/.env.example`

- [ ] **Step 19.1: Write `docker-compose.yml`**

Write `/mnt/c/dashboard/docker-compose.yml`:
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
    build:
      context: ./frontend
      target: build
    command: ["pnpm", "dev", "--host", "0.0.0.0"]
    ports: ["5173:5173"]
    volumes:
      - "./frontend/src:/app/src"
      - "./frontend/public:/app/public"
    environment:
      VITE_API_URL: "http://localhost:8000"
```

- [ ] **Step 19.2: Write `docker-compose.override.yml.example`**

Write `/mnt/c/dashboard/docker-compose.override.yml.example`:
```yaml
# Copy to docker-compose.override.yml and tweak for your local setup.
# Common WSL quirk: Vite HMR misses filesystem events across the WSL boundary.
# See memory feedback_vite_wsl_restart.md
services:
  frontend:
    environment:
      WATCHPACK_POLLING: "true"
      CHOKIDAR_USEPOLLING: "true"
```

- [ ] **Step 19.3: Write `.env.example`**

Write `/mnt/c/dashboard/.env.example`:
```env
APP_ENV=dev
APP_SECRET_KEY=change-me-32-bytes-base64
APP_CORS_ORIGINS=["http://localhost:5173"]

# Postgres runs NATIVELY on the NUC15PRO — never in Docker.
# Dev happens on the NUC itself (Claude in WSL2), so:
#   Dev:  host.docker.internal:5432  (NUC's own PG18 reached from WSL Docker)
#   Prod: 10.10.0.2:5432             (same NUC PG18 reached from VPS via WireGuard)
# New rebuild uses DB name "dashboard" to coexist with the legacy "trading" DB on the same server.
DATABASE_URL=postgresql+asyncpg://trader:change-me@host.docker.internal:5432/dashboard
POSTGRES_POOL_SIZE=5
POSTGRES_MAX_OVERFLOW=10

# Redis — runs in Docker (compose locally; Phase 1 will run it on VPS).
# NOTE: keep the password URL-safe (no special characters that need escaping).
REDIS_PASSWORD=change-me-url-safe
REDIS_URL=redis://:change-me-url-safe@redis:6379/0
```

- [ ] **Step 19.4: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add docker-compose.yml docker-compose.override.yml.example .env.example
git commit -m "feat: local-dev docker-compose (redis + backend + frontend)"
```

---

## Task 20: Scripts (deploy.sh stub, gen-types.sh stub, dev.sh)

**Files:**
- Create: `/mnt/c/dashboard/scripts/deploy.sh`
- Create: `/mnt/c/dashboard/scripts/gen-types.sh`
- Create: `/mnt/c/dashboard/scripts/dev.sh`

- [ ] **Step 20.1: Create `scripts/` + write `deploy.sh` stub**

Run:
```bash
mkdir -p /mnt/c/dashboard/scripts
```

Write `/mnt/c/dashboard/scripts/deploy.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
echo "deploy.sh — not yet implemented."
echo "Phase 1 will wire this up to rsync + ssh to trader@88.208.197.219."
exit 1
```

Run:
```bash
chmod +x /mnt/c/dashboard/scripts/deploy.sh
```

- [ ] **Step 20.2: Write `gen-types.sh` stub**

Write `/mnt/c/dashboard/scripts/gen-types.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
echo "gen-types.sh — not yet implemented."
echo "Phase 2 will generate TS types from the FastAPI OpenAPI schema."
exit 1
```

Run:
```bash
chmod +x /mnt/c/dashboard/scripts/gen-types.sh
```

- [ ] **Step 20.3: Write `dev.sh`**

Write `/mnt/c/dashboard/scripts/dev.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d
docker compose logs -f backend frontend
```

Run:
```bash
chmod +x /mnt/c/dashboard/scripts/dev.sh
```

- [ ] **Step 20.4: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add scripts/
git commit -m "chore: deploy/gen-types stubs and dev.sh convenience"
```

---

## Task 21: Placeholder folders (bots, deploy/nuc, fonts, migrations symlink)

**Files:**
- Create: `/mnt/c/dashboard/bots/.gitkeep`
- Create: `/mnt/c/dashboard/deploy/nuc/README.md`
- Create: `/mnt/c/dashboard/migrations` (symlink or stub)
- Create: `/mnt/c/dashboard/frontend/public/fonts/.gitkeep`

- [ ] **Step 21.1: Reserve `bots/`**

Run:
```bash
mkdir -p /mnt/c/dashboard/bots
touch /mnt/c/dashboard/bots/.gitkeep
```

- [ ] **Step 21.2: Write `deploy/nuc/README.md`**

Run:
```bash
mkdir -p /mnt/c/dashboard/deploy/nuc
```

Write `/mnt/c/dashboard/deploy/nuc/README.md`:
```md
# deploy/nuc — Windows ops glue placeholder

This directory will hold PowerShell + VBS helpers for the NUC15PRO:

- Broker auto-start (IB Gateway × 4 accounts, FutuOpenD)
- TOTP fill + 2FA handling
- Window hider (hides broker GUIs from the desktop)
- Watchdog (every 5 min; restarts dead brokers)
- Tray app showing broker health
- Daily restart scheduler

Phase 0 leaves this empty. The legacy deployment has working versions at
`C:\dashboard\deploy\nuc\*` on the live tree — those stay untouched
during Phase 0–1. Rewrite or port into this repo during Phase 4+
as each broker lands.

See memory:
- `ps1_nuc_bom_crlf.md` — PS1 files must be UTF-8 BOM + CRLF.
- `feedback_ibc_gotchas.md` — IBC multi-account quirks.
- `powershell_whereobject_unroll.md` — `Where-Object` `.Count` trap.
```

- [ ] **Step 21.3: Create `migrations` — symlink on POSIX, stub dir otherwise**

Try symlink first:
```bash
cd /mnt/c/dashboard
ln -s backend/alembic/versions migrations 2>/dev/null && echo "symlink created" || echo "symlink failed"
```
If the symlink failed (Windows FS via WSL can refuse):
```bash
cd /mnt/c/dashboard
mkdir -p migrations
cat > migrations/README.md <<'EOF'
# migrations

This is a pointer. Real migrations live at `backend/alembic/versions/`.
The constitution's directory-layout section names `migrations/` conventionally;
on POSIX hosts this is a symlink, on Windows it's this README.
EOF
```

- [ ] **Step 21.4: Reserve `public/fonts/`**

Run:
```bash
mkdir -p /mnt/c/dashboard/frontend/public/fonts
touch /mnt/c/dashboard/frontend/public/fonts/.gitkeep
```

- [ ] **Step 21.5: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add bots/ deploy/ migrations frontend/public/fonts/
git commit -m "chore: reserve bots, deploy/nuc, migrations, fonts placeholders"
```

---

## Task 22: End-to-end local-stack smoke test (gate — no commits)

- [ ] **Step 22.1: Confirm `dashboard` DB exists on the NUC's Postgres**

Run:
```bash
psql -h localhost -U trader -d postgres -c '\l' | grep -E '^\s*dashboard\s' || echo "MISSING"
```
Expected: row for `dashboard`. If MISSING:
```bash
psql -h localhost -U trader -d postgres -c 'CREATE DATABASE dashboard OWNER trader;'
```

- [ ] **Step 22.2: Create real `.env` from example**

Run:
```bash
cd /mnt/c/dashboard
cp .env.example .env
```
Edit `/mnt/c/dashboard/.env`: replace all `change-me` placeholders with real values. `APP_SECRET_KEY` ≥ 32 chars. `REDIS_PASSWORD` must be URL-safe and identical in both `REDIS_PASSWORD=` and the embedded password inside `REDIS_URL=`.

- [ ] **Step 22.3: Build and start the stack**

Run:
```bash
cd /mnt/c/dashboard
docker compose up -d --build
sleep 5
docker compose ps
```
Expected: three containers `running` or `healthy`.

- [ ] **Step 22.4: Verify backend `/health`**

Run:
```bash
curl -sf http://localhost:8000/health | python3 -m json.tool
```
Expected:
```json
{
    "status": "ok",
    "env": "dev",
    "db": "ok"
}
```
If `db: unreachable`: check `host.docker.internal` resolution, `pg_hba.conf` WSL network ACLs, password in `.env`.

- [ ] **Step 22.5: Verify frontend in browser**

Open `http://localhost:5173`. Expected: "Trading Dashboard" heading, "Backend: ok" text, "Recheck" button.

- [ ] **Step 22.6: Verify Storybook**

Run in a second terminal:
```bash
cd /mnt/c/dashboard/frontend
pnpm storybook
```
Open `http://localhost:6006`. Expected: Button primitive visible with all variants. Ctrl-C to stop.

- [ ] **Step 22.7: Tear down**

Run:
```bash
cd /mnt/c/dashboard
docker compose down
```

- [ ] **Step 22.8: No commit — this is a gate**

If all checks pass, proceed to Task 23. If any fail, debug and re-run 22.3–22.6.

---

## Task 23: Pre-commit + commitlint

**Files:**
- Create: `/mnt/c/dashboard/.pre-commit-config.yaml`
- Create: `/mnt/c/dashboard/commitlint.config.cjs`
- Modify: `/mnt/c/dashboard/frontend/package.json`

- [ ] **Step 23.1: Install pre-commit on WSL**

Run:
```bash
pipx install pre-commit || pip install --user pre-commit
pre-commit --version
```

- [ ] **Step 23.2: Install commitlint**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm add -D @commitlint/cli @commitlint/config-conventional
```

- [ ] **Step 23.3: Write `commitlint.config.cjs`**

Write `/mnt/c/dashboard/commitlint.config.cjs`:
```js
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'refactor', 'docs', 'test', 'chore', 'perf', 'ci'],
    ],
  },
};
```

- [ ] **Step 23.4: Write `.pre-commit-config.yaml`**

Write `/mnt/c/dashboard/.pre-commit-config.yaml`:
```yaml
# Revs pinned by `pre-commit autoupdate` at impl time.
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.0.0
    hooks:
      - id: ruff
        args: [--fix]
        files: ^backend/
      - id: ruff-format
        files: ^backend/
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v0.0.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic, sqlalchemy, pydantic-settings]
        files: ^backend/app/
  - repo: local
    hooks:
      - id: eslint
        name: eslint
        entry: bash -c 'cd frontend && pnpm lint --fix'
        language: system
        files: ^frontend/.*\.(ts|tsx|js|jsx|mjs|cjs)$
        pass_filenames: false
      - id: stylelint
        name: stylelint
        entry: bash -c 'cd frontend && pnpm stylelint --fix'
        language: system
        files: ^frontend/.*\.(css|tsx)$
        pass_filenames: false
      - id: commitlint
        name: commitlint
        stages: [commit-msg]
        entry: bash -c 'cd frontend && pnpm exec commitlint --config ../commitlint.config.cjs --edit "$1"'
        language: system
```

- [ ] **Step 23.5: Pin real revs**

Run:
```bash
cd /mnt/c/dashboard
pre-commit autoupdate
```
Expected: `.pre-commit-config.yaml` gets real `rev:` values.

- [ ] **Step 23.6: Install the hooks**

Run:
```bash
cd /mnt/c/dashboard
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

- [ ] **Step 23.7: Run all hooks**

Run:
```bash
cd /mnt/c/dashboard
pre-commit run --all-files
```
Expected: all hooks pass. Fix any issues they find before proceeding.

- [ ] **Step 23.8: Verify commitlint rejects bad messages**

Run:
```bash
cd /mnt/c/dashboard
echo '' >> CHANGELOG.md
git add CHANGELOG.md
git commit -m 'this is not conventional' && echo "FAIL: commitlint did not reject" || echo "CONFIRMED: commitlint rejected bad message"
git checkout CHANGELOG.md
```
Expected: `CONFIRMED: commitlint rejected bad message`.

- [ ] **Step 23.9: Commit hook config**

Run:
```bash
cd /mnt/c/dashboard
git add .pre-commit-config.yaml commitlint.config.cjs frontend/package.json frontend/pnpm-lock.yaml
git commit -m "chore: pre-commit hooks and commitlint for conventional commits"
```

---

## Task 24: GitHub Actions CI

**Files:**
- Create: `/mnt/c/dashboard/.github/workflows/ci.yml`

- [ ] **Step 24.1: Write the workflow**

Run:
```bash
mkdir -p /mnt/c/dashboard/.github/workflows
```

Write `/mnt/c/dashboard/.github/workflows/ci.yml`:
```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:18-alpine
        env:
          POSTGRES_USER: trader
          POSTGRES_PASSWORD: ci
          POSTGRES_DB: dashboard
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U trader"
          --health-interval 5s
          --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: '3.14'
      - name: Install backend deps
        working-directory: backend
        run: uv sync --frozen
      - name: Ruff check
        working-directory: backend
        run: uv run ruff check .
      - name: Ruff format check
        working-directory: backend
        run: uv run ruff format --check .
      - name: Mypy
        working-directory: backend
        run: uv run mypy app/
      - name: Pytest
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://trader:ci@localhost:5432/dashboard
          APP_SECRET_KEY: ci-secret-key-32-chars-minimum-req
          APP_ENV: dev
          APP_CORS_ORIGINS: '["http://localhost:5173"]'
          POSTGRES_POOL_SIZE: '2'
          POSTGRES_MAX_OVERFLOW: '2'
          REDIS_PASSWORD: ci
          REDIS_URL: redis://:ci@localhost:6379/0
        run: uv run pytest --cov=app --cov-report=term-missing
      - name: Docker build (main only)
        if: github.ref == 'refs/heads/main'
        working-directory: backend
        run: docker build -t trading-dashboard-backend:ci .

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '24'
          cache: 'pnpm'
      - name: Enable Corepack
        run: corepack enable
      - name: Install frontend deps
        working-directory: frontend
        run: pnpm install --frozen-lockfile
      - name: Lint
        working-directory: frontend
        run: pnpm lint
      - name: Stylelint
        working-directory: frontend
        run: pnpm stylelint
      - name: Typecheck
        working-directory: frontend
        run: pnpm typecheck
      - name: Tests
        working-directory: frontend
        run: pnpm test
      - name: Build
        working-directory: frontend
        run: pnpm build
      - name: Storybook build
        working-directory: frontend
        run: pnpm build-storybook
      - name: Docker build (main only)
        if: github.ref == 'refs/heads/main'
        working-directory: frontend
        run: docker build -t trading-dashboard-frontend:ci .
```

- [ ] **Step 24.2: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add .github/workflows/ci.yml
git commit -m "ci: github actions for backend and frontend"
```

---

## Task 25: `.vscode/settings.json` (shared editor baseline)

**Files:**
- Create: `/mnt/c/dashboard/.vscode/settings.json`

- [ ] **Step 25.1: Write the settings**

Run:
```bash
mkdir -p /mnt/c/dashboard/.vscode
```

Write `/mnt/c/dashboard/.vscode/settings.json`:
```json
{
  "editor.formatOnSave": true,
  "editor.codeActionsOnSave": {
    "source.fixAll.eslint": "explicit",
    "source.fixAll.stylelint": "explicit"
  },
  "[python]": { "editor.defaultFormatter": "charliermarsh.ruff" },
  "[typescript]": { "editor.defaultFormatter": "dbaeumer.vscode-eslint" },
  "[typescriptreact]": { "editor.defaultFormatter": "dbaeumer.vscode-eslint" },
  "python.defaultInterpreterPath": "backend/.venv/bin/python",
  "eslint.workingDirectories": [{ "directory": "frontend", "changeProcessCWD": true }],
  "stylelint.snippet": ["css", "tsx"],
  "stylelint.validate": ["css", "tsx"]
}
```

- [ ] **Step 25.2: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add .vscode/
git commit -m "chore: vscode workspace settings"
```

---

## Task 26: Write CLAUDE.md (constitution with deltas applied)

**Files:**
- Create: `/mnt/c/dashboard/CLAUDE.md`

- [ ] **Step 26.1: Write the full CLAUDE.md**

Write `/mnt/c/dashboard/CLAUDE.md`:
```md
# Trading Dashboard — Project Constitution

This file tells Claude Code how this project is structured, what conventions to follow, and which commands to use. Keep it current as the project evolves.

## Project Overview

A self-hosted multi-broker, multi-account trading dashboard. The dashboard should be able to trade common assets: Stocks, Forex, Commodities, Indexes, Bonds, ETF, Futures, Crypto, CFD, Options, Derivatives.

The frontend and backend run on an IONOS VPS behind Cloudflare. Broker gateways (IB Gateway, FutuOpenD) and PostgreSQL 18 run on the NUC15PRO and reach the VPS over WireGuard. A second home machine (RTX 4080 16 GB VRAM + 64 GB RAM) runs heavy Ollama models on demand.

## Stack

### Runtimes
- **Backend:** Python **3.14** + FastAPI (latest stable) + SQLAlchemy 2.0 async + Alembic + Pydantic v2 + asyncpg
- **Frontend:** **React 19** + **Vite** + **TypeScript strict**
- **Styling:** **Tailwind v4** (CSS-first `@theme`) + **shadcn/ui** primitives (owned in-repo)
- **State:** **Zustand** for global; `useState` for local
- **Charting:** klineschart (Phase 3+)
- **Component workbench:** **Storybook 9**
- **Testing:** Vitest + React Testing Library (frontend); pytest + pytest-asyncio + httpx (backend); Playwright deferred to Phase 5+
- **Cache / pubsub:** Redis 7 (always containerized — dev and prod)
- **Database:** PostgreSQL 18 running **natively on Windows on the NUC15PRO** (never containerized). Dev uses DB `dashboard`; the legacy Phase-1 deployment continues to use DB `trading` on the same server.
- **Broker adapters:** `ib_async` (IBKR), `futu-api` (Futu HK), `requests-oauthlib` (Schwab)
- **AI:** Ollama — 7-8B models on NUC, 14-70B on the heavy box (WoL-woken on demand)
- **Orchestration:** Docker Compose (`docker-compose.yml` locally; Phase 1 adds `docker-compose.prod.yml`)
- **Reverse proxy:** Nginx with Let's Encrypt DNS-01 via Cloudflare (Phase 1+, on the VPS)

### Fonts
Noto only. Self-hosted `.woff2` in `frontend/public/fonts/` — 3 Latin weights + 5 CJK regional variants (TC, SC, HK, JP, KR). Loaded via `@font-face` with `unicode-range`, variants selected via `lang` attributes. When rendering stock names always set `lang` via `langForMarket(exchange)` from `src/services/lang.ts` so the correct CJK glyph variant renders. Bold CJK is synthesized via CSS `font-synthesis` for MVP. (Phase 0 ships the stub; Phase 3 ships the real fonts and mapping.)

### Mobile
Mobile-first responsive design. Bottom tab bar on mobile (one-thumb reach), sidebar on desktop. Minimum 44×44 px equivalent (2.75 rem) touch targets. Tables collapse to card view below the `md` breakpoint.

### Versioning policy
**Latest stable at scaffold time.** Pin via lockfiles (`uv.lock`, `pnpm-lock.yaml`), never hand-pinned semver ranges.

## Component Architecture (Frontend)

Five layers with one-way dependencies. Enforced by `eslint-plugin-boundaries`; violations break CI.

| Layer | Path | May import from |
|---|---|---|
| `design-tokens/` | `src/design-tokens/**` | — (leaf) |
| `components/primitives/` | `src/components/primitives/**` | tokens, lib |
| `components/patterns/` | `src/components/patterns/**` | tokens, primitives, patterns, lib |
| `components/layout/` | `src/components/layout/**` | tokens, primitives, patterns, layout, lib |
| `features/` | `src/features/**` | everything |

Rules:
1. `design-tokens/` is the **only** place rem values, color hex codes, and font stacks are defined as raw literals. Everything else references tokens (usually via Tailwind classes).
2. Primitives and patterns have `Component.stories.tsx` and `Component.test.tsx` alongside them. Features do not — they're tested end-to-end.
3. No `px` or `em` anywhere on the site. Stylelint `unit-disallowed-list` enforces this.

## Configuration Storage

**The app keeps runtime settings in the database, not in `.env`.** (Phase 2+; Phase 0 has no DB-backed config yet.)

`.env` only holds bootstrap values the app needs before it can reach the DB:
`APP_ENV`, `APP_SECRET_KEY`, `APP_CORS_ORIGINS`, `DATABASE_URL`, `POSTGRES_POOL_SIZE`, `POSTGRES_MAX_OVERFLOW`, `REDIS_PASSWORD`, `REDIS_URL`.

(`POSTGRES_POOL_SIZE` / `POSTGRES_MAX_OVERFLOW` are here — not in `app_config` — because SQLAlchemy reads them at engine construction, which runs before `ConfigService` can reach the DB. `REDIS_PASSWORD` is split out from `REDIS_URL` so docker-compose can interpolate it into `redis-server --requirepass ${REDIS_PASSWORD}`.)

Everything else (broker hosts, Ollama URLs, Telegram tokens, API keys, WoL MAC, Schwab OAuth, etc.) will live in two tables from Phase 2 onward:

- `app_config` — plain-text settings, readable by any admin-authed client
- `app_secrets` — sensitive values encrypted with Fernet (key derived from `APP_SECRET_KEY`)

Both will be edited at runtime via `/api/admin/config` and `/api/admin/secrets`, or programmatically through `app.services.config.config.set()` / `set_secret()`. An in-memory cache is invalidated across all backend workers via Redis pub/sub on every write, so changes take effect immediately.

**Do not add new values to `.env` beyond the bootstrap list.** When writing code that needs a setting, read it via the `config` service (from Phase 2 onward) and fall back to a sensible default:

    from app.services.config import config
    heavy_url = await config.get("ollama.heavy_url", "http://10.10.0.3:11434")
    bot_token = await config.get_secret("telegram.bot_token")

Rotating `APP_SECRET_KEY` invalidates all encrypted secrets — treat it as permanent.

## Network Topology

| Node | Role | LAN IP | WG IP |
|------|------|--------|-------|
| IONOS VPS | Prod HTTP host (Phase 1+) | 88.208.197.219 | 10.10.0.1 |
| NUC15PRO | **Dev host + broker gateways + Postgres + light Ollama (24/7)** | 192.168.50.20 | 10.10.0.2 |
| Heavy AI box | Large Ollama + ML training (on-demand, WoL) | 192.168.50.30 | 10.10.0.3 |
| Router | | 192.168.50.1 | 10.10.0.254 |

**The NUC is the dev host.** Claude Code runs in WSL2 on the NUC; `/mnt/c/dashboard` is the NUC's own `C:\dashboard`. There is no separate Windows dev box.

SSH to VPS: `ssh -p 2222 trader@88.208.197.219` (key in `.ssh/`).

## Project Paths

- **NUC (dev host):** `C:\dashboard` — where `claude`, `pnpm dev`, `docker compose`, and (Phase 1+) `scripts/deploy.sh` all run. Also reachable from WSL as `/mnt/c/dashboard`.
- **VPS (prod host):** `/home/trader/trading-dashboard` — the rsync destination for Phase 1+. Docker Compose runs here for the prod stack.

Both trees contain the same repo. Deploy script rsyncs from the NUC to the VPS.

### Third-party services live OUTSIDE the repo

Installed on the NUC via their own installers:

| Service | NUC path | Why not in the repo |
|---------|----------|---------------------|
| IB Gateway | `C:\Jts\ibgateway\<version>\` | Third-party binary with its own updater |
| FutuOpenD | `C:\FutuOpenD\` | Third-party binary, login credentials in config |
| PostgreSQL 18 | `C:\Program Files\PostgreSQL\18\` | Windows service, own data dir |
| Ollama | `%LOCALAPPDATA%\Programs\Ollama\` | Auto-updating binary + model cache |

Ops glue (PowerShell + VBS helpers for broker auto-start, TOTP fill, window hiding, watchdog, daily restart) runs directly from the repo at `C:\dashboard\deploy\nuc\*`. Scheduled tasks reference those paths. Not part of the Docker build and not rsync'd to the VPS.

## Directory Layout

See `docs/superpowers/specs/2026-04-21-phase0-scaffold-design.md §3` for the canonical tree.

## Coding Conventions

### Python (backend)
- Python 3.14, type hints everywhere, Pydantic v2 for all I/O models.
- Async all the way down — no sync DB calls, use `asyncpg` driver.
- One adapter per broker file in `app/brokers/`. All adapters subclass `BrokerAdapter` in `base.py` (base lands in Phase 4 with the first concrete adapter — not speculatively earlier).
- When adding a method to `BrokerAdapter`, update every adapter in the same commit.
- Use dependency injection via FastAPI's `Depends` — never import singletons directly.
- Log via `structlog`, never `print`.
- No bare `except:` — always name the exception class.
- Lint: `ruff` (rules `E,F,W,I,N,UP,B,A,C4,ASYNC,RUF`). Format: `ruff format`. Types: `mypy --strict` on `app/`.

### TypeScript (frontend)
- Strict mode on. No `any` unless annotated with `// eslint-disable-next-line` and a comment explaining why.
- Function components only, no class components.
- Zustand for global state. Component-local state stays in `useState`.
- API calls go through `services/api.ts` — never `fetch` directly from a component.
- WebSocket subscriptions happen in `services/ws.ts` and feed Zustand stores.
- No `px` or `em` in CSS or inline styles. Only rem, %, vh/vw, fr, auto. Enforced by Stylelint `unit-disallowed-list`.
- Layer imports enforced by `eslint-plugin-boundaries` (see Component Architecture above).

### SQL
- All schema changes go through Alembic migrations. Never edit the DB manually.
- Column names: `snake_case`. Table names: plural `snake_case` (`orders`, `portfolio_snapshots`).
- Timestamps: `created_at`, `updated_at`, always `TIMESTAMPTZ`.
- Money: `NUMERIC(20, 8)`. Never `FLOAT` or `REAL`.

### Git
- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`). Enforced by commitlint at `commit-msg`.
- Feature branches off `main`. Squash-merge PRs.
- Never commit `.env`, `*.key`, or anything in `secrets/`.

## Common Commands

    # Local dev (on the NUC in WSL)
    docker compose up -d                              # Start full stack
    docker compose logs -f backend                    # Tail backend logs
    docker compose exec backend alembic upgrade head  # Run migrations (Phase 2+)
    docker compose exec backend pytest                # Run tests

    # Frontend dev (hot reload)
    cd frontend && pnpm dev

    # Backend dev (hot reload outside docker)
    cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

    # Storybook
    cd frontend && pnpm storybook

    # Lint
    cd frontend && pnpm lint && pnpm stylelint && pnpm typecheck
    cd backend && uv run ruff check . && uv run mypy app/

    # Database (Phase 2+)
    docker compose exec backend alembic revision --autogenerate -m "add_alerts_table"
    docker compose exec backend alembic upgrade head

    # Deploy to VPS (Phase 1+)
    ./scripts/deploy.sh

## Security Rules

- Never log API keys, OAuth tokens, or passwords — `structlog` redacts via a processor in `app/core/logging.py`.
- All broker credentials live in `app_secrets` (Fernet-encrypted) from Phase 2 onward, never in git.
- Postgres is only reachable via WireGuard from the VPS and directly on LAN/WSL from the NUC — never exposed publicly.
- Frontend never sees broker credentials. Only the backend holds them.
- Trade execution endpoints require a confirmation token (nonce) to prevent CSRF.

## Non-Goals (for now)

- Mobile native apps (responsive web UI only)
- Paper trading simulation (use broker-side paper accounts)

## When Claude Code Makes Changes

- Always run tests after edits: `docker compose exec backend pytest` and `cd frontend && pnpm test`.
- Always regenerate types when changing API schemas: see `scripts/gen-types.sh` (Phase 2+).
- Never modify `brokers/base.py` without also updating every concrete adapter.
- Prefer editing existing files over creating new ones.
- For schema changes, generate an Alembic migration instead of editing models and hoping for the best.

Use `/frontend-design` skill to design the frontend; discuss direction with the user.

The frontend supports both desktop and mobile. Rem-based CSS only, no px anywhere.

GitHub is the canonical repo. Update `CLAUDE.md`, `CHANGELOG.md`, `TASKS.md` on every phase completion; commit to repo.
```

- [ ] **Step 26.2: Lint-check the markdown**

Run:
```bash
cd /mnt/c/dashboard
grep -c '^##' CLAUDE.md
```
Expected: ≥ 10.

- [ ] **Step 26.3: Commit**

Run:
```bash
cd /mnt/c/dashboard
git add CLAUDE.md
git commit -m "docs: CLAUDE.md constitution updated for phase 0"
```

---

## Task 27: Create GitHub remote + push + tag v0.0.1

**⚠️ Confirm user intent before creating a remote repo** — this is a cross-system action.

- [ ] **Step 27.1: Confirm gh handle**

Run:
```bash
gh auth status
```
Note the username. If not `josephhungkk`, confirm with the user which handle to use before the next step.

- [ ] **Step 27.2: Create the remote**

Run (substitute handle from 27.1):
```bash
cd /mnt/c/dashboard
gh repo create josephhungkk/trading-dashboard --private --source=. --description "Self-hosted multi-broker trading dashboard"
```
Expected: remote created; `origin` added.

- [ ] **Step 27.3: Push main**

Run:
```bash
cd /mnt/c/dashboard
git push -u origin main
```
Expected: push succeeds.

- [ ] **Step 27.4: Tag v0.0.1**

Run:
```bash
cd /mnt/c/dashboard
git tag -a v0.0.1 -m "Phase 0 scaffold complete"
git push origin v0.0.1
```

- [ ] **Step 27.5: Watch CI**

Run:
```bash
cd /mnt/c/dashboard
gh run watch
```
Expected: both `backend` and `frontend` jobs succeed. If either fails, fix in a new commit (`fix(ci): …`) and re-push.

---

## Task 28: Smoke-PR to prove the PR→CI→merge loop

**Files:**
- Modify: `/mnt/c/dashboard/CHANGELOG.md`

- [ ] **Step 28.1: Create branch + trivial edit**

Run:
```bash
cd /mnt/c/dashboard
git checkout -b chore/smoke-pr-ci
```

Open `/mnt/c/dashboard/CHANGELOG.md`. Under `## [Unreleased]` add:
```
### Changed
- Smoke-test PR confirming CI loop works.
```

- [ ] **Step 28.2: Commit + push**

Run:
```bash
cd /mnt/c/dashboard
git add CHANGELOG.md
git commit -m "chore: smoke-test PR for ci loop"
git push -u origin chore/smoke-pr-ci
```

- [ ] **Step 28.3: Open PR**

Run:
```bash
cd /mnt/c/dashboard
gh pr create --title "chore: smoke-test PR for ci loop" --body "Trivial CHANGELOG update to confirm Phase 0 CI turns green on a PR against main."
```

- [ ] **Step 28.4: Wait for CI green**

Run:
```bash
cd /mnt/c/dashboard
gh pr checks --watch
```

- [ ] **Step 28.5: Merge (squash) + pull main**

Run:
```bash
cd /mnt/c/dashboard
gh pr merge --squash --delete-branch
git checkout main
git pull --ff-only origin main
```

---

## Task 29: Final verification against spec §10 success criteria

- [ ] **Step 29.1: Clean-slate restart of local stack**

Run:
```bash
cd /mnt/c/dashboard
docker compose down -v
docker compose up -d --build
sleep 10
curl -sf http://localhost:8000/health
```
Expected: `{"status":"ok","env":"dev","db":"ok"}`.

- [ ] **Step 29.2: Frontend browser check**

Open `http://localhost:5173`. Manual verify: "Trading Dashboard", "Backend: ok", "Recheck" button.

- [ ] **Step 29.3: Storybook check**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm storybook
```
Open `http://localhost:6006`. Manual verify: Button primitive visible. Ctrl-C to stop.

- [ ] **Step 29.4: Full lint + test sweep**

Run:
```bash
cd /mnt/c/dashboard/frontend
pnpm lint && pnpm stylelint && pnpm typecheck && pnpm test && pnpm build && pnpm build-storybook
cd /mnt/c/dashboard/backend
uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest
```
Expected: every command exits 0.

- [ ] **Step 29.5: Tick the spec's §10 checklist**

Open `docs/superpowers/specs/2026-04-21-phase0-scaffold-design.md` §10. Confirm each of the 11 items is satisfied.

- [ ] **Step 29.6: Update TASKS.md to mark Phase 0 complete**

Open `/mnt/c/dashboard/TASKS.md`. Change `Phase 0 — Repo scaffold & local-dev loop  *(in progress)*` to `Phase 0 — Repo scaffold & local-dev loop  *(complete — v0.0.1)*` and flip every `- [ ]` under Phase 0 to `- [x]`.

- [ ] **Step 29.7: Commit TASKS.md and push**

Run:
```bash
cd /mnt/c/dashboard
git add TASKS.md
git commit -m "docs: mark phase 0 complete in TASKS.md"
git push origin main
```

- [ ] **Step 29.8: Tear down and wrap**

Run:
```bash
cd /mnt/c/dashboard
docker compose down
```

Report back to the user:
- Repo URL.
- Last CI run URL.
- All 11 §10 success criteria ticked.
- Next: Phase 1 — VPS infra skeleton.

---

## Appendix A — Common failure modes & fixes

**`uv` says Python 3.14 isn't available.** `uv python install --preview 3.14`, or fall back to 3.13 and flag to the user. Update `pyproject.toml` and `Dockerfile` accordingly.

**`host.docker.internal` doesn't resolve from the backend container on Linux/WSL.** Already handled by `extra_hosts: ["host.docker.internal:host-gateway"]` in `docker-compose.yml`. If still failing, check Docker Desktop's "WSL integration" setting.

**Postgres connection refused from WSL Docker.** Check `pg_hba.conf` on the NUC — it needs a `host all trader 172.16.0.0/12 md5` row (or wider) to accept Docker's bridge network. After editing, restart the Postgres Windows service.

**Redis password has `@` or `:` in it.** Per memory `feedback_deploy.md`, keep Redis passwords URL-safe. If you must keep special chars, URL-encode the password in `REDIS_URL` (`@` → `%40`, `:` → `%3A`).

**Stylelint not lint-checking `.tsx` files.** Confirm `postcss-html` is installed and the override block in `stylelint.config.mjs` applies `customSyntax: 'postcss-html'` to `**/*.tsx`.

**ESLint `boundaries` false positive on a legitimate import.** Confirm `settings['boundaries/elements']` glob patterns match the actual file paths. Run `pnpm lint -- --debug` to see which element type each file is classified as.

**Pre-commit hook slow first run.** `pre-commit run --all-files` is slow initially because it caches environments. Subsequent commits only check changed files.

**Commitlint CLI passes but commit-msg hook rejects.** The `commit-msg` hook passes `"$1"` which is `.git/COMMIT_EDITMSG`. Confirm the entry in `.pre-commit-config.yaml` uses `--edit "$1"`.

**CI `pg_isready` command not found.** The `postgres:18-alpine` image includes `pg_isready`. If the check is timing out, increase `--health-retries` or add `--health-timeout`.

**`gh repo create` says "name already in use."** Skip creation; just `git remote add origin <url>` then `git push -u origin main`.

**Vite HMR misses WSL file events.** Per memory `feedback_vite_wsl_restart.md`: either copy `docker-compose.override.yml.example` → `docker-compose.override.yml` (enables polling) or restart Vite after each edit.

---

*End of plan.*
