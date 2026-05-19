# Backend Rules

Loads when Claude touches files in `backend/`. See root `CLAUDE.md` for cross-cutting invariants.

## Coding conventions

- Python 3.14; type hints everywhere; async-only; structlog (no `print`); no bare `except:`; use `except (A, B) as exc:` (Py3 tuple syntax — ruff enforces).
- SQL: schema via Alembic only; `snake_case`; `TIMESTAMPTZ`; money in `NUMERIC(20, 8)`.
- Git: conventional commits; body ≤100 chars; squash-merge.

Full conventions: `docs/CONVENTIONS.md`. Dev/lint/migrate/deploy commands: `docs/COMMANDS.md`.

## Test command

```bash
# Always run tests inside Docker — never against NUC prod DB
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Run once, save output, read all failures together before fixing. See memory `feedback_test_run_once_save_output.md`.

## Alembic

- Migrations live in `backend/alembic/versions/`. Always create a new migration; never edit existing ones.
- `CALL refresh_continuous_aggregate` is a PROCEDURE that COMMITs internally — use `op.get_context().autocommit_block()`.
- Migration 0046 protects `app_config`/`app_secrets` from unfiltered DELETE — use per-namespace deletes in tests.

## Key service invariants (load the file before changing)

| Surface | File | Key rule |
|---|---|---|
| Risk gate | `app/services/risk_service.py` | `RiskService.evaluate(ctx, mode)` is the chokepoint; 7 checks; fail-OPEN; `side` lowercased at audit boundary |
| Position sizing | `app/services/position_sizing_service.py` | Vol formula = `stddev(log returns) × sqrt(252)` over 14 bars, NOT ATR |
| Portfolio rollup | `app/services/portfolio_rollup_service.py` | `clock_timestamp()` not `now()` for multi-account inserts; partial 200 not whole-rollup 503 |
| AI router | `app/services/ai/router.py` | 8 capabilities; LOCAL_ONLY is 3-layer defence; 404 not 403 on unknown job id |
| Advisor | `app/services/advisor/service.py` | `AdvisorService.review()` fail-OPEN; OFF passthrough; semaphore per-bot (max_concurrent 1–4) |
| Param tuner | `app/services/param_tuner/service.py` | SELECT FOR UPDATE SKIP LOCKED; Redis cost reservation fail-OPEN; ranks by Sharpe+MAR |
| Shadow promoter | `app/services/shadow_promoter/service.py` | `create_shadow()` forces `mode='paper'`; `_insert_mapping` table validated against frozenset allowlist |
| Bot supervisor | `app/bot/supervisor.py` | State machine stopped→starting→running→pausing→paused→error; `restart()` = stop→pubsub-poll→start (10s timeout) |
| Backtest runner | `app/backtest/runner.py` | Atomic CAS `WHERE status='queued'`; FIFO long+short pairing with commission deduction |

## Broker adapter invariants

Per-phase detail in memory files — read before changing those surfaces:
- Phases 1–11a: `archive_closed_phases.md` in memory
- `phase4_sidecar_topology.md` — IBKR mTLS+CRL+watchdog
- `phase6_futu_topology.md` — Futu topology
- `phase7a_schwab_topology.md` — Schwab OAuth+two-tier+BackendCallback

Shipped phases (summary): see `docs/CLAUDE.md`.

## Prometheus metric conventions

Labels must match spec verbatim. Counter names: `{service}_{action}_total`. Histograms: `{service}_{action}_seconds`. Use structlog; never print.

## APScheduler

Jobs wired in `app/main.py` lifespan. New jobs need: trigger type (IntervalTrigger/CronTrigger), misfire grace, coalesce=True for long-running polls.
