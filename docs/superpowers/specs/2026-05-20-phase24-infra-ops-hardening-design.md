# Phase 24 — Infra & Ops Hardening Design

**Date:** 2026-05-20
**Version target:** v0.24.0
**Preceding phase:** 23 (UK CGT, v0.23.0)
**Following phase:** 25 (Quality Gate, v0.25.0) — *see ROADMAP for Phase 25 placeholder*

---

## Overview

Phase 24 hardens the platform's infrastructure and operational posture across six work streams before the Phase 25 quality gate and the Phase 26 PWA/v1.0 ship. It consumes all backlog items assigned to this phase in ROADMAP.md plus the operational gaps that accumulated through Phases 19–23.

**Six streams:**

| Stream | Theme |
|---|---|
| A | PostgreSQL client-cert auth (prod NUC + dev WSL) |
| B | Multi-worker uvicorn (Redis-backed nonce / replay / commission stores) |
| C | `account_balances` table decoupling |
| D | TimescaleDB CAGG backlog (10 aggregates) |
| E | Ops automation (sidecar cutover, post-deploy recovery, Futu restart) |
| F | Observability (Grafana dashboards, alert denoising, correlation IDs) |

---

## Stream A — PostgreSQL Client-Cert Auth

### Motivation

`DATABASE_URL` currently carries a plaintext password as a bootstrap secret. The WireGuard topology already provides network-level isolation; adding mTLS client certificates eliminates the password entirely from both the NUC prod instance and the WSL dev instance.

### Both instances in scope

| Instance | Host | Used by |
|---|---|---|
| NUC prod | `10.10.0.2:5432` over WireGuard | VPS backend container |
| WSL dev | `localhost:5432` (WSL-local docker-compose pg) | Local dev + `run-tests.sh` test container |

CI test containers remain password-auth (`DATABASE_URL` env-var override); the `asyncpg` connect path applies cert auth only when `PG_SSL_CERT_PATH` is present in the environment.

### Certificate layout

```
scripts/pg-cert/
  generate-ca.sh            # WSL: generate CA key + self-signed cert
  generate-client-cert.sh   # WSL: generate dashboard_backend client cert signed by CA
  install-dev.sh             # WSL: install into dev docker-compose PG + pg_hba patch
  generate-ca.ps1            # Windows/NUC: same for prod PG18
  generate-client-cert.ps1   # Windows/NUC: client cert for prod
  install-nuc.ps1            # Windows/NUC: install into PG18 data dir + pg_hba patch
  RUNBOOK-pg-cert-rotation.md
```

### `pg_hba.conf` change

```
# Before
host  dashboard  dashboard_user  10.10.0.0/24  scram-sha-256

# After
hostssl  dashboard  dashboard_user  10.10.0.0/24  cert  clientcert=verify-full
```

WSL dev pg_hba equivalently switches the `127.0.0.1/32` line.

### Backend DSN

`asyncpg` connection string gains three SSL parameters sourced from `app_secrets` (Fernet-encrypted paths) or Docker-mounted volume:

```
postgresql+asyncpg://dashboard_user@10.10.0.2:5432/dashboard
  ?sslmode=verify-full
  &sslcert=/run/secrets/pg_client.crt
  &sslkey=/run/secrets/pg_client.key
  &sslrootcert=/run/secrets/pg_ca.crt
```

No `password=` field. `APP_SECRET_KEY` bootstrap secret remains (needed for Fernet decryption of `app_secrets`).

### Validation

- `psql` from WSL using client cert with no password → connects.
- `psql` from WSL using wrong cert → `FATAL: certificate authentication failed`.
- Backend health endpoint returns 200 after cert switch on both NUC and WSL.
- `run-tests.sh` still passes (CI path uses password auth, not cert).

---

## Stream B — Multi-Worker Uvicorn

### Motivation

Three in-memory stores prevent safe N>1 workers today. With these on Redis, `--workers 4` is safe and the backend can saturate multi-core on the NUC.

### Stores to migrate

| Store | Current location | Redis key pattern | TTL |
|---|---|---|---|
| CSRF/order nonce | `dict` in `orders_service.py` | `nonce:{account_id}:{hash}` | 300 s |
| Replay cache (modify_order) | `dict` in `orders_service.py` | `replay:{account_id}:{broker_order_id}:{nonce}` | 60 s |
| Commission buffer | `dict` in `BrokerOrderEventConsumer` | `commission:{account_id}:{exec_id}` (hash) + sorted-set sweeper key | 120 s |
| `_last_position_tick_at` | in-memory dict in BrokerDiscoverer | `pos_tick:{account_id}:{broker_id}` | session-lived, no TTL |

All use `SETNX` / `GETDEL` / `SETEX` atomics already established by Phase 11d's Telegram nonce pattern. No new Redis patterns.

### Worker model

```yaml
# docker-compose.yml (after)
command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-4}
```

`BrokerDiscoverer`: each worker process runs its own discoverer loop. Redis pub/sub config invalidation (Phase 2) already propagates to all workers. No shared singleton needed.

`BrokerOrderEventConsumer`: one consumer per worker connects to each broker sidecar. IBKR sidecars support multiple simultaneous gRPC streams. Dedup via Redis `SETNX` on `order_event:{broker_order_id}:{event_hash}` (30 s TTL) prevents duplicate order event writes from N consumers. This dedup key is new.

### Testing

- Integration test: start 4 workers, place order from worker-1, cancel from worker-2 → nonce is consumed correctly.
- Stress test: 100 concurrent preview requests across 4 workers → zero nonce collisions.

---

## Stream C — `account_balances` Table Decoupling

### Motivation

`broker_accounts.last_nlv`, `last_nlv_currency`, `last_nlv_at` are read by 5+ services (BrokerDiscoverer, orders_service, risk_service, position_sizing_service, sizing API). Upcoming cash-by-currency, buying-power components, and margin details won't bolt cleanly onto `broker_accounts`. A dedicated current-state table isolates balance state from account metadata.

### New table: `account_balances`

```sql
CREATE TABLE account_balances (
    account_id      UUID PRIMARY KEY REFERENCES broker_accounts(id) ON DELETE CASCADE,
    nlv             NUMERIC(20, 8),
    nlv_currency    VARCHAR(8),
    cash_by_currency JSONB DEFAULT '{}',   -- {"GBP": "12345.67", "USD": "0.00"}
    buying_power    NUMERIC(20, 8),
    margin_used     NUMERIC(20, 8),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`account_balance_snapshots` (Phase 10b.2 hypertable) remains the history store; `account_balances` is the current-state row.

### Migration (Alembic 0074)

1. Create `account_balances`.
2. `INSERT INTO account_balances (account_id, nlv, nlv_currency, updated_at) SELECT id, last_nlv, last_nlv_currency, last_nlv_at FROM broker_accounts WHERE last_nlv IS NOT NULL`.
3. Drop columns `last_nlv`, `last_nlv_currency`, `last_nlv_at` from `broker_accounts`.

### New service: `AccountBalanceService`

```python
class AccountBalanceService:
    async def get_current(self, account_id: UUID) -> AccountBalanceRow | None: ...
    async def upsert(self, account_id: UUID, nlv: Decimal, currency: str, ...) -> None: ...
```

All 5 read sites redirected to `AccountBalanceService.get_current()`. `BrokerDiscoverer` calls `upsert()` instead of writing `last_nlv*` columns directly.

### FE impact

`AccountResponse` already exposes `nlv` / `nlv_currency` from `AccountService._format_account()`. The service layer change is transparent to the FE wire shape — no FE changes needed.

---

## Stream D — TimescaleDB CAGG Backlog

### Motivation

Phase 9 deferred 10 continuous aggregates pending production `bars_1s` traffic to validate refresh cadence and storage overhead. Phase 24 is the first natural point with sufficient prod traffic history.

### The 10 CAGGs

| Aggregate | Bucket | Refresh policy |
|---|---|---|
| `bars_5s` | 5 seconds | every 10 s, lag 10 s |
| `bars_10s` | 10 seconds | every 20 s, lag 20 s |
| `bars_15s` | 15 seconds | every 30 s, lag 30 s |
| `bars_30s` | 30 seconds | every 1 min, lag 1 min |
| `bars_45s` | 45 seconds | every 1 min, lag 1 min |
| `bars_5m` | 5 minutes | every 5 min, lag 5 min |
| `bars_15m` | 15 minutes | every 15 min, lag 15 min |
| `bars_30m` | 30 minutes | every 30 min, lag 30 min |
| `bars_1h` | 1 hour | every 1 h, lag 1 h |
| `bars_1d` | 1 day | every 1 d, lag 1 d |

`bars_1d` already exists from Phase 10b.1 — migration skips if present (`CREATE MATERIALIZED VIEW IF NOT EXISTS`).

### Storage gate

During the 24-hour monitoring window after migration: if raw `bars_1s` + all CAGGs > 2× raw `bars_1s` alone, drop the 5 sub-minute CAGGs (`bars_5s`–`bars_45s`) and defer to post-v1.0. The 5 longer-interval CAGGs (5m–1d) are always kept regardless.

### Alembic 0075

Single migration creates all 10 CAGGs and their refresh policies idempotently (`IF NOT EXISTS`).

---

## Stream E — Ops Automation

### E1 — Windows Sidecar Path Cutover

**Problem:** `C:\dashboard\sidecar\` still serves the live 4 IBKR sidecars from a 2026-04-29 stale build. WSL edits since the `sidecar/` → `sidecar_ibkr/` rename are invisible to the live process.

**Fix:**
1. Update `deploy/nuc/Launch-IBKRSidecar.vbs` + 4 `.ps1` launchers to point at `C:\dashboard\sidecar_ibkr\`.
2. Update `BrokerWatchdog` paths.
3. Rebuild + sync: `deploy/nuc/sync-to-windows.sh` run from WSL.
4. Operator performs one manual restart cycle (see updated runbook).

Files touched: `deploy/nuc/Launch-IBKRSidecar.vbs`, `deploy/nuc/register-ibkr-sidecar.ps1`, `deploy/nuc/BrokerWatchdog.ps1`, `deploy/nuc/sync-to-windows.sh`.

### E2 — Post-Deploy Recovery Script

**Problem:** After every VPS redeploy, operator must manually: run `provision-and-publish.ps1`, trigger 4 sidecar `schtasks /Run`, bounce nginx alongside backend restart.

**Fix:** `scripts/recover-after-deploy.sh` — single script that:
1. SSHes to NUC (`ssh -p 2222 ...`) and runs `provision-and-publish.ps1` via PowerShell remoting over WireGuard.
2. Triggers `schtasks /Run` for all 4 IBKR sidecar tasks.
3. Runs `docker compose restart backend` + `nginx -s reload` on VPS.
4. Polls `/api/health` until 200 or 60 s timeout.

### E3 — Futu OpenD Full-Restart Script

**Problem:** Futu OpenD must be fully killed + restarted (not just reloaded) after RSA key changes. Currently a manual multi-step runbook.

**Fix:** `scripts/restart-futu-full.sh`:
1. Kill FutuOpenD process on NUC (via SSH + PowerShell `Stop-Process`).
2. Kill `sidecar_futu` process.
3. Restart `docker compose` futu sidecar service.
4. Restart backend (to re-establish gRPC stream).
5. Verify via `GET /api/health` + `GET /api/accounts` (Futu account present).

---

## Stream F — Observability

### F1 — Grafana Dashboards

Provisioning files under `deploy/grafana/dashboards/`:

| Dashboard | Key panels |
|---|---|
| `broker-gateway.json` | Per-gateway health (IBKR/Futu/Schwab/Alpaca), stream reconnects/min, sidecar latency p99 |
| `quote-bus.json` | Messages/s per source, subscription count, stale-quote alerts |
| `risk-gate.json` | Gate decisions (ALLOW/WARN/BLOCK) rate, p99 latency, kill-switch toggles |
| `bot-orchestrator.json` | Bot count by state, exposure gate hits, auto-promote events, nightly retrain status |
| `cagg-lag.json` | Refresh lag per CAGG vs policy target |
| `workers.json` | Uvicorn worker count, per-worker request rate, Redis pool saturation |

### F2 — Alert Denoising

Audit `deploy/monitoring/alerts.yml`:

- `BrokerOrderEventStreamFlapping`: add `inhibit_rule` suppressing during IBKR maintenance windows (06:45–07:45 UTC daily, 05:00–14:00 UTC Sat–Sun per `ibkr_maintenance_schedule.md`).
- Alerts with `for: 0s` or `for: 1m` that produce noise on expected transient blips → raise to `for: 5m`.
- Add `BrokerSidecarCertExpirySoon` (14-day warning) after Stream A cert provisioning.

### F3 — Correlation IDs Across Workers

`app/core/logging.py` already uses `structlog.bind_contextvars(request_id=...)` in the FastAPI middleware. Extend to:

- APScheduler jobs: bind `job_id` at job entry.
- `BrokerOrderEventConsumer` background task: bind `consumer_id={broker_label}`.
- `BrokerDiscoverer` background task: bind `discoverer_tick={timestamp}`.

No new dependencies — pure structlog.

---

## Chunks & Delivery Order

| Chunk | Content | Alembic | Rationale |
|---|---|---|---|
| A | PG cert scripts + `pg_hba.conf` + asyncpg DSN — WSL dev first, NUC prod second | — | WSL first = safe rollback if cert logic wrong |
| B | Redis nonce/replay/commission migration + multi-worker switch | — | Depends on no other stream |
| C | `account_balances` table + AccountBalanceService + 5 read-site redirects | 0074 | Independent of B |
| D | 10 TimescaleDB CAGGs + storage monitoring | 0075 | Needs prod traffic — can run in parallel with A-C |
| E | Ops scripts (sidecar cutover + post-deploy recovery + Futu restart) | — | Operator-gate: cutover requires manual restart |
| F | Grafana dashboards + alert denoising + correlation IDs | — | Can parallelize with E |
| G | Close-out: CHANGELOG, TASKS, CLAUDE.md, tag v0.24.0 | — | Last |

---

## Non-goals

- ClickHouse migration: evaluation is deferred until Phase 24 CAGG monitoring (Stream D) shows TimescaleDB storage is actually a problem. If it is, ClickHouse migration becomes a separate post-Phase 25 item.
- New API endpoints: Phase 24 is infrastructure — no new product features.
- FE changes: `account_balances` decoupling is transparent to the FE wire shape.

---

## Testing strategy

| Stream | Test approach |
|---|---|
| A | `psql` smoke with cert / wrong-cert / no-cert; CI remains password-auth |
| B | Integration: nonce consumed across 2 workers; stress: 100 concurrent previews × 4 workers |
| C | Migration round-trip test (`test_0074_account_balances.py`); unit tests for `AccountBalanceService`; all 5 read sites have existing tests updated |
| D | CAGG presence + refresh policy test; 24-hour storage monitoring (manual gate) |
| E | Dry-run flags on all scripts; Pester test for updated NUC launcher paths |
| F | Grafana JSON schema lint; `alerts.yml` unit test via `promtool check rules` |

---

## File map

### New files

| Path | Purpose |
|---|---|
| `scripts/pg-cert/generate-ca.sh` | WSL: CA generation |
| `scripts/pg-cert/generate-client-cert.sh` | WSL: backend client cert |
| `scripts/pg-cert/install-dev.sh` | WSL: install into dev PG + pg_hba patch |
| `scripts/pg-cert/generate-ca.ps1` | NUC: CA generation |
| `scripts/pg-cert/generate-client-cert.ps1` | NUC: client cert |
| `scripts/pg-cert/install-nuc.ps1` | NUC: install + pg_hba patch |
| `docs/RUNBOOK-pg-cert-rotation.md` | Cert rotation runbook |
| `scripts/recover-after-deploy.sh` | Post-deploy full recovery |
| `scripts/restart-futu-full.sh` | Futu OpenD full kill+restart |
| `backend/app/services/account_balances.py` | AccountBalanceService |
| `backend/alembic/versions/0074_account_balances.py` | account_balances table, drop broker_accounts nlv cols |
| `backend/alembic/versions/0075_cagg_backlog.py` | 10 TimescaleDB CAGGs |
| `backend/tests/services/test_account_balance_service.py` | Unit tests |
| `backend/tests/migrations/test_0074_account_balances.py` | Migration round-trip |
| `backend/tests/migrations/test_0075_cagg_backlog.py` | CAGG presence + policy |
| `deploy/grafana/dashboards/broker-gateway.json` | Grafana dashboard |
| `deploy/grafana/dashboards/quote-bus.json` | Grafana dashboard |
| `deploy/grafana/dashboards/risk-gate.json` | Grafana dashboard |
| `deploy/grafana/dashboards/bot-orchestrator.json` | Grafana dashboard |
| `deploy/grafana/dashboards/cagg-lag.json` | Grafana dashboard |
| `deploy/grafana/dashboards/workers.json` | Grafana dashboard |

### Modified files

| Path | Change |
|---|---|
| `backend/app/core/database.py` | asyncpg DSN: sslmode/sslcert/sslkey/sslrootcert when `PG_SSL_CERT_PATH` present |
| `backend/app/services/orders_service.py` | Replace in-memory nonce + replay dicts with Redis SETNX/GETDEL/SETEX |
| `backend/app/services/order_event_consumer.py` | Replace in-memory commission buffer dict with Redis hash + sorted-set sweeper |
| `backend/app/services/broker_discoverer.py` | Write to `account_balances` via AccountBalanceService; replace `_last_position_tick_at` dict with Redis key |
| `backend/app/services/risk_service.py` | Read NLV from AccountBalanceService |
| `backend/app/services/position_sizing_service.py` | Read NLV from AccountBalanceService |
| `backend/app/api/sizing.py` | Read NLV from AccountBalanceService |
| `backend/app/core/logging.py` | Add correlation ID binding for APScheduler jobs + background consumers |
| `docker-compose.yml` | uvicorn `--workers ${UVICORN_WORKERS:-4}` |
| `deploy/nuc/Launch-IBKRSidecar.vbs` | Path: `sidecar\` → `sidecar_ibkr\` |
| `deploy/nuc/register-ibkr-sidecar.ps1` | Path update |
| `deploy/nuc/BrokerWatchdog.ps1` | Path update |
| `deploy/nuc/sync-to-windows.sh` | Include `sidecar_ibkr/` in sync |
| `deploy/monitoring/alerts.yml` | Maintenance window inhibit rules; `for:` guard raises; cert expiry alert |
