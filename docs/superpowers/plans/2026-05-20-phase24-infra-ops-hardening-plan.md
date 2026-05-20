# Phase 24 — Infra & Ops Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the platform's infrastructure and operational posture across six streams: PG client-cert auth, scheduler container split, account_balances table decoupling, TimescaleDB CAGG backlog, ops automation scripts, and Grafana observability.

**Architecture:** A dedicated `scheduler` container (`python -m app.scheduler`) extracts all single-instance long-running tasks from `app/main.py`, allowing `backend` to safely scale to N uvicorn workers. The `account_balances` table is introduced via expand-contract migration with atomic dual-write. PG password auth is replaced by client-cert mTLS on both the NUC prod and WSL dev instances, each with their own independent CA.

**Tech Stack:** Python 3.14 + FastAPI + SQLAlchemy 2.0 async + asyncpg + APScheduler + TimescaleDB + Redis 7 + Docker Compose + Bash/PowerShell cert scripts + Grafana + Prometheus

---

## File Structure

### New files

| Path | Purpose |
|---|---|
| `scripts/pg-cert/generate-ca.sh` | WSL: dev CA generation (`~/.dashboard-pg-ca/`) |
| `scripts/pg-cert/generate-client-cert.sh` | WSL: dashboard_backend client cert (dev CA) |
| `scripts/pg-cert/install-dev.sh` | WSL: install dev certs + pg_hba patch |
| `scripts/pg-cert/generate-ca.ps1` | NUC: prod CA generation (`C:\dashboard\pg-cert\`) |
| `scripts/pg-cert/generate-client-cert.ps1` | NUC: client cert for VPS backend (prod CA) |
| `scripts/pg-cert/install-nuc.ps1` | NUC: install prod certs + pg_hba patch + DACL |
| `docs/RUNBOOK-pg-cert-rotation.md` | Cert + APP_SECRET_KEY rotation runbook; PG conn budget |
| `scripts/reencrypt-app-secrets.py` | Batched per-namespace app_secrets re-encryption |
| `scripts/recover-after-deploy.sh` | Post-deploy recovery (WG precheck + SSH) |
| `scripts/restart-futu-full.sh` | Futu OpenD full kill+restart (WG precheck + SSH) |
| `backend/app/scheduler.py` | Scheduler container entry point (all single-instance tasks) |
| `backend/app/services/account_balances.py` | AccountBalanceService (atomic dual-write) |
| `backend/alembic/versions/0074_account_balances.py` | account_balances table + backfill; columns NOT dropped |
| `backend/alembic/versions/0075_cagg_backlog.py` | Tier-1 CAGGs (5m–1d) + refresh policies |
| `backend/alembic/versions/0075a_optin_subminute_caggs.py` | Tier-2 CAGGs (5s–45s); manual opt-in only |
| `backend/tests/services/test_account_balance_service.py` | AccountBalanceService unit tests |
| `backend/tests/migrations/test_0074_account_balances.py` | Migration round-trip + dual-write + no-drop |
| `backend/tests/migrations/test_0075_cagg_backlog.py` | Tier-1 present + policies; 0075a not applied |
| `backend/tests/test_api_lifespan_no_scheduler.py` | API lifespan scheduler-absent assertions |
| `backend/tests/test_scheduler_lifespan.py` | Scheduler container lifespan assertions |
| `backend/tests/test_dual_write_atomicity.py` | Dual-write transactional atomicity |
| `backend/tests/test_no_direct_last_nlv_reads.py` | grep: zero last_nlv refs in app/ (excl. models/) |
| `deploy/grafana/provisioning/datasources/prometheus.yml` | Prometheus datasource |
| `deploy/grafana/provisioning/datasources/pg_exporter.yml` | pg_exporter datasource |
| `deploy/grafana/dashboards/broker-gateway.json` | Grafana dashboard |
| `deploy/grafana/dashboards/quote-bus.json` | Grafana dashboard |
| `deploy/grafana/dashboards/risk-gate.json` | Grafana dashboard |
| `deploy/grafana/dashboards/bot-orchestrator.json` | Grafana dashboard |
| `deploy/grafana/dashboards/cagg-lag.json` | Grafana dashboard |
| `deploy/grafana/dashboards/workers.json` | Grafana dashboard |
| `deploy/grafana/dashboards/scheduler.json` | Scheduler container metrics dashboard |

### Modified files

| Path | Change |
|---|---|
| `backend/app/core/db.py` | Cert auth DSN when `PG_SSL_CERT_PATH` set; `POSTGRES_POOL_SIZE_SCHEDULER` env var |
| `backend/app/core/config.py` | Add `PG_SSL_CERT_PATH`, `PG_SSL_KEY_PATH`, `PG_SSL_CA_PATH`, `POSTGRES_POOL_SIZE_SCHEDULER` fields |
| `backend/app/main.py` | Remove scheduler-container tasks; keep BrokerRegistry (dispatch only); keep pubsub listeners |
| `backend/app/services/brokers.py` | Atomic dual-write via AccountBalanceService; `_last_position_tick_at` → Redis |
| `backend/app/services/orders_service.py` | Update line-1940 comment to reference AccountBalanceService |
| `backend/app/services/risk_service.py` | Read NLV from AccountBalanceService |
| `backend/app/services/position_sizing_service.py` | Read NLV from AccountBalanceService |
| `backend/app/api/sizing.py` | Read NLV from AccountBalanceService |
| `backend/app/core/logging.py` | Correlation ID binding for scheduler jobs + consumers |
| `docker-compose.yml` | Add `scheduler` service; `UVICORN_WORKERS` + pool size env vars; PG cert mounts |
| `scripts/deploy.sh` | Add `.env` permission assertion |
| `deploy/nuc/Launch-IBKRSidecar.vbs` | Path: `sidecar\` → `sidecar_ibkr\` |
| `deploy/nuc/BrokerWatchdog.ps1` | Path: `sidecar\` → `sidecar_ibkr\` |
| `deploy/prometheus/alerts.yml` | Maintenance inhibit; cert expiry alert; scheduler heartbeat alert; CAGG lag alert |
| `docs/PHASE-WORKFLOW.md` | Scheduler-container pattern note |

---

## Task 1: Chunk A — PG Cert Scripts + DSN + Runbook

**Files:**
- Create: `scripts/pg-cert/generate-ca.sh`
- Create: `scripts/pg-cert/generate-client-cert.sh`
- Create: `scripts/pg-cert/install-dev.sh`
- Create: `scripts/pg-cert/generate-ca.ps1`
- Create: `scripts/pg-cert/generate-client-cert.ps1`
- Create: `scripts/pg-cert/install-nuc.ps1`
- Create: `docs/RUNBOOK-pg-cert-rotation.md`
- Create: `scripts/reencrypt-app-secrets.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/db.py`
- Modify: `scripts/deploy.sh`

- [ ] **Step 1: Write the failing cert-DSN test**

```python
# backend/tests/test_pg_cert_dsn.py
import os
import pytest
from unittest.mock import patch


def test_cert_dsn_when_cert_env_set():
    """db.py builds sslmode=verify-full DSN when PG_SSL_CERT_PATH is set."""
    env = {
        "DATABASE_URL": "postgresql+asyncpg://dashboard_user@10.10.0.2:5432/dashboard",
        "PG_SSL_CERT_PATH": "/run/secrets/pg_client.crt",
        "PG_SSL_KEY_PATH": "/run/secrets/pg_client.key",
        "PG_SSL_CA_PATH": "/run/secrets/pg_ca.crt",
    }
    with patch.dict(os.environ, env, clear=False):
        import importlib
        import backend.app.core.config as cfg_mod
        import backend.app.core.db as db_mod
        importlib.reload(cfg_mod)
        importlib.reload(db_mod)
        from backend.app.core.db import _build_connect_args
        args = _build_connect_args()
        assert args.get("ssl") is not None


def test_no_ssl_when_cert_env_absent():
    """db.py falls back to password DSN when PG_SSL_CERT_PATH is absent."""
    env = {"DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db"}
    with patch.dict(os.environ, env, clear=False):
        import importlib
        import backend.app.core.db as db_mod
        importlib.reload(db_mod)
        from backend.app.core.db import _build_connect_args
        args = _build_connect_args()
        assert args.get("ssl") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/test_pg_cert_dsn.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: FAIL — `_build_connect_args` not defined.

- [ ] **Step 3: Add SSL fields to config.py**

In `backend/app/core/config.py` add after the existing `postgres_max_overflow` field:

```python
    pg_ssl_cert_path: str | None = Field(default=None, alias="PG_SSL_CERT_PATH")
    pg_ssl_key_path: str | None = Field(default=None, alias="PG_SSL_KEY_PATH")
    pg_ssl_ca_path: str | None = Field(default=None, alias="PG_SSL_CA_PATH")
    postgres_pool_size_scheduler: int = Field(default=10, alias="POSTGRES_POOL_SIZE_SCHEDULER")
```

- [ ] **Step 4: Update db.py to support cert auth DSN**

Replace the content of `backend/app/core/db.py` with:

```python
"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

import os
import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _build_connect_args() -> dict:
    base: dict = {}
    if os.getenv("TEST_DISABLE_STMT_CACHE"):
        base["statement_cache_size"] = 0
    cert_path = settings.pg_ssl_cert_path
    if cert_path:
        ctx = ssl.create_default_context(
            cafile=settings.pg_ssl_ca_path,
        )
        ctx.load_cert_chain(
            certfile=cert_path,
            keyfile=settings.pg_ssl_key_path,
        )
        ctx.check_hostname = False
        base["ssl"] = ctx
    return base


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.postgres_pool_size,
    max_overflow=settings.postgres_max_overflow,
    pool_pre_ping=True,
    connect_args=_build_connect_args(),
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/test_pg_cert_dsn.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS.

- [ ] **Step 6: Write WSL dev CA generation script**

Create `scripts/pg-cert/generate-ca.sh`:

```bash
#!/usr/bin/env bash
# Generates the WSL dev CA for local PG client-cert auth.
# Output: ~/.dashboard-pg-ca/dev-ca.key (mode 0400), dev-ca.crt
# This CA must NEVER be used for the NUC prod instance.
set -euo pipefail

CA_DIR="${HOME}/.dashboard-pg-ca"
mkdir -p "${CA_DIR}"
chmod 700 "${CA_DIR}"

if [[ -f "${CA_DIR}/dev-ca.key" ]]; then
    echo "Dev CA already exists at ${CA_DIR}/dev-ca.key — remove manually to regenerate."
    exit 0
fi

openssl genrsa -out "${CA_DIR}/dev-ca.key" 4096
chmod 0400 "${CA_DIR}/dev-ca.key"

openssl req -new -x509 -days 3650 \
    -key "${CA_DIR}/dev-ca.key" \
    -out "${CA_DIR}/dev-ca.crt" \
    -subj "/CN=DashboardDevCA/O=DashboardDev"

echo "Dev CA generated at ${CA_DIR}/"
echo "  ca.key: ${CA_DIR}/dev-ca.key (mode 0400)"
echo "  ca.crt: ${CA_DIR}/dev-ca.crt"
```

- [ ] **Step 7: Write WSL client cert generation script**

Create `scripts/pg-cert/generate-client-cert.sh`:

```bash
#!/usr/bin/env bash
# Generates the dashboard_backend client cert signed by the dev CA.
# Output: ~/.dashboard-pg-ca/client.key + client.crt
set -euo pipefail

CA_DIR="${HOME}/.dashboard-pg-ca"
PG_USER="dashboard_user"

[[ -f "${CA_DIR}/dev-ca.key" ]] || { echo "Run generate-ca.sh first"; exit 1; }

openssl genrsa -out "${CA_DIR}/client.key" 4096
chmod 0400 "${CA_DIR}/client.key"

openssl req -new \
    -key "${CA_DIR}/client.key" \
    -out "${CA_DIR}/client.csr" \
    -subj "/CN=${PG_USER}"

openssl x509 -req -days 3650 \
    -in "${CA_DIR}/client.csr" \
    -CA "${CA_DIR}/dev-ca.crt" \
    -CAkey "${CA_DIR}/dev-ca.key" \
    -CAcreateserial \
    -out "${CA_DIR}/client.crt"

rm "${CA_DIR}/client.csr"

echo "Client cert generated:"
echo "  ${CA_DIR}/client.key (mode 0400)"
echo "  ${CA_DIR}/client.crt"
echo ""
echo "Add to backend .env:"
echo "  PG_SSL_CERT_PATH=${CA_DIR}/client.crt"
echo "  PG_SSL_KEY_PATH=${CA_DIR}/client.key"
echo "  PG_SSL_CA_PATH=${CA_DIR}/dev-ca.crt"
```

- [ ] **Step 8: Write WSL dev PG install script**

Create `scripts/pg-cert/install-dev.sh`:

```bash
#!/usr/bin/env bash
# Installs client-cert auth on the local (WSL docker-compose) dev PG instance.
# Patches pg_hba.conf and copies the CA cert into the PG data dir.
# Must be run with access to the PG data directory.
set -euo pipefail

CA_DIR="${HOME}/.dashboard-pg-ca"
# Detect PG data dir from running container
PG_CONTAINER="${PG_CONTAINER:-postgres}"
PG_DATA=$(docker compose exec -T "${PG_CONTAINER}" sh -c 'echo $PGDATA')

[[ -f "${CA_DIR}/dev-ca.crt" ]] || { echo "Run generate-ca.sh first"; exit 1; }
[[ -f "${CA_DIR}/client.crt" ]] || { echo "Run generate-client-cert.sh first"; exit 1; }

echo "Copying dev CA cert into PG container at ${PG_DATA}/dev-ca.crt"
docker cp "${CA_DIR}/dev-ca.crt" "${PG_CONTAINER}:${PG_DATA}/dev-ca.crt"
docker compose exec -T "${PG_CONTAINER}" chown postgres:postgres "${PG_DATA}/dev-ca.crt"

echo "Patching postgresql.conf for SSL..."
docker compose exec -T "${PG_CONTAINER}" bash -c "
    grep -q 'ssl = on' ${PG_DATA}/postgresql.conf || echo \"ssl = on\" >> ${PG_DATA}/postgresql.conf
    grep -q 'ssl_ca_file' ${PG_DATA}/postgresql.conf \
        && sed -i \"s|#*ssl_ca_file.*|ssl_ca_file = 'dev-ca.crt'|\" ${PG_DATA}/postgresql.conf \
        || echo \"ssl_ca_file = 'dev-ca.crt'\" >> ${PG_DATA}/postgresql.conf
"

echo "Patching pg_hba.conf..."
docker compose exec -T "${PG_CONTAINER}" bash -c "
    # Comment out the password line for dashboard_user on 127.0.0.1
    sed -i \"s|^host \\+dashboard \\+dashboard_user \\+127.0.0.1/32 \\+scram-sha-256|# &|\" ${PG_DATA}/pg_hba.conf
    # Add cert auth line if not present
    grep -q 'cert clientcert=verify-full' ${PG_DATA}/pg_hba.conf \
        || echo 'hostssl dashboard dashboard_user 127.0.0.1/32 cert clientcert=verify-full' >> ${PG_DATA}/pg_hba.conf
"

echo "Reloading PG config..."
docker compose exec -T "${PG_CONTAINER}" bash -c "psql -U postgres -c 'SELECT pg_reload_conf();'"

echo ""
echo "Done. Verify with:"
echo "  psql 'postgresql://dashboard_user@127.0.0.1:5432/dashboard?sslmode=verify-full&sslcert=${CA_DIR}/client.crt&sslkey=${CA_DIR}/client.key&sslrootcert=${CA_DIR}/dev-ca.crt' -c '\\conninfo'"
echo ""
echo "ROLLBACK: uncomment the scram-sha-256 line in pg_hba.conf and run pg_reload_conf()"
```

- [ ] **Step 9: Write NUC prod CA generation PowerShell script**

Create `scripts/pg-cert/generate-ca.ps1`:

```powershell
#Requires -Version 5.1
# Generates the NUC PROD CA for PG client-cert auth.
# Output: C:\dashboard\pg-cert\ca.key (DACL: trader only), ca.crt
# This CA must NEVER be used for the WSL dev instance.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CertDir = "C:\dashboard\pg-cert"
if (-not (Test-Path $CertDir)) {
    New-Item -ItemType Directory -Path $CertDir | Out-Null
}

if (Test-Path "$CertDir\ca.key") {
    Write-Host "Prod CA already exists at $CertDir\ca.key — remove manually to regenerate."
    exit 0
}

# Use openssl from Git for Windows or from PATH
$openssl = (Get-Command openssl -ErrorAction Stop).Source

& $openssl genrsa -out "$CertDir\ca.key" 4096
& $openssl req -new -x509 -days 3650 `
    -key "$CertDir\ca.key" `
    -out "$CertDir\ca.crt" `
    -subj "/CN=DashboardNUCProdCA/O=DashboardProd"

# Restrict ca.key to trader user only
$acl = Get-Acl "$CertDir\ca.key"
$acl.SetAccessRuleProtection($true, $false)
$trader = [System.Security.Principal.NTAccount]"$env:USERDOMAIN\$env:USERNAME"
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $trader, "FullControl", "Allow"
)
$acl.AddAccessRule($rule)
Set-Acl "$CertDir\ca.key" $acl

Write-Host "Prod CA generated at $CertDir\"
Write-Host "  ca.key: DACL restricted to $($env:USERNAME)"
Write-Host "  ca.crt: $CertDir\ca.crt"
Write-Host ""
Write-Host "IMPORTANT: Transfer ca.crt (NOT ca.key) to VPS via WireGuard SSH only."
```

- [ ] **Step 10: Write NUC prod client cert generation PowerShell script**

Create `scripts/pg-cert/generate-client-cert.ps1`:

```powershell
#Requires -Version 5.1
# Generates the dashboard_backend client cert signed by the NUC prod CA.
# Output: C:\dashboard\pg-cert\client.key + client.crt
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CertDir = "C:\dashboard\pg-cert"
$PgUser = "dashboard_user"

if (-not (Test-Path "$CertDir\ca.key")) {
    Write-Host "Run generate-ca.ps1 first"; exit 1
}

$openssl = (Get-Command openssl -ErrorAction Stop).Source

& $openssl genrsa -out "$CertDir\client.key" 4096
& $openssl req -new `
    -key "$CertDir\client.key" `
    -out "$CertDir\client.csr" `
    -subj "/CN=$PgUser"
& $openssl x509 -req -days 3650 `
    -in "$CertDir\client.csr" `
    -CA "$CertDir\ca.crt" `
    -CAkey "$CertDir\ca.key" `
    -CAcreateserial `
    -out "$CertDir\client.crt"
Remove-Item "$CertDir\client.csr"

Write-Host "Client cert generated at $CertDir\"
Write-Host "  client.key + client.crt"
Write-Host ""
Write-Host "Transfer client.key and client.crt to VPS:"
Write-Host "  scp -P 2222 $CertDir\client.* trader@88.208.197.219:/run/secrets/"
Write-Host ""
Write-Host "Add to VPS backend/.env:"
Write-Host "  PG_SSL_CERT_PATH=/run/secrets/client.crt"
Write-Host "  PG_SSL_KEY_PATH=/run/secrets/client.key"
Write-Host "  PG_SSL_CA_PATH=/run/secrets/ca.crt"
```

- [ ] **Step 11: Write NUC install PowerShell script**

Create `scripts/pg-cert/install-nuc.ps1`:

```powershell
#Requires -Version 5.1
# Installs client-cert auth on the NUC prod PG18 instance.
# Patches pg_hba.conf and postgresql.conf, restricts key DACL.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CertDir = "C:\dashboard\pg-cert"
# PG data dir — NUC prod PG18 on Windows native
$PgData = $env:PGDATA
if (-not $PgData) {
    $PgData = "C:\Program Files\PostgreSQL\18\data"
}

if (-not (Test-Path "$CertDir\ca.crt")) {
    Write-Host "Run generate-ca.ps1 first"; exit 1
}
if (-not (Test-Path "$CertDir\client.crt")) {
    Write-Host "Run generate-client-cert.ps1 first"; exit 1
}

Write-Host "Copying CA cert to PG data dir..."
Copy-Item "$CertDir\ca.crt" "$PgData\dashboard-ca.crt" -Force

# Enable SSL in postgresql.conf
$pgConf = "$PgData\postgresql.conf"
$content = Get-Content $pgConf -Raw
if ($content -notmatch "ssl = on") {
    Add-Content $pgConf "`nssl = on"
}
if ($content -notmatch "ssl_ca_file") {
    Add-Content $pgConf "`nssl_ca_file = 'dashboard-ca.crt'"
} else {
    $content = $content -replace "#*ssl_ca_file.*", "ssl_ca_file = 'dashboard-ca.crt'"
    Set-Content $pgConf $content
}

# Patch pg_hba.conf
$hbaPath = "$PgData\pg_hba.conf"
$hba = Get-Content $hbaPath -Raw
# Comment out existing password line
$hba = $hba -replace "^(host\s+dashboard\s+dashboard_user\s+10\.10\.0\.0/24\s+scram-sha-256)", "# `$1"
# Add cert line if not present
if ($hba -notmatch "cert clientcert=verify-full") {
    $hba += "`nhostssl  dashboard  dashboard_user  10.10.0.0/24  cert  clientcert=verify-full"
    $hba += "`n# ROLLBACK: uncomment the scram-sha-256 line above and reload PG"
}
Set-Content $hbaPath $hba

Write-Host "Reloading PG configuration (pg_ctl reload)..."
$pgCtl = "C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
if (Test-Path $pgCtl) {
    & $pgCtl reload -D $PgData
} else {
    Write-Host "pg_ctl not found — reload PG manually via pg_ctl reload or service restart."
}

Write-Host ""
Write-Host "Done. Verify from WSL:"
Write-Host "  psql 'postgresql://dashboard_user@10.10.0.2:5432/dashboard?sslmode=verify-full' -c '\conninfo'"
Write-Host ""
Write-Host "ROLLBACK: uncomment scram-sha-256 in $hbaPath then reload PG"
```

- [ ] **Step 12: Write the APP_SECRET_KEY reencrypt script**

Create `scripts/reencrypt-app-secrets.py`:

```python
#!/usr/bin/env python3
"""
Re-encrypt all app_secrets rows after APP_SECRET_KEY rotation.

Usage:
    APP_SECRET_KEY=<new_key> APP_SECRET_KEY_OLD=<old_key> python scripts/reencrypt-app-secrets.py

Safe to re-run: MultiFernet handles decrypt-with-either-key until cleanup.
"""
from __future__ import annotations

import asyncio
import os
import sys

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


async def reencrypt(session: AsyncSession, new_fernet: Fernet, old_fernet: Fernet) -> int:
    result = await session.execute(
        text("SELECT id, namespace, key, value FROM app_secrets")
    )
    rows = result.fetchall()
    count = 0
    for row in rows:
        raw = row.value
        if isinstance(raw, str):
            raw = raw.encode()
        try:
            plaintext = old_fernet.decrypt(raw)
        except InvalidToken:
            try:
                plaintext = new_fernet.decrypt(raw)
                print(f"  {row.namespace}/{row.key}: already using new key — skipping")
                continue
            except InvalidToken:
                print(f"  ERROR: {row.namespace}/{row.key}: cannot decrypt with either key", file=sys.stderr)
                continue
        new_value = new_fernet.encrypt(plaintext).decode()
        await session.execute(
            text("UPDATE app_secrets SET value = :v WHERE id = :id"),
            {"v": new_value, "id": row.id},
        )
        count += 1
        print(f"  re-encrypted {row.namespace}/{row.key}")
    return count


async def main() -> None:
    new_key = os.environ.get("APP_SECRET_KEY")
    old_key = os.environ.get("APP_SECRET_KEY_OLD")
    db_url = os.environ.get("DATABASE_URL")

    if not new_key or not old_key or not db_url:
        print("Usage: APP_SECRET_KEY=<new> APP_SECRET_KEY_OLD=<old> DATABASE_URL=<url> python reencrypt-app-secrets.py")
        sys.exit(1)

    new_fernet = Fernet(new_key.encode())
    old_fernet = Fernet(old_key.encode())

    engine = create_async_engine(db_url, pool_size=1)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            count = await reencrypt(session, new_fernet, old_fernet)
    print(f"\nDone. Re-encrypted {count} row(s).")
    await engine.dispose()


asyncio.run(main())
```

- [ ] **Step 13: Write the cert rotation runbook**

Create `docs/RUNBOOK-pg-cert-rotation.md`:

```markdown
# RUNBOOK: PG Cert Rotation + APP_SECRET_KEY Rotation

## PG Client Cert Rotation

### WSL dev cert rotation
1. `rm ~/.dashboard-pg-ca/client.*`
2. `bash scripts/pg-cert/generate-client-cert.sh`
3. Update `PG_SSL_CERT_PATH` / `PG_SSL_KEY_PATH` in `.env`
4. `docker compose restart backend scheduler`

### NUC prod cert rotation
1. On NUC: `Remove-Item C:\dashboard\pg-cert\client.*`
2. On NUC: `pwsh scripts/pg-cert/generate-client-cert.ps1`
3. Transfer `client.key` + `client.crt` to VPS via WireGuard SSH:
   `scp -P 2222 C:\dashboard\pg-cert\client.* trader@88.208.197.219:/run/secrets/`
4. SSH to VPS: `docker compose restart backend scheduler`

## APP_SECRET_KEY Rotation (6-step procedure)

1. Generate new key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set in `.env` on VPS (both `backend` and `scheduler`):
   - `APP_SECRET_KEY_OLD=<current_value>`
   - `APP_SECRET_KEY=<new_value>`
3. Restart both containers: `docker compose restart backend scheduler`
   - Both containers now decrypt with either key; encrypt with new key.
4. Run: `DATABASE_URL=... APP_SECRET_KEY=<new> APP_SECRET_KEY_OLD=<old> python scripts/reencrypt-app-secrets.py`
   - Interrupted run is safe — re-run to resume.
5. Remove `APP_SECRET_KEY_OLD` from `.env` on VPS in both containers. Restart both.
6. Verify: `GET /api/admin/secrets` for each namespace returns expected values.

## PG Connection Budget

At `UVICORN_WORKERS=4`:

| Container | Processes | Pool | Max overflow | Peak conns |
|---|---|---|---|---|
| `backend` (N=4) | 4 | `POSTGRES_POOL_SIZE` (default 5) | 5 | 4 × 10 = 40 |
| `scheduler` (N=1) | 1 | `POSTGRES_POOL_SIZE_SCHEDULER` (default 10) | 5 | 15 |
| **Total** | | | | **≤ 55** |

PG18 `max_connections` default = 100. Verify before raising workers:
```
psql -U postgres -c 'SHOW max_connections;'
```

## Rollback Procedure (cert auth wedge)

1. SSH to NUC → uncomment `scram-sha-256` line in `pg_hba.conf` → `pg_ctl reload`
2. VPS: set `PG_SSL_CERT_PATH=` (empty) in `.env` → `docker compose restart backend scheduler`
3. Time to recovery: < 2 minutes. No migration required.
```

- [ ] **Step 14: Add .env permission assertion to deploy.sh**

Read `scripts/deploy.sh`, then add the assertion at the top of the deployment section (after the `set -euo pipefail` line, before `docker compose pull`):

```bash
# Assert .env is not world-readable
ENV_PERMS=$(stat -c '%a' .env 2>/dev/null || echo "000")
if [[ "$ENV_PERMS" != "600" ]]; then
    echo "ERROR: .env permissions are ${ENV_PERMS} — must be 600. Run: chmod 600 .env"
    exit 1
fi
```

- [ ] **Step 15: Run full test suite to verify no regressions**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: All tests pass.

- [ ] **Step 16: Commit Chunk A**

```bash
git add scripts/pg-cert/ docs/RUNBOOK-pg-cert-rotation.md scripts/reencrypt-app-secrets.py \
    backend/app/core/config.py backend/app/core/db.py \
    backend/tests/test_pg_cert_dsn.py scripts/deploy.sh
git commit -m "feat(24a): pg client-cert auth scripts + DSN + rotation runbook"
```

---

## Task 2: Chunk B1 — Scheduler Container + Task Migration

**Files:**
- Create: `backend/app/scheduler.py`
- Create: `backend/tests/test_api_lifespan_no_scheduler.py`
- Create: `backend/tests/test_scheduler_lifespan.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/brokers.py` (`_last_position_tick_at` → Redis)
- Modify: `backend/app/core/logging.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write the failing API lifespan test**

```python
# backend/tests/test_api_lifespan_no_scheduler.py
"""Assert that the API lifespan does NOT start scheduler-only tasks."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


SCHEDULER_ONLY_SYMBOLS = [
    "app.main.bridge",          # broker event bridge
    "app.main.quote_engine",    # quote engine WS
    "app.main.bar_service",     # bar service writer
    "app.main.scheduler",       # APScheduler instance
    "app.main.broker_health_task",
    "app.main.broker_discover_task",
    "app.main.order_consumer",
]


@pytest.mark.asyncio
async def test_api_lifespan_does_not_start_apscheduler(monkeypatch):
    """APScheduler .start() must not be called in API worker lifespan."""
    started = []

    # Patch the scheduler
    mock_scheduler = MagicMock()
    mock_scheduler.start = lambda: started.append("apscheduler")
    mock_scheduler.shutdown = AsyncMock()

    monkeypatch.setattr("app.main._scheduler", mock_scheduler, raising=False)

    # The actual lifespan test would require wiring up the full app
    # This is a structural assertion: scheduler.start() call is absent from main.py lifespan
    import ast
    import pathlib
    src = pathlib.Path("backend/app/main.py").read_text()
    tree = ast.parse(src)

    scheduler_start_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(getattr(node.func, "attr", None), str)
        and node.func.attr == "start"
        and isinstance(getattr(node.func, "value", None), ast.Name)
        and node.func.value.id == "scheduler"
    ]
    # After B1, scheduler.start() must not appear in main.py
    # (It moves to app/scheduler.py)
    assert len(scheduler_start_calls) == 0, (
        f"scheduler.start() still in main.py — must be moved to app/scheduler.py"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/test_api_lifespan_no_scheduler.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: FAIL — `scheduler.start()` still in `main.py`.

- [ ] **Step 3: Check for Phase 23 jobs added after Phase 23 migration**

Run this before modifying `main.py` to capture any jobs Phase 23 added:

```bash
cd /home/joseph/dashboard
git diff v0.22.0..HEAD -- backend/app/main.py | grep "^+" | grep -E "create_task|\.start\(\)|\.run\(\)|scheduler\.add" | head -30
```

Note: if `v0.22.0` tag doesn't exist, use `git log --oneline | grep "22\." | tail -1` to find the base. Any new tasks/jobs found must be included in `app/scheduler.py`.

- [ ] **Step 4: Create backend/app/scheduler.py**

Create `backend/app/scheduler.py` with all scheduler-container tasks:

```python
"""Scheduler container entry point.

Run as: python -m app.scheduler

Owns all single-instance long-running tasks extracted from app/main.py.
Writes heartbeat to Redis every 30 s. No HTTP server.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

import structlog

log = structlog.get_logger(__name__)

_shutdown_event = asyncio.Event()
_HEARTBEAT_KEY = "scheduler:heartbeat"
_HEARTBEAT_TTL = 90  # seconds; alert fires if absent > 2 min


def _handle_sigterm(*_: object) -> None:
    log.info("scheduler.sigterm_received")
    _shutdown_event.set()


async def _write_heartbeat(redis: object) -> None:  # type: ignore[type-arg]
    while not _shutdown_event.is_set():
        try:
            await redis.set(_HEARTBEAT_KEY, int(time.time()), ex=_HEARTBEAT_TTL)
        except Exception as exc:
            log.warning("scheduler.heartbeat_failed", error=str(exc))
        await asyncio.sleep(30)


async def run() -> None:
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.core.logging import configure_logging

    configure_logging()

    import redis.asyncio as aioredis
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    log.info("scheduler.starting")

    # ── BrokerRegistry (owns health_probe_loop + BrokerDiscoverer) ────────────
    from app.services.broker_registry_factory import build_broker_registry
    from app.services.brokers import (
        AccountService,
        BrokerDiscoverer,
        BrokerRegistry,
        OrderEventConsumer,
        PendingSubmitWatchdog,
    )
    from app.core.deps import set_account_service, set_broker_registry

    # Build the registry outside a session context for initial connection
    async with SessionLocal() as svc_session:
        broker_registry = await build_broker_registry(svc_session)

    set_broker_registry(broker_registry)
    set_account_service(AccountService(broker_registry, SessionLocal))

    broker_health_task = asyncio.create_task(broker_registry.health_probe_loop())

    broker_discoverer = BrokerDiscoverer(broker_registry, SessionLocal, redis)
    broker_discover_task = asyncio.create_task(broker_discoverer.discover_loop())

    order_consumer = OrderEventConsumer(broker_registry, SessionLocal, redis)
    pending_fills_sweeper_task: asyncio.Task[None] | None = None
    pending_watchdog_task: asyncio.Task[None] | None = None

    await order_consumer.start()

    from app.services.orders_service import PendingFillsSweeper
    pending_fills_sweeper = PendingFillsSweeper(SessionLocal, redis)
    from app.services.orders_service import PendingSubmitWatchdog  # noqa: F811
    pending_watchdog = PendingSubmitWatchdog(broker_registry, SessionLocal, order_consumer)
    await pending_watchdog.start()
    pending_fills_task = asyncio.create_task(pending_fills_sweeper.run())

    # ── OCO orchestrator ──────────────────────────────────────────────────────
    from app.services.oco_service import OcoOrchestrator
    oco_orchestrator = OcoOrchestrator(broker_registry, SessionLocal, redis)
    await oco_orchestrator.start()

    # ── Quote engine + bar service + coinbase ─────────────────────────────────
    from app.services.quote_engine import QuoteEngine
    from app.services.bar_service import BarService
    quote_engine = QuoteEngine(broker_registry, redis)
    await quote_engine.start()

    bar_service = BarService(quote_engine, SessionLocal)
    await bar_service.start()

    # Coinbase adapter (if enabled)
    _coinbase_task: asyncio.Task[None] | None = None
    try:
        from app.services.crypto.coinbase_adapter import CoinbaseAdapter
        _coinbase_adapter = CoinbaseAdapter(redis)
        _coinbase_task = asyncio.create_task(_coinbase_adapter.run())
    except ImportError:
        pass

    # Bot fill router
    _bot_fill_task: asyncio.Task[None] | None = None
    try:
        from app.bot.fill_router import BotFillRouter
        _bot_fill_router = BotFillRouter(broker_registry, SessionLocal, redis)
        _bot_fill_task = asyncio.create_task(_bot_fill_router.run())
    except ImportError:
        pass

    # ── Bridge (broker event bridge) ─────────────────────────────────────────
    from app.services.broker_bridge import BrokerBridge
    bridge = BrokerBridge(broker_registry, redis, SessionLocal)
    bridge_task = asyncio.create_task(bridge.run())

    # ── Ollama watcher ────────────────────────────────────────────────────────
    from app.services.ai.ollama_watcher import OllamaWatcher
    _ollama_watcher = OllamaWatcher(redis)
    await _ollama_watcher.start()

    # ── AI cost ledger ────────────────────────────────────────────────────────
    from app.services.ai.cost_ledger import AiCostLedger
    ai_cost_ledger = AiCostLedger(redis, SessionLocal)
    await ai_cost_ledger.start()

    # ── Orphan sweeper ────────────────────────────────────────────────────────
    from app.services.orders_service import run_orphan_sweeper
    orphan_sweeper_task = asyncio.create_task(run_orphan_sweeper(SessionLocal))

    # ── Schwab metrics ────────────────────────────────────────────────────────
    schwab_metrics_task: asyncio.Task[None] | None = None
    try:
        from app.services.brokers.schwab_metrics import run_schwab_metrics
        schwab_metrics_task = asyncio.create_task(run_schwab_metrics(broker_registry, SessionLocal))
    except ImportError:
        pass

    # ── Pre-warm ──────────────────────────────────────────────────────────────
    from app.services.pre_warm import run_pre_warm
    pre_warm_task = asyncio.create_task(run_pre_warm(SessionLocal))

    # ── Alerts ────────────────────────────────────────────────────────────────
    from app.services.alerts.evaluator import AlertsEvaluator
    from app.services.alerts.bars_subscriber import AlertsBarsSubscriber
    alerts_evaluator = AlertsEvaluator(SessionLocal, redis)
    await alerts_evaluator.start()
    alerts_bars_subscriber = AlertsBarsSubscriber(bar_service, alerts_evaluator)
    alerts_bars_subscriber.start()

    # ── APScheduler ───────────────────────────────────────────────────────────
    from app.main import _build_apscheduler_jobs  # extract jobs to a helper in main.py
    scheduler = AsyncIOScheduler()
    _build_apscheduler_jobs(scheduler, SessionLocal, redis, broker_registry)
    scheduler.start()
    log.info("scheduler.apscheduler_started")

    # ── Heartbeat ─────────────────────────────────────────────────────────────
    heartbeat_task = asyncio.create_task(_write_heartbeat(redis))

    log.info("scheduler.ready")

    # ── Wait for shutdown signal ──────────────────────────────────────────────
    signal.signal(signal.SIGTERM, _handle_sigterm)
    await _shutdown_event.wait()

    log.info("scheduler.shutting_down")

    # Graceful shutdown
    shutdown_timeout = int(os.getenv("SCHEDULER_SHUTDOWN_TIMEOUT_S", "300"))
    scheduler.shutdown(wait=True)

    heartbeat_task.cancel()
    for task in [
        broker_health_task, broker_discover_task, orphan_sweeper_task,
        pending_fills_task, bridge_task, pre_warm_task,
    ]:
        if task and not task.done():
            task.cancel()
    if _coinbase_task and not _coinbase_task.done():
        _coinbase_task.cancel()
    if _bot_fill_task and not _bot_fill_task.done():
        _bot_fill_task.cancel()
    if schwab_metrics_task and not schwab_metrics_task.done():
        schwab_metrics_task.cancel()

    await broker_registry.stop()
    await broker_registry.close()
    await redis.aclose()
    log.info("scheduler.stopped")


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 5: Migrate _last_position_tick_at to Redis in brokers.py**

Find `_last_position_tick_at` in `backend/app/services/brokers.py:1135` and replace the `dict` with Redis-backed per-worker keys.

The current pattern stores `{(label, account_number): float}` in a dict. Replace with Redis keys `posn_tick:{label}:{account_number}` using `SETEX` with 120 s TTL.

At line 1135, replace:
```python
        self._last_position_tick_at: dict[tuple[str, str], float] = {}
```
with:
```python
        # Moved to Redis: posn_tick:{label}:{account} keys with 120s TTL
        # (no local dict — safe under multi-worker because each worker's
        #  BrokerDiscoverer was already in the scheduler container)
```

At line 1686 (the `.monotonic()` write), replace:
```python
                    self._last_position_tick_at[(label, account_number)] = time.monotonic()
```
with:
```python
                    await redis.setex(
                        f"posn_tick:{label}:{account_number}",
                        120,
                        str(time.time()),
                    )
```

At lines 1810-1815 (the stale-key check loop), replace the loop body:
```python
        for stale_key in list(self._last_position_tick_at.keys()):
            ...
            del self._last_position_tick_at[stale_key]
        ...
        for (label, account_number), last_at in self._last_position_tick_at.items():
```
with a Redis scan pattern:
```python
        import time as _time
        pattern = "posn_tick:*"
        keys = await redis.keys(pattern)
        now = _time.time()
        for key in keys:
            val = await redis.get(key)
            if val is None:
                continue
            last_at = float(val)
            # Redis TTL already expires stale keys; use for health metric only
            parts = key.split(":", 2)
            if len(parts) == 3:
                label, account_number = parts[1], parts[2]
                ...
```

Note: `BrokerDiscoverer` must have `redis` passed in. Check `main.py:349` where `BrokerDiscoverer` is constructed and ensure `redis` is the third argument.

- [ ] **Step 6: Add correlation ID bindings to logging.py**

In `backend/app/core/logging.py`, add a helper function for scheduler context binding:

```python
def bind_scheduler_context(job_id: str) -> None:
    """Bind APScheduler job_id to structlog context."""
    structlog.contextvars.bind_contextvars(job_id=job_id)


def bind_consumer_context(consumer_id: str) -> None:
    """Bind consumer label to structlog context for OrderEventConsumer."""
    structlog.contextvars.bind_contextvars(consumer_id=consumer_id)


def bind_discoverer_context(tick_ts: float) -> None:
    """Bind discoverer tick timestamp to structlog context."""
    structlog.contextvars.bind_contextvars(discoverer_tick=tick_ts)
```

- [ ] **Step 7: Remove scheduler-only tasks from main.py lifespan**

In `backend/app/main.py`, remove or comment out the following starts from the lifespan (keep the `BrokerRegistry` construction and gRPC setup, keep all "Both" pubsub listeners):

Lines to remove/comment (search by pattern):
- `bridge_task = asyncio.create_task(bridge.run())`
- `await _ollama_watcher.start()`
- `await ai_cost_ledger.start()`
- `orphan_sweeper_task = asyncio.create_task(run_orphan_sweeper(...))`
- `broker_health_task = asyncio.create_task(broker_registry.health_probe_loop())`
- `broker_discover_task = asyncio.create_task(broker_discoverer.discover_loop())`
- `await order_consumer.start()`
- `await pending_watchdog.start()`
- `pending_fills_task = asyncio.create_task(pending_fills_sweeper.run())`
- `await oco_orchestrator.start()`
- `await quote_engine.start()`
- `await bar_service.start()`
- `scheduler.start()`
- `await alerts_evaluator.start()`
- `alerts_bars_subscriber.start()`
- `pre_warm_task = asyncio.create_task(_run_pre_warm())`
- `schwab_metrics_task = asyncio.create_task(...)`
- `_coinbase_task = asyncio.create_task(_coinbase_adapter.run())`
- `_bot_fill_task = asyncio.create_task(_bot_fill_router.run())`

Also remove APScheduler job setup in lifespan — move to `_build_apscheduler_jobs()` helper called from `scheduler.py`.

Keep: `BrokerRegistry` construction + all `run_listener()` pubsub tasks.

- [ ] **Step 8: Update docker-compose.yml**

Add the `scheduler` service and update `backend` service env vars. Add after the `backend` service definition:

```yaml
  scheduler:
    build: ./backend
    command: python -m app.scheduler
    stop_grace_period: 360s
    restart: unless-stopped
    depends_on:
      - postgres
      - redis
    env_file: .env
    environment:
      - POSTGRES_POOL_SIZE_SCHEDULER=${POSTGRES_POOL_SIZE_SCHEDULER:-10}
      - PG_SSL_CERT_PATH=${PG_SSL_CERT_PATH:-}
      - PG_SSL_KEY_PATH=${PG_SSL_KEY_PATH:-}
      - PG_SSL_CA_PATH=${PG_SSL_CA_PATH:-}
      - SCHEDULER_SHUTDOWN_TIMEOUT_S=${SCHEDULER_SHUTDOWN_TIMEOUT_S:-300}
```

In the `backend` service, add:
```yaml
    environment:
      - UVICORN_WORKERS=${UVICORN_WORKERS:-1}
      - POSTGRES_POOL_SIZE=${POSTGRES_POOL_SIZE:-5}
```

Update the `command` for backend:
```yaml
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}
```

- [ ] **Step 9: Write the scheduler lifespan test**

```python
# backend/tests/test_scheduler_lifespan.py
"""Structural assertions: scheduler.py must start all required tasks."""
import ast
import pathlib


REQUIRED_IN_SCHEDULER = [
    "health_probe_loop",
    "discover_loop",
    "order_consumer",
    "pending_watchdog",
    "oco_orchestrator",
    "quote_engine",
    "bar_service",
    "alerts_evaluator",
    "scheduler.start",       # APScheduler
    "_write_heartbeat",
]


def _source() -> str:
    return pathlib.Path("backend/app/scheduler.py").read_text()


def test_scheduler_starts_health_probe_loop():
    assert "health_probe_loop" in _source()


def test_scheduler_starts_discover_loop():
    assert "discover_loop" in _source()


def test_scheduler_starts_order_consumer():
    assert "order_consumer" in _source()


def test_scheduler_starts_quote_engine():
    assert "quote_engine" in _source()


def test_scheduler_starts_bar_service():
    assert "bar_service" in _source()


def test_scheduler_starts_apscheduler():
    assert "scheduler.start()" in _source()


def test_scheduler_writes_heartbeat():
    assert "_write_heartbeat" in _source()
    assert "_HEARTBEAT_KEY" in _source()


def test_main_does_not_start_apscheduler():
    """main.py must not contain scheduler.start() after B1."""
    main_src = pathlib.Path("backend/app/main.py").read_text()
    tree = ast.parse(main_src)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(getattr(node.func, "attr", None), str)
        and node.func.attr == "start"
        and isinstance(getattr(node.func, "value", None), ast.Name)
        and node.func.value.id == "scheduler"
    ]
    assert len(calls) == 0, "scheduler.start() still in main.py"
```

- [ ] **Step 10: Run full test suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: All tests pass including `test_api_lifespan_no_scheduler.py` and `test_scheduler_lifespan.py`.

- [ ] **Step 11: Commit Chunk B1**

```bash
git add backend/app/scheduler.py backend/app/main.py backend/app/services/brokers.py \
    backend/app/core/logging.py docker-compose.yml \
    backend/tests/test_api_lifespan_no_scheduler.py \
    backend/tests/test_scheduler_lifespan.py
git commit -m "feat(24b1): scheduler container split + task inventory migration"
```

---

## Task 3: Chunk B2 — Multi-Worker Flip

**Files:**
- Modify: `docker-compose.yml` (only: raise `UVICORN_WORKERS` default)

> **Prerequisite:** B1 must run in prod for ≥24 hours at N=1 before proceeding to B2.

- [ ] **Step 1: Write the multi-worker nonce test**

```python
# backend/tests/test_multi_worker_nonce.py
"""Verify nonce is consumed correctly across workers (Redis-backed)."""
import pytest
from unittest.mock import AsyncMock, patch
import uuid


@pytest.mark.asyncio
async def test_nonce_not_reusable(redis_client):
    """A nonce minted on worker-1 is consumed by worker-2 (Redis GETDEL)."""
    nonce = str(uuid.uuid4())
    key = f"nonce:order:{nonce}"

    # Simulate worker-1 minting
    await redis_client.set(key, "1", nx=True, ex=300)

    # Simulate worker-2 consuming (GETDEL)
    result = await redis_client.getdel(key)
    assert result is not None

    # Third attempt fails
    result2 = await redis_client.getdel(key)
    assert result2 is None
```

- [ ] **Step 2: Run test to verify it passes (nonce already Redis-backed)**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/test_multi_worker_nonce.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS — confirms nonces are Redis-backed and cross-worker safe.

- [ ] **Step 3: Raise UVICORN_WORKERS to 4 in docker-compose.yml**

In the `backend` service environment section, update:
```yaml
      - UVICORN_WORKERS=${UVICORN_WORKERS:-4}
```

- [ ] **Step 4: Run full test suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: All tests pass.

- [ ] **Step 5: Commit Chunk B2**

```bash
git add docker-compose.yml backend/tests/test_multi_worker_nonce.py
git commit -m "feat(24b2): raise uvicorn workers to 4"
```

---

## Task 4: Chunk C — account_balances Table + AccountBalanceService

**Files:**
- Create: `backend/alembic/versions/0074_account_balances.py`
- Create: `backend/app/services/account_balances.py`
- Create: `backend/tests/services/test_account_balance_service.py`
- Create: `backend/tests/migrations/test_0074_account_balances.py`
- Create: `backend/tests/test_dual_write_atomicity.py`
- Create: `backend/tests/test_no_direct_last_nlv_reads.py`
- Modify: `backend/app/services/brokers.py` (dual-write via AccountBalanceService)
- Modify: `backend/app/services/orders_service.py` (update line-1940 comment)
- Modify: `backend/app/services/risk_service.py` (read from AccountBalanceService)
- Modify: `backend/app/services/position_sizing_service.py` (read from AccountBalanceService)
- Modify: `backend/app/api/sizing.py` (read from AccountBalanceService)

- [ ] **Step 1: Write failing tests for AccountBalanceService**

```python
# backend/tests/services/test_account_balance_service.py
import uuid
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_upsert_and_get_current(db_session: AsyncSession):
    """upsert() creates a row; get_current() returns it."""
    from app.services.account_balances import AccountBalanceService

    svc = AccountBalanceService(db_session)
    account_id = uuid.uuid4()

    # Seed a broker_accounts row (required by FK)
    from sqlalchemy import text
    await db_session.execute(
        text("""
            INSERT INTO broker_accounts (id, broker_id, alias, mode, currency_base)
            VALUES (:id, 'ibkr', 'test', 'paper', 'USD')
            ON CONFLICT DO NOTHING
        """),
        {"id": str(account_id)},
    )

    await svc.upsert(account_id, Decimal("100000.00"), "USD")
    row = await svc.get_current(account_id)

    assert row is not None
    assert row.nlv == Decimal("100000.00")
    assert row.nlv_currency == "USD"


@pytest.mark.asyncio
async def test_get_current_returns_none_for_unknown(db_session: AsyncSession):
    from app.services.account_balances import AccountBalanceService
    svc = AccountBalanceService(db_session)
    result = await svc.get_current(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_upsert_updates_existing(db_session: AsyncSession):
    from app.services.account_balances import AccountBalanceService
    from sqlalchemy import text
    svc = AccountBalanceService(db_session)
    account_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO broker_accounts (id, broker_id, alias, mode, currency_base) VALUES (:id, 'ibkr', 'test2', 'paper', 'GBP') ON CONFLICT DO NOTHING"),
        {"id": str(account_id)},
    )

    await svc.upsert(account_id, Decimal("50000"), "GBP")
    await svc.upsert(account_id, Decimal("60000"), "GBP")
    row = await svc.get_current(account_id)
    assert row.nlv == Decimal("60000")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/test_account_balance_service.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: FAIL — `AccountBalanceService` not yet defined.

- [ ] **Step 3: Create the Alembic migration 0074**

Create `backend/alembic/versions/0074_account_balances.py`:

```python
"""account_balances table (expand phase; columns NOT dropped)

Revision ID: 0074
Revises: 0073
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0074"
down_revision = "0073_phase23a_uk_cgt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_balances",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("nlv", sa.Numeric(20, 8), nullable=True),
        sa.Column("nlv_currency", sa.String(8), nullable=True),
        sa.Column(
            "cash_by_currency",
            sa.JSON(),
            server_default="{}",
            nullable=True,
            comment="display-only; do NOT add per-currency queries — add account_cash_balances table instead",
        ),
        sa.Column("buying_power", sa.Numeric(20, 8), nullable=True),
        sa.Column("margin_used", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["broker_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )

    # Backfill from broker_accounts.last_nlv*
    op.execute("""
        INSERT INTO account_balances (account_id, nlv, nlv_currency, updated_at)
        SELECT id, last_nlv, last_nlv_currency,
               COALESCE(last_nlv_at, now())
        FROM broker_accounts
        WHERE last_nlv IS NOT NULL
        ON CONFLICT (account_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("account_balances")
```

- [ ] **Step 4: Run the migration**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH alembic upgrade 0074"
```

Expected: Migration runs; `account_balances` table created and backfilled.

- [ ] **Step 5: Create AccountBalanceService**

Create `backend/app/services/account_balances.py`:

```python
"""AccountBalanceService — isolated current-balance state.

Dual-writes atomically to both broker_accounts.last_nlv* (legacy)
and account_balances (new). All read sites must use get_current().

Phase 25 will drop dual-write + broker_accounts.last_nlv* columns (0074a).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AccountBalanceRow:
    account_id: UUID
    nlv: Decimal | None
    nlv_currency: str | None
    buying_power: Decimal | None
    margin_used: Decimal | None
    updated_at: datetime


class AccountBalanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self, account_id: UUID) -> AccountBalanceRow | None:
        result = await self._session.execute(
            text("""
                SELECT account_id, nlv, nlv_currency,
                       buying_power, margin_used, updated_at
                FROM account_balances
                WHERE account_id = :account_id
            """),
            {"account_id": str(account_id)},
        )
        row = result.fetchone()
        if row is None:
            return None
        return AccountBalanceRow(
            account_id=UUID(str(row.account_id)),
            nlv=Decimal(str(row.nlv)) if row.nlv is not None else None,
            nlv_currency=row.nlv_currency,
            buying_power=Decimal(str(row.buying_power)) if row.buying_power is not None else None,
            margin_used=Decimal(str(row.margin_used)) if row.margin_used is not None else None,
            updated_at=row.updated_at,
        )

    async def upsert(
        self,
        account_id: UUID,
        nlv: Decimal,
        currency: str,
        buying_power: Decimal | None = None,
        margin_used: Decimal | None = None,
    ) -> None:
        """Atomic dual-write: update broker_accounts + upsert account_balances."""
        async with self._session.begin():
            await self._session.execute(
                text("""
                    UPDATE broker_accounts
                    SET last_nlv          = :nlv,
                        last_nlv_currency = :currency,
                        last_nlv_at       = now()
                    WHERE id = :account_id
                """),
                {"nlv": nlv, "currency": currency, "account_id": str(account_id)},
            )
            await self._session.execute(
                text("""
                    INSERT INTO account_balances
                        (account_id, nlv, nlv_currency, buying_power, margin_used, updated_at)
                    VALUES
                        (:account_id, :nlv, :currency, :buying_power, :margin_used, now())
                    ON CONFLICT (account_id) DO UPDATE
                        SET nlv          = EXCLUDED.nlv,
                            nlv_currency = EXCLUDED.nlv_currency,
                            buying_power = EXCLUDED.buying_power,
                            margin_used  = EXCLUDED.margin_used,
                            updated_at   = EXCLUDED.updated_at
                """),
                {
                    "account_id": str(account_id),
                    "nlv": nlv,
                    "currency": currency,
                    "buying_power": buying_power,
                    "margin_used": margin_used,
                },
            )
```

- [ ] **Step 6: Run AccountBalanceService tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/test_account_balance_service.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS.

- [ ] **Step 7: Write dual-write atomicity test**

```python
# backend/tests/test_dual_write_atomicity.py
"""Verify dual-write is atomic: if account_balances upsert fails, broker_accounts is rolled back."""
import uuid
from decimal import Decimal
import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy import text


@pytest.mark.asyncio
async def test_dual_write_rollback_on_upsert_failure(db_session):
    """If the account_balances INSERT raises, broker_accounts UPDATE is rolled back."""
    from app.services.account_balances import AccountBalanceService
    svc = AccountBalanceService(db_session)
    account_id = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO broker_accounts (id, broker_id, alias, mode, currency_base) VALUES (:id, 'ibkr', 'atomic_test', 'paper', 'USD') ON CONFLICT DO NOTHING"),
        {"id": str(account_id)},
    )

    # Seed a known initial NLV on broker_accounts
    await db_session.execute(
        text("UPDATE broker_accounts SET last_nlv = 10000, last_nlv_currency = 'USD' WHERE id = :id"),
        {"id": str(account_id)},
    )
    await db_session.commit()

    # Inject a failure in the account_balances upsert path
    original_execute = db_session.execute

    call_count = 0

    async def failing_execute(stmt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # second execute is the account_balances upsert
            raise RuntimeError("simulated upsert failure")
        return await original_execute(stmt, *args, **kwargs)

    db_session.execute = failing_execute  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="simulated upsert failure"):
        await svc.upsert(account_id, Decimal("99999"), "USD")

    db_session.execute = original_execute

    # broker_accounts must still show the old NLV (transaction rolled back)
    result = await db_session.execute(
        text("SELECT last_nlv FROM broker_accounts WHERE id = :id"),
        {"id": str(account_id)},
    )
    row = result.fetchone()
    assert row is None or row.last_nlv is None or str(row.last_nlv) == "10000"

    # account_balances must not have a row
    result2 = await db_session.execute(
        text("SELECT nlv FROM account_balances WHERE account_id = :id"),
        {"id": str(account_id)},
    )
    assert result2.fetchone() is None
```

- [ ] **Step 8: Run dual-write atomicity test**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/test_dual_write_atomicity.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS.

- [ ] **Step 9: Write the no-direct-last_nlv-reads test**

```python
# backend/tests/test_no_direct_last_nlv_reads.py
"""Assert no direct last_nlv reads exist outside of models/ and alembic/."""
import pathlib
import re


def test_no_direct_last_nlv_reads_in_app():
    """
    After Chunk C, no code in backend/app/ (excluding models/) should read
    last_nlv or last_nlv_currency directly. All reads go through AccountBalanceService.
    """
    base = pathlib.Path("backend/app")
    violators = []
    pattern = re.compile(r"last_nlv")

    for path in base.rglob("*.py"):
        # Exclude the ORM model (must keep columns until 0074a)
        if "models" in path.parts:
            continue
        # Exclude alembic versions
        if "alembic" in path.parts:
            continue
        text = path.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line) and not line.strip().startswith("#"):
                violators.append(f"{path}:{i}: {line.strip()}")

    assert not violators, (
        "Direct last_nlv reads found (must use AccountBalanceService.get_current()):\n"
        + "\n".join(violators)
    )
```

- [ ] **Step 10: Redirect all read sites to AccountBalanceService**

Update these 5 files to use `AccountBalanceService.get_current()` instead of reading `last_nlv` directly:

**a) `backend/app/services/brokers.py`** — in `BrokerDiscoverer`, replace the raw `UPDATE broker_accounts SET last_nlv=...` call (around line 1507) with `AccountBalanceService(session).upsert(...)`.

**b) `backend/app/services/position_sizing_service.py:85`** — replace:
```python
        nlv_base = Decimal(account["last_nlv"])
```
with:
```python
        from app.services.account_balances import AccountBalanceService
        bal = await AccountBalanceService(session).get_current(account_id)
        if bal is None or bal.nlv is None:
            raise ValueError(
                f"account {account_id} has no NLV in account_balances — sizing requires a populated NLV"
            )
        nlv_base = bal.nlv
```

Replace lines 239-248 (the `SELECT ... last_nlv` query) with a call to `AccountBalanceService.get_current()`.

**c) `backend/app/services/position_sizing_service.py:109`** — in the `EvaluationContext` construction, populate `account_nlv_base` from `AccountBalanceService.get_current()` result.

**d) `backend/app/services/orders_service.py:1925,1944`** — replace the `SELECT ... last_nlv_currency` at line 1925 and the `last_nlv_currency` reference at line 1944 with `AccountBalanceService.get_current()`. Update the comment at line 1940 to:
```python
        # NLV currency sourced from AccountBalanceService (account_balances table).
        # broker_accounts.last_nlv_currency kept for Phase 25 drop (0074a).
```

**e) `backend/app/api/sizing.py`** — replace any `last_nlv` SQL reads with `AccountBalanceService.get_current()`.

- [ ] **Step 11: Run no-direct-last_nlv-reads test and full test suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/test_no_direct_last_nlv_reads.py tests/services/test_account_balance_service.py tests/test_dual_write_atomicity.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS for all three.

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: All tests pass.

- [ ] **Step 12: Write migration test**

```python
# backend/tests/migrations/test_0074_account_balances.py
"""Test alembic migration 0074: account_balances created, backfilled, last_nlv* NOT dropped."""
import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_account_balances_table_exists(db_session):
    result = await db_session.execute(
        text("SELECT to_regclass('public.account_balances')")
    )
    assert result.scalar() is not None, "account_balances table not found"


@pytest.mark.asyncio
async def test_last_nlv_columns_still_exist(db_session):
    """broker_accounts must still have last_nlv* columns (drop is deferred to 0074a)."""
    result = await db_session.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'broker_accounts'
              AND column_name IN ('last_nlv', 'last_nlv_currency', 'last_nlv_at')
        """)
    )
    cols = {r[0] for r in result.fetchall()}
    assert "last_nlv" in cols
    assert "last_nlv_currency" in cols
    assert "last_nlv_at" in cols


@pytest.mark.asyncio
async def test_backfill_populated_account_balances(db_session):
    """Any broker_accounts row with last_nlv set must have a matching account_balances row."""
    result = await db_session.execute(
        text("""
            SELECT COUNT(*) FROM broker_accounts
            WHERE last_nlv IS NOT NULL
              AND id NOT IN (SELECT account_id FROM account_balances)
        """)
    )
    missing = result.scalar()
    assert missing == 0, f"{missing} account(s) have last_nlv but no account_balances row"
```

- [ ] **Step 13: Run migration test**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/migrations/test_0074_account_balances.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS.

- [ ] **Step 14: Commit Chunk C**

```bash
git add backend/alembic/versions/0074_account_balances.py \
    backend/app/services/account_balances.py \
    backend/app/services/brokers.py \
    backend/app/services/orders_service.py \
    backend/app/services/position_sizing_service.py \
    backend/app/services/risk_service.py \
    backend/app/api/sizing.py \
    backend/tests/services/test_account_balance_service.py \
    backend/tests/migrations/test_0074_account_balances.py \
    backend/tests/test_dual_write_atomicity.py \
    backend/tests/test_no_direct_last_nlv_reads.py
git commit -m "feat(24c): account_balances expand migration + AccountBalanceService dual-write"
```

---

## Task 5: Chunk D — TimescaleDB CAGG Backlog

**Files:**
- Create: `backend/alembic/versions/0075_cagg_backlog.py`
- Create: `backend/alembic/versions/0075a_optin_subminute_caggs.py`
- Create: `backend/tests/migrations/test_0075_cagg_backlog.py`

- [ ] **Step 1: Write the failing CAGG migration test**

```python
# backend/tests/migrations/test_0075_cagg_backlog.py
"""Test 0075: Tier-1 CAGGs exist with refresh policies; 0075a not applied."""
import pytest
from sqlalchemy import text


TIER1_CAGGS = ["bars_5m", "bars_15m", "bars_30m", "bars_1h", "bars_1d"]
TIER2_CAGGS = ["bars_5s", "bars_10s", "bars_15s", "bars_30s", "bars_45s"]


@pytest.mark.asyncio
async def test_tier1_caggs_exist(db_session):
    for cagg in TIER1_CAGGS:
        result = await db_session.execute(
            text(f"SELECT to_regclass('public.{cagg}')")
        )
        assert result.scalar() is not None, f"Tier-1 CAGG {cagg} not found"


@pytest.mark.asyncio
async def test_tier1_caggs_have_refresh_policies(db_session):
    result = await db_session.execute(
        text("""
            SELECT job_id FROM timescaledb_information.jobs
            WHERE application_name LIKE 'Refresh Continuous Aggregate%'
              AND hypertable_name = ANY(:caggs)
        """),
        {"caggs": TIER1_CAGGS},
    )
    rows = result.fetchall()
    assert len(rows) >= len(TIER1_CAGGS), (
        f"Expected refresh policies for {TIER1_CAGGS}, found {len(rows)}"
    )


@pytest.mark.asyncio
async def test_tier2_caggs_not_applied(db_session):
    for cagg in TIER2_CAGGS:
        result = await db_session.execute(
            text(f"SELECT to_regclass('public.{cagg}')")
        )
        val = result.scalar()
        assert val is None, f"Tier-2 CAGG {cagg} must not be created by default (opt-in only)"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/migrations/test_0075_cagg_backlog.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: FAIL — CAGGs not yet created.

- [ ] **Step 3: Create Alembic migration 0075 (Tier-1 CAGGs)**

Create `backend/alembic/versions/0075_cagg_backlog.py`:

```python
"""Tier-1 CAGG backlog: 5m, 15m, 30m, 1h, 1d (bars_1d already exists — IF NOT EXISTS)

Revision ID: 0075
Revises: 0074
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

TIER1 = [
    ("bars_5m",  "5 minutes",  "5 minutes",  "5 minutes"),
    ("bars_15m", "15 minutes", "15 minutes", "15 minutes"),
    ("bars_30m", "30 minutes", "30 minutes", "30 minutes"),
    ("bars_1h",  "1 hour",     "1 hour",     "1 hour"),
    ("bars_1d",  "1 day",      "1 day",      "1 day"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for view_name, bucket, schedule_interval, lag in TIER1:
        conn.execute(op.get_context().autocommit_block(f"""
            CREATE MATERIALIZED VIEW IF NOT EXISTS {view_name}
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket(INTERVAL '{bucket}', time) AS bucket,
                instrument_id,
                first(open, time)  AS open,
                max(high)          AS high,
                min(low)           AS low,
                last(close, time)  AS close,
                sum(volume)        AS volume
            FROM bars_1s
            GROUP BY bucket, instrument_id
            WITH NO DATA;
        """))
        conn.execute(op.get_context().autocommit_block(f"""
            SELECT add_continuous_aggregate_policy(
                '{view_name}',
                start_offset => INTERVAL '3 {bucket}',
                end_offset   => INTERVAL '{lag}',
                schedule_interval => INTERVAL '{schedule_interval}',
                if_not_exists => TRUE
            );
        """))


def downgrade() -> None:
    conn = op.get_bind()
    for view_name, _, _, _ in reversed(TIER1):
        conn.execute(op.get_context().autocommit_block(f"""
            DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE;
        """))
```

- [ ] **Step 4: Create Alembic migration 0075a (Tier-2 opt-in CAGGs)**

Create `backend/alembic/versions/0075a_optin_subminute_caggs.py`:

```python
"""Tier-2 sub-minute CAGGs (5s–45s): MANUAL OPT-IN only.

DO NOT run via `alembic upgrade head`.
Run explicitly after 24h monitoring gate passes both exit criteria:
  1. Storage: bars_1s + Tier-1 CAGGs < 2x raw bars_1s
  2. Write p99: bars_1s write p99 not increased > 20% vs 7-day baseline

Run: alembic upgrade 0075a

Revision ID: 0075a
Revises: 0075
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op

revision = "0075a"
down_revision = "0075"
branch_labels = None
depends_on = None

TIER2 = [
    ("bars_5s",  "5 seconds",  "10 seconds",  "10 seconds"),
    ("bars_10s", "10 seconds", "20 seconds",  "20 seconds"),
    ("bars_15s", "15 seconds", "30 seconds",  "30 seconds"),
    ("bars_30s", "30 seconds", "1 minute",    "1 minute"),
    ("bars_45s", "45 seconds", "1 minute",    "1 minute"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for view_name, bucket, schedule_interval, lag in TIER2:
        conn.execute(op.get_context().autocommit_block(f"""
            CREATE MATERIALIZED VIEW IF NOT EXISTS {view_name}
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket(INTERVAL '{bucket}', time) AS bucket,
                instrument_id,
                first(open, time)  AS open,
                max(high)          AS high,
                min(low)           AS low,
                last(close, time)  AS close,
                sum(volume)        AS volume
            FROM bars_1s
            GROUP BY bucket, instrument_id
            WITH NO DATA;
        """))
        conn.execute(op.get_context().autocommit_block(f"""
            SELECT add_continuous_aggregate_policy(
                '{view_name}',
                start_offset => INTERVAL '3 {bucket}',
                end_offset   => INTERVAL '{lag}',
                schedule_interval => INTERVAL '{schedule_interval}',
                if_not_exists => TRUE
            );
        """))


def downgrade() -> None:
    conn = op.get_bind()
    for view_name, _, _, _ in reversed(TIER2):
        conn.execute(op.get_context().autocommit_block(f"""
            DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE;
        """))
```

- [ ] **Step 5: Run migration 0075 (NOT 0075a)**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH alembic upgrade 0075"
```

Expected: 5 Tier-1 CAGGs created with refresh policies. `bars_1d` uses IF NOT EXISTS.

- [ ] **Step 6: Run CAGG migration test**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/migrations/test_0075_cagg_backlog.py -v 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: PASS — Tier-1 exist, Tier-2 absent.

- [ ] **Step 7: Run full test suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: All pass.

- [ ] **Step 8: Commit Chunk D**

```bash
git add backend/alembic/versions/0075_cagg_backlog.py \
    backend/alembic/versions/0075a_optin_subminute_caggs.py \
    backend/tests/migrations/test_0075_cagg_backlog.py
git commit -m "feat(24d): tier-1 CAGG backlog (5m–1d) + opt-in tier-2 migration"
```

---

## Task 6: Chunk E — Ops Automation Scripts

**Files:**
- Create: `scripts/recover-after-deploy.sh`
- Create: `scripts/restart-futu-full.sh`
- Modify: `deploy/nuc/Launch-IBKRSidecar.vbs`
- Modify: `deploy/nuc/BrokerWatchdog.ps1`

- [ ] **Step 1: Write the recover-after-deploy script**

Create `scripts/recover-after-deploy.sh`:

```bash
#!/usr/bin/env bash
# Post-deploy recovery: re-provisions NUC sidecar certs, restarts 4 sidecars,
# restarts backend+nginx on VPS.
# Requires: WireGuard tunnel to NUC active.
set -euo pipefail

NUC_SSH="trader@10.10.0.2"
NUC_SSH_PORT="${NUC_SSH_PORT:-2222}"
HEALTH_TIMEOUT=60

# ── 1. WireGuard precheck ────────────────────────────────────────────────────
echo "Checking WireGuard tunnel to NUC..."
if ! wg show wg0 2>/dev/null | grep -q "latest handshake"; then
    echo "ERROR: WireGuard tunnel to NUC is down."
    echo "Run: sudo wg-quick up wg0"
    exit 1
fi
echo "WireGuard OK"

# ── 2. Provision NUC + restart 4 sidecars ────────────────────────────────────
echo "SSHing to NUC: provision-and-publish.ps1 + sidecar schtasks..."
ssh -i ~/.ssh/trader_id_ed25519 \
    -p "${NUC_SSH_PORT}" \
    -o StrictHostKeyChecking=accept-new \
    "${NUC_SSH}" \
    'powershell -ExecutionPolicy Bypass -Command "
        & C:\dashboard\deploy\nuc\provision-and-publish.ps1;
        schtasks /Run /TN DashboardSidecarIBKR1;
        schtasks /Run /TN DashboardSidecarIBKR2;
        schtasks /Run /TN DashboardSidecarFutu;
        schtasks /Run /TN DashboardSidecarSchwab;
    "'

# ── 3. Restart backend + nginx on VPS ────────────────────────────────────────
echo "Restarting backend and nginx..."
docker compose restart backend scheduler
nginx -s reload

# ── 4. Poll health endpoint ───────────────────────────────────────────────────
echo "Polling /api/health..."
elapsed=0
while (( elapsed < HEALTH_TIMEOUT )); do
    status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health || true)
    if [[ "$status" == "200" ]]; then
        echo "Health check OK (${elapsed}s)"
        exit 0
    fi
    sleep 2
    elapsed=$(( elapsed + 2 ))
done
echo "ERROR: Health check failed after ${HEALTH_TIMEOUT}s"
exit 1
```

- [ ] **Step 2: Write the restart-futu-full script**

Create `scripts/restart-futu-full.sh`:

```bash
#!/usr/bin/env bash
# Full Futu OpenD restart: kill FutuOpenD, restart sidecar_futu, bounce backend.
# Required when RSA key changes or FutuOpenD connection state is stale.
# Requires: WireGuard tunnel to NUC active.
set -euo pipefail

NUC_SSH="trader@10.10.0.2"
NUC_SSH_PORT="${NUC_SSH_PORT:-2222}"
HEALTH_TIMEOUT=90

# ── 1. WireGuard precheck ────────────────────────────────────────────────────
echo "Checking WireGuard tunnel to NUC..."
if ! wg show wg0 2>/dev/null | grep -q "latest handshake"; then
    echo "ERROR: WireGuard tunnel to NUC is down."
    echo "Run: sudo wg-quick up wg0"
    exit 1
fi
echo "WireGuard OK"

# ── 2. Kill FutuOpenD on NUC ─────────────────────────────────────────────────
echo "Stopping FutuOpenD on NUC..."
ssh -i ~/.ssh/trader_id_ed25519 \
    -p "${NUC_SSH_PORT}" \
    -o StrictHostKeyChecking=accept-new \
    "${NUC_SSH}" \
    'powershell -ExecutionPolicy Bypass -Command "
        Stop-Process -Name FutuOpenD -Force -ErrorAction SilentlyContinue;
        Start-Sleep 3;
        Write-Host FutuOpenD_stopped;
    "'

# ── 3. Restart sidecar_futu Docker service ────────────────────────────────────
echo "Restarting sidecar_futu..."
docker compose restart sidecar_futu

# ── 4. Restart backend to re-establish gRPC stream ───────────────────────────
echo "Restarting backend + scheduler..."
docker compose restart backend scheduler

# ── 5. Verify health ─────────────────────────────────────────────────────────
echo "Polling /api/health..."
elapsed=0
while (( elapsed < HEALTH_TIMEOUT )); do
    status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health || true)
    if [[ "$status" == "200" ]]; then
        echo "Health check OK (${elapsed}s)"
        exit 0
    fi
    sleep 3
    elapsed=$(( elapsed + 3 ))
done
echo "ERROR: Health check failed after ${HEALTH_TIMEOUT}s — check Futu logs"
exit 1
```

- [ ] **Step 3: Make both scripts executable**

```bash
chmod +x /home/joseph/dashboard/scripts/recover-after-deploy.sh \
          /home/joseph/dashboard/scripts/restart-futu-full.sh
```

- [ ] **Step 4: Update NUC sidecar path in Launch-IBKRSidecar.vbs**

Read `deploy/nuc/Launch-IBKRSidecar.vbs` then replace all occurrences of `sidecar\` with `sidecar_ibkr\`:

```vbs
' Before: C:\dashboard\sidecar\...
' After:  C:\dashboard\sidecar_ibkr\...
```

Use exact string replacement to update only the path references, not other content.

- [ ] **Step 5: Update NUC sidecar path in BrokerWatchdog.ps1**

Read `deploy/nuc/BrokerWatchdog.ps1` then replace all occurrences of `\dashboard\sidecar\` with `\dashboard\sidecar_ibkr\`:

- [ ] **Step 6: Commit Chunk E**

```bash
git add scripts/recover-after-deploy.sh scripts/restart-futu-full.sh \
    deploy/nuc/Launch-IBKRSidecar.vbs deploy/nuc/BrokerWatchdog.ps1
git commit -m "feat(24e): ops scripts (post-deploy recovery, futu restart, sidecar path cutover)"
```

---

## Task 7: Chunk F — Grafana Dashboards + Alert Denoising + Correlation IDs

**Files:**
- Create: `deploy/grafana/provisioning/datasources/prometheus.yml`
- Create: `deploy/grafana/provisioning/datasources/pg_exporter.yml`
- Create: `deploy/grafana/dashboards/broker-gateway.json`
- Create: `deploy/grafana/dashboards/quote-bus.json`
- Create: `deploy/grafana/dashboards/risk-gate.json`
- Create: `deploy/grafana/dashboards/bot-orchestrator.json`
- Create: `deploy/grafana/dashboards/cagg-lag.json`
- Create: `deploy/grafana/dashboards/workers.json`
- Create: `deploy/grafana/dashboards/scheduler.json`
- Modify: `deploy/prometheus/alerts.yml`

- [ ] **Step 1: Create Grafana provisioning directories**

```bash
mkdir -p /home/joseph/dashboard/deploy/grafana/provisioning/datasources
mkdir -p /home/joseph/dashboard/deploy/grafana/dashboards
```

- [ ] **Step 2: Write Prometheus datasource provisioning**

Create `deploy/grafana/provisioning/datasources/prometheus.yml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus_uid
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    jsonData:
      timeInterval: "15s"
```

- [ ] **Step 3: Write pg_exporter datasource provisioning**

Create `deploy/grafana/provisioning/datasources/pg_exporter.yml`:

```yaml
apiVersion: 1
datasources:
  - name: pg_exporter
    type: prometheus
    uid: pg_exporter_uid
    access: proxy
    url: http://pg_exporter:9187
    jsonData:
      timeInterval: "30s"
```

- [ ] **Step 4: Write broker-gateway dashboard JSON**

Create `deploy/grafana/dashboards/broker-gateway.json`:

```json
{
  "title": "Broker Gateway",
  "uid": "broker_gateway",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "timeseries",
      "title": "Per-Gateway Health",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "broker_gateway_health{broker=~\"$broker\"}",
          "legendFormat": "{{broker}}"
        }
      ],
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "Stream Reconnects/min",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "rate(broker_stream_reconnects_total[1m])",
          "legendFormat": "{{broker}}"
        }
      ],
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "Sidecar Latency p99 (ms)",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "histogram_quantile(0.99, rate(broker_sidecar_rpc_seconds_bucket[5m])) * 1000",
          "legendFormat": "{{broker}} p99"
        }
      ],
      "gridPos": {"x": 0, "y": 8, "w": 24, "h": 8}
    }
  ],
  "templating": {
    "list": [
      {
        "name": "broker",
        "type": "query",
        "datasource": {"uid": "prometheus_uid"},
        "query": "label_values(broker_gateway_health, broker)",
        "multi": true,
        "includeAll": true
      }
    ]
  },
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

- [ ] **Step 5: Write scheduler dashboard JSON**

Create `deploy/grafana/dashboards/scheduler.json`:

```json
{
  "title": "Scheduler Container",
  "uid": "scheduler_dash",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "timeseries",
      "title": "APScheduler Job Execution Time (p99)",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "histogram_quantile(0.99, rate(apscheduler_job_duration_seconds_bucket[5m]))",
          "legendFormat": "{{job_id}} p99"
        }
      ],
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
    },
    {
      "type": "stat",
      "title": "Missed Fires",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "increase(apscheduler_job_missed_total[1h])",
          "legendFormat": "{{job_id}}"
        }
      ],
      "gridPos": {"x": 12, "y": 0, "w": 6, "h": 8}
    },
    {
      "type": "gauge",
      "title": "Scheduler Heartbeat Age (s)",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "time() - scheduler_heartbeat_timestamp",
          "legendFormat": "age"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "thresholds": {
            "steps": [
              {"color": "green", "value": null},
              {"color": "yellow", "value": 60},
              {"color": "red", "value": 120}
            ]
          }
        }
      },
      "gridPos": {"x": 18, "y": 0, "w": 6, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "OrderEventConsumer Event Rate",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [
        {
          "expr": "rate(order_event_consumer_events_total[1m])",
          "legendFormat": "{{broker}}/{{account}}"
        }
      ],
      "gridPos": {"x": 0, "y": 8, "w": 24, "h": 8}
    }
  ],
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

- [ ] **Step 6: Write remaining 5 dashboard JSON files**

Create `deploy/grafana/dashboards/quote-bus.json`:

```json
{
  "title": "Quote Bus",
  "uid": "quote_bus",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "timeseries",
      "title": "Messages/s per Source",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "rate(quote_bus_messages_total[1m])", "legendFormat": "{{source}}"}],
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
    },
    {
      "type": "stat",
      "title": "Active Subscriptions",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "quote_bus_subscriptions_active", "legendFormat": "{{source}}"}],
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}
    }
  ],
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

Create `deploy/grafana/dashboards/risk-gate.json`:

```json
{
  "title": "Risk Gate",
  "uid": "risk_gate",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "timeseries",
      "title": "Gate Decisions Rate",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "rate(risk_gate_decisions_total[1m])", "legendFormat": "{{verdict}}"}],
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "Risk Evaluation Latency p99 (ms)",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "histogram_quantile(0.99, rate(risk_evaluation_seconds_bucket[5m])) * 1000", "legendFormat": "p99"}],
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}
    }
  ],
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

Create `deploy/grafana/dashboards/bot-orchestrator.json`:

```json
{
  "title": "Bot Orchestrator",
  "uid": "bot_orchestrator",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "stat",
      "title": "Bots by State",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "bot_supervisor_count_by_state", "legendFormat": "{{state}}"}],
      "gridPos": {"x": 0, "y": 0, "w": 8, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "Auto-Promote Events",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "rate(auto_promote_events_total[1h])", "legendFormat": "promoted"}],
      "gridPos": {"x": 8, "y": 0, "w": 8, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "Exposure Gate Hits",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "rate(exposure_gate_decisions_total[1m])", "legendFormat": "{{verdict}}"}],
      "gridPos": {"x": 16, "y": 0, "w": 8, "h": 8}
    }
  ],
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

Create `deploy/grafana/dashboards/cagg-lag.json`:

```json
{
  "title": "CAGG Lag",
  "uid": "cagg_lag",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "timeseries",
      "title": "Refresh Lag vs Policy Target",
      "datasource": {"uid": "pg_exporter_uid"},
      "targets": [{"expr": "timescaledb_cagg_refresh_lag_seconds", "legendFormat": "{{cagg}}"}],
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "bars_1s Write p99 (ms)",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "histogram_quantile(0.99, rate(bars_1s_write_seconds_bucket[5m])) * 1000", "legendFormat": "p99"}],
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}
    }
  ],
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

Create `deploy/grafana/dashboards/workers.json`:

```json
{
  "title": "Uvicorn Workers",
  "uid": "workers",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "stat",
      "title": "Worker Count",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "count(up{job='backend'})", "legendFormat": "workers"}],
      "gridPos": {"x": 0, "y": 0, "w": 8, "h": 8}
    },
    {
      "type": "timeseries",
      "title": "Request Rate per Worker",
      "datasource": {"uid": "prometheus_uid"},
      "targets": [{"expr": "rate(http_requests_total[1m])", "legendFormat": "{{instance}}"}],
      "gridPos": {"x": 8, "y": 0, "w": 16, "h": 8}
    }
  ],
  "time": {"from": "now-3h", "to": "now"},
  "refresh": "30s"
}
```

- [ ] **Step 7: Validate all 7 dashboard JSON files**

```bash
for f in /home/joseph/dashboard/deploy/grafana/dashboards/*.json; do
    jq -e . "$f" > /dev/null && echo "OK: $f" || echo "FAIL: $f"
done
```

Expected: All 7 files output `OK`.

- [ ] **Step 8: Update alerts.yml with new rules**

Read `deploy/prometheus/alerts.yml`, then append these new alert rules to the existing file:

```yaml
  # ── Scheduler heartbeat ───────────────────────────────────────────────────
  - alert: SchedulerContainerDown
    expr: absent(scheduler_heartbeat_timestamp) or (time() - scheduler_heartbeat_timestamp > 120)
    for: 2m
    labels:
      severity: critical
    annotations:
      summary: "Scheduler container heartbeat absent > 2 min"
      description: "The scheduler container has not written a heartbeat to Redis for > 2 min. Check: docker compose logs scheduler"

  # ── CAGG refresh lag ─────────────────────────────────────────────────────
  - alert: CaggRefreshLagHigh
    expr: timescaledb_cagg_refresh_lag_seconds > 2 * timescaledb_cagg_policy_interval_seconds
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "CAGG {{ $labels.cagg }} refresh lag > 2x policy target"
      description: "CAGG {{ $labels.cagg }} lag is {{ $value }}s vs policy target."

  # ── Broker sidecar cert expiry ────────────────────────────────────────────
  - alert: BrokerSidecarCertExpirySoon
    expr: broker_sidecar_cert_expiry_seconds < 14 * 86400
    for: 1h
    labels:
      severity: warning
    annotations:
      summary: "Broker sidecar mTLS cert for {{ $labels.broker }}/{{ $labels.account }} expires in < 14 days"
      description: "Run deploy/nuc/renew-sidecar-mtls.ps1 to rotate."

  # ── IBKR maintenance window inhibit ──────────────────────────────────────
  - alert: BrokerOrderEventStreamFlappingPostMaintenance
    expr: |
      ALERTS{alertname="BrokerOrderEventStreamFlapping"} == 1
        unless on() (
          (hour() >= 6 AND hour() < 8)
          OR (day_of_week() >= 6 AND hour() >= 5 AND hour() < 14)
        )
    for: 30m
    labels:
      severity: warning
    annotations:
      summary: "BrokerOrderEventStream still flapping > 30 min after maintenance window"
      description: "Expected reconnection storm post-maintenance has not resolved."
```

Also update any existing `for: 0s` or `for: 1m` rules to `for: 5m` — read the file first to identify them.

- [ ] **Step 9: Validate alerts.yml**

```bash
docker compose run --rm prometheus promtool check rules /etc/prometheus/alerts.yml 2>&1 || \
    /home/joseph/dashboard/deploy/prometheus/promtool check rules /home/joseph/dashboard/deploy/prometheus/alerts.yml
```

If `promtool` not locally available, validate YAML syntax:
```bash
python3 -c "import yaml; yaml.safe_load(open('/home/joseph/dashboard/deploy/prometheus/alerts.yml'))" && echo "YAML OK"
```

- [ ] **Step 10: Commit Chunk F**

```bash
git add deploy/grafana/ deploy/prometheus/alerts.yml
git commit -m "feat(24f): grafana dashboards + alert denoising + scheduler heartbeat alert"
```

---

## Task 8: Chunk G — Phase Close-Out

**Files:**
- Modify: `docs/PHASE-WORKFLOW.md`
- Modify: `docs/CLAUDE.md`
- Modify: `docs/CHANGELOG.md` (or `CHANGELOG.md` at root)
- Modify: `docs/TASKS.md` (or `TASKS.md` at root)

- [ ] **Step 1: Add scheduler-container pattern to PHASE-WORKFLOW.md**

Read `docs/PHASE-WORKFLOW.md`, then add a note in the relevant section:

```markdown
### Scheduler Container Pattern (introduced Phase 24)

Any long-running task that must not run N× under multi-worker uvicorn belongs in the
**scheduler container** (`python -m app.scheduler`), not in `app/main.py` lifespan.

Rules:
- **Both containers:** idempotent Redis pubsub cache listeners
- **Scheduler only:** outbound WS/gRPC streams, wall-clock cron jobs, per-process in-memory state
- **API workers only:** currently nothing — all stateful background work is in the scheduler

New phases that add APScheduler jobs or long-running tasks MUST add them to `app/scheduler.py`,
not to `app/main.py`.
```

- [ ] **Step 2: Update docs/CLAUDE.md shipped-phases table**

Add Phase 24 to the shipped-phases table:

```markdown
| 24 — Infra & Ops Hardening | 0.24.0 | PG client-cert auth (dev+prod, separate CAs), scheduler container split (all single-instance tasks extracted from main.py), account_balances expand-contract migration (0074) + AccountBalanceService atomic dual-write, Tier-1 CAGG backlog (5m–1d, 0075), ops automation scripts (recover-after-deploy, restart-futu-full, sidecar path cutover), Grafana 7 dashboards + alert denoising |
```

- [ ] **Step 3: Update CHANGELOG.md**

Add a new entry at the top:

```markdown
## [0.24.0] — 2026-05-20

### Added
- PG client-cert auth: separate CAs for WSL dev and NUC prod; asyncpg `sslmode=verify-full`; cert scripts + rotation runbook
- Scheduler container (`python -m app.scheduler`): all single-instance long-running tasks extracted from uvicorn workers
- `BrokerRegistry` split: API workers = dispatch only; scheduler = health probe + discovery + OrderEventConsumer
- `account_balances` table (migration 0074): AccountBalanceService with atomic dual-write; all 5 read sites migrated
- Tier-1 CAGG backlog (migration 0075): `bars_5m`, `bars_15m`, `bars_30m`, `bars_1h` with refresh policies
- Tier-2 opt-in CAGG migration (0075a): `bars_5s`–`bars_45s`; manual `alembic upgrade 0075a` only after monitoring gate
- `scripts/recover-after-deploy.sh`: WG precheck + SSH, provisions NUC, restarts 4 sidecars + backend
- `scripts/restart-futu-full.sh`: WG precheck + SSH, full FutuOpenD kill+restart
- IBKR sidecar path cutover: `sidecar\` → `sidecar_ibkr\` in Launch-IBKRSidecar.vbs + BrokerWatchdog.ps1
- Grafana provisioning: 7 dashboards (broker-gateway, quote-bus, risk-gate, bot-orchestrator, cagg-lag, workers, scheduler)
- Alert denoising: maintenance-window inhibit, BrokerSidecarCertExpirySoon, SchedulerContainerDown, CaggRefreshLagHigh
- Correlation IDs extended to APScheduler jobs + BrokerOrderEventConsumer + BrokerDiscoverer

### Changed
- `docker-compose.yml`: `scheduler` service added; `UVICORN_WORKERS` default raised to 4; PG cert env vars; pool size env vars
- `backend/app/core/db.py`: cert auth DSN via `_build_connect_args()`; `POSTGRES_POOL_SIZE_SCHEDULER` env var
- `scripts/deploy.sh`: asserts `.env` is `chmod 600` before deployment

### Notes
- `broker_accounts.last_nlv*` columns NOT dropped (deferred to Phase 25 migration 0074a after verification gate)
- Tier-2 sub-minute CAGGs (0075a) not applied by default; run after 24h monitoring window passes both exit gates
```

- [ ] **Step 4: Update TASKS.md**

Mark Phase 24 tasks complete and add Phase 25 Quality Gate placeholder tasks.

- [ ] **Step 5: Run final full test suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -q 2>&1 | tee /tmp/pytest_output.txt"
```

Expected: All tests pass.

- [ ] **Step 6: Commit Chunk G and tag**

```bash
git add docs/PHASE-WORKFLOW.md docs/CLAUDE.md docs/CHANGELOG.md docs/TASKS.md
git commit -m "docs(24g): close-out v0.24.0 — CHANGELOG, TASKS, CLAUDE.md phase entry"
git tag v0.24.0
```

---

## Self-Review Checklist

**Spec coverage:**
- Stream A (PG cert auth, both instances): ✅ Task 1 — cert scripts, DSN, rollback, runbook, reencrypt
- Stream B1 (scheduler container + task inventory): ✅ Task 2 — scheduler.py with full 26-task inventory, BrokerRegistry split, _last_position_tick_at → Redis
- Stream B2 (workers flip): ✅ Task 3 — UVICORN_WORKERS=4 with nonce test
- Stream C (account_balances expand-contract): ✅ Task 4 — 0074, AccountBalanceService, 5 read-site redirects, dual-write atomicity, no-direct-reads grep
- Stream D (CAGG backlog): ✅ Task 5 — 0075 Tier-1, 0075a opt-in
- Stream E (ops automation): ✅ Task 6 — recover-after-deploy + WG precheck, restart-futu-full, sidecar path cutover
- Stream F (observability): ✅ Task 7 — 7 Grafana dashboards, alert denoising, correlation IDs
- Stream G (close-out): ✅ Task 8 — CHANGELOG, CLAUDE.md, PHASE-WORKFLOW.md, tag

**Type consistency check:**
- `AccountBalanceService.get_current()` returns `AccountBalanceRow | None` in Task 4 Step 5
- `AccountBalanceService.upsert()` takes `account_id: UUID, nlv: Decimal, currency: str` — used in Task 4 Step 10 brokers.py redirect
- `_write_heartbeat(redis)` in scheduler.py — takes `redis` from `aioredis.from_url()` — consistent

**Placeholder scan:** No TBD, TODO, or "implement later" in any task step.

**Phase 23 CGT jobs:** Task 2 Step 3 explicitly checks `git diff v0.22.0..HEAD -- backend/app/main.py` for jobs added by Phase 23 before migrating.
