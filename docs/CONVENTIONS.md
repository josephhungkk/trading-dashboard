# Coding conventions

Detailed conventions moved out of CLAUDE.md (token-budget hygiene). The
load-bearing one-liners stay in CLAUDE.md; the rest lives here.

## Python

- 3.14, type hints everywhere, Pydantic v2 for I/O.
- Async all the way down (asyncpg, no sync DB).
- One adapter per broker in `app/brokers/`. Subclass `BrokerAdapter`
  (`base.py`, lands Phase 4 with first concrete adapter).
- Adding to `BrokerAdapter` → update every adapter same commit.
- DI via `Depends`. Never import singletons directly.
- structlog only, never `print`. No bare `except:`.
- Lint: `ruff` (`E,F,W,I,N,UP,B,A,C4,ASYNC,RUF`).
- Format: `ruff format`.
- Types: `mypy --strict app/`.

## TypeScript

- Strict (`exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`).
  No `any` w/o disable + reason.
- Function components only.
- Zustand global, `useState` local.
- API via `services/api.ts` only. WS via `services/ws.ts` → Zustand.
- No `px`/`em` in CSS or inline. Only rem, %, vh/vw, fr, auto.
- Layer imports per `eslint-plugin-boundaries`.

## SQL

- Schema changes via Alembic only.
- `snake_case` columns, plural snake_case tables.
- Timestamps: `created_at`/`updated_at`, `TIMESTAMPTZ`.
- Money: `NUMERIC(20, 8)`, never FLOAT/REAL.

## Git

- Conventional commits (`feat`/`fix`/`refactor`/`docs`/`test`/`chore`/`perf`/`ci`).
- Body lines ≤100 chars.
- Feature branches off `main`. Squash-merge.
- Never commit `.env`/`*.key`/`secrets/*`.
