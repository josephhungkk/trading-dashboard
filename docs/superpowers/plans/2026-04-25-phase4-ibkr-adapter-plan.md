# Phase 4 — IBKR adapter (read-only) + broker_accounts + gRPC sidecars — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 3's mocked `accounts`/`positions`/`orders` services with **real, read-only** data from the 4 IBKR Gateways already running on the NUC, via Windows-native gRPC sidecars over WireGuard with mTLS.

**Architecture:** One PyInstaller-frozen Python sidecar per IBKR gateway (4 instances; ports 18001-18004 on `10.10.0.2`). Each sidecar wraps a single `ib_async.IB()` and serves a gRPC contract (`proto/broker/v1.proto`). FastAPI backend on the VPS aggregates across sidecars; REST endpoints under `/api/accounts/*` are CF-Access-gated. Soft-delete is scoped to "healthy sidecar reported missing"; mTLS uses a file-based CRL reloaded every 60s.

**Tech Stack:** Python 3.14 + ib_async + grpcio + grpcio-tools + structlog + PyInstaller (Windows sidecars) · FastAPI + SQLAlchemy 2 async + asyncpg + Alembic (backend) · React 19 + Vite 7 + TS 6 (frontend) · buf + protoc-gen-es (codegen) · WireGuard + nginx + CF Tunnel (network) · Windows Task Scheduler + PowerShell + DPAPI (NUC ops glue)

**Source spec:** `docs/superpowers/specs/2026-04-25-phase4-ibkr-adapter-design.md` @ `49bec88` — locked design choices, do NOT re-litigate. Architect-review findings already applied (see spec §12).

**Delegation rule (active from 2026-04-24, see TASKS.md Phase 3 header):** Coding tasks (backend Python, sidecar Python, frontend TS) are delegated to **Codex** via `codex:rescue` → `codex:codex-rescue` subagent. Claude Code keeps tests/stories/verification/commits, **plus all `.ps1`/`.vbs` Windows ops glue** (PowerShell scripts are not in Codex's scope; they're hand-written by Claude using the `POWERSHELL-WINDOWS` skill).

**Per-commit review chain (per CLAUDE.md "Step 6 — Implementation"):** implementer → spec compliance → code quality → language reviewer (`python-reviewer` for backend/sidecar, `typescript-reviewer` for frontend) → security/database/type/silent-failure as applicable. Every commit goes through commitlint (lowercase subject; body lines ≤100 chars; never `--no-verify`).

**Coverage gate:** 80%+ on backend `app/brokers/`, `app/services/brokers.py`, `app/services/ibkr_maintenance.py`, sidecar `sidecar/`. CI fails below.

---

## File structure map (new + modified files locked from spec §9)

```
proto/
├── broker/v1.proto                                NEW — gRPC contract (~250 LOC)
├── buf.yaml                                       NEW
├── buf.gen.yaml                                   NEW
└── buf.lock                                       NEW (committed, generated)

backend/
├── app/
│   ├── brokers/base.py                            NEW — Pydantic mirror of proto + AccountResponse boundary
│   ├── services/brokers.py                        NEW — BrokerSidecarClient + BrokerRegistry + discover_loop
│   ├── services/ibkr_maintenance.py               NEW — in_weekend_reset, in_daily_reset, seconds_until_window_ends
│   ├── api/accounts.py                            NEW — REST routes + Pydantic models + error envelopes
│   ├── core/deps.py                               MODIFY — get_account_service, get_broker_registry
│   ├── api/__init__.py                            MODIFY — register accounts router
│   └── main.py                                    MODIFY — lifespan: BrokerRegistry start/stop + discover_loop
├── migrations/versions/0002_broker_accounts.py    NEW — broker_accounts table + last_seen_via column
├── pyproject.toml                                 MODIFY — + grpcio, grpcio-tools, ib_async
├── Dockerfile                                     MODIFY — + tzdata
└── tests/
    ├── api/test_accounts.py                       NEW
    ├── services/test_brokers.py                   NEW
    └── services/test_ibkr_maintenance.py          NEW

sidecar/                                           NEW — entirely new top-level dir
├── ibkr_sidecar.py                                NEW — entrypoint
├── handlers.py                                    NEW — gRPC service handlers
├── normalize.py                                   NEW — UK-pence + decimals + per-account avg_cost_unit
├── pnl_cache.py                                   NEW — reqPnLSingle subscription cache
├── probe.py                                       NEW — Health-only client for probe-sidecar.exe
├── pyproject.toml                                 NEW
├── tests/
│   ├── test_handlers.py                           NEW
│   ├── test_normalize.py                          NEW
│   └── golden/                                    NEW — recorded ib_async fixtures
└── scripts/
    ├── build-windows.ps1                          NEW — PyInstaller --onedir wrapper
    └── record-golden-traces.ps1                   NEW

deploy/nuc/                                        NEW + ports
├── verify-wg-windows.ps1                          NEW — §0 prerequisite halt-the-phase check
├── Launch-IBKRSidecar.vbs                         NEW
├── register-ibkr-sidecar.ps1                      NEW — sidecar +30s after gateway stagger
├── provision-sidecar-mtls.ps1                     NEW — CA + 4 server + 1 client + initial empty CRL
├── provision-and-publish.ps1                      NEW — wraps provision + POST to admin secrets API
├── renew-sidecar-mtls.ps1                         NEW
├── revoke-cert.ps1                                NEW — appends serial to crl.pem
├── Probe-Sidecar.ps1                              NEW — wraps probe-sidecar.exe; writes state file
├── RUNBOOK-mtls-recovery.md                       NEW — NUC compromise tabletop
└── (ported from /mnt/c/Dashboard_old/deploy/nuc/) BrokerWatchdog.ps1, BrokerTray.ps1, DailyRestart.ps1,
    Launch-{Watchdog,Tray,Hider,DailyRestart}.vbs, HideBrokerWindows.ps1, register-{daily-restart,
    watchdog,autostart}.ps1, verify-autostart.ps1, restart-{ib,futu,tray}.ps1, pause-*.ps1, resume-*.ps1,
    encrypt-ib-secrets.ps1, harden-post-install.ps1, start-gateways.ps1

frontend/
├── src/
│   ├── lib/decimal.ts                             NEW — safeParseDecimal helper
│   ├── services/{accounts,positions,orders}.ts    MODIFY — USE_MOCKS branch + MaintenanceError handling
│   ├── services/admin-api.ts                      MODIFY — add MaintenanceError class
│   ├── stores/global/fleet-health.ts              NEW — degraded_sidecars store + useFleetHealth selector
│   └── proto-gen/                                 NEW (gitignored) — TS proto codegen output
├── eslint-rules/no-unsafe-decimal-arithmetic.js   NEW
├── eslint.config.mjs                              MODIFY — register custom rule
├── package.json                                   MODIFY — + @bufbuild/protoc-gen-es, proto:gen script
├── .gitignore                                     MODIFY — + src/proto-gen/
└── .storybook/preview.ts                          MODIFY — set VITE_USE_MOCKS=true

.github/workflows/
├── ci.yml                                         MODIFY — + buf lint/generate, sidecar tests, frontend ESLint custom rule
├── deploy.yml                                     MODIFY — + accounts smoke
└── nightly-real-ibkr.yml                          NEW — cron 06:00 UTC, real-IB contract test

tests/e2e/smoke.spec.ts                            MODIFY — + Phase 4 frontend block (4 tests)

CHANGELOG.md, TASKS.md, CLAUDE.md, .env.example   MODIFY — Phase 4 close-out
```

---

## Chunk A — Prerequisites + scaffold

### Task 1: §0 prerequisite check — `verify-wg-windows.ps1` (CRITICAL gate; halts the phase if any check fails)

**Owner:** Claude (Windows ops glue, not Codex).

**Files:**
- Create: `deploy/nuc/verify-wg-windows.ps1`
- Create: `deploy/nuc/sync-to-windows.sh`

- [ ] **Step 1.1: Write the PowerShell verifier.** See spec §0 for the four checks: WG service running; `10.10.0.2` on a Windows interface (NOT WSL); firewall rule for ports 18001-18004 (idempotent create); test bind succeeds. Exit 0 on success, 1 on any failure with `==== PHASE 4 PREREQUISITE CHECK FAILED ====` banner.

- [ ] **Step 1.2: Add `deploy/nuc/sync-to-windows.sh`** — one-line rsync helper for keeping `C:\dashboard\deploy\nuc\` in step with the WSL repo (per CLAUDE.md L125 Phase 4+ work item):

```bash
#!/usr/bin/env bash
rsync -a --delete /home/joseph/dashboard/deploy/nuc/ /mnt/c/dashboard/deploy/nuc/
```

- [ ] **Step 1.3: Manually run on the NUC** (operator, PowerShell — NOT WSL).

```powershell
cd C:\dashboard\deploy\nuc
powershell -NoProfile -ExecutionPolicy Bypass -File verify-wg-windows.ps1
```

Expected (success): four `[PASS]` lines + `==== PHASE 4 PREREQUISITES OK ====` + exit 0. **If FAIL, halt the phase here and re-brainstorm.**

- [ ] **Step 1.4: Commit**

```bash
chmod +x deploy/nuc/sync-to-windows.sh
git add deploy/nuc/verify-wg-windows.ps1 deploy/nuc/sync-to-windows.sh
git commit -m "feat(deploy): add §0 wg-on-windows verifier + nuc sync helper"
```

---

### Task 2: Proto contract — `proto/broker/v1.proto` + buf config

**Owner:** Claude (declarative).

**Files:**
- Create: `proto/broker/v1.proto`, `proto/buf.yaml`, `proto/buf.gen.yaml`

- [ ] **Step 2.1: Write `proto/broker/v1.proto`** verbatim from spec §4.1 (~250 lines). Verify all 7 enums, the 6 RPCs, all message types are present. `Money.value` and quantities are decimal-as-string; conid is string; timestamps are `google.protobuf.Timestamp`.

- [ ] **Step 2.2: Write `proto/buf.yaml`**

```yaml
version: v2
modules:
  - path: broker
lint:
  use:
    - DEFAULT
breaking:
  use:
    - FILE
```

- [ ] **Step 2.3: Write `proto/buf.gen.yaml`**

```yaml
version: v2
plugins:
  - remote: buf.build/grpc/python:v1.66.0
    out: ../backend/app/brokers/_generated
  - remote: buf.build/grpc/python:v1.66.0
    out: ../sidecar/_generated
  - remote: buf.build/protocolbuffers/python:v28.2
    out: ../backend/app/brokers/_generated
  - remote: buf.build/protocolbuffers/python:v28.2
    out: ../sidecar/_generated
  - remote: buf.build/bufbuild/es:v2.2.2
    out: ../frontend/src/proto-gen
    opt:
      - target=ts
```

- [ ] **Step 2.4: Validate**

```bash
cd proto && buf lint
cd proto && buf generate
git add proto/buf.lock
```

- [ ] **Step 2.5: Add gitignore for generated dirs**

Append to `.gitignore`:
```
backend/app/brokers/_generated/
sidecar/_generated/
frontend/src/proto-gen/
```

- [ ] **Step 2.6: Commit**

```bash
git add proto/ .gitignore
git commit -m "feat(proto): broker v1 grpc contract + buf codegen wiring"
```

---

### Task 3: Backend Python codegen wiring + dependency bumps

**Owner:** Claude (build config).

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/scripts/proto-gen.sh`

- [ ] **Step 3.1: Add deps to `backend/pyproject.toml`** under `[project] dependencies`:

```toml
"grpcio>=1.66.0",
"grpcio-tools>=1.66.0",
"ib_async>=1.0.3",
"protobuf>=5.28.0",
```

- [ ] **Step 3.2: Lockfile refresh**

```bash
cd backend && uv lock
```

- [ ] **Step 3.3: Write `backend/scripts/proto-gen.sh`** — convenience wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cd ../proto && buf generate
cd ../backend
mkdir -p app/brokers/_generated
touch app/brokers/_generated/__init__.py
echo "[ok] backend proto codegen complete"
```

- [ ] **Step 3.4: Smoke test**

```bash
chmod +x backend/scripts/proto-gen.sh
./backend/scripts/proto-gen.sh
ls backend/app/brokers/_generated/  # expect: broker_pb2.py, broker_pb2_grpc.py, __init__.py
```

- [ ] **Step 3.5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/scripts/proto-gen.sh
git commit -m "build(backend): add grpcio + ib_async deps + proto-gen wrapper"
```

---

### Task 4: Sidecar package skeleton + Python codegen wiring

**Owner:** Claude.

**Files:**
- Create: `sidecar/pyproject.toml`, `sidecar/__init__.py`, `sidecar/scripts/proto-gen.sh`

- [ ] **Step 4.1: Write `sidecar/pyproject.toml`**

```toml
[project]
name = "ibkr-sidecar"
version = "0.4.0"
requires-python = ">=3.14"
dependencies = [
  "ib_async>=1.0.3",
  "grpcio>=1.66.0",
  "grpcio-tools>=1.66.0",
  "protobuf>=5.28.0",
  "structlog>=24.4.0",
  "cryptography>=43.0.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=9.0.0",
  "pytest-asyncio>=0.24.0",
  "pytest-cov>=5.0.0",
  "pytest-grpc>=0.8.0",
  "pyinstaller>=6.10.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.coverage.run]
omit = ["sidecar/_generated/*"]
```

- [ ] **Step 4.2: Empty `sidecar/__init__.py`** with `__version__ = "0.4.0"`.

- [ ] **Step 4.3: `sidecar/scripts/proto-gen.sh`** — same shape as backend's:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cd ../proto && buf generate
cd ../sidecar
mkdir -p _generated
touch _generated/__init__.py
echo "[ok] sidecar proto codegen complete"
```

- [ ] **Step 4.4: Lockfile + smoke**

```bash
cd sidecar && uv lock
chmod +x scripts/proto-gen.sh
./scripts/proto-gen.sh
ls _generated/
```

- [ ] **Step 4.5: Commit**

```bash
git add sidecar/pyproject.toml sidecar/__init__.py sidecar/scripts/proto-gen.sh sidecar/uv.lock
git commit -m "build(sidecar): pyproject + proto-gen scaffold"
```

---

### Task 5: Frontend TS codegen wiring

**Owner:** Claude.

**Files:**
- Modify: `frontend/package.json`, `frontend/.gitignore`

- [ ] **Step 5.1: Add to `frontend/package.json` devDependencies**

```json
"@bufbuild/protoc-gen-es": "^2.2.2",
"@bufbuild/protobuf": "^2.2.2"
```

And to `scripts`:

```json
"proto:gen": "cd ../proto && buf generate"
```

- [ ] **Step 5.2: Add `frontend/src/proto-gen/` to `frontend/.gitignore`**

- [ ] **Step 5.3: Install + smoke**

```bash
cd frontend && CI=true pnpm install --no-frozen-lockfile
pnpm proto:gen
ls src/proto-gen/broker/v1/
```

- [ ] **Step 5.4: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/.gitignore
git commit -m "build(frontend): wire bufbuild es codegen for proto-gen output"
```

---

## Chunk B — Sidecar core

### Task 6: Sidecar entrypoint skeleton — argparse + structlog + clean exit

**Owner:** Codex (Python source) — Claude writes test.

**Files:**
- Create: `sidecar/ibkr_sidecar.py`, `sidecar/tests/test_entrypoint.py`

- [ ] **Step 6.1: Dispatch Codex** to scaffold the entrypoint per spec §4.2 inputs (CLI flags or env): `--label`, `--gateway-port`, `--grpc-port`, `--tls-cert-pem`, `--tls-key-pem`, `--tls-ca-bundle-pem`, `--tls-crl-pem`, `--log-dir`, `--state-dir`. Use `argparse`. Configure structlog with JSON-render + per-key redaction pattern (`^(password|secret|token|tls_key|private_key|api_key)$`). Add `TimedRotatingFileHandler(when='midnight', backupCount=14, encoding='utf-8')` to `--log-dir`. Provide `main()` and `__main__` block. Stub `run()` async function that just sleeps until SIGTERM.

- [ ] **Step 6.2: Claude writes test** at `sidecar/tests/test_entrypoint.py` — `--help` exits 0, redaction processor strips `password=foo` but keeps `cert_path=/x`, log file rotates daily.

- [ ] **Step 6.3: Verify**

```bash
cd sidecar && uv run python -m sidecar.ibkr_sidecar --help
cd sidecar && uv run pytest tests/test_entrypoint.py -v
```

- [ ] **Step 6.4: Commit**

```bash
git add sidecar/ibkr_sidecar.py sidecar/tests/test_entrypoint.py
git commit -m "feat(sidecar): entrypoint skeleton — argparse + structlog + log rotation"
```

---

### Task 7: Self-throttled startup backoff

**Owner:** Codex (logic) + Claude (test).

**Files:**
- Create: `sidecar/backoff.py`, `sidecar/tests/test_backoff.py`

- [ ] **Step 7.1: Codex writes `sidecar/backoff.py`** per spec §4.2 H6:
  - `apply_startup_backoff(state_dir: Path) -> None`: read `<state_dir>/last_fail.txt` (epoch + delay); if `now - last_fail < min(prev_delay * 2, 60)`, sleep remainder.
  - `record_failure(state_dir: Path, prev_delay: float) -> None`: writes `(now, min(prev_delay * 2, 60))`.
  - `clear_failure(state_dir: Path) -> None`: deletes the file.

- [ ] **Step 7.2: Claude writes test** covering: no file → no sleep; recent file → sleeps; old file → no sleep; clear deletes file; record doubles delay capped at 60.

```python
# sidecar/tests/test_backoff.py
import time
from pathlib import Path
from sidecar.backoff import apply_startup_backoff, record_failure, clear_failure

def test_no_state_file_does_not_sleep(tmp_path: Path):
    t0 = time.time()
    apply_startup_backoff(tmp_path)
    assert time.time() - t0 < 0.1

def test_record_then_apply_sleeps(tmp_path: Path, monkeypatch):
    record_failure(tmp_path, 0.5)  # next delay = 1.0
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    apply_startup_backoff(tmp_path)
    assert sleeps and 0.5 < sleeps[0] <= 1.0

def test_clear_failure_removes_file(tmp_path: Path):
    record_failure(tmp_path, 1.0)
    assert (tmp_path / "last_fail.txt").exists()
    clear_failure(tmp_path)
    assert not (tmp_path / "last_fail.txt").exists()

def test_record_caps_at_60(tmp_path: Path):
    record_failure(tmp_path, 100.0)
    txt = (tmp_path / "last_fail.txt").read_text()
    _, delay = txt.split(",")
    assert float(delay) == 60.0
```

- [ ] **Step 7.3: Run tests + commit**

```bash
cd sidecar && uv run pytest tests/test_backoff.py -v
git add sidecar/backoff.py sidecar/tests/test_backoff.py
git commit -m "feat(sidecar): self-throttled startup backoff state machine"
```

---

### Task 8: mTLS context + CRL reloader

**Owner:** Codex (logic) + Claude (tests).

**Files:**
- Create: `sidecar/tls.py`, `sidecar/tests/test_tls.py`

- [ ] **Step 8.1: Codex writes `sidecar/tls.py`**:
  - `build_grpc_server_credentials(cert_pem, key_pem, ca_bundle_pem, crl_pem) -> grpc.ServerCredentials` — mTLS-required, TLS 1.3 minimum, CRL-aware.
  - `start_crl_reloader(crl_path, server, every_seconds=60) -> asyncio.Task` — reload CRL every 60s; rebuild credentials; swap on running server.
  - `clientcert_sha256(der) -> str` — for `cert_verify_fail` log lines.

- [ ] **Step 8.2: Claude writes test** with handcrafted self-signed CA + leaf cert via `cryptography.hazmat.primitives.asymmetric.rsa`. Verify (a) untampered cert accepted; (b) tampered cert rejected; (c) revoked cert (added to CRL) rejected after reload.

- [ ] **Step 8.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_tls.py -v
git add sidecar/tls.py sidecar/tests/test_tls.py
git commit -m "feat(sidecar): mtls context + crl reloader (60s reload, tls 1.3 min)"
```

---

### Task 9: Currency + decimal normalization (UK pence + per-account avg_cost_unit)

**Owner:** Codex (port from v1) + Claude (table-driven tests).

**Files:**
- Create: `sidecar/normalize.py`, `sidecar/tests/test_normalize.py`

- [ ] **Step 9.1: Codex writes `sidecar/normalize.py`** per spec §5.3:
  - `GBX_EXCHANGES: frozenset[str] = frozenset({"LSE", "LSEETF", "IBIS", "BATEUK", "CHIXUK"})` — full list lifted from `/mnt/c/Dashboard_old/backend/app/services/quotes/base.py`.
  - `normalize_quote_currency(price: Decimal, currency: str, exchange: str) -> Decimal` — divide by 100 if `currency == 'GBP'` AND `exchange in GBX_EXCHANGES`.
  - `normalize_avg_cost(value: Decimal, account_number: str, config_unit: Literal["pounds","pence"]) -> Decimal` — divides by 100 if `pence`.
  - `decimal_str(value: float | Decimal | None) -> str` — canonical str-of-Decimal handling None as `"0"`.

- [ ] **Step 9.2: Claude writes table-driven test** — see spec §5.3 examples; cover GBX→GBP, USD untouched, decimal precision preserved, both avg_cost units.

- [ ] **Step 9.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_normalize.py -v
git add sidecar/normalize.py sidecar/tests/test_normalize.py
git commit -m "feat(sidecar): uk-pence quote normalize + per-account avg_cost_unit"
```

---

### Task 10: PnL subscription cache (`reqPnLSingle`)

**Owner:** Codex.

**Files:**
- Create: `sidecar/pnl_cache.py`, `sidecar/tests/test_pnl_cache.py`

- [ ] **Step 10.1: Codex writes `sidecar/pnl_cache.py`**:
  - `class PnLCache: __init__(ib: IB) → cache_dict[(account, conid)] = PnLSingle`
  - `async def get(account, conid) -> PnLSingle` — issues `ib.reqPnLSingleAsync(account, "", conid)` if not cached.
  - `async def cancel_all() -> None` — for each cached `PnLSingle`, call `ib.cancelPnLSingle(account, '', conid)`; clear dict.
  - `def snapshot(account, conid) -> tuple[Decimal | None, Decimal | None, Decimal | None]` — `(unrealized, realized_today, daily)`. NaN → None.

- [ ] **Step 10.2: Claude writes test** with `FakeIB` (mock providing `reqPnLSingleAsync` and `cancelPnLSingle`). Cover: cache hit on second call, NaN → None, cancel_all calls cancel for each cached entry.

- [ ] **Step 10.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_pnl_cache.py -v
git add sidecar/pnl_cache.py sidecar/tests/test_pnl_cache.py
git commit -m "feat(sidecar): pnl cache backed by reqpnlsingle (per stock_splits.md)"
```

---

### Task 11: gRPC handlers — Health + ListManagedAccounts + GetAccountSummary

**Owner:** Codex.

**Files:**
- Create: `sidecar/handlers.py`, `sidecar/tests/test_handlers_health_summary.py`

- [ ] **Step 11.1: Codex writes the first 3 handlers in `sidecar/handlers.py`**:
  - `class BrokerHandlers(broker_pb2_grpc.BrokerServicer):`
    - `__init__(ib: IB, label: str, version: str, last_tick_ref: dict)`.
    - `async def Health(req, ctx) -> HealthResponse`: returns `label`, `gateway_connected = ib.isConnected()`, `gateway_version` if connected, `last_tick_at`, `sidecar_version`.
    - `async def ListManagedAccounts(req, ctx) -> AccountsResponse`: maps `await ib.reqManagedAccountsAsync()` → list[Account]. `mode = LIVE if not account.startswith('D') else PAPER`. `currency_base` from cached `accountSummary` BASE tag.
    - `async def GetAccountSummary(req: AccountRef, ctx) -> SummaryResponse`: reads cached `accountSummary` for `req.account_number`, builds Money fields.

- [ ] **Step 11.2: Claude writes test** with FakeIB returning canned managed-accounts + summary tags. Asserts `D`-prefix → MODE_PAPER, `BASE=GBP, NetLiquidation=100000.50` → correct Money.

- [ ] **Step 11.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_handlers_health_summary.py -v
git add sidecar/handlers.py sidecar/tests/test_handlers_health_summary.py
git commit -m "feat(sidecar): handlers — health + listmanaged + accountsummary"
```

---

### Task 12: gRPC handlers — GetPositions (with PnL cache + UK-pence + multi-account filter)

**Owner:** Codex.

**Files:**
- Modify: `sidecar/handlers.py`
- Create: `sidecar/tests/test_handlers_positions.py`

- [ ] **Step 12.1: Codex extends `BrokerHandlers`** with `async def GetPositions(req: AccountRef, ctx)`:
  - Calls `await ib.reqPositionsAsync()` (returns ALL accounts).
  - Client-side filters by `req.account_number`; logs WARN if filter trimmed any rows (per spec invariant).
  - For each kept position: PnL via `PnLCache.snapshot(account, conid)`.
  - Money: `normalize_quote_currency` to `market_price` (only); `avg_cost` via `normalize_avg_cost` with `config_unit` from `ConfigService.get("broker", f"{account_number}.avg_cost_unit", default="pounds")`.

- [ ] **Step 12.2: Claude writes test** with FakeIB returning AAPL/USD, SGLN/GBP/LSE (UK pence), VWRP/GBP/LSE (split-adjusted via fake PnL). Assert: SGLN market_price scaled GBX→GBP, VWRP unrealized_pnl from cache, multi-account row filtered out, WARN log emitted.

- [ ] **Step 12.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_handlers_positions.py -v
git add sidecar/handlers.py sidecar/tests/test_handlers_positions.py
git commit -m "feat(sidecar): getpositions — pnl cache, gbx normalize, account filter"
```

---

### Task 13: gRPC handlers — GetOrders + GetContract

**Owner:** Codex.

**Files:**
- Modify: `sidecar/handlers.py`
- Create: `sidecar/tests/test_handlers_orders_contract.py`

- [ ] **Step 13.1: Codex extends `BrokerHandlers`**:
  - `async def GetOrders(req, ctx) -> OrdersResponse`: `ib.openTrades()` (open) + `ib.fills()` filtered to today (filled). Map IBKR Trade/Order/Fill to proto Order. Status mapping: `Submitted/PendingSubmit → SUBMITTED`, `PreSubmitted → PENDING`, `Filled → FILLED`, `Cancelled/ApiCancelled → CANCELLED`, `Inactive → REJECTED`.
  - `async def GetContract(req: ContractRef, ctx) -> ContractResponse`: `ib.qualifyContractsAsync(Contract(conId=int(req.conid)))` → proto Contract.

- [ ] **Step 13.2: Claude writes test** covering one open limit order + one filled-today market order; conId-only contract resolution.

- [ ] **Step 13.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_handlers_orders_contract.py -v
git add sidecar/handlers.py sidecar/tests/test_handlers_orders_contract.py
git commit -m "feat(sidecar): getorders + getcontract handlers"
```

---

### Task 14: Sidecar lifecycle wiring — connect, subscribe, serve, shutdown

**Owner:** Codex.

**Files:**
- Modify: `sidecar/ibkr_sidecar.py`
- Create: `sidecar/tests/test_lifecycle.py`

- [ ] **Step 14.1: Codex extends `ibkr_sidecar.py`** to wire Tasks 7-13:
  - On startup: `apply_startup_backoff(state_dir)` → load TLS material → connect `ib_async.IB()` to `127.0.0.1:<gateway-port>` with `clientId = (fnv1a32((hostname + "|" + label).encode()) % 900) + 100` → `await ib.reqManagedAccountsAsync()` → for each account `await ib.reqAccountSummaryAsync(group="All", tags="NetLiquidation,TotalCashValue,RealizedPnL,UnrealizedPnL,BuyingPower,BASE")` → start `BrokerHandlers` server bound to `10.10.0.2:<grpc-port>` with mTLS creds → start CRL reloader task.
  - Watch `IB.disconnectedEvent` — if disconnected for >30s, exit 64.
  - On `SIGTERM`/`SIGINT`: `pnl_cache.cancel_all()` → `ib.cancelAccountSummary` per account → `ib.disconnect()` → drain gRPC server → `clear_failure(state_dir)` → exit 0.

- [ ] **Step 14.2: Claude writes test** using FakeIB + in-process gRPC server: assert lifecycle order, clean shutdown clears backoff file, abnormal exit records failure.

- [ ] **Step 14.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/ -v --cov=sidecar --cov-report=term-missing
git add sidecar/ibkr_sidecar.py sidecar/tests/test_lifecycle.py
git commit -m "feat(sidecar): full lifecycle wiring — connect, subscribe, serve, shutdown"
```

---

## Chunk C — Sidecar packaging + golden traces

### Task 15: Probe-only client — `sidecar/probe.py`

**Owner:** Codex.

**Files:**
- Create: `sidecar/probe.py`, `sidecar/tests/test_probe.py`

- [ ] **Step 15.1: Codex writes `sidecar/probe.py`** — gRPC client:
  - Takes `--label --port --client-cert --client-key --ca`.
  - Builds mTLS channel to `10.10.0.2:<port>`.
  - Calls `Broker.Health()` with 3s deadline.
  - Exits 0 if `gateway_connected=true`; 1 otherwise. Prints `[ok|degraded|down] label=<label> gw=<connected> ver=<version>`.

- [ ] **Step 15.2: Claude writes test** with in-process server returning canned Health responses; assert exit code matrix.

- [ ] **Step 15.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_probe.py -v
git add sidecar/probe.py sidecar/tests/test_probe.py
git commit -m "feat(sidecar): probe-only client for watchdog (probe-sidecar.exe target)"
```

---

### Task 16: PyInstaller `--onedir` build script

**Owner:** Claude (PowerShell).

**Files:**
- Create: `sidecar/scripts/build-windows.ps1`

- [ ] **Step 16.1: Write `sidecar/scripts/build-windows.ps1`**

```powershell
[CmdletBinding()] param([string]$OutDir = "dist")
$ErrorActionPreference = 'Stop'
Write-Host "[build] sidecar build starting..." -ForegroundColor Cyan
& "$PSScriptRoot/../scripts/proto-gen.sh"
uv sync --extra dev
uv run pyinstaller --onedir --noconfirm --name ibkr-sidecar --distpath $OutDir `
    --paths . --hidden-import grpc --hidden-import ib_async --collect-data ib_async ibkr_sidecar.py
uv run pyinstaller --onedir --noconfirm --name probe-sidecar --distpath $OutDir `
    --paths . --hidden-import grpc probe.py
Write-Host "[build] artifacts:" -ForegroundColor Green
Get-ChildItem -Recurse -Filter "*.exe" -Path $OutDir | ForEach-Object { Write-Host "  $($_.FullName)" }
$zip = "$OutDir/ibkr-sidecar-$(Get-Date -Format 'yyyyMMdd-HHmm').zip"
Compress-Archive -Force -Path "$OutDir/ibkr-sidecar","$OutDir/probe-sidecar" -DestinationPath $zip
Write-Host "[build] $zip" -ForegroundColor Green
```

- [ ] **Step 16.2: Manual verification on the NUC**:

```powershell
cd C:\dashboard\sidecar
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
.\dist\ibkr-sidecar\ibkr-sidecar.exe --help
.\dist\probe-sidecar\probe-sidecar.exe --help
```

Expected: --help output, no AV false positives, no missing-DLL errors.

- [ ] **Step 16.3: Commit**

```bash
git add sidecar/scripts/build-windows.ps1
git commit -m "build(sidecar): pyinstaller --onedir wrapper for windows native binaries"
```

---

### Task 17: Golden-trace recorder

**Owner:** Claude (PowerShell wrapper) + Codex (Python helper).

**Files:**
- Create: `sidecar/scripts/record-golden-traces.ps1`, `sidecar/scripts/record_traces.py`, `sidecar/tests/golden/.gitkeep`

- [ ] **Step 17.1: Claude writes `record-golden-traces.ps1`**:

```powershell
[CmdletBinding()] param([int]$Port = 4002, [string]$OutDir = "$PSScriptRoot/../tests/golden")
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "[record] connecting to paper gateway $Port..." -ForegroundColor Cyan
uv run python -m sidecar.scripts.record_traces --port $Port --out-dir $OutDir
```

- [ ] **Step 17.2: Codex writes `record_traces.py`** — connects ib_async → calls `reqManagedAccountsAsync`, `reqAccountSummaryAsync`, `reqPositionsAsync`, `openTrades`, `fills`, `qualifyContractsAsync` for AAPL conId 265598 → JSON-serializes results to `<OutDir>/<method>.json`.

- [ ] **Step 17.3: Manually run on NUC**

```powershell
cd C:\dashboard\sidecar
powershell -ExecutionPolicy Bypass -File scripts/record-golden-traces.ps1 -Port 4002
ls tests/golden/
```

- [ ] **Step 17.4: Commit recorded fixtures**

```bash
git add sidecar/scripts/record-golden-traces.ps1 sidecar/scripts/record_traces.py sidecar/tests/golden/
git commit -m "test(sidecar): golden-trace recorder + initial paper-gateway fixtures"
```

---

### Task 18: Golden-trace replay tests

**Owner:** Claude.

**Files:**
- Create: `sidecar/tests/test_golden_replay.py`, `sidecar/conftest.py`

- [ ] **Step 18.1: Write `sidecar/conftest.py`** with `golden_fake_ib(method_name) → FakeIB` fixture that loads `tests/golden/<method>.json` and returns it from the corresponding `await` call.

- [ ] **Step 18.2: Write `sidecar/tests/test_golden_replay.py`**:

```python
import json
from pathlib import Path
import pytest
from sidecar.handlers import BrokerHandlers
from sidecar._generated.broker.v1 import broker_pb2

GOLDEN_DIR = Path(__file__).parent / "golden"

@pytest.mark.asyncio
async def test_listmanagedaccounts_replay(golden_fake_ib):
    fake = golden_fake_ib("reqManagedAccountsAsync")
    handlers = BrokerHandlers(ib=fake, label="isa-paper", version="test", last_tick_ref={})
    resp = await handlers.ListManagedAccounts(broker_pb2.Empty(), ctx=None)
    assert len(resp.accounts) >= 1

@pytest.mark.asyncio
async def test_getpositions_replay_with_pnl(golden_fake_ib):
    fake = golden_fake_ib("reqPositionsAsync")
    handlers = BrokerHandlers(ib=fake, label="isa-paper", version="test", last_tick_ref={})
    resp = await handlers.GetPositions(broker_pb2.AccountRef(account_number="DU0000000"), ctx=None)
    for pos in resp.positions:
        assert pos.unrealized_pnl.value != ""

@pytest.mark.asyncio
async def test_proto_shape_round_trip(golden_fake_ib):
    fake = golden_fake_ib("reqManagedAccountsAsync")
    handlers = BrokerHandlers(ib=fake, label="isa-paper", version="test", last_tick_ref={})
    resp = await handlers.ListManagedAccounts(broker_pb2.Empty(), ctx=None)
    raw = resp.SerializeToString()
    decoded = broker_pb2.AccountsResponse.FromString(raw)
    assert decoded == resp
```

- [ ] **Step 18.3: Run + commit**

```bash
cd sidecar && uv run pytest tests/test_golden_replay.py -v
git add sidecar/conftest.py sidecar/tests/test_golden_replay.py
git commit -m "test(sidecar): golden-trace replay assertions for proto contract"
```

---

## Chunk D — mTLS provisioning + revocation

### Task 19: Root CA + 4 server certs + 1 client cert provisioner

**Owner:** Claude (PowerShell).

**Files:**
- Create: `deploy/nuc/provision-sidecar-mtls.ps1`

- [ ] **Step 19.1: Write the script** — generates self-signed CA, 4 server certs (CN=`sidecar-<label>`, SAN=`10.10.0.2`), 1 client cert (CN=`dashboard-backend`), initial empty CRL. Idempotent. Uses openssl from Git-for-Windows. Tightens ACL on `C:\dashboard\secrets\` to SYSTEM:F + current user:R. Prints client material as `==BEGIN CLIENT_CERT_PEM==` blocks for `provision-and-publish.ps1` to consume.

- [ ] **Step 19.2: Manually run on NUC** to generate the initial materials.

```powershell
powershell -ExecutionPolicy Bypass -File deploy/nuc/provision-sidecar-mtls.ps1
ls C:\dashboard\secrets\  # expect: ca.{key,pem}, sidecar-*.{key,crt}, client-backend.{key,crt}, crl.pem
```

- [ ] **Step 19.3: Commit (the script only — secrets are gitignored)**

```bash
git add deploy/nuc/provision-sidecar-mtls.ps1
git commit -m "feat(deploy): mtls ca + 4 server certs + 1 client cert provisioner"
```

---

### Task 20: `provision-and-publish.ps1` — automated cert→backend distribution

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/provision-and-publish.ps1`

- [ ] **Step 20.1: Write the script** — wraps Task 19's provisioner + POSTs the 3 client PEMs to `/api/admin/secrets/broker/mtls.{client_cert,client_key,ca_bundle}_pem` via CF Access service token (`Invoke-RestMethod` with `CF-Access-Client-Id` + `CF-Access-Client-Secret` headers).

- [ ] **Step 20.2: Manually verify on NUC** (operator runs once after first provisioning).

- [ ] **Step 20.3: Commit**

```bash
git add deploy/nuc/provision-and-publish.ps1
git commit -m "feat(deploy): provision-and-publish — automate cert -> admin api flow"
```

---

### Task 21: `revoke-cert.ps1` — append serial to CRL + bump mtime

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/revoke-cert.ps1`

- [ ] **Step 21.1: Write the script** — uses `openssl ca -gencrl` against a minimal openssl config bootstrapped on first run (`ca.cnf`, `index.txt`, `crlnumber`). Appends a revoke entry to `index.txt` then regenerates the CRL. Bumps file mtime so the sidecars' 60s reloader notices.

- [ ] **Step 21.2: Commit**

```bash
git add deploy/nuc/revoke-cert.ps1
git commit -m "feat(deploy): revoke-cert.ps1 — append serial to crl, bump mtime"
```

---

### Task 22: `renew-sidecar-mtls.ps1` — annual rotation

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/renew-sidecar-mtls.ps1`

- [ ] **Step 22.1: Write the script** — re-uses provisioner (without `-RegenerateRoot`) to mint fresh leaf certs, then `Stop-ScheduledTask` → `Start-ScheduledTask` per sidecar (one at a time).

- [ ] **Step 22.2: Commit**

```bash
git add deploy/nuc/renew-sidecar-mtls.ps1
git commit -m "feat(deploy): annual cert renewal — rolls one sidecar at a time"
```

---

### Task 23: `RUNBOOK-mtls-recovery.md` — NUC compromise tabletop

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/RUNBOOK-mtls-recovery.md`

- [ ] **Step 23.1: Write the runbook** — 6 steps (stop sidecars → stop backend → regenerate root CA → republish via provision-and-publish → start backend + sidecars → verify with curl). Note 5-min downtime budget. Trust window note: treat sidecar→backend traffic between t-of-pwn and step 5 as compromised. Rehearsal cadence: quarterly on paper sidecars only.

- [ ] **Step 23.2: Commit**

```bash
git add deploy/nuc/RUNBOOK-mtls-recovery.md
git commit -m "docs(deploy): runbook for nuc-compromise mtls recovery"
```

---

## Chunk E — NUC ops glue

### Task 24: Port `BrokerWatchdog.ps1` from Dashboard_old

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/BrokerWatchdog.ps1`, `deploy/nuc/Launch-Watchdog.vbs`, `deploy/nuc/register-watchdog.ps1`

- [ ] **Step 24.1: Verbatim copy from Dashboard_old**

```bash
cp /mnt/c/Dashboard_old/deploy/nuc/BrokerWatchdog.ps1 deploy/nuc/
cp /mnt/c/Dashboard_old/deploy/nuc/Launch-Watchdog.vbs deploy/nuc/
cp /mnt/c/Dashboard_old/deploy/nuc/register-watchdog.ps1 deploy/nuc/
wc -l deploy/nuc/BrokerWatchdog.ps1   # expect ~299
```

- [ ] **Step 24.2: Verify `Test-InResetWindow` is intact** by grepping for the function definition (line 53-90 area per memory).

- [ ] **Step 24.3: Add `# (BACKLOG: extend with sidecar probes — see Task 28)` comment** at the bottom of the script.

- [ ] **Step 24.4: Commit**

```bash
git add deploy/nuc/BrokerWatchdog.ps1 deploy/nuc/Launch-Watchdog.vbs deploy/nuc/register-watchdog.ps1
git commit -m "deploy(nuc): port brokerwatchdog + launch + register from dashboard_old"
```

---

### Task 25: Port `BrokerTray.ps1` + helpers (with M19 layout review)

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/BrokerTray.ps1`, `deploy/nuc/Launch-Tray.vbs`, `deploy/nuc/restart-tray.ps1`

- [ ] **Step 25.1: Copy from Dashboard_old + read end-to-end** (per spec M19).

```bash
cp /mnt/c/Dashboard_old/deploy/nuc/BrokerTray.ps1 deploy/nuc/
cp /mnt/c/Dashboard_old/deploy/nuc/Launch-Tray.vbs deploy/nuc/
cp /mnt/c/Dashboard_old/deploy/nuc/restart-tray.ps1 deploy/nuc/
```

- [ ] **Step 25.2: Decision point — does the v1 layout fit 8 dots cleanly?**
  - Read the `Build-TrayMenu` function (or equivalent).
  - If "1 line per gateway" — 8 lines fit; just add sidecar lines.
  - If fixed-grid — budget ~200 lines additional layout in Task 28.
  - Document inline at the top: `# Phase 4 layout decision (YYYY-MM-DD): EXTEND | REWRITE — <reason>`.

- [ ] **Step 25.3: Commit**

```bash
git add deploy/nuc/BrokerTray.ps1 deploy/nuc/Launch-Tray.vbs deploy/nuc/restart-tray.ps1
git commit -m "deploy(nuc): port brokertray + helpers (sidecar dots wired in task 28)"
```

---

### Task 26: Port remaining NUC ops scripts

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/DailyRestart.ps1`, `Launch-DailyRestart.vbs`, `register-daily-restart.ps1`, `HideBrokerWindows.ps1`, `Launch-Hider.vbs`, `start-gateways.ps1`, `restart-ib.ps1`, `restart-futu.ps1`, `pause-brokers.ps1`, `pause-paper-brokers.ps1`, `resume-brokers.ps1`, `resume-paper-brokers.ps1`, `encrypt-ib-secrets.ps1`, `harden-post-install.ps1`, `register-autostart.ps1`, `verify-autostart.ps1`

- [ ] **Step 26.1: Bulk copy from `/mnt/c/Dashboard_old/deploy/nuc/`**

```bash
for f in DailyRestart.ps1 Launch-DailyRestart.vbs register-daily-restart.ps1 \
         HideBrokerWindows.ps1 Launch-Hider.vbs start-gateways.ps1 \
         restart-ib.ps1 restart-futu.ps1 pause-brokers.ps1 pause-paper-brokers.ps1 \
         resume-brokers.ps1 resume-paper-brokers.ps1 encrypt-ib-secrets.ps1 \
         harden-post-install.ps1 register-autostart.ps1 verify-autostart.ps1; do
  cp /mnt/c/Dashboard_old/deploy/nuc/$f deploy/nuc/
done
```

- [ ] **Step 26.2: Spot-check** that paths are still consistent (`C:\IBC\secrets\` references stay untouched per spec L26).

- [ ] **Step 26.3: Commit**

```bash
git add deploy/nuc/
git commit -m "deploy(nuc): port remaining ops glue scripts from dashboard_old"
```

---

### Task 27: New `Launch-IBKRSidecar.vbs` + `register-ibkr-sidecar.ps1`

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/Launch-IBKRSidecar.vbs`, `deploy/nuc/register-ibkr-sidecar.ps1`

- [ ] **Step 27.1: Write `Launch-IBKRSidecar.vbs`** — wscript-based hidden-console launcher (per `feedback_ibc_gotchas.md` issue 6). Takes label as arg; resolves gateway port + grpc port via lookup; invokes `C:\dashboard\sidecar\dist\ibkr-sidecar\ibkr-sidecar.exe` with all required flags (cert paths, log/state dirs).

- [ ] **Step 27.2: Write `register-ibkr-sidecar.ps1`** — registers 4 scheduled tasks with offsets +30/+60/+90/+120s after logon (per spec M25):

```powershell
[CmdletBinding()] param(
    [string[]]$Labels = @("isa-live","isa-paper","normal-live","normal-paper"),
    [int[]]$Offsets = @(30, 60, 90, 120),
    [string]$VbsPath = "C:\dashboard\deploy\nuc\Launch-IBKRSidecar.vbs"
)
$ErrorActionPreference = 'Stop'
for ($i = 0; $i -lt $Labels.Length; $i++) {
    $label = $Labels[$i]; $offset = $Offsets[$i]
    $taskName = "IBKRSidecar-$label"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument """$VbsPath"" $label"
    $trigger = New-ScheduledTaskTrigger -AtLogon
    $trigger.Delay = "PT${offset}S"
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 9999 `
        -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings | Out-Null
    Write-Host "[register] $taskName at +${offset}s after logon" -ForegroundColor Green
}
```

- [ ] **Step 27.3: Commit**

```bash
git add deploy/nuc/Launch-IBKRSidecar.vbs deploy/nuc/register-ibkr-sidecar.ps1
git commit -m "deploy(nuc): launch + register scheduled tasks for 4 ibkr sidecars"
```

---

### Task 28: Watchdog extension — `Probe-Sidecar.ps1` + `Adapt-SidecarHealth` block + tray dots

**Owner:** Claude.

**Files:**
- Create: `deploy/nuc/Probe-Sidecar.ps1`
- Modify: `deploy/nuc/BrokerWatchdog.ps1`, `deploy/nuc/BrokerTray.ps1`

- [ ] **Step 28.1: Write `Probe-Sidecar.ps1`** — wraps `dist/probe-sidecar/probe-sidecar.exe`; writes JSON state to `C:\dashboard\state\sidecar-<label>.health` with `{label, status, last_probe_at, probe_output}`. Exits 0/1 based on probe result.

- [ ] **Step 28.2: Modify `BrokerWatchdog.ps1`** — add `Adapt-SidecarHealth` function that runs after the existing gateway probe block:
  - Skip during weekend reset (`Test-InResetWindow` returns `true` AND name `weekend`).
  - For each `(label, port)` in 18001-18004, invoke `Probe-Sidecar.ps1`.
  - 2 consecutive BAD outside reset window → `Stop-ScheduledTask` + `Start-ScheduledTask`. Track count in `C:\dashboard\state\sidecar-<label>.badcount`.
  - Wire `Adapt-SidecarHealth` into the watchdog main loop after `Adapt-IBGatewayHealth`.

- [ ] **Step 28.3: Modify `BrokerTray.ps1`** — extend the menu with 4 sidecar dots reading from `C:\dashboard\state\sidecar-<label>.health`. Per Task 25.2 layout decision: extend if v1 layout supports it; otherwise rewrite.

- [ ] **Step 28.4: Commit**

```bash
git add deploy/nuc/Probe-Sidecar.ps1 deploy/nuc/BrokerWatchdog.ps1 deploy/nuc/BrokerTray.ps1
git commit -m "deploy(nuc): watchdog + tray learn about sidecars (probe-sidecar.exe)"
```

---

## Chunk F — Backend service layer

### Task 29: Alembic migration `0002_broker_accounts`

**Owner:** Codex.

**Files:**
- Create: `backend/migrations/versions/0002_broker_accounts.py`, `backend/tests/migrations/test_0002.py`

- [ ] **Step 29.1: Codex writes the migration** per spec §4.4. Schema must include `last_seen_via TEXT NOT NULL` (race-free soft-delete column from C1). Include the partial index. Down-migration drops the table + the two enum types.

- [ ] **Step 29.2: Claude writes test** — runs migration up + down + up again; checks: table exists with all columns; partial index exists; enum types created/dropped; UNIQUE constraint enforced.

- [ ] **Step 29.3: Verify**

```bash
cd backend && uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
uv run pytest tests/migrations/test_0002.py -v
```

- [ ] **Step 29.4: Commit**

```bash
git add backend/migrations/versions/0002_broker_accounts.py backend/tests/migrations/test_0002.py
git commit -m "feat(backend): alembic 0002 — broker_accounts table + last_seen_via"
```

---

### Task 30: Maintenance window helpers — `ibkr_maintenance.py`

**Owner:** Codex (port from v1).

**Files:**
- Create: `backend/app/services/ibkr_maintenance.py`, `backend/tests/services/test_ibkr_maintenance.py`

- [ ] **Step 30.1: Codex writes `ibkr_maintenance.py`** with `in_weekend_reset(now)`, `in_daily_reset(now)`, `seconds_until_window_ends(now)`. Source the per-region daily windows from the v1 PowerShell `Test-InResetWindow` (memory `ibkr_maintenance_schedule.md`). All three take tz-aware `datetime`. Use `ZoneInfo("America/New_York")`.

- [ ] **Step 30.2: Claude writes parametric tests** covering edge transitions:
  - Fri 22:59:59 ET → not weekend; Fri 23:00:00 ET → weekend
  - Sat 02:59:59 ET → weekend; Sat 03:00:00 ET → not weekend
  - Sun 00:30 ET → in NA daily; Sun 02:00 ET → not in NA daily
  - DST boundary (Mar 9, 2026 — DST starts) — transitions correct
  - `seconds_until_window_ends` returns 0 when not in window; >0 when in window

- [ ] **Step 30.3: Verify + commit**

```bash
cd backend && uv run pytest tests/services/test_ibkr_maintenance.py -v
git add backend/app/services/ibkr_maintenance.py backend/tests/services/test_ibkr_maintenance.py
git commit -m "feat(backend): ibkr maintenance window helpers — weekend + daily + dst"
```

---

### Task 31: `BrokerSidecarClient` — gRPC channel + Health + RPC wrappers

**Owner:** Codex.

**Files:**
- Create: `backend/app/brokers/base.py`, `backend/app/services/brokers.py` (just `BrokerSidecarClient` initially), `backend/tests/services/test_broker_client.py`

- [ ] **Step 31.1: Codex writes `backend/app/brokers/base.py`** — Pydantic v2 mirrors of the proto types (Account, Money, Summary, Position, Order, Contract, AccountResponse, AccountListResponse, AccountAliasUpdate). `AccountResponse` excludes `gateway_label` and `account_number` (M15). Pattern validators per L30.

- [ ] **Step 31.2: Codex writes `BrokerSidecarClient` in `backend/app/services/brokers.py`** with mTLS gRPC channel built from `app_secrets`, all 6 RPC wrappers, structlog instrumentation per call (latency_ms, label, method).

- [ ] **Step 31.3: Claude writes test** with in-process gRPC server + canned handlers. Verify each RPC roundtrips, mTLS handshake works, timeout returns DeadlineExceeded.

- [ ] **Step 31.4: Verify + commit**

```bash
cd backend && uv run pytest tests/services/test_broker_client.py -v
git add backend/app/brokers/base.py backend/app/services/brokers.py backend/tests/services/test_broker_client.py
git commit -m "feat(backend): brokersidecarclient + pydantic boundary models"
```

---

### Task 32: `BrokerRegistry` + healthy_clients + observation cache

**Owner:** Codex.

**Files:**
- Modify: `backend/app/services/brokers.py`, `backend/tests/services/test_brokers.py`

- [ ] **Step 32.1: Codex extends `brokers.py`** with `BrokerRegistry`:
  - `__init__(clients: dict[str, BrokerSidecarClient], db: Database)`.
  - `_health_state: dict[str, tuple[float, bool]]` — last probe ts + result per label.
  - `async def get_client(label) -> BrokerSidecarClient` — direct dict lookup.
  - `async def healthy_clients() -> list[BrokerSidecarClient]` — clients whose latest Health was ok within last 90s.
  - `async def health_probe_loop()` — every 5s when any unhealthy, every 60s when all healthy; updates `_health_state`.

- [ ] **Step 32.2: Claude writes test** with 4 mock clients. Verify: probe loop transitions a client unknown→healthy→unhealthy→healthy; `healthy_clients()` reflects the state; 90s expiry works.

- [ ] **Step 32.3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_brokers.py -v
git add backend/app/services/brokers.py backend/tests/services/test_brokers.py
git commit -m "feat(backend): brokerregistry + health probe loop"
```

---

### Task 33: `discover_loop` with race-free soft-delete

**Owner:** Codex (logic) + Claude (test for the C1 invariants).

**Files:**
- Modify: `backend/app/services/brokers.py`, `backend/tests/services/test_brokers.py`

- [ ] **Step 33.1: Codex implements `discover_loop` + `_discover_once`** per spec §4.3 pseudocode. The soft-delete UPDATE statement scopes to `last_seen_via = ANY(:healthy_labels)`; if `healthy_labels` is empty, NO rows match. Per-iteration `try/except`.

- [ ] **Step 33.2: Claude writes test** covering ALL the C1 invariants:

```python
@pytest.mark.asyncio
async def test_soft_delete_only_when_sidecar_healthy(...):
    # Pre-seed 2 accounts (both via sidecar 'isa-live')
    # Run _discover_once where sidecar is HEALTHY but reports neither account
    # → both rows get deleted_at set

@pytest.mark.asyncio
async def test_soft_delete_skipped_when_all_sidecars_unhealthy(...):
    # Pre-seed 4 accounts across 4 sidecars
    # All 4 sidecars report unhealthy this tick
    # → no soft-delete fires; all 4 accounts still active

@pytest.mark.asyncio
async def test_discover_loop_survives_iteration_failure(...):
    # Make one client raise on list_managed_accounts
    # → loop iteration logs error, increments err counter, continues

@pytest.mark.asyncio
async def test_reappearance_clears_deleted_at(...):
    # Account marked deleted_at = ts1
    # Same sidecar reports it again
    # → deleted_at cleared, last_seen_at bumped
```

- [ ] **Step 33.3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_brokers.py::test_soft_delete -v
git add backend/app/services/brokers.py backend/tests/services/test_brokers.py
git commit -m "feat(backend): discover_loop with race-free soft-delete (c1 + h13)"
```

---

### Task 34: `AccountService` orchestration

**Owner:** Codex.

**Files:**
- Modify: `backend/app/services/brokers.py`
- Create: `backend/tests/services/test_account_service.py`

- [ ] **Step 34.1: Codex implements `AccountService`**:
  - `list_accounts() -> tuple[list[AccountRow], list[str]]` — returns `(active_rows, degraded_labels)`.
  - `get_summary(account_id)` / `get_positions(account_id)` / `get_orders(account_id)` — DB lookup uuid→tuple, fan to sidecar via registry.
  - `update_alias(account_id, alias)` — Pydantic-validated alias update.
  - **Invariant check** (H11): on `get_positions`, if `Σ(quantity × avg_cost) > 1.5 × NLV`, increment `avg_cost_unit_suspected_wrong{account}` metric + log WARN.

- [ ] **Step 34.2: Claude writes test** for the invariant + the basic methods.

- [ ] **Step 34.3: Run + commit**

```bash
cd backend && uv run pytest tests/services/test_account_service.py -v
git add backend/app/services/brokers.py backend/tests/services/test_account_service.py
git commit -m "feat(backend): accountservice + avg_cost_unit invariant"
```

---

### Task 35: Lifespan wiring + DI providers

**Owner:** Codex.

**Files:**
- Modify: `backend/app/main.py`, `backend/app/core/deps.py`

- [ ] **Step 35.1: Codex wires `BrokerRegistry` + `discover_loop` + `health_probe_loop` into FastAPI lifespan**:

```python
# backend/app/main.py — extend existing lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing Phase 2 ConfigService startup ...

    broker_registry = await build_broker_registry(config_service, db)
    health_task = asyncio.create_task(broker_registry.health_probe_loop())
    discover_task = asyncio.create_task(broker_registry.discover_loop())
    app.state.broker_registry = broker_registry

    yield

    discover_task.cancel()
    health_task.cancel()
    await asyncio.gather(discover_task, health_task, return_exceptions=True)
    await broker_registry.close()
```

- [ ] **Step 35.2: Codex adds `get_broker_registry` and `get_account_service` to `core/deps.py`**.

- [ ] **Step 35.3: Smoke test**

```bash
cd backend && uv run uvicorn app.main:app --port 8001 &
sleep 3
curl -sf http://localhost:8001/health
kill %1
```

- [ ] **Step 35.4: Commit**

```bash
git add backend/app/main.py backend/app/core/deps.py
git commit -m "feat(backend): lifespan + di for brokerregistry + discover_loop"
```

---

### Task 35b: tzdata in backend Dockerfile (M20)

**Owner:** Claude.

**Files:**
- Modify: `backend/Dockerfile`

- [ ] **Step 35b.1: Add `RUN apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*`** to the runtime stage. Required for `ZoneInfo("America/New_York")`.

- [ ] **Step 35b.2: Build + smoke**

```bash
docker build -t backend-test backend/
docker run --rm backend-test python -c 'from zoneinfo import ZoneInfo; print(ZoneInfo("America/New_York"))'
```

- [ ] **Step 35b.3: Commit**

```bash
git add backend/Dockerfile
git commit -m "build(backend): + tzdata for zoneinfo (america/new_york)"
```

---

## Chunk G — REST routes

### Task 36: `GET /api/accounts` + `PATCH /api/accounts/{id}`

**Owner:** Codex.

**Files:**
- Create: `backend/app/api/accounts.py`, `backend/tests/api/test_accounts_list.py`
- Modify: `backend/app/api/__init__.py`

- [ ] **Step 36.1: Codex writes `accounts.py`** with `GET /api/accounts` returning `AccountListResponse(accounts, degraded_sidecars)` and `PATCH /api/accounts/{id}` accepting `AccountAliasUpdate`. All routes gated by `require_admin_jwt`. Include error-envelope helper for 404/503.

- [ ] **Step 36.2: Register the router in `app/api/__init__.py`**:

```python
from app.api.accounts import router as accounts_router
api_router.include_router(accounts_router, prefix="/api/accounts", tags=["accounts"])
```

- [ ] **Step 36.3: Claude writes test** — list returns shape with no `gateway_label`/`account_number`; patch validates alias pattern; 404 on bad UUID; 503 on all-unreachable.

- [ ] **Step 36.4: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_accounts_list.py -v
git add backend/app/api/accounts.py backend/app/api/__init__.py backend/tests/api/test_accounts_list.py
git commit -m "feat(api): get /api/accounts + patch alias (boundary-stripped responses)"
```

---

### Task 37: `GET /api/accounts/{id}/{summary,positions,orders}`

**Owner:** Codex.

**Files:**
- Modify: `backend/app/api/accounts.py`
- Create: `backend/tests/api/test_accounts_detail.py`

- [ ] **Step 37.1: Codex adds 3 routes** to `accounts.py`. Each route's error contract per spec §4.5:
  - `404` if uuid unknown OR soft-deleted.
  - `503 + Retry-After: 30` + `{"error":"sidecar_unreachable","label":"<gateway_label>"}` if outside reset window AND sidecar down.
  - `503 + Retry-After: <seconds>` + `{"error":"broker_maintenance","window":"weekend|daily","until":"<iso>"}` if inside reset window.

- [ ] **Step 37.2: Claude writes test** with mocked `AccountService` — covers all 4 status code paths (200, 404, 503-unreachable, 503-maintenance).

- [ ] **Step 37.3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_accounts_detail.py -v
git add backend/app/api/accounts.py backend/tests/api/test_accounts_detail.py
git commit -m "feat(api): get summary/positions/orders with 503+retry-after error envelope"
```

---

### Task 38: API documentation polish + OpenAPI assertions

**Owner:** Claude.

**Files:**
- Create: `backend/tests/api/test_openapi_phase4.py`

- [ ] **Step 38.1: Write a smoke test** that fetches `/openapi.json` and asserts:
  - Each Phase 4 path is listed (`/api/accounts`, `/api/accounts/{id}/summary`, etc.).
  - `AccountResponse` schema has NO `gateway_label` and NO `account_number` properties.
  - `AccountListResponse` has `degraded_sidecars`.
  - 503 response example body matches the maintenance/unreachable shape.

- [ ] **Step 38.2: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_openapi_phase4.py -v
git add backend/tests/api/test_openapi_phase4.py
git commit -m "test(api): openapi schema assertions for phase 4 boundary stripping"
```

---

## Chunk H — Frontend wiring

### Task 39: `decimal.ts` — safeParseDecimal helper + ESLint custom rule

**Owner:** Codex (helper) + Claude (ESLint rule).

**Files:**
- Create: `frontend/src/lib/decimal.ts`, `frontend/src/lib/decimal.test.ts`, `frontend/eslint-rules/no-unsafe-decimal-arithmetic.js`
- Modify: `frontend/eslint.config.mjs`

- [ ] **Step 39.1: Codex writes `decimal.ts`**:

```ts
export interface ParsedDecimal {
  display: number;
  precise: string;
  lossy: boolean;
}

export function safeParseDecimal(s: string): ParsedDecimal {
  if (!s) return { display: 0, precise: "0", lossy: false };
  const n = Number(s);
  return {
    display: Number.isFinite(n) ? n : 0,
    precise: s,
    lossy: !Number.isFinite(n) || n.toString() !== s,
  };
}
```

- [ ] **Step 39.2: Codex writes `decimal.test.ts`** — table-driven cases for round-trip and lossy detection.

- [ ] **Step 39.3: Claude writes the ESLint rule** at `frontend/eslint-rules/no-unsafe-decimal-arithmetic.js`. Detects `Number(x.value)` where `x.value` is on a Money-typed object, flags arithmetic on the result.

- [ ] **Step 39.4: Wire the rule** in `frontend/eslint.config.mjs`.

- [ ] **Step 39.5: Verify + commit**

```bash
cd frontend && pnpm test src/lib/decimal.test.ts && pnpm lint
git add frontend/src/lib/decimal.ts frontend/src/lib/decimal.test.ts \
        frontend/eslint-rules/no-unsafe-decimal-arithmetic.js \
        frontend/eslint.config.mjs
git commit -m "feat(lib): safeparsedecimal + no-unsafe-decimal-arithmetic eslint rule"
```

---

### Task 40: `MaintenanceError` class + 503 envelope handling in services

**Owner:** Codex.

**Files:**
- Create: `frontend/src/services/errors.ts`
- Modify: `frontend/src/services/accounts.ts`, `frontend/src/services/accounts.test.ts`

- [ ] **Step 40.1: Codex writes `MaintenanceError` + `SidecarUnreachableError`**:

```ts
export class MaintenanceError extends Error {
  constructor(public window: "weekend" | "daily", public until: string) {
    super(`broker_maintenance ${window} until ${until}`);
    this.name = "MaintenanceError";
  }
}
export class SidecarUnreachableError extends Error {
  constructor(public label: string) {
    super(`sidecar_unreachable label=${label}`);
    this.name = "SidecarUnreachableError";
  }
}
```

- [ ] **Step 40.2: Codex updates `accounts.ts`** with `USE_MOCKS` branch + 503 envelope parsing + custom errors per spec §4.6.

- [ ] **Step 40.3: Claude updates the test** for the new error path with MSW.

- [ ] **Step 40.4: Run + commit**

```bash
cd frontend && pnpm test src/services/accounts.test.ts
git add frontend/src/services/errors.ts frontend/src/services/accounts.ts frontend/src/services/accounts.test.ts
git commit -m "feat(services): maintenanceerror + sidecarunreachableerror + 503 envelope"
```

---

### Task 41: `positions.ts` + `orders.ts` real-API wiring

**Owner:** Codex.

**Files:**
- Modify: `frontend/src/services/positions.ts`, `frontend/src/services/orders.ts`, `frontend/src/services/positions.test.ts`, `frontend/src/services/orders.test.ts`

- [ ] **Step 41.1: Codex flips both services** with the same `USE_MOCKS` pattern + MaintenanceError handling.

- [ ] **Step 41.2: Claude verifies tests** — flips `VITE_USE_MOCKS=false`, asserts real-fetch path including 503 cases.

- [ ] **Step 41.3: Run + commit**

```bash
cd frontend && pnpm test src/services/{positions,orders}.test.ts
git add frontend/src/services/{positions,orders}{,.test}.ts
git commit -m "feat(services): positions + orders real-api wiring (vite_use_mocks branch)"
```

---

### Task 42: `useFleetHealth` selector + topbar pill

**Owner:** Codex.

**Files:**
- Create: `frontend/src/stores/global/fleet-health.ts`
- Modify: `frontend/src/components/patterns/ConnectedDropdown/ConnectedDropdown.tsx`

- [ ] **Step 42.1: Codex writes `fleet-health.ts`** — Zustand store with `degraded_sidecars: string[]` + `setDegraded` action; `useFleetHealth` selector returns `{ ok: boolean, count: number, labels: string[] }`.

- [ ] **Step 42.2: Codex updates `ConnectedDropdown`** — adds a "X broker(s) degraded" pill when `useFleetHealth().ok === false`.

- [ ] **Step 42.3: Claude updates test** to assert the pill appears when `degraded_sidecars` is non-empty.

- [ ] **Step 42.4: Run + commit**

```bash
cd frontend && pnpm test src/components/patterns/ConnectedDropdown/
git add frontend/src/stores/global/fleet-health.ts frontend/src/components/patterns/ConnectedDropdown/
git commit -m "feat(frontend): usefleethealth + degraded-broker pill in connecteddropdown"
```

---

### Task 43: Storybook + Vitest mocks default

**Owner:** Claude.

**Files:**
- Modify: `frontend/.storybook/preview.ts`

- [ ] **Step 43.1: Set `VITE_USE_MOCKS=true` in Storybook preview**:

```ts
if (typeof import.meta.env !== "undefined") {
  (import.meta.env as Record<string, string>).VITE_USE_MOCKS = "true";
}
```

- [ ] **Step 43.2: Run storybook build to confirm**

```bash
cd frontend && pnpm build-storybook
```

- [ ] **Step 43.3: Commit**

```bash
git add frontend/.storybook/preview.ts
git commit -m "test(storybook): pin vite_use_mocks=true so stories never hit real api"
```

---

## Chunk I — Tests + smoke

### Task 44: Backend integration test — discover loop end-to-end with in-process gRPC

**Owner:** Claude.

**Files:**
- Create: `backend/tests/integration/test_discover_e2e.py`

- [ ] **Step 44.1: Write the test** — spins up 4 in-process gRPC servers (one per fake sidecar), wires `BrokerRegistry` to them, runs one `_discover_once` cycle, asserts:
  - All 4 fake accounts upserted with correct `gateway_label`, `currency_base`.
  - One sidecar killed mid-tick → `degraded_sidecars` reflects it.
  - Zero soft-deletes when all sidecars unhealthy.

- [ ] **Step 44.2: Run + commit**

```bash
cd backend && uv run pytest tests/integration/test_discover_e2e.py -v
git add backend/tests/integration/test_discover_e2e.py
git commit -m "test(backend): discover loop e2e with 4 in-process grpc fake sidecars"
```

---

### Task 45: Playwright smoke — 4 new Phase 4 frontend tests

**Owner:** Claude.

**Files:**
- Modify: `tests/e2e/smoke.spec.ts`

- [ ] **Step 45.1: Append a `Phase 4 broker accounts` describe block**:

```ts
test.describe('Phase 4 broker accounts', () => {
  test('GET /api/accounts returns AccountListResponse without internal fields', async ({ request }) => {
    const r = await request.get('/api/accounts');
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.accounts)).toBe(true);
    expect(body).toHaveProperty('degraded_sidecars');
    for (const acc of body.accounts) {
      expect(acc).not.toHaveProperty('gateway_label');
      expect(acc).not.toHaveProperty('account_number');
      expect(typeof acc.id).toBe('string');
    }
  });

  test('GET positions returns proto-shaped JSON with Decimal-string Money', async ({ request }) => {
    const list = await (await request.get('/api/accounts')).json();
    if (list.accounts.length === 0) test.skip();
    const id = list.accounts[0].id;
    const r = await request.get(`/api/accounts/${id}/positions`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    for (const pos of body.positions) {
      expect(typeof pos.avg_cost.value).toBe('string');
      expect(typeof pos.avg_cost.currency).toBe('string');
    }
  });

  test('GET summary returns Money with currency', async ({ request }) => {
    const list = await (await request.get('/api/accounts')).json();
    if (list.accounts.length === 0) test.skip();
    const id = list.accounts[0].id;
    const r = await request.get(`/api/accounts/${id}/summary`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.summary.net_liquidation.currency).toMatch(/^[A-Z]{3}$/);
  });

  test('GET orders returns OrdersResponse (possibly empty)', async ({ request }) => {
    const list = await (await request.get('/api/accounts')).json();
    if (list.accounts.length === 0) test.skip();
    const id = list.accounts[0].id;
    const r = await request.get(`/api/accounts/${id}/orders`);
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(Array.isArray(body.orders)).toBe(true);
  });
});
```

- [ ] **Step 45.2: Local typecheck + commit**

```bash
cd tests/e2e && pnpm exec tsc --noEmit
git add tests/e2e/smoke.spec.ts
git commit -m "test(e2e): phase 4 smoke — accounts list/positions/summary/orders"
```

---

### Task 46: Nightly real-IBKR cron workflow

**Owner:** Claude.

**Files:**
- Create: `.github/workflows/nightly-real-ibkr.yml`, `deploy/nuc/RUNBOOK-self-hosted-runner.md`

- [ ] **Step 46.1: Write the workflow** — cron 06:00 UTC, self-hosted runner on the NUC, runs the sidecar contract tests against paper Gateway 4002:

```yaml
name: Nightly real-IBKR contract tests
on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch: {}
jobs:
  real-ibkr:
    runs-on: [self-hosted, nuc]
    timeout-minutes: 15
    env:
      CI_USE_REAL_IBKR: '1'
      IBKR_PAPER_PORT: '4002'
    steps:
      - uses: actions/checkout@v4
      - run: cd sidecar && uv sync --extra dev
      - run: cd sidecar && uv run pytest tests/test_handlers_*.py -v -m real_ibkr
      - if: failure()
        run: echo "::error::Nightly real-IBKR contract tests failed. Check ib_async release notes."
```

- [ ] **Step 46.2: Document the self-hosted runner setup** in `deploy/nuc/RUNBOOK-self-hosted-runner.md` (one-time NUC setup; not blocking Phase 4 tag).

- [ ] **Step 46.3: Commit**

```bash
git add .github/workflows/nightly-real-ibkr.yml deploy/nuc/RUNBOOK-self-hosted-runner.md
git commit -m "ci: nightly real-ibkr cron at 06:00 utc on self-hosted nuc runner"
```

---

### Task 47: CI workflow updates — buf lint/generate + sidecar tests + ESLint rule

**Owner:** Claude.

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 47.1: Add a `proto` job** before backend/frontend:

```yaml
proto:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: bufbuild/buf-setup-action@v1
    - run: cd proto && buf lint
    - run: cd proto && buf format --diff --exit-code
```

- [ ] **Step 47.2: Add sidecar test job**:

```yaml
sidecar:
  runs-on: ubuntu-latest
  needs: proto
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v5
    - run: cd proto && buf generate
    - run: cd sidecar && uv sync --extra dev --frozen
    - run: cd sidecar && uv run pytest --cov=sidecar --cov-fail-under=80 -m 'not real_ibkr'
```

- [ ] **Step 47.3: Existing backend + frontend jobs depend on `proto`.**

- [ ] **Step 47.4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: + proto job + sidecar tests + frontend custom eslint rule"
```

---

## Chunk J — Close-out

### Task 48: Update CHANGELOG, TASKS, CLAUDE.md

**Owner:** Claude.

**Files:**
- Modify: `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md`

- [ ] **Step 48.1: Append `## [0.4.0] — 2026-04-XX` to CHANGELOG.md** with the full feature list (proto contract, 4 sidecars, mTLS+CRL, broker_accounts table, `/api/accounts/*` routes, frontend service flip, watchdog extensions).

- [ ] **Step 48.2: Update TASKS.md** — Phase 4 header to `*(complete — v0.4.0 · 2026-04-XX)*` with the chunk-level checklist; mark Phase 5 as `*(next)*`.

- [ ] **Step 48.3: Update CLAUDE.md** — add a new section between "Frontend Runtime Notes" and "Configuration Storage":

```markdown
## Broker Adapter Notes (Phase 4+)

- **Sidecar topology:** one PyInstaller-frozen Python sidecar per IBKR gateway on the NUC at `10.10.0.2:18001-18004`. Backend reaches them over WireGuard with mTLS; CRL at `C:\dashboard\secrets\crl.pem` reloaded every 60s.
- **Read-only in v0.4.0:** trade execution lands in Phase 5. No order placement / modify / cancel from the dashboard.
- **avg_cost unit:** `broker.<account_number>.avg_cost_unit` config key (default `pounds`) per `app_config`. Sanity invariant `Σ(qty × avg_cost) > 1.5 × NLV` triggers `avg_cost_unit_suspected_wrong{account}` metric.
- **Maintenance windows:** `app/services/ibkr_maintenance.py` is the source of truth; backend returns `503 + Retry-After` during reset windows; watchdog skips probes during weekend reset.
- **NUC ops surface:** `deploy/nuc/` contains all PowerShell + VBS launchers + watchdog. `provision-and-publish.ps1` is the one-shot mTLS rotation flow.
```

- [ ] **Step 48.4: Commit**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase4): changelog + tasks + claude.md close-out for v0.4.0"
```

---

### Task 49: Pre-flight sweep

**Owner:** Claude.

- [ ] **Step 49.1: Frontend gates**

```bash
cd frontend && pnpm lint && pnpm stylelint && pnpm typecheck && pnpm test && pnpm build && pnpm build-storybook
```

- [ ] **Step 49.2: Backend gates**

```bash
cd backend && uv run ruff check . && uv run mypy app/ && uv run pytest --cov=app --cov-fail-under=80
```

- [ ] **Step 49.3: Sidecar gates**

```bash
cd sidecar && uv run ruff check . && uv run mypy . && uv run pytest --cov=sidecar --cov-fail-under=80
```

- [ ] **Step 49.4: NUC ops smoke** (operator-driven on the NUC):
  - `verify-wg-windows.ps1` exits 0
  - `provision-sidecar-mtls.ps1` succeeds, `C:\dashboard\secrets\` populated
  - `provision-and-publish.ps1` succeeds, backend reloads with new mTLS material
  - All 4 sidecars register as scheduled tasks; manually `Start-ScheduledTask` each; `Probe-Sidecar.ps1 -Label isa-paper -Port 18002` exits 0
  - Backend `/api/accounts` returns the 4 expected accounts

- [ ] **Step 49.5: Playwright smoke against local preview**

```bash
cd frontend && pnpm preview --port 4173 &
sleep 4
SMOKE_BASE_URL=http://localhost:4173 pnpm --dir tests/e2e exec playwright test smoke.spec.ts \
  --project=chromium --grep "Phase 4 broker accounts|Phase 3 frontend shell"
pkill -f "vite preview"
```

- [ ] **Step 49.6: CRL revocation drill** — `revoke-cert.ps1 -Serial <client-cert-serial>`; verify backend mTLS fails within 60s; restore via `provision-sidecar-mtls.ps1`.

- [ ] **Step 49.7: Maintenance-window drill** — set NUC clock forward to Fri 23:00 ET; verify watchdog skips sidecar probes; backend `/api/accounts/{id}/positions` returns `503 + Retry-After`. Restore clock.

---

### Task 50: USER GATE — push + tag v0.4.0 + verify CI + Deploy

**Owner:** User confirms; Claude executes.

- [ ] **Step 50.1: Push**

```bash
git push origin main
```

- [ ] **Step 50.2: Watch CI + Deploy**

```bash
gh run watch
```

Both must pass. If Deploy's accounts smoke fails, debug per the Phase 3 playbook (artifacts → screenshots → root cause).

- [ ] **Step 50.3: Tag**

```bash
git tag -a v0.4.0 -m "v0.4.0 — IBKR adapter (read-only) + broker_accounts + gRPC sidecars

See CHANGELOG.md for full feature list. Spec at
docs/superpowers/specs/2026-04-25-phase4-ibkr-adapter-design.md."
git push origin v0.4.0
```

- [ ] **Step 50.4: Wait for first nightly-real-ibkr run** (next 06:00 UTC) before declaring v0.4.0 fully landed. If it fails, file a Phase 4.x follow-up.

- [ ] **Step 50.5: Prod-verify (7 criteria, manual browser):**
  1. Login via CF Access on `https://dashboard.kiusinghung.com/`
  2. Overview renders with the 4 real accounts in the picker
  3. Positions page shows real holdings
  4. Orders page renders (possibly empty)
  5. Mode toggle works
  6. Command palette works
  7. Topbar shows degraded pill if any sidecar is down (test by `Stop-ScheduledTask` on one sidecar)

---

### Task 51: Memory updates

**Owner:** Claude.

**Files:** `~/.claude/projects/-home-joseph-dashboard/memory/*.md`

- [ ] **Step 51.1: Update `MEMORY.md` index** with new entries:

```
- [Phase 4 sidecar topology](phase4_sidecar_topology.md) — 4 grpc sidecars on NUC, mTLS+CRL over WG, soft-delete invariants, maintenance-window contract
- [mTLS recovery runbook](references) — see deploy/nuc/RUNBOOK-mtls-recovery.md
```

- [ ] **Step 51.2: Create `phase4_sidecar_topology.md`** capturing:
  - Port mapping (4001-4004 gateway, 18001-18004 sidecar)
  - clientId formula
  - Soft-delete invariant + `last_seen_via` column
  - 503 + Retry-After contract
  - per-account `avg_cost_unit` config key
  - Any deviations from the spec discovered during impl

- [ ] **Step 51.3: Update `project_tooling_inventory.md`** Phase 4-6 row to reflect live tooling.

- [ ] **Step 51.4: No git commit** — memory is outside the repo.

---

## Spec coverage checklist

Mapping spec §10 exit criteria to plan tasks:

| Spec exit criterion | Tasks |
|---|---|
| §0 verify-wg-windows.ps1 exits 0 | Task 1 |
| 4 IBKR sidecars register as scheduled tasks, survive logoff | Task 27 |
| GET /api/accounts returns 4 IBKR accounts | Tasks 31-37 + 49.4 |
| GET /api/accounts/{id}/{summary,positions,orders} round-trip | Task 37 + 49.4 |
| Frontend with VITE_USE_MOCKS=false renders real data | Tasks 40-43 |
| Watchdog kills + restarts stuck sidecar within 10 min outside reset | Task 28 + 49.4 |
| 80%+ test coverage backend + sidecar | Tasks 6-14, 29-37, 39-44 + Task 49 |
| Playwright smoke green: 11 prior + 4 new | Task 45 + Task 50 |
| mTLS proven (tampered cert rejected, revoked cert rejected within 60s) | Tasks 8 + 19-22 + 49.6 |
| CHANGELOG/TASKS/CLAUDE.md updated; v0.4.0 tagged | Tasks 48 + 50 |
| Golden traces + nightly real-IBKR cron green for 7 runs | Tasks 17-18 + 46 + post-tag wait |

## Type consistency check (self-review)

- `BrokerSidecarClient.health() -> HealthResponse` matches Task 31 + Task 32's `health_probe_loop` consumer.
- `AccountResponse` shape (no `gateway_label`, no `account_number`) is consistent across Task 31 (Pydantic), Task 36 (route), Task 38 (OpenAPI assertion), Task 45 (smoke test).
- `MaintenanceError(window, until)` ↔ backend body `{"error","window","until"}` matches across Tasks 37 + 40.
- `safeParseDecimal(s)` returns `{display, precise, lossy}` — used consistently in Tasks 39 + downstream NumericCell consumers (Phase 4.5).
- `last_seen_via` column referenced consistently in Tasks 29 (migration), 33 (discover loop), 44 (e2e test).
- Sidecar PyInstaller artifact path `dist/ibkr-sidecar/ibkr-sidecar.exe` matches across Task 16 (build), Task 27 (Launch-IBKRSidecar.vbs).
- `clientId` formula `(fnv1a32(hostname || "|" || label) % 900) + 100` documented in Task 14.

## Placeholder scan (self-review)

No "TBD", "TODO", "fill in details", or "similar to Task N" patterns. Each task has explicit file paths, code snippets where code is required, and exact commands. Open items are tagged in §11 of the spec (3 explicit "Resolved" choices) and not deferred to TBD.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-25-phase4-ibkr-adapter-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Claude dispatches a fresh `codex:codex-rescue` subagent per code task (backend Python, sidecar Python, frontend TS) and writes tests/stories/verification/commits inline; PowerShell/VBS tasks Claude does directly. Fast iteration, two-stage review per CLAUDE.md "Step 6".

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batched with checkpoints for review.

**Delegation rule still applies (TASKS.md Phase 3 header):** Codex writes source code; Claude writes tests/stories/verification/commits + all `.ps1`/`.vbs` Windows ops glue. Override per-task if you want.
