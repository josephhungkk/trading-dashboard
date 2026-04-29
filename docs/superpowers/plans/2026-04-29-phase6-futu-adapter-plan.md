# Phase 6 — Futu Adapter (HK only) + CJK Font Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single Futu sidecar at `10.10.0.2:18005` (label `"futu"`) speaking the same gRPC `Broker` contract as IBKR, plus a `Configure` RPC for app_secrets-driven creds, plus the JP kanji font fix. Read + place + cancel for HK stocks/ETFs/warrants/CBBC. Modify/bracket deferred to Phase 7.

**Architecture:** New `sidecar-futu/` Python package (separate `pyproject.toml`, `futu-api` + `grpcio` deps), PyInstaller-frozen → `dist-staging-futu/futu-sidecar.exe`. Backend's existing `BrokerRegistry`/`BrokerDiscoverer`/`OrderEventConsumer`/`PendingSubmitWatchdog` infrastructure is reused unchanged — broker-specific logic lives entirely in `sidecar-futu/normalize.py`. Configure RPC ships unlock_pwd_md5 + RSA priv key from `app_secrets` over the mTLS-secured gRPC channel; sidecar caches in memory and uses for OpenD `InitConnect` + `unlock_trade`. Cred rotation via new `POST /api/admin/brokers/{label}/reconfigure` admin endpoint.

**Tech Stack:** Python 3.14 (sidecar + backend), `futu-api` (sync, wrapped in `asyncio.to_thread`), gRPC + protobuf, FastAPI, Pydantic v2, PostgreSQL 18, Tailwind v4, React 19, Vite 7, `pyftsubset` (one-time operator-side font subsetting).

**Spec:** `docs/superpowers/specs/2026-04-29-phase6-futu-adapter-design.md` (commit `e81c43b`, architect-review applied — 5 HIGH + 6 MEDIUM resolved inline). Read it before starting any task; the invariants in §6.4 are load-bearing.

**Owner conventions per task:** `Codex` writes source; `Claude` writes tests, verifies, and commits. Per project memory `feedback_codex_fallback.md`, if Codex hits quota or stalls, Claude takes over the same task and the next planned-Codex task fires a canary retry.

**Reviewer chain (mandatory at every commit boundary, never batched per `feedback_proactive_tooling.md`):**

1. Implementer subagent (uses `superpowers:subagent-driven-development/implementer-prompt.md`)
2. **spec-compliance reviewer** (always)
3. **code-quality reviewer** (always)
4. **language-specific reviewer:** `python-reviewer` for backend/sidecar Python; `typescript-reviewer` for frontend
5. Conditional reviewers fire when their trigger surface is touched (per-task list under each task header below):
   - `security-reviewer` — secrets/auth/user-input/crypto paths
   - `database-reviewer` — schema/migration/SQL paths
   - `type-design-analyzer` — Pydantic/proto surfaces
   - `silent-failure-hunter` — async paths, broker adapter critical flows
   - `a11y-architect` — frontend UI changes
   - `build-error-resolver` — when `pnpm build` / `uv run` / PyInstaller fails
   - `tdd-guide` — when tests fail unexpectedly

**Snippet-file parallelism:** Per memory `feedback_snippet_file_parallelism.md`, when multiple tasks edit the same canonical file (`proto/broker/v1/broker.proto`, `backend/app/services/broker_registry_factory.py`, `sidecar-futu/handlers.py`), dispatch agents to write snippets to `/tmp/<task>.py`. Controller splices, dedupes imports, commits once. Tasks marked **PARALLEL-SAFE** below can dispatch concurrently; sequential tasks must wait.

---

## File structure

### New files (created)

| Path | Purpose |
|---|---|
| `sidecar-futu/pyproject.toml` | Package metadata, deps. |
| `sidecar-futu/uv.lock` | Pinned dep tree. |
| `sidecar-futu/futu_sidecar.py` | gRPC server entrypoint, mTLS bootstrap, signal handling. |
| `sidecar-futu/handlers.py` | gRPC `Broker` service implementation. |
| `sidecar-futu/futu_client.py` | Owns `OpenSecTradeContext` + reconnect loop + cred cache + `_init_task` cancellation. |
| `sidecar-futu/normalize.py` | Proto↔futu-api type mapping. Full `AssetClass` surface. |
| `sidecar-futu/sim.py` | `--simulator` branch with per-account `_order_event_queues`. |
| `sidecar-futu/tls.py` | mTLS server credentials loader (mirrors `sidecar/tls.py`). |
| `sidecar-futu/metrics.py` | Sidecar-local Prometheus counters. |
| `sidecar-futu/scripts/build-windows.ps1` | PyInstaller build → `dist-staging-futu/futu-sidecar.exe`. |
| `sidecar-futu/scripts/proto-gen.sh` | Codegen helper. |
| `sidecar-futu/tests/conftest.py` | Pytest fixtures for sidecar tests. |
| `sidecar-futu/tests/test_handlers_health.py` | Health handler unit tests. |
| `sidecar-futu/tests/test_handlers_configure.py` | Configure validation + cancellation tests. |
| `sidecar-futu/tests/test_futu_client.py` | InitConnect / backoff unit tests. |
| `sidecar-futu/tests/test_handlers_list_accounts.py` | ListManagedAccounts + trd_env mapping. |
| `sidecar-futu/tests/test_normalize.py` | Type-mapping unit tests. |
| `sidecar-futu/tests/test_status_mapping.py` | Futu→internal order status table. |
| `sidecar-futu/tests/test_handlers_summary.py` | GetAccountSummary unit tests. |
| `sidecar-futu/tests/test_handlers_positions.py` | GetPositions + AssetClass mapping. |
| `sidecar-futu/tests/test_handlers_contracts.py` | GetContract + SearchContracts. |
| `sidecar-futu/tests/test_handlers_place.py` | PlaceOrder unit tests. |
| `sidecar-futu/tests/test_handlers_cancel.py` | CancelOrder unit tests. |
| `sidecar-futu/tests/test_sim.py` | SIM dispatch tests. |
| `sidecar-futu/tests/test_handlers_orderevent.py` | OrderEvent stream + drop-pre-subscribe. |
| `sidecar-futu/tests/test_handlers_futu_contract.py` | Real grpc server contract tests. |
| `backend/app/api/brokers_admin.py` | `POST /api/admin/brokers/{label}/reconfigure`. |
| `backend/tests/api/test_brokers_admin.py` | Tests for the reconfigure endpoint. |
| `backend/tests/api/test_contracts_search_broker.py` | Tests for the broker-aware /api/contracts/search. |
| `backend/tests/integration/test_e2e_futu_chain.py` | preview→place→cancel mock E2E (HK stock, ETF, warrant, CBBC). |
| `backend/tests/integration/test_reconfigure_cycle.py` | H2 regression: sidecar restart re-Configure. |
| `backend/tests/fixtures/futu_test_data.py` | Futu-shape test data factories. |
| `frontend/public/fonts/NotoSansJP-kana-400.woff2` | Operator-generated kana subset (~50KB). |
| `frontend/public/fonts/NotoSansJP-kanji-400.woff2` | Operator-generated kanji subset (~1-2MB, lazy-loaded). |
| `frontend/public/fonts/README.md` | Subset pipeline + provenance. |
| `frontend/src/components/primitives/Text/CJKText.stories.tsx` | Storybook visual diff `lang="ja"` vs `zh-TW` vs `zh-CN`. |
| `deploy/nuc/build-windows-futu.ps1` | Build + sign + stage `futu-sidecar.exe`. |
| `deploy/nuc/restart-futu-sidecar.ps1` | Operator helper. |
| `deploy/nuc/runbook-futu-setup.md` | Operator runbook: RSA gen, OpenD config, app_secrets seed. |

### Modified files

| Path | Change |
|---|---|
| `proto/broker/v1/broker.proto` | Add `Configure` RPC, `ConfigureRequest`/`ConfigureResponse` messages, `Health.broker_id` + `Health.started_at`, `AssetClass.CBBC = 10`. |
| `backend/app/_generated/broker/v1/*` | Regenerated stubs (do not hand-edit). |
| `sidecar/_generated/broker/v1/*` | Regenerated stubs (IBKR sidecar). |
| `sidecar/handlers.py` | IBKR sidecar: implement `Configure` as no-op-returning-ok=true; populate `Health.broker_id="ibkr"` + `Health.started_at`. |
| `backend/app/brokers/base.py` | Extend `AssetClass` Literal with `"CBBC"`. |
| `backend/app/services/brokers.py` | `BrokerRegistry`: `_configured: dict[str, datetime]`, `_init_task` per label, `_probe_client` cross-checks `health.broker_id == SIDECAR_BROKERS[label]`. |
| `backend/app/services/broker_registry_factory.py` | `SIDECAR_PORTS["futu"]=18005`. New `SIDECAR_BROKERS = {"isa-live":"ibkr",...,"futu":"futu"}`. Lifespan calls Configure on futu sidecar. |
| `backend/app/api/contracts.py` | Accept `?broker=ibkr\|futu\|schwab` Pydantic Literal Query param; route to that label's healthy client; 422 on invalid; 503 on `schwab`. |
| `backend/app/api/admin.py` | Mount `brokers_admin` router. |
| `frontend/src/styles/global.css` | Two `@font-face` declarations under `font-family: "Noto Sans JP"`; `[lang\|="ja"]` selector. |
| `frontend/src/features/orders/ContractSearchInput.tsx` | Pass `?broker=` derived from active account. |
| `frontend/src/features/orders/TradeTicketModal.tsx` | Field-disable warrants/CBBC stop-limit. |
| `frontend/src/services/orders.ts` | `searchContracts` accepts optional `broker` arg. |
| `deploy/prometheus/alerts.yml` | Add `BrokerLabelMismatch` (page) + `BrokerFutuNormalizeUnknown` (warning) alerts. |
| `backend/app/core/metrics.py` | New counters: `broker_registry_label_mismatch_total{label}`, `broker_normalize_unknown_total{label,field}`. |
| `backend/tests/observability/test_metrics_orders.py` | Tests for the two new alerts. |
| `CHANGELOG.md` | New `[0.6.0]` section. |
| `TASKS.md` | Mark Phase 6 complete; carry over Phase 7 deferred items. |
| `CLAUDE.md` | Add §"Phase 6 — Futu adapter (v0.6.0)" subsection. |

---

## Pre-flight

- [ ] **PF1: Verify clean working tree on `main` at `e81c43b`+** (or branch off if user prefers a feature branch).

```bash
git status
git log --oneline -1
```

Expected: clean tree, head ≥ `e81c43b` (the architect-review-applied spec).

- [ ] **PF2: Verify `buf` is on PATH.**

```bash
buf --version
```

Expected: `1.x.x` printed.

- [ ] **PF3: Verify `uv` works.**

```bash
cd backend && uv --version
```

Expected: `0.x.x` printed.

- [ ] **PF4: Read the spec.**

Especially §4 (data flow), §5 (status mapping), §6 (invariants), §13 (architect findings table).

- [ ] **PF5: Read the futu-api docs introduction.**

Open `https://openapi.futunn.com/futu-api-doc/en/` in a browser. Section "Trade API" → `OpenSecTradeContext` reference (`place_order`, `cancel_order`, `get_acc_list`, `position_list_query`, `order_list_query`, `unlock_trade`, `InitConnect`). `TradeOrderHandlerBase`, `TradeDealHandlerBase`. Bookmark.

---

## Chunk A — Proto + wiring shells (~0.5 day)

### Task A1 — Proto: `Configure` RPC + `Health` extensions + `AssetClass.CBBC`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Modify: `proto/broker/v1/broker.proto`

- [ ] **Step 1: Read current proto**

```bash
cat proto/broker/v1/broker.proto | head -80
```

- [ ] **Step 2: Add `Configure` RPC inside `service Broker`**

```protobuf
  rpc Configure(ConfigureRequest) returns (ConfigureResponse);
```

- [ ] **Step 3: Add request + response messages at end of file**

```protobuf
message ConfigureRequest {
  string unlock_pwd_md5 = 1;
  // PEM-encoded RSA private key (1024-bit per Futu requirement); ~1.2KB,
  // well under gRPC default 4MB message limit. ASCII PEM, so `string`
  // is correct (not `bytes`).
  string rsa_priv_pem = 2;
  string opend_host = 3;
  int32 opend_port = 4;
  string connection_id = 5;
  map<string, string> metadata = 6;
}

message ConfigureResponse {
  bool ok = 1;
  string detail = 2;
}
```

- [ ] **Step 4: Extend `HealthResponse` with `started_at` + `broker_id`**

```protobuf
message HealthResponse {
  // existing fields 1..4 unchanged
  google.protobuf.Timestamp started_at = 5;
  string broker_id = 6;  // "ibkr" | "futu" | "schwab"
}
```

(If `google.protobuf.Timestamp` is not yet imported at the top of the file, add `import "google/protobuf/timestamp.proto";`.)

- [ ] **Step 5: Add `AssetClass.CBBC = 10`**

```protobuf
enum AssetClass {
  // existing values unchanged (0..9)
  CBBC = 10;  // HK callable bull/bear contract (牛熊證)
}
```

- [ ] **Step 6: Lint**

```bash
buf lint proto/
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add proto/broker/v1/broker.proto
git commit -m "feat(proto): add configure rpc + health.broker_id + assetclass.cbbc"
```

### Task A2 — Regenerate proto codegen for backend + sidecar + sidecar-futu

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality (auto-generated; review the diff for unexpected churn)

**Files:**
- Regenerate: `backend/app/_generated/broker/v1/broker_pb2.py`, `broker_pb2_grpc.py`, `broker_pb2.pyi`
- Regenerate: `sidecar/_generated/broker/v1/broker_pb2.py`, `broker_pb2_grpc.py`, `broker_pb2.pyi`

- [ ] **Step 1: Run proto-gen for backend**

```bash
bash backend/scripts/proto-gen.sh
```

- [ ] **Step 2: Run proto-gen for sidecar**

```bash
bash sidecar/scripts/proto-gen.sh
```

- [ ] **Step 3: Verify generated stubs include the new RPC + fields**

```bash
grep -n "Configure" backend/app/_generated/broker/v1/broker_pb2_grpc.py | head
grep -n "started_at\|broker_id" backend/app/_generated/broker/v1/broker_pb2.pyi | head
grep -n "CBBC" backend/app/_generated/broker/v1/broker_pb2.pyi | head
```

Expected: all three patterns visible.

- [ ] **Step 4: Smoke import**

```bash
cd backend && uv run python -c "from app._generated.broker.v1 import broker_pb2; print(broker_pb2.AssetClass.CBBC); print(broker_pb2.HealthResponse.DESCRIPTOR.fields_by_name['broker_id'])"
```

Expected: prints `10` then a `FieldDescriptor` repr.

- [ ] **Step 5: Commit**

```bash
git add backend/app/_generated/ sidecar/_generated/
git commit -m "chore(proto): regen stubs for configure rpc + health + cbbc"
```

### Task A3 — Extend `AssetClass` Literal in `backend/app/brokers/base.py`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Modify: `backend/app/brokers/base.py`

- [ ] **Step 1: Find current `AssetClass` Literal**

```bash
grep -n "AssetClass = Literal" backend/app/brokers/base.py
```

- [ ] **Step 2: Add `"CBBC"`**

```python
AssetClass = Literal[
    "ASSET_UNSPECIFIED",
    "STOCK",
    "ETF",
    "OPTION",
    "FUTURE",
    "FOREX",
    "CRYPTO",
    "BOND",
    "MUTUAL_FUND",
    "WARRANT",
    "CBBC",  # NEW (M1, Phase 6)
]
```

- [ ] **Step 3: Verify mypy**

```bash
cd backend && uv run mypy app/
```

Expected: no errors. (If existing `match` statements lack `case "CBBC":`, add `case _:` defaults.)

- [ ] **Step 4: Run existing tests**

```bash
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs); uv run pytest tests/ -x -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/brokers/base.py
git commit -m "feat(brokers): add cbbc to assetclass literal (5c m1)"
```

### Task A4 — `SIDECAR_BROKERS` dict + `SIDECAR_PORTS["futu"]` in registry factory

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `backend/app/services/broker_registry_factory.py`

- [ ] **Step 1: Read current file**

```bash
cat backend/app/services/broker_registry_factory.py
```

- [ ] **Step 2: Add `SIDECAR_BROKERS` mapping + futu port**

```python
SIDECAR_PORTS: dict[str, int] = {
    "isa-live": 18001,
    "isa-paper": 18002,
    "normal-live": 18003,
    "normal-paper": 18004,
    "futu": 18005,  # NEW
}

# H4: backend cross-checks Health.broker_id against this map at every probe.
# Mismatch → CRITICAL log + degraded label + BrokerLabelMismatch page alert.
SIDECAR_BROKERS: dict[str, str] = {
    "isa-live": "ibkr",
    "isa-paper": "ibkr",
    "normal-live": "ibkr",
    "normal-paper": "ibkr",
    "futu": "futu",
}
```

- [ ] **Step 3: Run registry-factory unit test**

```bash
cd backend && uv run pytest tests/services/test_brokers.py -k registry_factory -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/broker_registry_factory.py
git commit -m "feat(registry): wire sidecar_ports + sidecar_brokers for futu (h4)"
```

### Task A5 — Backend metrics: `broker_registry_label_mismatch_total` + `broker_normalize_unknown_total`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Add the two counters**

```python
broker_registry_label_mismatch_total = Counter(
    "broker_registry_label_mismatch_total",
    "Health.broker_id from sidecar disagreed with SIDECAR_BROKERS map.",
    labelnames=["label"],
    registry=registry,
)

broker_normalize_unknown_total = Counter(
    "broker_normalize_unknown_total",
    "Sidecar normalize layer received an unknown enum value from broker SDK.",
    labelnames=["label", "field"],
    registry=registry,
)
```

- [ ] **Step 2: Run existing metrics tests**

```bash
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs); uv run pytest tests/observability/test_metrics_orders.py -v
```

Expected: existing tests still pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/metrics.py
git commit -m "feat(metrics): broker_registry_label_mismatch + normalize_unknown counters"
```

### Task A6 — `BrokerLabelMismatch` + `BrokerFutuNormalizeUnknown` alerts + tests

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `deploy/prometheus/alerts.yml`
- Modify: `backend/tests/observability/test_metrics_orders.py`

- [ ] **Step 1: Add alerts to `phase5b_orders` group**

```yaml
      - alert: BrokerLabelMismatch
        expr: increase(broker_registry_label_mismatch_total[5m]) > 0
        for: 1m
        labels: { severity: page }
        annotations:
          summary: "Broker label mismatch on {{ $labels.label }}"
          description: "Sidecar at label {{ $labels.label }} reports a different broker_id than SIDECAR_BROKERS map. Operator misconfiguration; check deploy logs and broker_registry_factory.py."

      - alert: BrokerFutuNormalizeUnknown
        expr: increase(broker_normalize_unknown_total{label="futu"}[15m]) > 5
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Futu sidecar saw >5 unknown enum values in 15m on {{ $labels.field }}"
          description: "futu-api emitted enum values normalize.py doesn't know about. Likely a futu-api SDK upgrade introduced a new SecurityType / OrderStatus / TrdEnv. Check sidecar logs for the raw value."
```

- [ ] **Step 2: Add tests**

```python
def test_broker_label_mismatch_alert_present() -> None:
    alert = _phase5b_rule("BrokerLabelMismatch")
    assert alert.get("for") == "1m"
    assert alert.get("labels", {}).get("severity") == "page"
    assert "broker_registry_label_mismatch_total" in alert["expr"]


def test_broker_futu_normalize_unknown_alert_present() -> None:
    alert = _phase5b_rule("BrokerFutuNormalizeUnknown")
    assert alert.get("for") == "5m"
    assert alert.get("labels", {}).get("severity") == "warning"
    assert 'label="futu"' in alert["expr"]
    assert "[15m]" in alert["expr"]
```

- [ ] **Step 3: Run tests**

```bash
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs); uv run pytest tests/observability/test_metrics_orders.py -v
```

Expected: both new tests PASS.

- [ ] **Step 4: Commit**

```bash
git add deploy/prometheus/alerts.yml backend/tests/observability/test_metrics_orders.py
git commit -m "feat(ops): brokerlabelmismatch + brokerfutunormalizeunknown alerts"
```

### Task A7 — Operator runbook for FutuOpenD setup + `app_secrets` seed

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality

**Files:**
- Create: `deploy/nuc/runbook-futu-setup.md`

- [ ] **Step 1: Write the runbook**

The runbook covers: (1) Install FutuOpenD on NUC, (2) Generate 1024-bit RSA keypair, (3) Configure OpenD with public key, (4) Compute MD5 of trading password, (5) Seed `app_secrets`/`app_config` via `/api/admin` endpoints, (6) Wipe local plaintext, (7) Trigger `Configure` via reconfigure admin endpoint, (8) Defender exclusion glob extension. See spec §11.6 for context.

```markdown
# FutuOpenD Sidecar Setup Runbook (Phase 6, v0.6.0)

One-time operator setup. ~30 minutes end-to-end.

## 1. Install FutuOpenD on the NUC

Download `FutuOpenD-Windows.zip` from `https://www.futunn.com/en-US/download/openAPI`.
Extract to `C:\FutuOpenD\`. Run `FutuOpenD.exe`.

Configure via web UI: login with Futu account, OpenD listen port = `11111`,
"Allow API connection" = ON, set Trading password (your Futu trading PIN).

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 11111
```
Expected: `TcpTestSucceeded: True`.

## 2. Generate 1024-bit RSA keypair

**CRITICAL:** Futu requires 1024-bit (per memory `futu_1024_rsa_key.md`); 2048-bit fails InitConnect.

```powershell
cd C:\dashboard\secrets\
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:1024 -out futu-priv-tmp.pem
openssl pkcs8 -topk8 -nocrypt -in futu-priv-tmp.pem -out futu-priv.pem
openssl rsa -in futu-priv.pem -pubout -out futu-pub.pem
Remove-Item futu-priv-tmp.pem
```

## 3. Configure OpenD with the public key

In FutuOpenD web UI: Settings → API → "RSA Public Key" → paste contents of `futu-pub.pem`.
Click Save. Note your "Connection ID" (e.g. `default_conn`).

## 4. Compute MD5 of trading password

```powershell
$pwd = Read-Host -AsSecureString "Trading password"
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pwd))
$md5 = [System.BitConverter]::ToString([Security.Cryptography.MD5]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($plain))).Replace("-","").ToLower()
Write-Host $md5
```

## 5. Seed app_secrets + app_config

From WSL with `CF_ACCESS_CLIENT_ID/SECRET` set:

```bash
RSA_PEM=$(cat /mnt/c/dashboard/secrets/futu-priv.pem)
MD5=<32-char hex from step 4>
CONN_ID=<from step 3>

# Encrypted secrets:
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/secrets \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg pem "$RSA_PEM" '{namespace:"broker", key:"futu.rsa_priv_pem", value:$pem, value_type:"string"}')"

curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/secrets \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"namespace\":\"broker\",\"key\":\"futu.unlock_pwd_md5\",\"value\":\"$MD5\",\"value_type\":\"string\"}"

# Plain config:
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"broker","key":"futu.opend_host","value":"127.0.0.1","value_type":"string"}'

curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"namespace":"broker","key":"futu.opend_port","value":"11111","value_type":"int"}'

curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/config \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"namespace\":\"broker\",\"key\":\"futu.connection_id\",\"value\":\"$CONN_ID\",\"value_type\":\"string\"}"
```

## 6. Wipe local plaintext

```powershell
Remove-Item C:\dashboard\secrets\futu-pub.pem
Clear-History
```

## 7. Trigger Configure after sidecar deploy (Chunk G)

```bash
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/brokers/futu/reconfigure \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```

Expected: `{"ok": true, "detail": ""}`.

## 8. Windows Defender exclusion (one-time)

```powershell
Add-MpPreference -ExclusionPath "C:\dashboard\dist-staging-*"
```

Otherwise the kanji-rich PyInstaller payload triggers a Defender scan on every restart.
```

- [ ] **Step 2: Commit**

```bash
git add deploy/nuc/runbook-futu-setup.md
git commit -m "docs(phase6): operator runbook for futu sidecar setup"
```

---

## Chunk B — Sidecar core (~1.75 days)

### Task B1 — `sidecar-futu/` package skeleton + proto codegen

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Create: `sidecar-futu/pyproject.toml`, `sidecar-futu/uv.lock`, `sidecar-futu/__init__.py`, `sidecar-futu/futu_sidecar.py` (stub), `sidecar-futu/scripts/proto-gen.sh`, `sidecar-futu/tests/__init__.py`

- [ ] **Step 1: Create the package structure**

```bash
mkdir -p sidecar-futu/scripts sidecar-futu/tests sidecar-futu/_generated
touch sidecar-futu/__init__.py sidecar-futu/tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "futu-sidecar"
version = "0.6.0"
requires-python = ">=3.14"
dependencies = [
    "futu-api>=9.3.5308",
    "grpcio>=1.69.0",
    "protobuf>=5.28.3",
    "structlog>=24.4.0",
    "cryptography>=44.0.0",
    "googleapis-common-protos>=1.66.0",
    "prometheus-client>=0.21.0",
]

[dependency-groups]
dev = [
    "pytest>=9.0.0",
    "pytest-asyncio>=1.3.0",
    "ruff>=0.7.4",
    "mypy>=1.13.0",
    "grpcio-tools>=1.69.0",
    "types-protobuf>=5.28.0",
    "pyinstaller>=6.11.1",
]

[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "ASYNC", "RUF"]

[tool.mypy]
strict = true
python_version = "3.14"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Lock deps**

```bash
cd sidecar-futu && uv lock
```

- [ ] **Step 4: Write `proto-gen.sh`**

```bash
cat > sidecar-futu/scripts/proto-gen.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m grpc_tools.protoc \
  -I=../proto \
  --python_out=_generated \
  --grpc_python_out=_generated \
  --pyi_out=_generated \
  ../proto/broker/v1/broker.proto
find _generated -name '*.py' -exec sed -i 's|^from broker.v1|from sidecar_futu._generated.broker.v1|g' {} \;
echo "Generated stubs into sidecar-futu/_generated/"
EOF
chmod +x sidecar-futu/scripts/proto-gen.sh
```

- [ ] **Step 5: Stub `futu_sidecar.py`**

```python
"""futu-sidecar entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import signal
from datetime import UTC, datetime
from pathlib import Path

import structlog
from grpc.aio import server as grpc_server

from sidecar_futu._generated.broker.v1 import broker_pb2_grpc

log = structlog.get_logger(__name__)
BIND_ADDRESS = "10.10.0.2:18005"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--cert-dir", default=r"C:\dashboard\secrets")
    p.add_argument(
        "--simulator",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="SIM mode (default ON for safety; --no-simulator for real placement)",
    )
    return p.parse_args()


async def _serve(args: argparse.Namespace) -> None:
    started_at = datetime.now(UTC)
    server = grpc_server()
    server.add_insecure_port(BIND_ADDRESS)  # replaced with mTLS in B6
    log.info("futu_sidecar_start", bind=BIND_ADDRESS, simulator=args.simulator)
    await server.start()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_event_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.stop(grace=5)


def main() -> None:
    args = _parse_args()
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run codegen**

```bash
bash sidecar-futu/scripts/proto-gen.sh
```

- [ ] **Step 7: Smoke import**

```bash
cd sidecar-futu && uv run python -c "from sidecar_futu._generated.broker.v1 import broker_pb2; print(broker_pb2.AssetClass.CBBC)"
```

Expected: `10`.

- [ ] **Step 8: Commit**

```bash
git add sidecar-futu/
git commit -m "feat(sidecar-futu): scaffold package + proto codegen"
```

### Task B2 — `Health` handler with `broker_id="futu"` + `started_at`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Create: `sidecar-futu/handlers.py`
- Create: `sidecar-futu/tests/test_handlers_health.py`

- [ ] **Step 1: Write the failing test**

```python
# sidecar-futu/tests/test_handlers_health.py
import pytest
from datetime import UTC, datetime, timedelta

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_health_returns_broker_id_and_started_at():
    started = datetime.now(UTC)
    handlers = BrokerHandlers(started_at=started)
    request = broker_pb2.HealthRequest()
    response = await handlers.Health(request, context=None)

    assert response.broker_id == "futu"
    assert response.gateway_connected is False  # Configure not called yet
    response_dt = response.started_at.ToDatetime(tzinfo=UTC)
    assert abs(response_dt - started) < timedelta(seconds=1)
```

- [ ] **Step 2: Run test, expect fail**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_health.py -v
```

Expected: FAIL — `BrokerHandlers` not defined.

- [ ] **Step 3: Write `handlers.py`**

```python
"""gRPC Broker service handlers for the Futu sidecar."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import grpc
import structlog
from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu._generated.broker.v1 import broker_pb2, broker_pb2_grpc

log = structlog.get_logger(__name__)


class BrokerHandlers(broker_pb2_grpc.BrokerServicer):
    """Implements proto Broker service for Futu."""

    def __init__(self, *, started_at: datetime, simulator: bool = True) -> None:
        self._started_at = started_at
        self._sim_mode = simulator
        # FutuClient + sim queue dict are wired in B3 / C7.

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: Any,
    ) -> broker_pb2.HealthResponse:
        ts = Timestamp()
        ts.FromDatetime(self._started_at)
        return broker_pb2.HealthResponse(
            label="futu",
            gateway_connected=getattr(self, "_client", None) is not None
                and self._client.gateway_connected,
            gateway_version="",
            sidecar_version="0.6.0",
            started_at=ts,
            broker_id="futu",
        )
```

- [ ] **Step 4: Run test, expect pass**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_health.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sidecar-futu/handlers.py sidecar-futu/tests/test_handlers_health.py
git commit -m "feat(sidecar-futu): health handler with broker_id + started_at"
```

### Task B3 — `Configure` RPC: validation-only, ok-gated, in-flight cancellation (H1+H3+M3)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + security-reviewer + silent-failure-hunter

**Files:**
- Create: `sidecar-futu/futu_client.py`
- Modify: `sidecar-futu/handlers.py`
- Create: `sidecar-futu/tests/test_handlers_configure.py`

- [ ] **Step 1: Write tests for valid + invalid + cancellation paths**

```python
# sidecar-futu/tests/test_handlers_configure.py
import pytest
from datetime import UTC, datetime
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _make_rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.mark.asyncio
async def test_configure_accepts_valid_creds():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    response = await handlers.Configure(broker_pb2.ConfigureRequest(
        unlock_pwd_md5="0123456789abcdef0123456789abcdef",
        rsa_priv_pem=_make_rsa_pem(),
        opend_host="127.0.0.1", opend_port=11111, connection_id="x",
    ), context=None)
    assert response.ok is True
    assert response.detail == ""


@pytest.mark.asyncio
async def test_configure_rejects_invalid_md5():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    response = await handlers.Configure(broker_pb2.ConfigureRequest(
        unlock_pwd_md5="not-md5",
        rsa_priv_pem=_make_rsa_pem(),
        opend_host="x", opend_port=11111, connection_id="x",
    ), context=None)
    assert response.ok is False
    assert response.detail == "invalid_unlock_pwd_md5"


@pytest.mark.asyncio
async def test_configure_rejects_invalid_pem():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    response = await handlers.Configure(broker_pb2.ConfigureRequest(
        unlock_pwd_md5="0123456789abcdef0123456789abcdef",
        rsa_priv_pem="not-a-pem",
        opend_host="x", opend_port=11111, connection_id="x",
    ), context=None)
    assert response.ok is False
    assert response.detail == "invalid_rsa_pem"


@pytest.mark.asyncio
async def test_configure_cancels_previous_init_task():
    """H3 — Configure-during-reconnect race."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    valid_pem = _make_rsa_pem()
    req = broker_pb2.ConfigureRequest(
        unlock_pwd_md5="0123456789abcdef0123456789abcdef",
        rsa_priv_pem=valid_pem, opend_host="x", opend_port=11111, connection_id="x",
    )
    await handlers.Configure(req, context=None)
    first_task = handlers._client._init_task
    assert first_task is not None
    await handlers.Configure(req, context=None)
    second_task = handlers._client._init_task
    assert second_task is not first_task
    assert first_task.cancelled() or first_task.done()
```

- [ ] **Step 2: Run tests, expect fail**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_configure.py -v
```

- [ ] **Step 3: Write `futu_client.py`**

```python
"""Owns OpenSecTradeContext lifecycle, cred caching, in-flight init cancellation."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import structlog
from cryptography.hazmat.primitives.serialization import load_pem_private_key

log = structlog.get_logger(__name__)

_MD5_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass(frozen=True)
class FutuCreds:
    unlock_pwd_md5: str
    rsa_priv_pem: str
    opend_host: str
    opend_port: int
    connection_id: str


class FutuClient:
    """Holds creds + OpenD connection. Configure is validation-only;
    InitConnect runs in self._init_task. Configure-while-in-flight
    cancels the prior _init_task (H3)."""

    def __init__(self) -> None:
        self._creds: FutuCreds | None = None
        self._init_task: asyncio.Task[None] | None = None
        self._trade_ctx: Any | None = None
        self.gateway_connected: bool = False
        self._order_event_queues: dict[str, list[asyncio.Queue[Any]]] = {}

    def validate(self, request: Any) -> str | None:
        """Return error detail string on rejection, None on success (M3)."""
        if not _MD5_PATTERN.match(request.unlock_pwd_md5):
            return "invalid_unlock_pwd_md5"
        try:
            load_pem_private_key(request.rsa_priv_pem.encode(), password=None)
        except Exception:  # noqa: BLE001
            return "invalid_rsa_pem"
        return None

    async def configure(self, request: Any) -> None:
        """Cache creds + restart InitConnect background task. Caller has
        already validated via .validate()."""
        # H3: cancel prior in-flight task before swapping creds.
        if self._init_task is not None and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.warning("futu_init_task_cleanup_error", error=str(exc))

        self._creds = FutuCreds(
            unlock_pwd_md5=request.unlock_pwd_md5,
            rsa_priv_pem=request.rsa_priv_pem,
            opend_host=request.opend_host,
            opend_port=request.opend_port,
            connection_id=request.connection_id,
        )
        self.gateway_connected = False
        self._init_task = asyncio.create_task(
            self._init_loop(),
            name="futu-init-connect",
        )

    async def _init_loop(self) -> None:
        """Stub: B4 replaces with real InitConnect loop. For Configure tests,
        the task simply exists in a runnable state."""
        log.info("futu_init_loop_stub", host=self._creds.opend_host if self._creds else None)
        # Tests expect the task to be created; real impl in B4.
        await asyncio.sleep(60)  # let cancellation tests verify cancel behavior
```

- [ ] **Step 4: Wire `Configure` in `handlers.py`**

```python
# Add at top:
from sidecar_futu.futu_client import FutuClient

# In BrokerHandlers.__init__:
def __init__(self, *, started_at: datetime, simulator: bool = True) -> None:
    self._started_at = started_at
    self._sim_mode = simulator
    self._client = FutuClient()

# Add method:
async def Configure(  # noqa: N802
    self,
    request: broker_pb2.ConfigureRequest,
    context: Any,
) -> broker_pb2.ConfigureResponse:
    detail = self._client.validate(request)
    if detail is not None:
        log.warning("configure_rejected", detail=detail)
        return broker_pb2.ConfigureResponse(ok=False, detail=detail)
    await self._client.configure(request)
    log.info("configure_accepted")
    return broker_pb2.ConfigureResponse(ok=True, detail="")
```

- [ ] **Step 5: Run tests, expect pass**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_configure.py -v
```

- [ ] **Step 6: Commit**

```bash
git add sidecar-futu/futu_client.py sidecar-futu/handlers.py sidecar-futu/tests/test_handlers_configure.py
git commit -m "feat(sidecar-futu): configure rpc - validate + cancel-and-respawn (h1 h3 m3)"
```

### Task B4 — `_init_loop`: real OpenSecTradeContext + unlock_trade + backoff

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter

**Files:**
- Modify: `sidecar-futu/futu_client.py`
- Create: `sidecar-futu/tests/test_futu_client.py`

- [ ] **Step 1: Write tests with mocked OpenSecTradeContext**

```python
import asyncio
from unittest.mock import MagicMock
import pytest

from sidecar_futu.futu_client import FutuClient, FutuCreds


@pytest.mark.asyncio
async def test_init_attempt_marks_connected_on_success(monkeypatch):
    client = FutuClient()
    client._creds = FutuCreds(
        unlock_pwd_md5="0" * 32, rsa_priv_pem="-",
        opend_host="x", opend_port=11111, connection_id="x",
    )

    fake_ctx = MagicMock()
    fake_ctx.unlock_trade.return_value = (0, "OK")
    monkeypatch.setattr(
        "sidecar_futu.futu_client.OpenSecTradeContext",
        lambda **kwargs: fake_ctx,
    )

    await client._init_attempt()
    assert client.gateway_connected is True
    fake_ctx.unlock_trade.assert_called_once()


@pytest.mark.asyncio
async def test_init_loop_retries_on_failure(monkeypatch):
    client = FutuClient()
    client._creds = FutuCreds(
        unlock_pwd_md5="0" * 32, rsa_priv_pem="-",
        opend_host="x", opend_port=11111, connection_id="x",
    )

    call_count = 0

    def fake_factory(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("OpenD down")
        ctx = MagicMock()
        ctx.unlock_trade.return_value = (0, "OK")
        return ctx

    monkeypatch.setattr("sidecar_futu.futu_client.OpenSecTradeContext", fake_factory)
    monkeypatch.setattr("sidecar_futu.futu_client._BACKOFF_BASE_S", 0.01)

    task = asyncio.create_task(client._init_loop())
    while not client.gateway_connected:
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert client.gateway_connected is True
    assert call_count == 3
```

- [ ] **Step 2: Implement `_init_attempt` + replace `_init_loop` stub**

```python
# Add imports near top:
from futu import OpenSecTradeContext, TrdMarket, SecurityFirm, RET_OK

_BACKOFF_BASE_S = 1.0
_BACKOFF_MAX_S = 30.0


# In FutuClient: replace the stub _init_loop with these two methods:
async def _init_attempt(self) -> None:
    """One InitConnect → unlock_trade attempt. Raises on failure."""
    assert self._creds is not None

    def _connect() -> Any:
        ctx = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.HK,
            host=self._creds.opend_host,
            port=self._creds.opend_port,
            security_firm=SecurityFirm.FUTUSECURITIES,
        )
        ret, msg = ctx.unlock_trade(unlock_password_md5=self._creds.unlock_pwd_md5)
        if ret != RET_OK:
            ctx.close()
            raise RuntimeError(f"unlock_trade failed: {msg}")
        return ctx

    ctx = await asyncio.to_thread(_connect)
    self._trade_ctx = ctx
    self.gateway_connected = True
    log.info("futu_init_connected", host=self._creds.opend_host)


async def _init_loop(self) -> None:
    """Reconnect with exponential backoff (1s/2s/.../30s capped)."""
    backoff = _BACKOFF_BASE_S
    while True:
        try:
            await self._init_attempt()
            # Stay alive — caller may keep this task running for reconnect handling
            await asyncio.Event().wait()  # block until cancelled
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "futu_init_failed",
                error=str(exc), error_type=type(exc).__name__,
                backoff_seconds=backoff,
            )
            self.gateway_connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_S)
```

- [ ] **Step 3: Run tests**

```bash
cd sidecar-futu && uv run pytest tests/test_futu_client.py tests/test_handlers_configure.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add sidecar-futu/futu_client.py sidecar-futu/tests/test_futu_client.py
git commit -m "feat(sidecar-futu): init_loop with futu opensectrade + backoff"
```

### Task B5 — `ListManagedAccounts` handler + `account_from_futu_row` normalize + metrics

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Create: `sidecar-futu/normalize.py`
- Create: `sidecar-futu/metrics.py`
- Modify: `sidecar-futu/handlers.py`
- Modify: `sidecar-futu/futu_client.py`
- Create: `sidecar-futu/tests/test_normalize.py`
- Create: `sidecar-futu/tests/test_handlers_list_accounts.py`

- [ ] **Step 1: Write `metrics.py`**

```python
"""Sidecar-local Prometheus counters."""
from prometheus_client import CollectorRegistry, Counter

registry = CollectorRegistry()

broker_normalize_unknown_total = Counter(
    "broker_normalize_unknown_total",
    "Sidecar normalize layer received an unknown enum value from broker SDK.",
    labelnames=["label", "field"],
    registry=registry,
)
```

- [ ] **Step 2: Write `normalize.py` with account mapping + L3 unknown-trd_env handling**

```python
"""Type mapping between futu-api responses and proto messages."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu._generated.broker.v1 import broker_pb2

_HK_TZ = ZoneInfo("Asia/Hong_Kong")


class AccountSkipReason(str, Enum):
    UNKNOWN_TRD_ENV = "unknown_trd_env"


_TRD_ENV_TO_MODE = {
    "REAL": broker_pb2.TradingMode.LIVE,
    "SIMULATE": broker_pb2.TradingMode.PAPER,
}


def account_from_futu_row(row: dict[str, Any]) -> tuple[broker_pb2.Account | None, AccountSkipReason | None]:
    """Map one futu acc_list row to proto Account."""
    trd_env = row.get("trd_env", "")
    mode = _TRD_ENV_TO_MODE.get(trd_env)
    if mode is None:
        return None, AccountSkipReason.UNKNOWN_TRD_ENV
    return (
        broker_pb2.Account(
            account_number=str(row["acc_id"]),
            mode=mode,
            gateway_label="futu",
            currency_base="",
        ),
        None,
    )


def hk_local_to_utc_timestamp(s: str) -> Timestamp:
    """Parse HK-local '2026-04-29 14:30:00' → UTC google.protobuf.Timestamp."""
    naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    hk = naive.replace(tzinfo=_HK_TZ)
    utc_dt = hk.astimezone(UTC)
    ts = Timestamp()
    ts.FromDatetime(utc_dt)
    return ts


def _money(value: str | int | float, currency: str) -> broker_pb2.Money:
    d = Decimal(str(value)).quantize(Decimal("1e-8"))
    return broker_pb2.Money(value=format(d, "f"), currency=currency)
```

- [ ] **Step 3: Write tests**

```python
# sidecar-futu/tests/test_normalize.py
import pytest
from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.normalize import account_from_futu_row, AccountSkipReason


def test_account_real_trd_env_maps_to_live():
    acc, skip = account_from_futu_row({"acc_id": 12345678, "trd_env": "REAL", "acc_type": "MARGIN"})
    assert skip is None
    assert acc.account_number == "12345678"
    assert acc.mode == broker_pb2.TradingMode.LIVE
    assert acc.gateway_label == "futu"


def test_account_simulate_trd_env_maps_to_paper():
    acc, skip = account_from_futu_row({"acc_id": 99999999, "trd_env": "SIMULATE", "acc_type": "CASH"})
    assert skip is None
    assert acc.mode == broker_pb2.TradingMode.PAPER


def test_account_unknown_trd_env_skipped():
    acc, skip = account_from_futu_row({"acc_id": 1, "trd_env": "PAPER_PROD", "acc_type": "CASH"})
    assert skip == AccountSkipReason.UNKNOWN_TRD_ENV
    assert acc is None
```

- [ ] **Step 4: Add `list_accounts` to `FutuClient`**

```python
async def list_accounts(self) -> list[dict[str, Any]]:
    if not self.gateway_connected or self._trade_ctx is None:
        return []

    def _query() -> list[dict[str, Any]]:
        from futu import RET_OK
        ret, data = self._trade_ctx.get_acc_list()
        if ret != RET_OK:
            log.warning("futu_get_acc_list_failed", msg=str(data))
            return []
        return data.to_dict("records")

    return await asyncio.to_thread(_query)
```

- [ ] **Step 5: Add `ListManagedAccounts` handler**

```python
# handlers.py
from sidecar_futu.normalize import account_from_futu_row, AccountSkipReason
from sidecar_futu import metrics

async def ListManagedAccounts(  # noqa: N802
    self,
    request: broker_pb2.Empty,
    context: Any,
) -> broker_pb2.AccountsResponse:
    rows = await self._client.list_accounts()
    accounts: list[broker_pb2.Account] = []
    for row in rows:
        acc, skip = account_from_futu_row(row)
        if skip is not None:
            metrics.broker_normalize_unknown_total.labels(
                label="futu", field="trd_env"
            ).inc()
            log.warning("futu_normalize_unknown_trd_env", row=row)
            continue
        if acc is not None:
            accounts.append(acc)
    return broker_pb2.AccountsResponse(accounts=accounts)
```

- [ ] **Step 6: Write handler test**

```python
# sidecar-futu/tests/test_handlers_list_accounts.py
import pytest
from unittest.mock import AsyncMock
from datetime import UTC, datetime

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_list_accounts_returns_proto_accounts():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.list_accounts = AsyncMock(return_value=[
        {"acc_id": 11111111, "trd_env": "REAL", "acc_type": "MARGIN"},
        {"acc_id": 22222222, "trd_env": "SIMULATE", "acc_type": "CASH"},
    ])
    response = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)
    assert len(response.accounts) == 2
    assert response.accounts[0].account_number == "11111111"
    assert response.accounts[0].mode == broker_pb2.TradingMode.LIVE
    assert response.accounts[1].mode == broker_pb2.TradingMode.PAPER


@pytest.mark.asyncio
async def test_list_accounts_skips_unknown_trd_env():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.list_accounts = AsyncMock(return_value=[
        {"acc_id": 11111111, "trd_env": "REAL"},
        {"acc_id": 22222222, "trd_env": "GAMMA"},
    ])
    response = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)
    assert len(response.accounts) == 1
    assert response.accounts[0].account_number == "11111111"
```

- [ ] **Step 7: Run all sidecar-futu tests**

```bash
cd sidecar-futu && uv run pytest tests/ -v
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add sidecar-futu/normalize.py sidecar-futu/metrics.py sidecar-futu/handlers.py sidecar-futu/futu_client.py sidecar-futu/tests/test_normalize.py sidecar-futu/tests/test_handlers_list_accounts.py
git commit -m "feat(sidecar-futu): list_managed_accounts + normalize trd_env (l3)"
```

### Task B6 — mTLS server bootstrap + register handlers in `futu_sidecar.py`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + security-reviewer

**Files:**
- Create: `sidecar-futu/tls.py`
- Modify: `sidecar-futu/futu_sidecar.py`

- [ ] **Step 1: Write `tls.py`**

```python
"""mTLS server credentials loader for the Futu sidecar."""
from __future__ import annotations

from pathlib import Path

import grpc


def load_server_credentials(cert_dir: Path) -> grpc.ServerCredentials:
    cert = (cert_dir / "sidecar-cert.pem").read_bytes()
    key = (cert_dir / "sidecar-key.pem").read_bytes()
    ca = (cert_dir / "ca-bundle.pem").read_bytes()
    return grpc.ssl_server_credentials(
        [(key, cert)],
        root_certificates=ca,
        require_client_auth=True,
    )
```

- [ ] **Step 2: Replace `_serve` in `futu_sidecar.py`**

```python
from datetime import UTC, datetime
from pathlib import Path

from sidecar_futu._generated.broker.v1 import broker_pb2_grpc
from sidecar_futu.handlers import BrokerHandlers
from sidecar_futu.tls import load_server_credentials


async def _serve(args: argparse.Namespace) -> None:
    started_at = datetime.now(UTC)
    handlers = BrokerHandlers(started_at=started_at, simulator=args.simulator)

    server = grpc_server()
    broker_pb2_grpc.add_BrokerServicer_to_server(handlers, server)

    creds = load_server_credentials(Path(args.cert_dir))
    server.add_secure_port(BIND_ADDRESS, creds)
    log.info("futu_sidecar_start", bind=BIND_ADDRESS, simulator=args.simulator)
    await server.start()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_event_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.stop(grace=5)
```

- [ ] **Step 3: Smoke import**

```bash
cd sidecar-futu && uv run python -c "from sidecar_futu import futu_sidecar; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add sidecar-futu/tls.py sidecar-futu/futu_sidecar.py
git commit -m "feat(sidecar-futu): mtls server bootstrap + register handlers"
```

### Task B7 — PyInstaller build script

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality

**Files:**
- Create: `sidecar-futu/scripts/build-windows.ps1`

- [ ] **Step 1: Write the script**

```powershell
[CmdletBinding()]
param(
    [string]$Dist = 'dist-staging-futu',
    [string]$Version = '0.6.0'
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

Write-Host "==> Cleaning $Dist"
if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }

Write-Host "==> Locking deps"
uv lock

Write-Host "==> PyInstaller freeze"
uv run pyinstaller `
  --onefile `
  --name "futu-sidecar" `
  --distpath $Dist `
  --workpath "$Dist\build" `
  --specpath "$Dist\spec" `
  --hidden-import futu `
  --hidden-import google.protobuf `
  --hidden-import grpc `
  futu_sidecar.py

Write-Host "==> Build done: $Dist\futu-sidecar.exe v$Version"
Get-Item "$Dist\futu-sidecar.exe" | Select-Object Length, LastWriteTime
```

- [ ] **Step 2: Commit (build itself happens on Windows in Chunk G)**

```bash
git add sidecar-futu/scripts/build-windows.ps1
git commit -m "feat(sidecar-futu): pyinstaller build script"
```

---

## Chunk C — Sidecar read+trade (~3.0 days)

### Task C1 — `GetAccountSummary`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Modify: `sidecar-futu/normalize.py`, `sidecar-futu/futu_client.py`, `sidecar-futu/handlers.py`
- Create: `sidecar-futu/tests/test_handlers_summary.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from unittest.mock import AsyncMock
from datetime import UTC, datetime

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_get_account_summary_returns_proto():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.get_account_summary = AsyncMock(return_value={
        "total_assets": "1000000.00",
        "cash": "500000.00",
        "currency": "HKD",
    })
    response = await handlers.GetAccountSummary(
        broker_pb2.AccountRef(account_number="12345678"), context=None,
    )
    assert response.summary.net_liquidation.value == "1000000.00000000"
    assert response.summary.net_liquidation.currency == "HKD"
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Add `summary_from_futu_row` in `normalize.py`**

```python
def summary_from_futu_row(row: dict, *, account_number: str) -> broker_pb2.Summary:
    currency = row.get("currency", "HKD")
    return broker_pb2.Summary(
        net_liquidation=_money(row.get("total_assets", "0"), currency),
        total_cash=_money(row.get("cash", "0"), currency),
        realized_pnl=_money(row.get("realized_pl", "0"), currency),
        unrealized_pnl=_money(row.get("unrealized_pl", "0"), currency),
        buying_power=_money(row.get("power", "0"), currency),
    )
```

- [ ] **Step 4: Add `get_account_summary` to FutuClient + handler**

```python
# futu_client.py
async def get_account_summary(self, account_number: str) -> dict[str, Any]:
    if not self.gateway_connected or self._trade_ctx is None:
        return {}

    def _query() -> dict[str, Any]:
        from futu import RET_OK, TrdEnv
        ret, data = self._trade_ctx.accinfo_query(
            trd_env=TrdEnv.REAL, acc_id=int(account_number),
        )
        if ret != RET_OK or data.empty:
            return {}
        return data.iloc[0].to_dict()

    return await asyncio.to_thread(_query)


# handlers.py
from sidecar_futu.normalize import summary_from_futu_row

async def GetAccountSummary(  # noqa: N802
    self,
    request: broker_pb2.AccountRef,
    context: Any,
) -> broker_pb2.SummaryResponse:
    row = await self._client.get_account_summary(request.account_number)
    summary = summary_from_futu_row(row, account_number=request.account_number)
    return broker_pb2.SummaryResponse(summary=summary)
```

- [ ] **Step 5: Run + commit**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_summary.py -v
git add sidecar-futu/handlers.py sidecar-futu/normalize.py sidecar-futu/futu_client.py sidecar-futu/tests/test_handlers_summary.py
git commit -m "feat(sidecar-futu): get_account_summary handler"
```

### Task C2 — `GetPositions` + AssetClass mapping (STOCK/ETF/WARRANT/CBBC)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Modify: `sidecar-futu/normalize.py`, `sidecar-futu/futu_client.py`, `sidecar-futu/handlers.py`
- Create: `sidecar-futu/tests/test_handlers_positions.py`

- [ ] **Step 1: Write tests covering all 4 asset classes**

```python
import pytest
from unittest.mock import AsyncMock
from datetime import UTC, datetime

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_get_positions_maps_stock_etf_warrant_cbbc():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.get_positions = AsyncMock(return_value=[
        {"code": "HK.00700", "stock_name": "Tencent", "qty": "100",
         "cost_price": "320.00", "nominal_price": "350.00",
         "market_val": "35000.00", "unrealized_pl": "3000.00",
         "realized_pl": "0", "today_pl": "200",
         "security_type": "STOCK", "currency": "HKD"},
        {"code": "HK.02800", "stock_name": "Tracker Fund", "qty": "500",
         "cost_price": "20.00", "nominal_price": "21.00",
         "market_val": "10500.00", "unrealized_pl": "500.00",
         "realized_pl": "0", "today_pl": "10",
         "security_type": "ETF", "currency": "HKD"},
        {"code": "HK.13234", "stock_name": "Warrant X", "qty": "1000",
         "cost_price": "0.10", "nominal_price": "0.15",
         "market_val": "150.00", "unrealized_pl": "50.00",
         "realized_pl": "0", "today_pl": "5",
         "security_type": "WARRANT", "currency": "HKD"},
        {"code": "HK.62345", "stock_name": "Bull CBBC", "qty": "1000",
         "cost_price": "0.20", "nominal_price": "0.18",
         "market_val": "180.00", "unrealized_pl": "-20.00",
         "realized_pl": "0", "today_pl": "-2",
         "security_type": "BOND", "currency": "HKD"},  # futu reports CBBC under BOND
    ])
    response = await handlers.GetPositions(
        broker_pb2.AccountRef(account_number="12345678"), context=None,
    )
    assert len(response.positions) == 4
    assert response.positions[0].contract.asset_class == broker_pb2.AssetClass.STOCK
    assert response.positions[1].contract.asset_class == broker_pb2.AssetClass.ETF
    assert response.positions[2].contract.asset_class == broker_pb2.AssetClass.WARRANT
    assert response.positions[3].contract.asset_class == broker_pb2.AssetClass.CBBC
    assert response.positions[0].contract.symbol == "HK.00700"
```

- [ ] **Step 2: Add normalize functions**

```python
# normalize.py
_SECURITY_TYPE_TO_ASSET_CLASS = {
    "STOCK": broker_pb2.AssetClass.STOCK,
    "ETF": broker_pb2.AssetClass.ETF,
    "WARRANT": broker_pb2.AssetClass.WARRANT,
    "BOND": broker_pb2.AssetClass.CBBC,  # futu reports CBBC under BOND
    "CBBC": broker_pb2.AssetClass.CBBC,
}


def asset_class_from_security_type(security_type: str) -> int:
    return _SECURITY_TYPE_TO_ASSET_CLASS.get(security_type, broker_pb2.AssetClass.ASSET_UNSPECIFIED)


def contract_from_futu_row(row: dict) -> broker_pb2.Contract:
    return broker_pb2.Contract(
        symbol=row["code"],
        exchange="SEHK",
        currency=row.get("currency", "HKD"),
        asset_class=asset_class_from_security_type(row.get("security_type", "")),
        conid=row["code"],
        local_symbol=row.get("stock_name", ""),
    )


def position_from_futu_row(row: dict) -> broker_pb2.Position:
    currency = row.get("currency", "HKD")
    return broker_pb2.Position(
        contract=contract_from_futu_row(row),
        quantity=str(row.get("qty", "0")),
        avg_cost=_money(row.get("cost_price", "0"), currency),
        market_price=_money(row.get("nominal_price", "0"), currency),
        market_value=_money(row.get("market_val", "0"), currency),
        unrealized_pnl=_money(row.get("unrealized_pl", "0"), currency),
        realized_pnl_today=_money(row.get("realized_pl", "0"), currency),
        daily_pnl=_money(row.get("today_pl", "0"), currency),
    )
```

- [ ] **Step 3: Add `get_positions` to FutuClient + handler**

```python
# futu_client.py
async def get_positions(self, account_number: str) -> list[dict[str, Any]]:
    if not self.gateway_connected or self._trade_ctx is None:
        return []

    def _query() -> list[dict[str, Any]]:
        from futu import RET_OK, TrdEnv
        ret, data = self._trade_ctx.position_list_query(
            trd_env=TrdEnv.REAL, acc_id=int(account_number),
        )
        if ret != RET_OK:
            return []
        return data.to_dict("records")

    return await asyncio.to_thread(_query)


# handlers.py
from sidecar_futu.normalize import position_from_futu_row

async def GetPositions(  # noqa: N802
    self,
    request: broker_pb2.AccountRef,
    context: Any,
) -> broker_pb2.PositionsResponse:
    rows = await self._client.get_positions(request.account_number)
    positions = [position_from_futu_row(row) for row in rows]
    return broker_pb2.PositionsResponse(positions=positions)
```

- [ ] **Step 4: Run + commit**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_positions.py -v
git add sidecar-futu/normalize.py sidecar-futu/futu_client.py sidecar-futu/handlers.py sidecar-futu/tests/test_handlers_positions.py
git commit -m "feat(sidecar-futu): get_positions + asset_class mapping (m1)"
```

### Task C3 — `GetOrders` + status mapping table

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer + silent-failure-hunter

**Files:**
- Modify: `sidecar-futu/normalize.py`, `sidecar-futu/futu_client.py`, `sidecar-futu/handlers.py`
- Create: `sidecar-futu/tests/test_status_mapping.py`

- [ ] **Step 1: Write the table test (per spec §5)**

```python
import pytest
from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.normalize import status_from_futu_status


@pytest.mark.parametrize("futu_status,expected", [
    ("UNSUBMITTED", broker_pb2.OrderStatus.PENDING),
    ("SUBMITTING", broker_pb2.OrderStatus.PENDING),
    ("WAITING_SUBMIT", broker_pb2.OrderStatus.SUBMITTED),
    ("SUBMITTED", broker_pb2.OrderStatus.SUBMITTED),
    ("FILLED_PART", broker_pb2.OrderStatus.PARTIAL),
    ("FILLED_ALL", broker_pb2.OrderStatus.FILLED),
    ("CANCELLED_PART", broker_pb2.OrderStatus.CANCELLED),
    ("CANCELLED_ALL", broker_pb2.OrderStatus.CANCELLED),
    ("FAILED", broker_pb2.OrderStatus.REJECTED),
    ("DISABLED", broker_pb2.OrderStatus.REJECTED),
])
def test_status_mapping(futu_status, expected):
    assert status_from_futu_status(futu_status) == expected


def test_unknown_status_maps_to_unspecified():
    assert status_from_futu_status("MOON_PHASE") == broker_pb2.OrderStatus.STATUS_UNSPECIFIED
```

- [ ] **Step 2: Add status + order/tif/order-type mappings to `normalize.py`**

```python
_FUTU_STATUS_TO_PROTO = {
    "UNSUBMITTED": broker_pb2.OrderStatus.PENDING,
    "SUBMITTING": broker_pb2.OrderStatus.PENDING,
    "WAITING_SUBMIT": broker_pb2.OrderStatus.SUBMITTED,
    "SUBMITTED": broker_pb2.OrderStatus.SUBMITTED,
    "FILLED_PART": broker_pb2.OrderStatus.PARTIAL,
    "FILLED_ALL": broker_pb2.OrderStatus.FILLED,
    "CANCELLED_PART": broker_pb2.OrderStatus.CANCELLED,
    "CANCELLED_ALL": broker_pb2.OrderStatus.CANCELLED,
    "FAILED": broker_pb2.OrderStatus.REJECTED,
    "DISABLED": broker_pb2.OrderStatus.REJECTED,
    "DELETED": broker_pb2.OrderStatus.STATUS_UNSPECIFIED,  # backend translates → expired
}


def status_from_futu_status(s: str) -> int:
    return _FUTU_STATUS_TO_PROTO.get(s, broker_pb2.OrderStatus.STATUS_UNSPECIFIED)


_STATUS_STRING = {
    "UNSUBMITTED": "pending_submit",
    "SUBMITTING": "pending_submit",
    "WAITING_SUBMIT": "submitted",
    "SUBMITTED": "submitted",
    "FILLED_PART": "partial",
    "FILLED_ALL": "filled",
    "CANCELLED_PART": "cancelled",
    "CANCELLED_ALL": "cancelled",
    "FAILED": "rejected",
    "DISABLED": "rejected",
    "DELETED": "expired",
}


def status_string_from_futu(s: str) -> str:
    return _STATUS_STRING.get(s, "")


def order_from_futu_row(row: dict) -> broker_pb2.Order:
    currency = row.get("currency", "HKD")
    return broker_pb2.Order(
        order_id=str(row["order_id"]),
        contract=contract_from_futu_row(row),
        side=broker_pb2.OrderSide.BUY if row.get("trd_side") == "BUY" else broker_pb2.OrderSide.SELL,
        order_type=_order_type_from_futu(row.get("order_type", "")),
        quantity=str(row.get("qty", "0")),
        limit_price=_money(row.get("price", "0"), currency),
        stop_price=_money(row.get("aux_price", "0"), currency),
        time_in_force=_tif_from_futu(row.get("time_in_force", "DAY")),
        status=status_from_futu_status(row.get("order_status", "")),
        quantity_filled=str(row.get("dealt_qty", "0")),
        avg_fill_price=_money(row.get("dealt_avg_price", "0"), currency),
        submitted_at=hk_local_to_utc_timestamp(row["create_time"]) if row.get("create_time") else None,
        updated_at=hk_local_to_utc_timestamp(row["updated_time"]) if row.get("updated_time") else None,
    )


def _order_type_from_futu(t: str) -> int:
    return {
        "NORMAL": broker_pb2.OrderType.LIMIT,
        "MARKET": broker_pb2.OrderType.MARKET,
        "STOP": broker_pb2.OrderType.STOP,
        "STOP_LIMIT": broker_pb2.OrderType.STOP_LIMIT,
    }.get(t, broker_pb2.OrderType.TYPE_UNSPECIFIED)


def _tif_from_futu(t: str) -> int:
    return {"DAY": broker_pb2.TimeInForce.DAY, "GTC": broker_pb2.TimeInForce.GTC}.get(
        t, broker_pb2.TimeInForce.DAY
    )
```

- [ ] **Step 3: Add `get_orders` to FutuClient + `GetOrders` handler**

```python
# futu_client.py
async def get_orders(self, account_number: str) -> list[dict[str, Any]]:
    if not self.gateway_connected or self._trade_ctx is None:
        return []

    def _query() -> list[dict[str, Any]]:
        from futu import RET_OK, TrdEnv
        ret, data = self._trade_ctx.order_list_query(
            trd_env=TrdEnv.REAL, acc_id=int(account_number),
        )
        if ret != RET_OK:
            return []
        return data.to_dict("records")

    return await asyncio.to_thread(_query)


# handlers.py
from sidecar_futu.normalize import order_from_futu_row

async def GetOrders(  # noqa: N802
    self,
    request: broker_pb2.AccountRef,
    context: Any,
) -> broker_pb2.OrdersResponse:
    rows = await self._client.get_orders(request.account_number)
    orders = [order_from_futu_row(row) for row in rows]
    return broker_pb2.OrdersResponse(orders=orders)
```

- [ ] **Step 4: Run + commit**

```bash
cd sidecar-futu && uv run pytest tests/ -v
git add sidecar-futu/normalize.py sidecar-futu/futu_client.py sidecar-futu/handlers.py sidecar-futu/tests/test_status_mapping.py
git commit -m "feat(sidecar-futu): get_orders + futu order status mapping (spec §5)"
```

### Task C4 — `GetContract` + `SearchContracts`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `sidecar-futu/handlers.py`, `sidecar-futu/futu_client.py`
- Create: `sidecar-futu/tests/test_handlers_contracts.py`

- [ ] **Step 1: Write tests asserting native HK.XXXXX preserved + AssetClass tag**

```python
@pytest.mark.asyncio
async def test_search_contracts_returns_hk_format():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.search_contracts = AsyncMock(return_value=[
        {"code": "HK.00700", "stock_name": "Tencent", "security_type": "STOCK", "currency": "HKD"},
    ])
    response = await handlers.SearchContracts(
        broker_pb2.SearchContractsRequest(query="Tencent"), context=None,
    )
    assert len(response.contracts) == 1
    assert response.contracts[0].symbol == "HK.00700"
    assert response.contracts[0].asset_class == broker_pb2.AssetClass.STOCK
```

- [ ] **Step 2: Implement** (futu_client uses OpenQuoteContext.get_stock_basicinfo or get_market_snapshot for search; sidecar opens an OpenQuoteContext separately from the trade context — see futu-api docs section "Quote API")

```python
# futu_client.py
async def search_contracts(self, query: str, asset_class: str = "") -> list[dict[str, Any]]:
    if not self.gateway_connected:
        return []

    def _query() -> list[dict[str, Any]]:
        from futu import OpenQuoteContext, RET_OK, Market, SecurityType
        # Open a transient quote context (closing immediately to avoid leaks)
        qc = OpenQuoteContext(host=self._creds.opend_host, port=self._creds.opend_port)
        try:
            ret, data = qc.get_stock_basicinfo(market=Market.HK, code_list=[query] if query.startswith("HK.") else None)
            if ret != RET_OK:
                return []
            df = data
            if not query.startswith("HK."):
                # text search fallback — match stock_name/code substring
                mask = df["stock_name"].str.contains(query, case=False, na=False) | df["code"].str.contains(query, case=False, na=False)
                df = df[mask]
            return df.head(20).to_dict("records")
        finally:
            qc.close()

    return await asyncio.to_thread(_query)


# handlers.py
async def SearchContracts(  # noqa: N802
    self,
    request: broker_pb2.SearchContractsRequest,
    context: Any,
) -> broker_pb2.SearchContractsResponse:
    rows = await self._client.search_contracts(request.query, request.asset_class)
    contracts = [contract_from_futu_row(row) for row in rows]
    return broker_pb2.SearchContractsResponse(contracts=contracts)


async def GetContract(  # noqa: N802
    self,
    request: broker_pb2.ContractRef,
    context: Any,
) -> broker_pb2.ContractResponse:
    rows = await self._client.search_contracts(request.conid)
    if not rows:
        await context.abort(grpc.StatusCode.NOT_FOUND, f"contract {request.conid} not found")
    return broker_pb2.ContractResponse(contract=contract_from_futu_row(rows[0]))
```

- [ ] **Step 3: Run + commit**

```bash
git add sidecar-futu/handlers.py sidecar-futu/futu_client.py sidecar-futu/tests/test_handlers_contracts.py
git commit -m "feat(sidecar-futu): get_contract + search_contracts handlers"
```

### Task C5 — `PlaceOrder`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer + silent-failure-hunter + security-reviewer

**Files:**
- Modify: `sidecar-futu/handlers.py`, `sidecar-futu/futu_client.py`
- Create: `sidecar-futu/tests/test_handlers_place.py`

- [ ] **Step 1: Write the failing test (mocks `_trade_ctx.place_order`)**

```python
import pandas as pd
import pytest
from unittest.mock import MagicMock
from datetime import UTC, datetime

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_place_order_returns_broker_order_id():
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client._trade_ctx = MagicMock()
    handlers._client._trade_ctx.place_order.return_value = (
        0,  # RET_OK
        pd.DataFrame([{"order_id": 999111}]),
    )

    response = await handlers.PlaceOrder(broker_pb2.PlaceOrderRequest(
        account_number="12345678", client_order_id="018f9c00-0000-7000-8000-000000000000",
        contract=broker_pb2.Contract(symbol="HK.00700", currency="HKD",
                                     asset_class=broker_pb2.AssetClass.STOCK),
        side=broker_pb2.OrderSide.BUY,
        order_type=broker_pb2.OrderType.LIMIT,
        quantity="100",
        limit_price=broker_pb2.Money(value="350.00", currency="HKD"),
        time_in_force=broker_pb2.TimeInForce.DAY,
    ), context=MagicMock())
    assert response.broker_order_id == "999111"
    assert response.status == "submitted"
```

- [ ] **Step 2: Implement**

```python
# futu_client.py
async def place_order(self, request: Any) -> tuple[str, str]:
    """Returns (broker_order_id, status). Raises on RET_ERROR."""
    if self._trade_ctx is None:
        raise RuntimeError("trade context not connected")

    def _place() -> tuple[str, str]:
        from futu import RET_OK, OrderType, TrdSide
        ret, data = self._trade_ctx.place_order(
            price=float(request.limit_price.value),
            qty=int(request.quantity),
            code=request.contract.symbol,
            trd_side=TrdSide.BUY if request.side == broker_pb2.OrderSide.BUY else TrdSide.SELL,
            order_type=OrderType.NORMAL if request.order_type == broker_pb2.OrderType.LIMIT else OrderType.MARKET,
            acc_id=int(request.account_number),
            remark=request.client_order_id[:64],  # futu remark max 64 chars
        )
        if ret != RET_OK:
            raise RuntimeError(f"place_order_failed: {data}")
        order_id = str(data.iloc[0]["order_id"])
        return order_id, "submitted"

    return await asyncio.to_thread(_place)


# handlers.py
async def PlaceOrder(  # noqa: N802
    self,
    request: broker_pb2.PlaceOrderRequest,
    context: Any,
) -> broker_pb2.PlaceOrderResponse:
    if self._sim_mode:
        return await self._sim_place(request)  # implemented in C7
    if not self._client.gateway_connected:
        await context.abort(grpc.StatusCode.UNAVAILABLE, "gateway not connected")
    try:
        broker_order_id, status = await self._client.place_order(request)
    except Exception as exc:  # noqa: BLE001
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
    return broker_pb2.PlaceOrderResponse(broker_order_id=broker_order_id, status=status)
```

- [ ] **Step 3: Run + commit**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_place.py -v
git add sidecar-futu/handlers.py sidecar-futu/futu_client.py sidecar-futu/tests/test_handlers_place.py
git commit -m "feat(sidecar-futu): place_order handler (real branch)"
```

### Task C6 — `CancelOrder`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter

**Files:**
- Modify: `sidecar-futu/handlers.py`, `sidecar-futu/futu_client.py`
- Create: `sidecar-futu/tests/test_handlers_cancel.py`

- [ ] **Step 1: Write test + implement**

```python
# futu_client.py
async def cancel_order(self, account_number: str, broker_order_id: str) -> bool:
    if self._trade_ctx is None:
        return False

    def _cancel() -> bool:
        from futu import RET_OK, ModifyOrderOp
        ret, _ = self._trade_ctx.modify_order(
            ModifyOrderOp.CANCEL,
            order_id=int(broker_order_id),
            qty=0, price=0,
            acc_id=int(account_number),
        )
        return ret == RET_OK

    return await asyncio.to_thread(_cancel)


# handlers.py
async def CancelOrder(  # noqa: N802
    self,
    request: broker_pb2.CancelOrderRequest,
    context: Any,
) -> broker_pb2.CancelOrderResponse:
    if self._sim_mode:
        return await self._sim_cancel(request)  # implemented in C7
    accepted = await self._client.cancel_order(
        request.account_number, request.broker_order_id,
    )
    return broker_pb2.CancelOrderResponse(accepted=accepted)
```

- [ ] **Step 2: Commit**

```bash
git add sidecar-futu/handlers.py sidecar-futu/futu_client.py sidecar-futu/tests/test_handlers_cancel.py
git commit -m "feat(sidecar-futu): cancel_order handler"
```

### Task C7 — SIM mode (`--simulator` default ON) + per-account event queues

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter + security-reviewer

**Files:**
- Create: `sidecar-futu/sim.py`
- Modify: `sidecar-futu/handlers.py`
- Create: `sidecar-futu/tests/test_sim.py`

- [ ] **Step 1: Write tests for SIM place + cancel + queue dispatch**

```python
import asyncio
import pytest
from datetime import UTC, datetime

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _place_req(client_order_id: str = "cid-abc") -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="12345678", client_order_id=client_order_id,
        contract=broker_pb2.Contract(symbol="HK.00700"),
        side=broker_pb2.OrderSide.BUY, order_type=broker_pb2.OrderType.LIMIT,
        quantity="100", limit_price=broker_pb2.Money(value="350.00", currency="HKD"),
    )


@pytest.mark.asyncio
async def test_sim_place_returns_sim_prefix_and_dispatches():
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    queue: asyncio.Queue = asyncio.Queue()
    handlers._client._order_event_queues["12345678"] = [queue]

    resp = await handlers.PlaceOrder(_place_req(), context=None)
    assert resp.broker_order_id.startswith("SIM-")
    assert resp.status == "submitted"

    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.broker_order_id == resp.broker_order_id
    assert event.status == "submitted"


@pytest.mark.asyncio
async def test_sim_cancel_emits_synthetic_event():
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    queue: asyncio.Queue = asyncio.Queue()
    handlers._client._order_event_queues["12345678"] = [queue]

    place_resp = await handlers.PlaceOrder(_place_req(), context=None)
    sim_id = place_resp.broker_order_id
    _ = await queue.get()

    cancel_resp = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(account_number="12345678", broker_order_id=sim_id),
        context=None,
    )
    assert cancel_resp.accepted is True

    cancel_event = await asyncio.wait_for(queue.get(), timeout=1)
    assert cancel_event.broker_order_id == sim_id
    assert cancel_event.status == "cancelled"
```

- [ ] **Step 2: Write `sim.py`**

```python
"""SIM dispatch — mirrors v0.5.5 IBKR sidecar SIM pattern."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu._generated.broker.v1 import broker_pb2


def make_sim_id() -> str:
    return f"SIM-{uuid.uuid4()}"


def synthetic_place_event(*, broker_order_id: str, client_order_id: str) -> broker_pb2.OrderEventMessage:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return broker_pb2.OrderEventMessage(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        status="submitted",
        broker_event_at=ts,
        kind="status",
    )


def synthetic_cancel_event(*, broker_order_id: str, client_order_id: str) -> broker_pb2.OrderEventMessage:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return broker_pb2.OrderEventMessage(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        status="cancelled",
        broker_event_at=ts,
        kind="status",
    )


def dispatch(queues: list[asyncio.Queue[broker_pb2.OrderEventMessage]],
             event: broker_pb2.OrderEventMessage) -> None:
    """put_nowait — drops if queue full."""
    for q in queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
```

- [ ] **Step 3: Wire SIM in handlers.py**

```python
# Add to BrokerHandlers.__init__:
self._sim_orders: dict[str, dict[str, str]] = {}

# Add helper methods + adjust PlaceOrder/CancelOrder to call them in sim_mode:
from sidecar_futu import sim


async def _sim_place(self, request: broker_pb2.PlaceOrderRequest) -> broker_pb2.PlaceOrderResponse:
    sim_id = sim.make_sim_id()
    self._sim_orders[sim_id] = {
        "client_order_id": request.client_order_id,
        "account_number": request.account_number,
    }
    queues = self._client._order_event_queues.get(request.account_number, [])
    sim.dispatch(queues, sim.synthetic_place_event(
        broker_order_id=sim_id, client_order_id=request.client_order_id,
    ))
    return broker_pb2.PlaceOrderResponse(broker_order_id=sim_id, status="submitted")


async def _sim_cancel(self, request: broker_pb2.CancelOrderRequest) -> broker_pb2.CancelOrderResponse:
    if not request.broker_order_id.startswith("SIM-"):
        # SIM mode but non-SIM id — accept-but-no-op
        return broker_pb2.CancelOrderResponse(accepted=False)
    entry = self._sim_orders.pop(request.broker_order_id, None)
    if entry is None:
        return broker_pb2.CancelOrderResponse(accepted=False)
    queues = self._client._order_event_queues.get(entry["account_number"], [])
    sim.dispatch(queues, sim.synthetic_cancel_event(
        broker_order_id=request.broker_order_id, client_order_id=entry["client_order_id"],
    ))
    return broker_pb2.CancelOrderResponse(accepted=True)
```

- [ ] **Step 4: Run + commit**

```bash
cd sidecar-futu && uv run pytest tests/test_sim.py -v
git add sidecar-futu/sim.py sidecar-futu/handlers.py sidecar-futu/tests/test_sim.py
git commit -m "feat(sidecar-futu): sim mode with per-account queues (v0.5.5 pattern)"
```

### Task C8 — `OrderEvent` gRPC stream + drop-pre-subscribe (H5)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter

**Files:**
- Modify: `sidecar-futu/handlers.py`, `sidecar-futu/futu_client.py`, `sidecar-futu/normalize.py`
- Create: `sidecar-futu/tests/test_handlers_orderevent.py`

- [ ] **Step 1: Write tests covering subscribe + dispatch + pre-subscribe drop**

```python
import asyncio
import pytest
from datetime import UTC, datetime

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_orderevent_dispatches_callback_after_subscribe():
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    queue: asyncio.Queue = asyncio.Queue()
    handlers._client._order_event_queues.setdefault("12345678", []).append(queue)

    fake_order = {"order_id": 555, "code": "HK.00700", "order_status": "FILLED_ALL",
                  "dealt_qty": "100", "dealt_avg_price": "350",
                  "create_time": "2026-04-29 14:30:00",
                  "updated_time": "2026-04-29 14:31:00", "remark": "cid-abc"}
    handlers._client._on_order_update("12345678", fake_order)

    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.broker_order_id == "555"
    assert event.status == "filled"
    assert event.client_order_id == "cid-abc"


@pytest.mark.asyncio
async def test_orderevent_pre_subscribe_dropped():
    """H5 — pre-subscribe callbacks are dropped."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    fake_order = {"order_id": 1, "code": "HK.00700", "order_status": "SUBMITTED",
                  "create_time": "2026-04-29 14:30:00",
                  "updated_time": "2026-04-29 14:30:00", "remark": ""}
    handlers._client._on_order_update("12345678", fake_order)

    queue: asyncio.Queue = asyncio.Queue()
    handlers._client._order_event_queues.setdefault("12345678", []).append(queue)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.1)
```

- [ ] **Step 2: Add normalize functions for events**

```python
# normalize.py
def order_event_from_futu_order_row(row: dict) -> broker_pb2.OrderEventMessage:
    ts = hk_local_to_utc_timestamp(row["updated_time"]) if row.get("updated_time") else None
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(row["order_id"]),
        client_order_id=row.get("remark", ""),
        status=status_string_from_futu(row.get("order_status", "")),
        filled_qty=str(row.get("dealt_qty", "0")),
        avg_fill_price=str(row.get("dealt_avg_price", "0")),
        broker_event_at=ts,
        kind="status",
    )


def order_event_from_futu_deal_row(row: dict) -> broker_pb2.OrderEventMessage:
    ts = hk_local_to_utc_timestamp(row["create_time"]) if row.get("create_time") else None
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(row.get("order_id", "")),
        filled_qty=str(row.get("qty", "0")),
        avg_fill_price=str(row.get("price", "0")),
        broker_event_at=ts,
        exec_id=str(row.get("deal_id", "")),
        kind="exec_details",
    )


def commission_event_from_futu_deal_row(row: dict) -> broker_pb2.OrderEventMessage:
    ts = hk_local_to_utc_timestamp(row["create_time"]) if row.get("create_time") else None
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(row.get("order_id", "")),
        broker_event_at=ts,
        exec_id=str(row.get("deal_id", "")),
        kind="commission_report",
        raw_payload=str({
            "commission": str(row.get("commission", "0")),
            "currency": row.get("currency", "HKD"),
        }),
    )
```

- [ ] **Step 3: Add `_on_order_update` + `_on_deal_update` to FutuClient**

```python
# futu_client.py
def _on_order_update(self, account_number: str, futu_row: dict) -> None:
    """Called by TradeOrderHandlerBase callback (futu-api thread)."""
    queues = self._order_event_queues.get(account_number, [])
    if not queues:
        return  # H5: drop, don't buffer

    from sidecar_futu.normalize import order_event_from_futu_order_row
    event = order_event_from_futu_order_row(futu_row)
    for q in queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("orderevent_queue_full", account=account_number)


def _on_deal_update(self, account_number: str, futu_row: dict) -> None:
    """Called by TradeDealHandlerBase. Emits exec_details + commission_report."""
    queues = self._order_event_queues.get(account_number, [])
    if not queues:
        return
    from sidecar_futu.normalize import (
        order_event_from_futu_deal_row, commission_event_from_futu_deal_row,
    )
    for ev in (order_event_from_futu_deal_row(futu_row),
               commission_event_from_futu_deal_row(futu_row)):
        for q in queues:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                log.warning("orderevent_queue_full", account=account_number)
```

- [ ] **Step 4: Implement `OrderEvent` server-streaming handler**

```python
# handlers.py
from collections.abc import AsyncIterator

async def OrderEvent(  # noqa: N802
    self,
    request: broker_pb2.AccountRef,
    context: Any,
) -> AsyncIterator[broker_pb2.OrderEventMessage]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    self._client._order_event_queues.setdefault(request.account_number, []).append(queue)
    log.info("orderevent_subscribed", account=request.account_number)
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        try:
            self._client._order_event_queues[request.account_number].remove(queue)
        except (KeyError, ValueError):
            pass
        log.info("orderevent_unsubscribed", account=request.account_number)
```

- [ ] **Step 5: Run + commit**

```bash
cd sidecar-futu && uv run pytest tests/test_handlers_orderevent.py -v
git add sidecar-futu/handlers.py sidecar-futu/futu_client.py sidecar-futu/normalize.py sidecar-futu/tests/test_handlers_orderevent.py
git commit -m "feat(sidecar-futu): orderevent stream + drop-pre-subscribe (h5)"
```

### Task C9 — `ModifyOrder` + `PlaceBracket` return UNIMPLEMENTED

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `sidecar-futu/handlers.py`

- [ ] **Step 1: Add stubs**

```python
async def ModifyOrder(  # noqa: N802
    self,
    request: broker_pb2.ModifyOrderRequest,
    context: Any,
) -> broker_pb2.ModifyOrderResponse:
    await context.abort(grpc.StatusCode.UNIMPLEMENTED, "Modify deferred to Phase 7")


async def PlaceBracket(  # noqa: N802
    self,
    request: broker_pb2.PlaceBracketRequest,
    context: Any,
) -> broker_pb2.PlaceBracketResponse:
    await context.abort(grpc.StatusCode.UNIMPLEMENTED, "Bracket deferred to Phase 7")
```

- [ ] **Step 2: Commit**

```bash
git add sidecar-futu/handlers.py
git commit -m "feat(sidecar-futu): modify/bracket return unimplemented (phase 7)"
```

### Task C10 — IBKR sidecar implements `Configure` as no-op + populates `Health.broker_id`/`started_at`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `sidecar/handlers.py`

- [ ] **Step 1: Add Configure no-op + extend Health**

```python
# sidecar/handlers.py — find existing class and add:

async def Configure(  # noqa: N802
    self, request, context,
):
    # IBKR sidecars don't need Configure; treat as no-op.
    return broker_pb2.ConfigureResponse(ok=True, detail="")


# In Health response construction, add:
def _health_response(self) -> broker_pb2.HealthResponse:
    ts = Timestamp()
    ts.FromDatetime(self._started_at)  # _started_at set in __init__
    return broker_pb2.HealthResponse(
        ...,  # existing fields
        started_at=ts,
        broker_id="ibkr",
    )
```

- [ ] **Step 2: Run sidecar tests**

```bash
cd sidecar && uv run pytest tests/ -v
```

- [ ] **Step 3: Commit**

```bash
git add sidecar/handlers.py
git commit -m "feat(sidecar): configure no-op + health.broker_id/started_at (h2 h4)"
```

---

## Chunk D — Backend service updates (~0.75 day)

### Task D1 — `BrokerRegistry` cross-checks `health.broker_id` (H4)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + security-reviewer + silent-failure-hunter

**Files:**
- Modify: `backend/app/services/brokers.py`
- Modify: `backend/tests/services/test_brokers.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_brokers.py — append
@pytest.mark.asyncio
async def test_label_mismatch_marks_label_degraded_and_increments_metric():
    from prometheus_client import REGISTRY as PCREG

    fake_client = MagicMock()
    fake_client.health = AsyncMock(return_value=base.HealthResponse(
        label="futu", gateway_connected=True, gateway_version="0.6.0",
        sidecar_version="0.6.0", started_at=datetime.now(UTC),
        broker_id="ibkr",  # mismatch (label=futu but reports ibkr)
    ))

    registry = BrokerRegistry({"futu": fake_client})
    await registry.probe_once()

    degraded = await registry.degraded_labels()
    assert "futu" in degraded
    sample = PCREG.get_sample_value("broker_registry_label_mismatch_total", {"label": "futu"})
    assert sample == 1.0
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement cross-check in `_probe_client`**

```python
# backend/app/services/brokers.py
from app.services.broker_registry_factory import SIDECAR_BROKERS
from app.core import metrics


async def _probe_client(self, label: str, client: BrokerSidecarClient) -> None:
    try:
        health = await client.health()
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout, Exception) as exc:
        await self._mark_health(label, ok=False, health=None)
        log.debug("broker_registry_probe_failed", label=label, error=str(exc))
        return

    expected_broker = SIDECAR_BROKERS.get(label)
    if (expected_broker and health.broker_id
            and health.broker_id != expected_broker):
        log.critical(
            "broker_registry_label_mismatch",
            label=label, expected=expected_broker, actual=health.broker_id,
        )
        metrics.broker_registry_label_mismatch_total.labels(label=label).inc()
        await self._mark_health(label, ok=False, health=health)
        return

    await self._mark_health(label, ok=True, health=health)
```

- [ ] **Step 4: Run + commit**

```bash
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs); uv run pytest tests/services/test_brokers.py -v
git add backend/app/services/brokers.py backend/tests/services/test_brokers.py
git commit -m "feat(brokers): cross-check health.broker_id vs sidecar_brokers (h4)"
```

### Task D2 — `BrokerRegistry._configured: dict[str, datetime]` + `BrokerConfigurer` (H1+H2)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter

**Files:**
- Modify: `backend/app/services/brokers.py`
- Modify: `backend/app/services/broker_registry_factory.py`

- [ ] **Step 1: Add `_configured` tracking + retry logic in `_probe_client`**

```python
# brokers.py — extend BrokerRegistry
def __init__(self, ..., **kwargs):
    ...  # existing
    self._configured: dict[str, datetime] = {}
    self._configurer: Any | None = None  # set by factory


async def _probe_client(self, label, client):
    # existing health probe + cross-check (D1)
    ...
    # H2: re-Configure if sidecar restarted (started_at differs from cached)
    started_at_dt = health.started_at.ToDatetime(tzinfo=UTC) if health.started_at else None
    cached = self._configured.get(label)
    if (self._configurer is not None and label in self._configurer.targets
            and (cached is None or cached != started_at_dt)):
        try:
            ok = await self._configurer.configure(label)
            if ok and started_at_dt is not None:
                self._configured[label] = started_at_dt
        except Exception as exc:  # noqa: BLE001
            log.warning("broker_reconfigure_failed", label=label, error=str(exc))
```

- [ ] **Step 2: Add `BrokerConfigurer` to factory + initial Configure call**

```python
# broker_registry_factory.py
from dataclasses import dataclass


@dataclass
class BrokerConfigurer:
    config_service: ConfigService
    registry: "BrokerRegistry"
    targets: set[str]

    async def configure(self, label: str) -> bool:
        if label not in self.targets:
            return True
        creds_md5 = await self.config_service.reveal_secret("broker", f"{label}.unlock_pwd_md5")
        rsa_pem = await self.config_service.reveal_secret("broker", f"{label}.rsa_priv_pem")
        host = await self.config_service.get("broker", f"{label}.opend_host", default="127.0.0.1")
        port = await self.config_service.get_int("broker", f"{label}.opend_port", default=11111)
        conn_id = await self.config_service.get("broker", f"{label}.connection_id", default="")
        if not creds_md5 or not rsa_pem:
            log.warning("broker_configure_creds_missing", label=label)
            return False
        client = await self.registry.get_client(label)
        resp = await client.configure(
            unlock_pwd_md5=creds_md5, rsa_priv_pem=rsa_pem,
            opend_host=host, opend_port=port, connection_id=conn_id,
        )
        return resp.ok


async def build_broker_registry(config_service, *, host="10.10.0.2") -> BrokerRegistry:
    ...  # existing setup
    registry = BrokerRegistry(...)
    configurer = BrokerConfigurer(config_service, registry, targets={"futu"})
    registry._configurer = configurer
    for label in configurer.targets:
        try:
            await configurer.configure(label)
        except Exception as exc:  # noqa: BLE001
            log.warning("broker_initial_configure_failed", label=label, error=str(exc))
    return registry
```

- [ ] **Step 3: Add `BrokerSidecarClient.configure` method**

```python
# brokers.py — BrokerSidecarClient
async def configure(self, **kwargs) -> Any:
    request = broker_pb2.ConfigureRequest(**kwargs)
    return await self._call(self._stub.Configure, request)
```

- [ ] **Step 4: Run tests + commit**

```bash
cd backend && uv run pytest tests/services/ -v
git add backend/app/services/brokers.py backend/app/services/broker_registry_factory.py
git commit -m "feat(brokers): _configured tracking + brokerconfigurer (h1 h2)"
```

### Task D3 — `POST /api/admin/brokers/{label}/reconfigure` admin endpoint (H3)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + security-reviewer + type-design-analyzer

**Files:**
- Create: `backend/app/api/brokers_admin.py`
- Modify: `backend/app/main.py` (include router)
- Create: `backend/tests/api/test_brokers_admin.py`

- [ ] **Step 1: Test**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, require_admin_jwt
from app.main import app


@pytest.mark.asyncio
async def test_reconfigure_calls_configurer():
    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)
    fake_registry = MagicMock(_configurer=fake_configurer)

    app.dependency_overrides[get_broker_registry] = lambda: fake_registry
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@t", kind="user", claims={},
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/admin/brokers/futu/reconfigure")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        fake_configurer.configure.assert_called_once_with("futu")
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Implement**

```python
# backend/app/api/brokers_admin.py
"""Admin endpoint for triggering Configure on a broker sidecar."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_broker_registry, require_admin_jwt

router = APIRouter(
    prefix="/api/admin/brokers",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.post("/{label}/reconfigure")
async def reconfigure(label: str, registry=Depends(get_broker_registry)) -> dict[str, object]:
    if registry._configurer is None or label not in registry._configurer.targets:
        return {"ok": False, "detail": f"label {label} does not require Configure"}
    ok = await registry._configurer.configure(label)
    return {"ok": ok, "detail": "" if ok else "configure_failed"}
```

- [ ] **Step 3: Mount in `main.py`**

```python
# backend/app/main.py — add import + include
from app.api.brokers_admin import router as brokers_admin_router
...
app.include_router(brokers_admin_router)
```

- [ ] **Step 4: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_brokers_admin.py -v
git add backend/app/api/brokers_admin.py backend/app/main.py backend/tests/api/test_brokers_admin.py
git commit -m "feat(api): admin/brokers/{label}/reconfigure endpoint (h3)"
```

### Task D4 — `/api/contracts/search` accepts `?broker=` Pydantic Literal (L4)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer + security-reviewer

**Files:**
- Modify: `backend/app/api/contracts.py`
- Create: `backend/tests/api/test_contracts_search_broker.py`

- [ ] **Step 1: Tests for ?broker=ibkr|futu|missing|invalid|schwab(=503)**

```python
@pytest.mark.asyncio
async def test_search_broker_invalid_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/contracts/search?q=AAPL&broker=evil")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_broker_schwab_returns_503():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/contracts/search?q=AAPL&broker=schwab")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_search_broker_futu_routes_to_futu_client():
    # mock registry such that get_client("futu") returns a client whose
    # search_contracts returns a known list; assert the response.
    ...
```

- [ ] **Step 2: Implement**

```python
# backend/app/api/contracts.py
from typing import Literal


@router.get("/search")
async def search_contracts(
    q: Annotated[str, Query(min_length=1, max_length=64)],
    redis: RedisDep,
    registry: RegistryDep,
    identity: IdentityDep,
    asset_class: str = "",
    broker: Annotated[Literal["ibkr", "futu", "schwab"] | None, Query()] = None,
) -> JSONResponse:
    rate_err = await _check_rate_limit(redis, identity.email)
    if rate_err is not None:
        return rate_err

    if broker == "schwab":
        return JSONResponse(
            status_code=503,
            content={"error": "schwab_not_yet_supported"},
            headers={"Retry-After": "86400"},
        )

    cache_k = _cache_key(q, asset_class)
    cached = await redis.get(cache_k)
    if cached is not None:
        return JSONResponse(content={"contracts": _contracts_from_json(cached)})

    try:
        if broker == "futu":
            client = await registry.get_client("futu")
        elif broker == "ibkr":
            clients = await registry.healthy_clients()
            ibkr_clients = [c for c in clients if c.label.startswith(("isa-", "normal-"))]
            client = ibkr_clients[0] if ibkr_clients else await registry.get_client("isa-paper")
        else:
            clients = await registry.healthy_clients()
            client = clients[0] if clients else await registry.get_client("isa-paper")
    except KeyError:
        return JSONResponse(
            status_code=503,
            content={"error": f"{broker}_not_configured" if broker else "no_client"},
        )

    contracts = await client.search_contracts(query=q, asset_class=asset_class)
    payload = _contracts_to_json(contracts)
    await redis.set(cache_k, payload, ex=300)
    return JSONResponse(content={"contracts": _contracts_from_json(payload)})
```

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/api/test_contracts_search_broker.py -v
git add backend/app/api/contracts.py backend/tests/api/test_contracts_search_broker.py
git commit -m "feat(api): contracts/search accepts ?broker= literal (l4)"
```

### Task D5 — Parametrize existing tests for `label="futu"`

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Modify: `backend/tests/services/test_brokers.py`, `test_account_service.py`, `test_order_event_consumer.py`, `test_pending_submit_watchdog.py`

- [ ] **Step 1: Find broker-agnostic test cases**

```bash
grep -n 'label="isa-' backend/tests/services/*.py | head -20
```

- [ ] **Step 2: Add `@pytest.mark.parametrize("label", ["isa-live", "futu"])` where the test logic is broker-agnostic** (mechanical refactor; tests asserting IBKR-specific behavior keep `label="isa-live"`).

- [ ] **Step 3: Run all backend tests**

```bash
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs); uv run pytest tests/ -x -q
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/services/
git commit -m "test(services): parametrize broker-agnostic tests for futu label"
```

---

## Chunk E — Tests (~2.0 days)

### Task E1 — `FakeBrokerServicer` refactor — broker-agnostic via label parameter (M5)

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + python-reviewer + type-design-analyzer

**Files:**
- Modify: `backend/tests/fixtures/sidecar_servicer.py`
- Modify: `backend/tests/fixtures/test_sidecar_servicer.py`

- [ ] **Step 1: Read the existing servicer**

```bash
sed -n '1,40p' backend/tests/fixtures/sidecar_servicer.py
```

- [ ] **Step 2: Add `broker_id: str = "ibkr"` constructor param + populate Health.broker_id + Health.started_at + implement Configure as no-op**

```python
class FakeBrokerServicer(broker_pb2_grpc.BrokerServicer):
    def __init__(
        self,
        *,
        label: str = "isa-paper",
        broker_id: Literal["ibkr", "futu"] = "ibkr",
        accounts: list[broker_pb2.Account] | None = None,
        ...
    ):
        self._label = label
        self._broker_id = broker_id
        self._started_at = datetime.now(UTC)
        self._configure_call_count = 0
        ...

    async def Health(self, request, context):
        ts = Timestamp()
        ts.FromDatetime(self._started_at)
        return broker_pb2.HealthResponse(
            label=self._label, gateway_connected=True,
            broker_id=self._broker_id, started_at=ts,
            sidecar_version="test", gateway_version="test",
        )

    async def Configure(self, request, context):
        self._configure_call_count += 1
        return broker_pb2.ConfigureResponse(ok=True, detail="")
```

- [ ] **Step 3: Verify existing tests still pass + futu shape works**

```bash
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs); uv run pytest tests/fixtures/test_sidecar_servicer.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/fixtures/sidecar_servicer.py backend/tests/fixtures/test_sidecar_servicer.py
git commit -m "test(fixtures): refactor fakebrokerservicer broker-agnostic (m5)"
```

### Task E2 — `futu_test_data.py` — HK stock/ETF/warrant/CBBC fixtures

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Create: `backend/tests/fixtures/futu_test_data.py`

- [ ] **Step 1: Define fixtures**

```python
"""Test data with Futu-shape (HK.XXXXX symbols, numeric account IDs)."""
from app._generated.broker.v1 import broker_pb2

FUTU_HK_STOCK = broker_pb2.Contract(
    symbol="HK.00700", exchange="SEHK", currency="HKD",
    asset_class=broker_pb2.AssetClass.STOCK, conid="HK.00700",
    local_symbol="Tencent",
)

FUTU_HK_ETF = broker_pb2.Contract(
    symbol="HK.02800", exchange="SEHK", currency="HKD",
    asset_class=broker_pb2.AssetClass.ETF, conid="HK.02800",
    local_symbol="Tracker Fund",
)

FUTU_HK_WARRANT = broker_pb2.Contract(
    symbol="HK.13234", exchange="SEHK", currency="HKD",
    asset_class=broker_pb2.AssetClass.WARRANT, conid="HK.13234",
    local_symbol="WARRANT-700-X",
)

FUTU_HK_CBBC = broker_pb2.Contract(
    symbol="HK.62345", exchange="SEHK", currency="HKD",
    asset_class=broker_pb2.AssetClass.CBBC, conid="HK.62345",
    local_symbol="CBBC-700-BULL",
)

FUTU_LIVE_ACCOUNT = broker_pb2.Account(
    account_number="11111111", mode=broker_pb2.TradingMode.LIVE,
    gateway_label="futu", currency_base="HKD",
)

FUTU_PAPER_ACCOUNT = broker_pb2.Account(
    account_number="22222222", mode=broker_pb2.TradingMode.PAPER,
    gateway_label="futu", currency_base="HKD",
)
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/fixtures/futu_test_data.py
git commit -m "test(fixtures): futu hk test data (stock/etf/warrant/cbbc)"
```

### Task E3 — `test_e2e_futu_chain.py` — preview→place→cancel mock E2E

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality + python-reviewer + tdd-guide

**Files:**
- Create: `backend/tests/integration/test_e2e_futu_chain.py`

- [ ] **Step 1: Write the chain test parametrized over the 4 asset classes**

```python
import asyncio
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from backend.tests.fixtures.sidecar_servicer import FakeBrokerServicer
from backend.tests.fixtures.futu_test_data import (
    FUTU_HK_STOCK, FUTU_HK_ETF, FUTU_HK_WARRANT, FUTU_HK_CBBC, FUTU_PAPER_ACCOUNT,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("contract", [
    FUTU_HK_STOCK, FUTU_HK_ETF, FUTU_HK_WARRANT, FUTU_HK_CBBC,
])
async def test_futu_preview_place_cancel_chain(contract, db_session, monkeypatch):
    # Spin a futu-shape FakeBrokerServicer
    servicer = FakeBrokerServicer(
        label="futu", broker_id="futu",
        accounts=[FUTU_PAPER_ACCOUNT],
    )
    # Wire it into the registry (test fixture pattern from existing tests)
    ...

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # 1. Preview
        preview = await c.post("/api/orders/preview", json={
            "account_id": str(account_uuid),
            "contract": _contract_to_dict(contract),
            "side": "BUY", "order_type": "LIMIT", "quantity": "100",
            "limit_price": "350.00", "time_in_force": "DAY",
        })
        assert preview.status_code == 200
        nonce = preview.json()["nonce"]
        client_order_id = preview.json()["client_order_id"]

        # 2. Place
        place = await c.post("/api/orders", json={
            "account_id": str(account_uuid),
            "contract": _contract_to_dict(contract),
            "side": "BUY", "order_type": "LIMIT", "quantity": "100",
            "limit_price": "350.00", "time_in_force": "DAY",
            "nonce": nonce, "client_order_id": client_order_id,
        })
        assert place.status_code == 200
        order_id = place.json()["id"]

        # 3. Cancel
        cancel = await c.delete(f"/api/orders/{order_id}")
        assert cancel.status_code == 202

        # 4. Verify status flips to cancelled
        await asyncio.sleep(0.5)
        get = await c.get(f"/api/orders/{order_id}")
        assert get.json()["status"] == "cancelled"
```

- [ ] **Step 2: Run + commit**

```bash
cd backend && uv run pytest tests/integration/test_e2e_futu_chain.py -v
git add backend/tests/integration/test_e2e_futu_chain.py
git commit -m "test(integration): e2e_futu_chain stock/etf/warrant/cbbc"
```

### Task E4 — Reconfigure-cycle integration test (H2 regression)

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality + python-reviewer + silent-failure-hunter

**Files:**
- Create: `backend/tests/integration/test_reconfigure_cycle.py`

- [ ] **Step 1: Write the test**

```python
import pytest
from datetime import UTC, datetime, timedelta

from backend.tests.fixtures.sidecar_servicer import FakeBrokerServicer


@pytest.mark.asyncio
async def test_reconfigure_after_sidecar_restart_re_calls_configure(monkeypatch):
    """H2 — Sidecar restart leaves it permanently unconfigured: regression."""
    T1 = datetime.now(UTC)
    T2 = T1 + timedelta(seconds=30)

    servicer = FakeBrokerServicer(label="futu", broker_id="futu")
    servicer._started_at = T1

    # build_broker_registry → BrokerConfigurer → call configure(futu)
    from app.services.broker_registry_factory import build_broker_registry
    registry = await build_broker_registry(...)

    assert servicer._configure_call_count == 1
    assert registry._configured.get("futu") == T1

    # Simulate sidecar restart
    servicer._started_at = T2
    servicer._configure_call_count = 0

    await registry.probe_once()

    # H2: re-Configure should have fired
    assert servicer._configure_call_count >= 1
    assert registry._configured["futu"] == T2
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/integration/test_reconfigure_cycle.py
git commit -m "test(integration): reconfigure cycle on sidecar restart (h2)"
```

### Task E5 — Sidecar-futu contract tests (real grpc server)

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality + python-reviewer

**Files:**
- Create: `sidecar-futu/tests/test_handlers_futu_contract.py`

- [ ] **Step 1: Spin a real grpc server, mock futu-api at boundary**

```python
import asyncio
import grpc
import pytest
from datetime import UTC, datetime
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from sidecar_futu._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar_futu.handlers import BrokerHandlers


def _make_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.mark.asyncio
async def test_full_handler_chain_against_real_grpc():
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(handlers, server)
    port = server.add_insecure_port("[::]:0")
    await server.start()

    async with grpc.aio.insecure_channel(f"localhost:{port}") as ch:
        stub = broker_pb2_grpc.BrokerStub(ch)

        h = await stub.Health(broker_pb2.HealthRequest())
        assert h.broker_id == "futu"

        cr = await stub.Configure(broker_pb2.ConfigureRequest(
            unlock_pwd_md5="0" * 32, rsa_priv_pem=_make_pem(),
            opend_host="x", opend_port=0, connection_id="x",
        ))
        assert cr.ok is True

    await server.stop(grace=1)
```

- [ ] **Step 2: Commit**

```bash
git add sidecar-futu/tests/test_handlers_futu_contract.py
git commit -m "test(sidecar-futu): contract tests against real grpc server"
```

---

## Chunk F — Frontend (~1.25 days)

### Task F1 — JP kanji subset regen + provenance README (M6)

**Owner:** Operator (one-time) or Claude (in WSL with pyftsubset installed)
**Reviewers:** spec-compliance + code-quality

**Files:**
- Create: `frontend/public/fonts/NotoSansJP-kana-400.woff2`
- Create: `frontend/public/fonts/NotoSansJP-kanji-400.woff2`
- Delete: `frontend/public/fonts/NotoSansCJK-JP-400.subset.woff2`
- Create: `frontend/public/fonts/README.md`

- [ ] **Step 1: Write the README**

```markdown
# Fonts — Subsetting Pipeline

Self-hosted Noto Sans woff2 subsets. CJK split per language for unicode-range targeting (per spec §7.1, M6).

## Regenerate JP kana + kanji

Source: `NotoSansJP-Regular.otf` from `https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/Japanese/NotoSansJP-Regular.otf`.

```bash
pip install fonttools brotli zopfli

# Kana-only (~50KB)
pyftsubset NotoSansJP-Regular.otf \
  --output-file=NotoSansJP-kana-400.woff2 \
  --flavor=woff2 \
  --unicodes=U+3040-309F,U+30A0-30FF,U+31F0-31FF

# Kanji-only (~1-2MB, lazy-loaded)
pyftsubset NotoSansJP-Regular.otf \
  --output-file=NotoSansJP-kanji-400.woff2 \
  --flavor=woff2 \
  --unicodes=U+4E00-9FFF,U+3400-4DBF,U+F900-FAFF
```

The two woff2 files share family name "Noto Sans JP" in CSS; browser loads only the file whose unicode-range matches rendered content.
```

- [ ] **Step 2: Generate the woff2 files**

```bash
cd /tmp
curl -sL "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/Japanese/NotoSansJP-Regular.otf" -o NotoSansJP-Regular.otf
pip install --quiet fonttools brotli zopfli
pyftsubset NotoSansJP-Regular.otf --output-file=/home/joseph/dashboard/frontend/public/fonts/NotoSansJP-kana-400.woff2 --flavor=woff2 --unicodes=U+3040-309F,U+30A0-30FF,U+31F0-31FF
pyftsubset NotoSansJP-Regular.otf --output-file=/home/joseph/dashboard/frontend/public/fonts/NotoSansJP-kanji-400.woff2 --flavor=woff2 --unicodes=U+4E00-9FFF,U+3400-4DBF,U+F900-FAFF
ls -lh /home/joseph/dashboard/frontend/public/fonts/NotoSansJP-*
```

Expected: kana ~50KB, kanji ~1-2MB.

- [ ] **Step 3: Commit + delete old subset**

```bash
git rm frontend/public/fonts/NotoSansCJK-JP-400.subset.woff2
git add frontend/public/fonts/NotoSansJP-kana-400.woff2 frontend/public/fonts/NotoSansJP-kanji-400.woff2 frontend/public/fonts/README.md
git commit -m "feat(fonts): split jp into kana+kanji subsets for lazy loading (m6)"
```

### Task F2 — `global.css` font-face split + `[lang|="ja"]` selector

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + typescript-reviewer + a11y-architect

**Files:**
- Modify: `frontend/src/styles/global.css`

- [ ] **Step 1: Replace existing JP @font-face block with two faces + selector**

```css
/* Replace the existing single JP @font-face with these two: */

@font-face {
  font-family: "Noto Sans JP";
  font-weight: 400;
  font-style: normal;
  font-display: swap;
  src: url("/fonts/NotoSansJP-kana-400.woff2") format("woff2");
  unicode-range: U+3040-309F, U+30A0-30FF, U+31F0-31FF;
}

@font-face {
  font-family: "Noto Sans JP";
  font-weight: 400;
  font-style: normal;
  font-display: swap;
  src: url("/fonts/NotoSansJP-kanji-400.woff2") format("woff2");
  unicode-range: U+4E00-9FFF, U+3400-4DBF, U+F900-FAFF;
}

[lang|="ja"] {
  font-family: "Noto Sans JP", "Noto Sans", system-ui, sans-serif;
}
```

- [ ] **Step 2: Run frontend typecheck**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles/global.css
git commit -m "feat(fonts): two-face jp + lang-selector for kanji glyphs"
```

### Task F3 — `CJKText.stories.tsx` Storybook visual diff

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + typescript-reviewer + a11y-architect

**Files:**
- Create: `frontend/src/components/primitives/Text/CJKText.stories.tsx`

- [ ] **Step 1: Write the story**

```tsx
import type { Meta, StoryObj } from '@storybook/react';

const meta: Meta = { title: 'Primitives/Text/CJK' };
export default meta;

const KANJI_SAMPLE = '腾讯控股 (00700) — 騰訊控股 — 텐센트홀딩스';

export const TraditionalChinese: StoryObj = {
  render: () => <p lang="zh-TW" style={{ fontSize: '2rem' }}>{KANJI_SAMPLE}</p>,
};

export const SimplifiedChinese: StoryObj = {
  render: () => <p lang="zh-CN" style={{ fontSize: '2rem' }}>{KANJI_SAMPLE}</p>,
};

export const Japanese: StoryObj = {
  render: () => <p lang="ja" style={{ fontSize: '2rem' }}>{KANJI_SAMPLE}</p>,
};

export const Korean: StoryObj = {
  render: () => <p lang="ko" style={{ fontSize: '2rem' }}>{KANJI_SAMPLE}</p>,
};
```

- [ ] **Step 2: Run Storybook smoke**

```bash
cd frontend && pnpm storybook --ci --smoke-test
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/primitives/Text/CJKText.stories.tsx
git commit -m "story(fonts): cjk visual diff jp/zh-tw/zh-cn/ko"
```

### Task F4 — `ContractSearchInput` passes `?broker=` from active account

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + typescript-reviewer + a11y-architect

**Files:**
- Modify: `frontend/src/services/orders.ts`
- Modify: `frontend/src/features/orders/ContractSearchInput.tsx`

- [ ] **Step 1: Find current invocation**

```bash
grep -n 'contracts/search\|searchContracts' frontend/src/features/orders/ContractSearchInput.tsx frontend/src/services/orders.ts
```

- [ ] **Step 2: Add `broker` arg to service**

```typescript
// frontend/src/services/orders.ts
export async function searchContracts(
  q: string,
  asset_class?: string,
  broker?: 'ibkr' | 'futu',
): Promise<Contract[]> {
  const params = new URLSearchParams({ q });
  if (asset_class) params.set('asset_class', asset_class);
  if (broker) params.set('broker', broker);
  const res = await fetch(`/api/contracts/search?${params}`);
  if (!res.ok) throw new Error(`search_${res.status}`);
  const body = await res.json();
  return body.contracts;
}
```

- [ ] **Step 3: Update `ContractSearchInput`**

```tsx
// ContractSearchInput.tsx
const activeAccount = useActiveStores().selected;
const broker = activeAccount?.broker_id as 'ibkr' | 'futu' | undefined;

// In the debounced search call:
const results = await searchContracts(q, asset_class, broker);
```

- [ ] **Step 4: Test + commit**

```bash
cd frontend && pnpm test ContractSearchInput
git add frontend/src/features/orders/ContractSearchInput.tsx frontend/src/services/orders.ts
git commit -m "feat(frontend): contract search passes ?broker= from active account"
```

### Task F5 — `TradeTicketModal` field-disable warrants/CBBC stop-limit

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality + typescript-reviewer + a11y-architect

**Files:**
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Find existing field-disable logic**

```bash
grep -n 'fieldDisable\|disabled\|warrant\|cbbc' frontend/src/features/orders/TradeTicketModal.tsx
```

- [ ] **Step 2: Add the rule**

```tsx
const stopLimitDisabled =
  account?.broker_id === 'futu' &&
  (contract?.asset_class === 'WARRANT' || contract?.asset_class === 'CBBC');

// In the OrderType picker:
<RadioGroup.Item value="STOP_LIMIT" disabled={stopLimitDisabled}>
  Stop-Limit
  {stopLimitDisabled && <span className="text-xs"> (unavailable for HK warrants/CBBC)</span>}
</RadioGroup.Item>
```

- [ ] **Step 3: Test + commit**

```bash
cd frontend && pnpm test TradeTicketModal
git add frontend/src/features/orders/TradeTicketModal.tsx
git commit -m "feat(frontend): tradeticketmodal disables stop-limit for futu warrants/cbbc"
```

---

## Chunk G — Ops + close-out (~1.25 days)

### Task G1 — NUC ops scripts: `build-windows-futu.ps1` + `restart-futu-sidecar.ps1`

**Owner:** Codex
**Reviewers:** spec-compliance + code-quality

**Files:**
- Create: `deploy/nuc/build-windows-futu.ps1`
- Create: `deploy/nuc/restart-futu-sidecar.ps1`

- [ ] **Step 1: `build-windows-futu.ps1`**

```powershell
[CmdletBinding()] param([string]$Version = "0.6.0")
$ErrorActionPreference = 'Stop'

cd C:\dashboard\sidecar-futu
& .\scripts\build-windows.ps1 -Version $Version

$dest = "C:\dashboard\dist-staging-futu"
if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Force -Path $dest | Out-Null }
Copy-Item ".\dist-staging-futu\futu-sidecar.exe" -Destination $dest -Force
Write-Host "[OK] Built v$Version → $dest\futu-sidecar.exe"
```

- [ ] **Step 2: `restart-futu-sidecar.ps1`**

```powershell
[CmdletBinding()] param()
$ErrorActionPreference = 'Continue'

$cu = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal $cu).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error 'Must run as Administrator.'; return }

Write-Host '-> Stopping BrokerSidecarFutu scheduled task'
& schtasks.exe /End /TN 'BrokerSidecarFutu' 2>$null | Out-Null

Write-Host '-> Killing any orphan futu-sidecar.exe'
Get-Process | Where-Object Name -eq 'futu-sidecar' | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 1
Write-Host '-> Re-firing BrokerSidecarFutu'
& schtasks.exe /Run /TN 'BrokerSidecarFutu'

Start-Sleep -Seconds 3
$alive = Get-Process | Where-Object Name -eq 'futu-sidecar'
if ($alive) { Write-Host "[OK] futu-sidecar pid=$($alive.Id) running" }
else { Write-Warning "[WARN] futu-sidecar not running after restart" }
```

- [ ] **Step 3: Commit**

```bash
git add deploy/nuc/build-windows-futu.ps1 deploy/nuc/restart-futu-sidecar.ps1
git commit -m "feat(nuc): build + restart helpers for futu sidecar"
```

### Task G2 — Defender exclusion glob + scheduled task registration

**Owner:** Operator (one-time on Windows)
**Reviewers:** spec-compliance

**Files:** None (operational)

- [ ] **Step 1: Defender exclusion (per runbook step 8)**

```powershell
Add-MpPreference -ExclusionPath "C:\dashboard\dist-staging-*"
```

- [ ] **Step 2: Register `BrokerSidecarFutu` scheduled task**

```powershell
$action = New-ScheduledTaskAction -Execute "C:\dashboard\dist-staging-futu\futu-sidecar.exe"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "BrokerSidecarFutu" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

- [ ] **Step 3: Note in runbook (already covered by Task A7 step 8) — no commit needed**

### Task G3 — Deploy backend + USER GATE: verify Futu sidecar discovers accounts + SIM canary

**Owner:** USER + Claude assists
**Reviewers:** spec-compliance + code-quality + tdd-guide + silent-failure-hunter

- [ ] **Step 1: Push all Phase 6 commits**

```bash
git push origin main
```

- [ ] **Step 2: Operator runs the FutuOpenD setup runbook (`deploy/nuc/runbook-futu-setup.md`)**

End-to-end: install OpenD, generate 1024-bit RSA, configure OpenD web UI, compute MD5, seed `app_secrets`/`app_config`.

- [ ] **Step 3: Operator builds + deploys the futu sidecar on Windows**

```powershell
cd C:\dashboard
.\deploy\nuc\build-windows-futu.ps1
.\deploy\nuc\restart-futu-sidecar.ps1
```

- [ ] **Step 4: Deploy backend to VPS**

```bash
./scripts/deploy.sh
```

- [ ] **Step 5: Trigger Configure**

```bash
curl -sf -X POST https://dashboard.kiusinghung.com/api/admin/brokers/futu/reconfigure \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```

Expected: `{"ok": true, "detail": ""}`.

- [ ] **Step 6: Verify Futu accounts visible**

```bash
curl -sf https://dashboard.kiusinghung.com/api/brokers/accounts \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" | jq '.accounts[] | select(.broker=="futu")'
```

Expected: rows with `connected:true`.

- [ ] **Step 7: SIM canary in browser**

At `https://dashboard.kiusinghung.com/orders`:
- Pick a Futu account
- Search "HK.00700", place LIMIT BUY 100 @ 350.00
- Verify status `submitted`
- Click Cancel → status `cancelled` within ~2s

- [ ] **Step 8: USER GATE — confirm before tagging**

Output: "✅ Phase 6 deploy verified — OpenD discovered, accounts visible, SIM canary works end-to-end. Tag v0.6.0?"

Wait for explicit user authorization before G4.

### Task G4 — Close-out: CHANGELOG + TASKS + CLAUDE.md + memory + tag v0.6.0

**Owner:** Claude
**Reviewers:** spec-compliance + code-quality

**Files:**
- Modify: `CHANGELOG.md`, `TASKS.md`, `CLAUDE.md`
- Create memory: `phase6_futu_topology.md`, `reference_futu_api_docs.md`
- Modify memory: `MEMORY.md`

- [ ] **Step 1: CHANGELOG `[0.6.0]` entry under `[Unreleased]`**

```markdown
## [0.6.0] — 2026-04-30 (or actual deploy date)

### Phase 6 — Futu HK adapter + JP kanji font polish

- New `sidecar-futu/` Python package (PyInstaller-frozen → `dist-staging-futu/futu-sidecar.exe`).
- Single Futu sidecar at `10.10.0.2:18005` (label `"futu"`); same gRPC `Broker` contract as IBKR plus new `Configure` RPC.
- Read + place + cancel for HK stocks/ETFs/warrants/CBBC. Modify/bracket return UNIMPLEMENTED (Phase 7).
- `Health.broker_id` + `Health.started_at` added; `BrokerRegistry` cross-checks against `SIDECAR_BROKERS` map (H4) and re-Configures on sidecar restart (H2).
- New `POST /api/admin/brokers/{label}/reconfigure` admin endpoint for cred rotation.
- `?broker=ibkr|futu|schwab` Pydantic Literal on `/api/contracts/search`; `schwab` returns 503.
- JP kanji font split: `Noto Sans JP` family with two `@font-face` declarations (kana + kanji-only); browser lazy-loads kanji file via unicode-range matching.
- New Prometheus alerts: `BrokerLabelMismatch` (page), `BrokerFutuNormalizeUnknown` (warning).
```

- [ ] **Step 2: TASKS.md — mark Phase 6 done; carry over Phase 7 deferred items**

- [ ] **Step 3: CLAUDE.md — add §"Phase 6 — Futu adapter (v0.6.0)" subsection pointing to spec §6.4 invariants**

- [ ] **Step 4: Memory `phase6_futu_topology.md`**

```markdown
---
name: Phase 6 Futu sidecar topology (v0.6.0)
description: Single Futu sidecar at 10.10.0.2:18005 (label "futu"); Configure RPC for app_secrets-driven creds; HK only.
type: project
---

Sidecar lives in `sidecar-futu/` (separate Python package). Port 18005, label "futu", broker_id "futu". Same mTLS triple as IBKR. Configure RPC ships unlock_pwd_md5 + RSA priv key from app_secrets; sidecar caches in memory and uses for OpenD InitConnect + unlock_trade. Health.started_at + Health.broker_id added to detect sidecar restarts and label mismatches. Modify/bracket return UNIMPLEMENTED (Phase 7).
```

- [ ] **Step 5: Memory `reference_futu_api_docs.md`**

```markdown
---
name: futu-api documentation
description: Canonical futu-api docs at openapi.futunn.com — consult before guessing API behavior.
type: reference
---

https://openapi.futunn.com/futu-api-doc/en/

Sections relevant for the Phase 6 sidecar:
- Trade API → OpenSecTradeContext (place_order, cancel_order, get_acc_list, position_list_query, order_list_query, accinfo_query, modify_order, unlock_trade)
- Trade API → TradeOrderHandlerBase (callback for order updates)
- Trade API → TradeDealHandlerBase (callback for fills)
- Quote API → OpenQuoteContext (get_stock_basicinfo)
- Common → InitConnect (1024-bit RSA per memory `futu_1024_rsa_key.md`)
```

- [ ] **Step 6: MEMORY.md pointers**

```markdown
- [Phase 6 Futu sidecar topology](phase6_futu_topology.md) — single sidecar, Configure RPC, HK only
- [futu-api docs](reference_futu_api_docs.md) — canonical reference
```

- [ ] **Step 7: Commit + tag (only after USER approval at G3)**

```bash
git add CHANGELOG.md TASKS.md CLAUDE.md
git commit -m "docs(phase6): close v0.6.0 — futu hk adapter shipped"
git tag -a v0.6.0 -m "v0.6.0 — phase 6 futu hk adapter (read+place+cancel) + jp kanji fix"
git push origin main
git push origin v0.6.0
```

- [ ] **Step 8: Save memory files (use Write tool to create the files in `~/.claude/projects/-home-joseph-dashboard/memory/`)**

---

## Self-review

**Spec coverage:**
- §1 Goal → Pre-flight + all chunks
- §2 Architecture → A1, A2, A3, A4, B1, B6
- §3 Components → Each table row maps to a file in File Structure + Tasks
- §4 Data flow → §4.1 (BrokerDiscoverer) covered by D1+D2+E3; §4.2 (Configure lifespan) covered by D2+D3; §4.3 (Place + OrderEvent) covered by C5+C6+C8
- §5 Status mapping → C3 (full table test)
- §6 Edge cases & invariants → 6.1 reconnect+unlock = B4+C4; 6.2 timezone = B5; 6.3 unknown = B5+normalize; 6.4 invariants = enforced via per-task design
- §7 Frontend → F1, F2, F3, F4, F5
- §8 Test strategy → E1-E5
- §9 Chunks → A-G map exactly
- §13 Architect findings:
  - H1 (Configure response.ok gate) → B3 + D2
  - H2 (sidecar-restart re-Configure) → B2 + D2 + E4
  - H3 (Configure-during-reconnect race) → B3 + D3
  - H4 (SIDECAR_BROKERS typo defense) → A1 + A4 + D1
  - H5 (pre-subscribe drop) → C8
  - M1 (CBBC enum) → A1 + A3 + C2
  - M2 (multi-market account_number policy) → invariants in spec §6.4#2; not impl-blocking in Phase 6
  - M3 (Configure validation-only) → B3
  - M4 (single-worker invariant) → CLAUDE.md update at G4
  - M5 (FakeBrokerServicer refactor) → E1
  - M6 (JP kanji split) → F1 + F2

**Placeholder scan:** Searched the doc for "TBD", "implement later", "fill in details", "similar to". The only matches are the legitimate "implemented in B4" / "implemented in C7" cross-references between tasks; each referenced task contains the actual code.

**Type consistency:** Function names match across tasks: `account_from_futu_row`, `position_from_futu_row`, `order_from_futu_row`, `contract_from_futu_row`, `summary_from_futu_row`, `status_from_futu_status`, `status_string_from_futu`, `_init_attempt`, `_init_loop`, `_on_order_update`, `_on_deal_update`, `make_sim_id`, `synthetic_place_event`, `synthetic_cancel_event`. Method signatures reused throughout.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-phase6-futu-adapter-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Claude Code dispatches a fresh subagent per task, applies the per-commit reviewer chain, fast iteration. Parallel-safe dispatch noted on relevant tasks.

**2. Inline Execution** — Execute tasks in this session using superpowers:executing-plans, batch execution with checkpoints for user review.

**Which approach?**
