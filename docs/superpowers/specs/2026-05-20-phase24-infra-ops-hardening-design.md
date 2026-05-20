# Phase 24 — Infra & Ops Hardening Design

**Date:** 2026-05-20
**Version target:** v0.24.0
**Preceding phase:** 23 (UK CGT, v0.23.0)
**Following phase:** 25 (Quality Gate, v0.25.0) — *see ROADMAP for Phase 25 placeholder*

> **Architect review applied 2026-05-20:** 4 CRIT + 6 HIGH + 5 MED inline. 3 LOW deferred to chunk close-out checklists.

---

## Overview

Phase 24 hardens the platform's infrastructure and operational posture across six work streams before the Phase 25 quality gate and the Phase 26 PWA/v1.0 ship. It consumes all backlog items assigned to this phase in ROADMAP.md plus the operational gaps that accumulated through Phases 19–23.

**Six streams:**

| Stream | Theme |
|---|---|
| A | PostgreSQL client-cert auth (prod NUC + dev WSL) |
| B | Multi-worker uvicorn (single-worker scheduler container split + `_last_position_tick_at` Redis migration) |
| C | `account_balances` table decoupling (expand-contract migration) |
| D | TimescaleDB CAGG backlog (10 aggregates; sub-minute opt-in only) |
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

*[HIGH-3 applied]* CI/test containers also exercise the cert auth path via an ephemeral CA + cert generated in container setup (see §Testing). Password-auth fallback exists only as an emergency rollback line (commented out in `pg_hba.conf`).

### Certificate layout

```
scripts/pg-cert/
  generate-ca.sh              # WSL: generate CA key + self-signed cert
  generate-client-cert.sh     # WSL: generate dashboard_backend client cert signed by CA
  install-dev.sh               # WSL: install into dev docker-compose PG + pg_hba patch
  generate-ca.ps1              # Windows/NUC: same for prod PG18
  generate-client-cert.ps1     # Windows/NUC: client cert for prod
  install-nuc.ps1              # Windows/NUC: install + pg_hba patch
docs/RUNBOOK-pg-cert-rotation.md   # LOW-1 applied: runbook lives in docs/, not scripts/
```

### `pg_hba.conf` change

```
# Before
host  dashboard  dashboard_user  10.10.0.0/24  scram-sha-256

# After
hostssl  dashboard  dashboard_user  10.10.0.0/24  cert  clientcert=verify-full
# ROLLBACK LINE (leave commented — remove only after one full phase-cycle confirmed):
# host  dashboard  dashboard_user  10.10.0.0/24  scram-sha-256
```

*[MED-4 applied]* The password `pg_hba.conf` line is commented out (not deleted) for the Phase 24 cycle, enabling a 60-second operator rollback without a migration. Remove it at Phase 25 start once cert auth has run for the full phase.

WSL dev pg_hba equivalently switches the `127.0.0.1/32` line.

### Backend DSN

*[HIGH-2 applied]* SSL cert paths come exclusively from Docker-mounted volume + env vars. Reading them from `app_secrets` would be circular (DB connection required to decrypt → cert paths required to connect). `app_secrets` path is not viable.

```
postgresql+asyncpg://dashboard_user@10.10.0.2:5432/dashboard
  ?sslmode=verify-full
  &sslcert=/run/secrets/pg_client.crt
  &sslkey=/run/secrets/pg_client.key
  &sslrootcert=/run/secrets/pg_ca.crt
```

Env vars: `PG_SSL_CERT_PATH`, `PG_SSL_KEY_PATH`, `PG_SSL_CA_PATH`. Cert auth activates when `PG_SSL_CERT_PATH` is set; password auth is used otherwise (emergency rollback path only).

No `password=` field in the DSN when cert auth is active.

### Threat model and APP_SECRET_KEY handling

*[HIGH-1 applied]* After dropping the PG password, `APP_SECRET_KEY` (Fernet master key, stored in `.env` on the VPS) becomes the sole bootstrap secret. Threat assessment:

- **Risk:** An attacker with `.env` read access + WireGuard access can decrypt all `app_secrets` including broker credentials.
- **Accepted risk:** Root access on the VPS is already game-over for the application. The `.env` file is protected at the OS level.
- **Mitigation required by this phase:**
  1. Bootstrap script (`scripts/deploy.sh`) asserts `stat -c '%a' .env` == `600`. Deploy fails if permissions are wrong.
  2. `docs/RUNBOOK-pg-cert-rotation.md` includes a §"APP_SECRET_KEY rotation" procedure: generate new key → dual-write old+new via `MultiFernet` → re-encrypt all `app_secrets` rows → remove old key from `.env`.
  3. Document that moving `APP_SECRET_KEY` to Docker secrets / host keyring is a post-v1.0 hardening item.

### Validation

- `psql` from WSL using client cert with no password → connects.
- `psql` from WSL using wrong cert → `FATAL: certificate authentication failed`.
- `psql` from WSL with no cert → `FATAL: certificate authentication failed`.
- Backend health endpoint returns 200 after cert switch on both NUC and WSL.
- CI matrix job with ephemeral cert: connect succeeds; connect with wrong cert fails (see §Testing).

### Rollback procedure

*[MED-4 applied]* If cert auth wedges prod at any time:
1. Operator SSHes to NUC: uncomment the `scram-sha-256` line in `pg_hba.conf`, run `pg_ctl reload`.
2. Set `PG_CERT_AUTH=false` in `.env` on VPS → backend falls back to password DSN.
3. Restart backend (`scripts/restart-backend.sh`).
4. Time to recovery: < 2 minutes. No migration required.

---

## Stream B — Multi-Worker Uvicorn

### Motivation

The backend currently runs as a single uvicorn worker. Before scaling to N workers, two classes of blocking issues must be resolved:

1. **State that is NOT multi-worker-safe today:** `BrokerDiscoverer._last_position_tick_at` (in-memory dict, one per process) and APScheduler-wired jobs (fire N× under `--workers N`).
2. **Consumers that must stay single-instance:** `BrokerOrderEventConsumer` (one stream per (broker, account) per worker = N× sidecar load and N× duplicate events) and all APScheduler jobs.

*[CRIT-1 applied]* The nonce, replay cache, and commission buffer are **already Redis-backed** from Phases 11d/5c. Verified:
- `orders_service.py:276,1013,1022,1496` — nonce uses `redis.set(... nx=True)` + `GETDEL`
- `orders_service.py:1301,1682` — `_modify_replay_lookup` / `_modify_replay_store` are Redis-backed
- `order_event_consumer.py:79–98,426,847` — commission buffer is Redis-backed

No Redis migration needed for these. Stream B is entirely about the scheduler split and `_last_position_tick_at`.

### Sub-division: Chunk B1 before Chunk B2

*[MED-2 applied]* Multi-worker is enabled only AFTER B1 lands and runs in prod for ≥24 h at N=1.

**B1 — Scheduler/Consumer split (prerequisite)**

*[CRIT-2 applied]* All APScheduler jobs in `app/main.py` lifespan fire once per worker process. With `--workers 4`, every scheduled job runs 4×:
- NightlyRetrain (02:00 UTC) — 4× ML compute
- HealthDigest (03:00 UTC)
- AttributionService poller (900 s)
- Mute-expiry job (Phase 11c)
- ParamTuner (Phase 21b)
- AutoPromoteEvaluator (Phase 22a)
- ShadowPromoter (Phase 21b)

*[CRIT-3 applied]* `BrokerOrderEventConsumer` opens one gRPC stream per (broker, account) per worker. N workers = N× sidecar stream count + N× event delivery. SETNX dedup on `order_event:{broker_order_id}:{event_hash}` would have a TOCTOU window: worker crashes after SETNX but before PG write → event lost for 30 s. Single consumer eliminates this entirely.

**Fix: dedicated scheduler container.**

```yaml
# docker-compose.yml (after B1)
services:
  backend:
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}
    # workers bumped to 4 in B2 only

  scheduler:
    command: python -m app.scheduler        # new entry point
    # single process; owns APScheduler + BrokerOrderEventConsumer
```

`app/scheduler.py` entry point:
- Starts APScheduler with all jobs
- Starts `BrokerOrderEventConsumer` for all broker accounts
- No HTTP server; no uvicorn
- Shares the same DB + Redis connection pool as the API workers

`app/main.py` lifespan: remove APScheduler startup and `BrokerOrderEventConsumer` startup. API workers are now stateless request handlers.

Fail-CLOSED: if the scheduler container crashes, jobs do not run. An alert `SchedulerContainerDown` fires if the scheduler process is absent for > 2 min (heartbeat key `scheduler:heartbeat` written every 30 s; alert if absent for 120 s).

Metric: `apscheduler_jobs_skipped_total{reason="scheduler_unavailable"}` (incremented by any API path that would have triggered a scheduler action and found the heartbeat absent).

**B1 also: `_last_position_tick_at` → Redis**

*[HIGH-6 applied]* `BrokerDiscoverer._last_position_tick_at` dict is per-worker under multi-process. Under N workers, Worker A writes Redis key for (label, account), Worker B reads the key written by A → believes its own stream is fresh → suppresses stale-position alert for a genuinely stale stream.

Fix: per-worker Redis keys `pos_tick:{worker_id}:{account_id}:{broker_id}`. The watchdog reads `MAX(updated_at)` across all worker keys for the (account, broker) pair. This is idempotent and doesn't require leader election.

Since `BrokerDiscoverer` moves to the scheduler container in B1, there is only one worker_id in practice. The per-worker key pattern is retained for forward compatibility.

Redis key pattern: `pos_tick:{worker_id}:{account_id}:{broker_id}`, no TTL (session-lived; cleared on scheduler container restart).

**B2 — uvicorn workers flip**

*[MED-2 applied]* After B1 has run in prod for ≥24 h with N=1:

```yaml
backend:
  command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-4}
```

`BrokerDiscoverer` runs in the scheduler container only — not in API workers. API workers are fully stateless.

### Testing

- B1: integration test — scheduler container starts, APScheduler heartbeat key written to Redis within 30 s; API worker does NOT start APScheduler.
- B1: integration test — `BrokerOrderEventConsumer` not present in API worker lifespan.
- B2: integration test — place order from worker-1 (nonce minted), cancel from worker-2 (nonce consumed correctly via existing Redis path).
- B2: stress test — 100 concurrent preview requests across 4 workers → zero nonce collisions.

---

## Stream C — `account_balances` Table Decoupling

### Motivation

`broker_accounts.last_nlv`, `last_nlv_currency`, `last_nlv_at` are read by 5+ services. Upcoming cash-by-currency, buying-power components, and margin details won't bolt cleanly onto `broker_accounts`. A dedicated current-state table isolates balance state from account metadata.

### New table: `account_balances`

*[MED-1 applied]* `cash_by_currency` starts as JSONB for display-only read paths. An ADR justifying this choice: per-currency rows (a separate `account_cash_balances` table) are the correct eventual shape for per-currency `FOR UPDATE` and margin-by-currency queries. JSONB is acceptable for Phase 24 because: (a) Phase 24 has no margin-by-currency consumers, (b) the JSONB column is on a separate table (easy to add a sibling table in Phase 25+), (c) the column is not indexed. This is documented here to prevent future authors from adding per-currency queries against the JSONB column — that is the trigger to add `account_cash_balances`.

```sql
CREATE TABLE account_balances (
    account_id       UUID PRIMARY KEY REFERENCES broker_accounts(id) ON DELETE CASCADE,
    nlv              NUMERIC(20, 8),
    nlv_currency     VARCHAR(8),
    -- display-only; do NOT add per-currency queries against this column —
    -- add account_cash_balances(account_id, currency) table instead
    cash_by_currency JSONB DEFAULT '{}',
    buying_power     NUMERIC(20, 8),
    margin_used      NUMERIC(20, 8),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Expand-contract migration

*[CRIT-4 applied]* Single-phase column drop breaks live read paths in `orders_service.py:1925,1944` (reads `last_nlv_currency` as currency_base fallback) and `position_sizing_service.py:85,239,246,248` (reads `last_nlv` directly). Expand-contract pattern required.

**Alembic 0074 (expand — this phase):**
1. Create `account_balances`.
2. Backfill: `INSERT INTO account_balances (account_id, nlv, nlv_currency, updated_at) SELECT id, last_nlv, last_nlv_currency, last_nlv_at FROM broker_accounts WHERE last_nlv IS NOT NULL`.
3. `BrokerDiscoverer` dual-writes: updates BOTH `account_balances` (via `AccountBalanceService.upsert()`) AND the legacy `broker_accounts.last_nlv*` columns.
4. All 5+ read sites redirected to `AccountBalanceService.get_current()`.
5. `orders_service.py:1940` comment block updated explicitly to reference `AccountBalanceService`.
6. `broker_accounts.last_nlv*` columns remain — no drop in this migration.

**Verification gate (required before 0074a):**
- `grep -rn 'last_nlv' backend/app/` must return zero results (enforced by a pre-commit grep hook added in Chunk C).
- CI test `test_no_direct_last_nlv_reads.py` asserts the grep returns empty.

**Alembic 0074a (contract — Phase 25 or follow-up patch, NOT Phase 24):**
- Drop dual-write from `BrokerDiscoverer`.
- Drop columns `last_nlv`, `last_nlv_currency`, `last_nlv_at` from `broker_accounts`.
- Remove the pre-commit grep hook.

### New service: `AccountBalanceService`

```python
class AccountBalanceService:
    async def get_current(self, account_id: UUID) -> AccountBalanceRow | None: ...
    async def upsert(self, account_id: UUID, nlv: Decimal, currency: str,
                     buying_power: Decimal | None = None,
                     margin_used: Decimal | None = None) -> None: ...
```

`BrokerDiscoverer` calls `upsert()` and also keeps the legacy column writes until 0074a.

### FE impact

`AccountResponse` already exposes `nlv` / `nlv_currency` from `AccountService._format_account()`. The service layer change is transparent to the FE wire shape — no FE changes needed.

---

## Stream D — TimescaleDB CAGG Backlog

### Motivation

Phase 9 deferred 10 continuous aggregates pending production `bars_1s` traffic to validate refresh cadence and storage overhead. Phase 24 is the first natural point with sufficient prod traffic history.

### CAGG delivery: two tiers

*[HIGH-4 applied]* Sub-minute CAGGs (5s–45s) refreshing every 10–60 s on a high-cardinality hypertable generate ~6–10 refresh jobs/min, compete with `bars_1s` writer for advisory locks, and may saturate the TimescaleDB bgworker pool. Sub-minute *charting* reads directly off `bars_1s` (already fast for windows ≤ 30 s); CAGGs are pre-aggregations for backfills and ≥ 5 m windows only.

**Tier 1 — Always ship (5 CAGGs):**

| Aggregate | Bucket | Refresh policy |
|---|---|---|
| `bars_5m` | 5 minutes | every 5 min, lag 5 min |
| `bars_15m` | 15 minutes | every 15 min, lag 15 min |
| `bars_30m` | 30 minutes | every 30 min, lag 30 min |
| `bars_1h` | 1 hour | every 1 h, lag 1 h |
| `bars_1d` | 1 day | every 1 d, lag 1 d |

`bars_1d` already exists from Phase 10b.1 — migration skips if present (`CREATE MATERIALIZED VIEW IF NOT EXISTS`).

**Tier 2 — Opt-in only (5 sub-minute CAGGs):**

| Aggregate | Bucket | Refresh policy |
|---|---|---|
| `bars_5s` | 5 seconds | every 10 s, lag 10 s |
| `bars_10s` | 10 seconds | every 20 s, lag 20 s |
| `bars_15s` | 15 seconds | every 30 s, lag 30 s |
| `bars_30s` | 30 seconds | every 1 min, lag 1 min |
| `bars_45s` | 45 seconds | every 1 min, lag 1 min |

Tier 2 CAGGs are created in Alembic 0075 **but their refresh policies are NOT registered by default**. An operator can opt in by running `SELECT add_continuous_aggregate_policy('bars_5s', ...)` manually after validating write latency. They are dropped (not opt-in) if the monitoring window shows `bars_1s` write p99 degrades after enabling them.

### Exit metrics (monitoring window)

*[HIGH-4 applied]* The storage gate alone is insufficient. Two metrics govern the 24-hour monitoring window:

1. **Storage:** `bars_1s` + all active CAGGs must be < 2× raw `bars_1s` alone.
2. **Write contention:** `bars_1s` write p99 latency must not increase by > 20% vs the 7-day baseline. Measured via `timescaledb_writer_latency_seconds` histogram.

Add metric: `timescaledb_cagg_refresh_lag_seconds{cagg=...}` (lag between refresh policy target and actual last-refresh). Alert `CaggRefreshLagHigh` if > 2× policy target for > 5 min.

### Alembic 0075

Single migration creates all 10 CAGGs idempotently (`IF NOT EXISTS`). Tier 1 refresh policies registered. Tier 2 refresh policies **not** registered (opt-in only, documented in migration header).

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

*[HIGH-5 applied]* PowerShell remoting (WinRM) is not needed and opens an unnecessary remote management surface. Use the existing SSH transport (port 2222, already established, key-auth only).

**Fix:** `scripts/recover-after-deploy.sh` — single script that:
1. SSHes to NUC (`ssh -i ~/.ssh/trader_id_ed25519 trader@10.10.0.2 -p 2222 'powershell -ExecutionPolicy Bypass -File C:\dashboard\deploy\nuc\provision-and-publish.ps1'`) — SSH key auth, no WinRM.
2. Triggers `schtasks /Run` for all 4 IBKR sidecar tasks via same SSH session.
3. Runs `docker compose restart backend && nginx -s reload` on VPS.
4. Polls `/api/health` (no auth required) until 200 or 60 s timeout.

*[LOW-2 applied]* Health probe uses `/api/health` only — not `/api/accounts`, which requires CF Access auth.

### E3 — Futu OpenD Full-Restart Script

**Problem:** Futu OpenD must be fully killed + restarted (not just reloaded) after RSA key changes. Currently a manual multi-step runbook.

**Fix:** `scripts/restart-futu-full.sh`:
1. SSH to NUC → `Stop-Process -Name FutuOpenD -Force` via PowerShell over SSH.
2. Kill `sidecar_futu` Docker container.
3. Restart `docker compose` futu sidecar service.
4. Restart backend (to re-establish gRPC stream).
5. Verify via `/api/health` only (CF Access exempt endpoint).

---

## Stream F — Observability

### F1 — Grafana Dashboards

*[MED-3 applied]* Prerequisites: Prometheus scrape target already exists on FastAPI `/metrics`. Add `pg_exporter` (Postgres metrics including CAGG refresh stats) as a new scrape target. Document datasource UID conventions under `deploy/grafana/provisioning/datasources/prometheus.yml` and `pg_exporter.yml`.

Provisioning files under `deploy/grafana/dashboards/`:

| Dashboard | Key panels | Datasource |
|---|---|---|
| `broker-gateway.json` | Per-gateway health (IBKR/Futu/Schwab/Alpaca), stream reconnects/min, sidecar latency p99 | Prometheus |
| `quote-bus.json` | Messages/s per source, subscription count, stale-quote alerts | Prometheus |
| `risk-gate.json` | Gate decisions (ALLOW/WARN/BLOCK) rate, p99 latency, kill-switch toggles | Prometheus |
| `bot-orchestrator.json` | Bot count by state, exposure gate hits, auto-promote events, retrain status, scheduler heartbeat | Prometheus |
| `cagg-lag.json` | Refresh lag per CAGG vs policy target; `bars_1s` write p99 | pg_exporter + Prometheus |
| `workers.json` | Uvicorn worker count, per-worker request rate, scheduler container heartbeat age | Prometheus |

### F2 — Alert Denoising

*[MED-5 applied]* Pair each maintenance-window inhibit with a post-window counter alert.

Audit `deploy/monitoring/alerts.yml`:

- `BrokerOrderEventStreamFlapping`: add `inhibit_rule` suppressing during IBKR maintenance windows (06:45–07:45 UTC daily, 05:00–14:00 UTC Sat–Sun per `ibkr_maintenance_schedule.md`). **Paired with:** `BrokerOrderEventStreamFlappingPostMaintenance` — fires if flapping is still observed > 30 min after the scheduled maintenance window end.
- Alerts with `for: 0s` or `for: 1m` that produce noise on expected transient blips → raise to `for: 5m`.
- Add `BrokerSidecarCertExpirySoon` (14-day warning) after Stream A cert provisioning.
- Add `SchedulerContainerDown` (heartbeat key `scheduler:heartbeat` absent > 2 min).
- Add `CaggRefreshLagHigh` (lag > 2× policy target for > 5 min per CAGG).

### F3 — Correlation IDs Across Workers

`app/core/logging.py` already uses `structlog.bind_contextvars(request_id=...)` in the FastAPI middleware. Extend to:

- APScheduler jobs (in scheduler container): bind `job_id` at job entry.
- `BrokerOrderEventConsumer` background task: bind `consumer_id={broker_label}`.
- `BrokerDiscoverer` background task: bind `discoverer_tick={timestamp}`.

No new dependencies — pure structlog.

---

## Chunks & Delivery Order

*[MED-2 applied]* Stream B is sub-divided: B1 must run ≥24 h at N=1 before B2 flips to N=4.

| Chunk | Content | Alembic | Rationale |
|---|---|---|---|
| A | PG cert scripts (WSL + NUC) + `pg_hba.conf` change + asyncpg DSN + `.env` permission assertion + rollback procedure | — | WSL dev first; NUC prod second |
| B1 | Scheduler container split (`app/scheduler.py`) + `BrokerOrderEventConsumer` moved out of API lifespan + `_last_position_tick_at` → Redis per-worker keys | — | Must run ≥24 h at `--workers 1` before B2 |
| B2 | uvicorn `--workers 4` flip + verification | — | Depends on B1 |
| C | `account_balances` table + `AccountBalanceService` + 5 read-site redirects + dual-write in `BrokerDiscoverer` + pre-commit grep hook | 0074 | Columns NOT dropped (0074a deferred to Phase 25) |
| D | Tier-1 CAGGs × 5 (5m–1d) + Tier-2 CAGGs × 5 created but NO refresh policies + monitoring metrics | 0075 | Needs prod traffic window |
| E | Ops scripts (sidecar path cutover + post-deploy recovery via SSH + Futu restart) | — | Operator-gate for E1 cutover |
| F | Grafana provisioning setup + 6 dashboards + alert denoising + post-maintenance alerts + scheduler/CAGG alerts + correlation IDs | — | Can parallelize with E |
| G | Close-out: CHANGELOG, TASKS, CLAUDE.md shipped-phases entry, tag v0.24.0 | — | Last; LOW-3 applied |

---

## Non-goals

- ClickHouse migration: deferred until Stream D monitoring shows TimescaleDB storage is a real problem. Trigger: storage overhead > 2× raw or write p99 degrades > 20% with Tier-1 CAGGs alone. If triggered, ClickHouse becomes a post-Phase 25 item.
- New API endpoints: Phase 24 is infrastructure only.
- FE changes: `account_balances` decoupling is transparent to the FE wire shape.
- `account_balances` column drop (Alembic 0074a): deferred to Phase 25 after the verification gate passes.

---

## Testing strategy

| Stream | Test approach |
|---|---|
| A | `psql` smoke: cert connect ✓, wrong-cert ✗, no-cert ✗. CI matrix job: generate ephemeral CA + cert in container, install into test PG, assert connect ✓ and wrong-cert ✗ (~10 s overhead). `run-tests.sh` continues to use password auth for non-cert tests. |
| B1 | Scheduler container heartbeat written within 30 s. API worker lifespan does NOT start APScheduler or OrderEventConsumer (asserted in `test_api_lifespan_no_scheduler.py`). |
| B2 | Place order (worker-1 nonce mint) → cancel (worker-2 nonce consume) integration test. 100 concurrent preview requests × 4 workers → zero nonce collisions. |
| C | Migration round-trip (`test_0074_account_balances.py`): backfill correctness; dual-write verified; `last_nlv*` columns still present. `test_no_direct_last_nlv_reads.py`: grep asserts zero `last_nlv` references in `backend/app/`. Unit tests for `AccountBalanceService` upsert + get_current. |
| D | CAGG presence test: Tier-1 CAGGs exist + refresh policies registered. Tier-2 CAGGs exist but NO refresh policies. 24-hour monitoring window: storage gate + write p99 gate (manual sign-off). |
| E | Dry-run flags on all shell scripts. Pester test for updated NUC launcher paths. `restart-futu-full.sh` dry-run mode validates SSH connectivity without killing live processes. |
| F | Grafana JSON schema lint (`jq -e . *.json`). `promtool check rules alerts.yml`. Scheduler heartbeat alert fires after test-wiping the Redis key. |

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
| `docs/RUNBOOK-pg-cert-rotation.md` | Cert rotation + APP_SECRET_KEY rotation runbook |
| `scripts/recover-after-deploy.sh` | Post-deploy full recovery (SSH-based, no WinRM) |
| `scripts/restart-futu-full.sh` | Futu OpenD full kill+restart |
| `backend/app/scheduler.py` | Scheduler container entry point (APScheduler + OrderEventConsumer) |
| `backend/app/services/account_balances.py` | AccountBalanceService |
| `backend/alembic/versions/0074_account_balances.py` | account_balances table + backfill; columns NOT dropped |
| `backend/alembic/versions/0075_cagg_backlog.py` | 10 TimescaleDB CAGGs; Tier-1 refresh policies; Tier-2 no policies |
| `backend/tests/services/test_account_balance_service.py` | Unit tests |
| `backend/tests/migrations/test_0074_account_balances.py` | Migration round-trip + dual-write + no-column-drop verification |
| `backend/tests/migrations/test_0075_cagg_backlog.py` | CAGG presence + Tier-1 policies present + Tier-2 policies absent |
| `backend/tests/test_api_lifespan_no_scheduler.py` | Assert API lifespan does not start APScheduler or OrderEventConsumer |
| `backend/tests/test_no_direct_last_nlv_reads.py` | grep assertion: zero `last_nlv` references in `backend/app/` |
| `deploy/grafana/provisioning/datasources/prometheus.yml` | Prometheus datasource |
| `deploy/grafana/provisioning/datasources/pg_exporter.yml` | pg_exporter datasource |
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
| `backend/app/main.py` | Remove APScheduler startup + `BrokerOrderEventConsumer` startup from lifespan (moved to `app/scheduler.py`) |
| `backend/app/services/broker_discoverer.py` | Dual-write `account_balances` via AccountBalanceService AND legacy `last_nlv*` columns; `_last_position_tick_at` dict → Redis per-worker keys |
| `backend/app/services/orders_service.py` | Update `orders_service.py:1940` comment block to reference AccountBalanceService |
| `backend/app/services/risk_service.py` | Read NLV from AccountBalanceService |
| `backend/app/services/position_sizing_service.py` | Read NLV from AccountBalanceService |
| `backend/app/api/sizing.py` | Read NLV from AccountBalanceService |
| `backend/app/core/logging.py` | Correlation ID binding for APScheduler jobs + background consumers |
| `docker-compose.yml` | Add `scheduler` service; `backend` gains `UVICORN_WORKERS` env var (default 1 in B1, raised to 4 in B2) |
| `scripts/deploy.sh` | Add `.env` permission assertion (`stat -c '%a' .env` must be `600`) |
| `deploy/nuc/Launch-IBKRSidecar.vbs` | Path: `sidecar\` → `sidecar_ibkr\` |
| `deploy/nuc/register-ibkr-sidecar.ps1` | Path update |
| `deploy/nuc/BrokerWatchdog.ps1` | Path update |
| `deploy/nuc/sync-to-windows.sh` | Include `sidecar_ibkr/` in sync |
| `deploy/monitoring/alerts.yml` | Maintenance window inhibit + post-window alert; `for:` guard raises; cert expiry alert; scheduler heartbeat alert; CAGG lag alert |
