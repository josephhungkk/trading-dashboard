# Phase 6 — Futu adapter (HK only) + CJK font polish

**Date:** 2026-04-29
**Status:** Drafted — awaiting architect review (Step 3) and user spec approval (Step 4)
**Builds on:** v0.5.7 (Phase 5 closed). The 4 IBKR sidecars at `10.10.0.2:18001-18004` plus all the Phase 5 trade infrastructure (BrokerDiscoverer, OrderEventConsumer, PendingSubmitWatchdog, /api/orders, SSE stream, /api/brokers/accounts, alerts, syrupy snapshots).

## 1. Goal

Add a single Futu adapter for HK stocks/ETFs/warrants/CBBC trading (read + place + cancel), following the same gRPC sidecar topology used for IBKR. Plus the JP kanji routing fix flagged in TASKS.md.

**Scope = (b)** in the brainstorm: HK stocks + ETFs + warrants + CBBC; read + place + cancel.
**Out of scope:** modify, bracket, options, futures, FX, crypto. Modify and bracket are deferred to Phase 7 alongside the Schwab adapter; the remaining asset classes are deferred until cross-broker symbol normalization makes adding them worthwhile.

**Enum surface designed for full coverage now** (so we don't run enum-add migrations later when Phase 7 brings Schwab + symbol normalization). Only the Phase 6 subset is implemented; the rest emit `*_UNSPECIFIED`.

## 2. Architecture overview

**Single new sidecar** at `10.10.0.2:18005`, registered as `gateway_label="futu"`. The Futu trade context inside is HK-only on day 1 (`Trd_HK` filter), with US/HKCC contexts addable later by config alone — no topology change.

**Same gRPC `Broker` service contract** as IBKR (`proto/broker/v1/broker.proto`). All existing infrastructure wires unchanged: BrokerRegistry becomes a 5-entry dict, BrokerDiscoverer fans out to 5 labels, OrderEventConsumer spawns one stream per (label, account), PendingSubmitWatchdog applies fleet-wide. No broker-specific branches in any of those modules — all broker-specific logic lives in the sidecar.

**Same mTLS triple as the IBKR sidecars.** All sidecars run on the same NUC; one cert/CA/CRL covers all. The new sidecar's gRPC server reads its server cert/key from local files at `C:\dashboard\secrets\` (the only allowed local-secret exception, same circular-bootstrap rationale as IBKR).

**Proto delta — additive:**

```protobuf
service Broker {
  // existing 12 RPCs unchanged
  rpc Configure(ConfigureRequest) returns (ConfigureResponse);  // NEW
}

message ConfigureRequest {
  string unlock_pwd_md5 = 1;
  // PEM-encoded RSA private key (1024-bit per Futu requirement); ~1.2KB, well
  // under gRPC's default 4MB message limit. ASCII PEM, so `string` is correct
  // (not `bytes`).
  string rsa_priv_pem = 2;
  string opend_host = 3;
  int32 opend_port = 4;
  string connection_id = 5;
  map<string, string> metadata = 6;  // future creds without proto edits
}

message ConfigureResponse {
  bool ok = 1;
  string detail = 2;  // human-readable error or empty
}

// Health response gains TWO new fields (additive, default values preserve
// backward compat for existing IBKR sidecars):
message HealthResponse {
  // existing fields unchanged
  google.protobuf.Timestamp started_at = 5;  // NEW (H2): sidecar process start time
  string broker_id = 6;                       // NEW (H4): "ibkr" | "futu" | "schwab"
}
```

`AssetClass` enum extension (M1):
```protobuf
enum AssetClass {
  // existing values unchanged
  CBBC = 10;  // HK callable bull/bear contract (牛熊證)
}
```

IBKR sidecars implement `Configure` as a no-op-returning-`ok=true` so the proto stays universal. They also populate `Health.started_at` (process boot time) and `Health.broker_id="ibkr"`.

**OpenD itself stays operator-managed.** Windows service or scheduled task, identical to IBKR Gateway lifecycle. Sidecar tolerates OpenD-down: gRPC server answers `Health(gateway_connected=false)`, retries `InitConnect` in a background loop, doesn't crash.

**Reuse from `Dashboard_old/`:** `backend/app/brokers/futu.py` (629 lines, sync in-process) is the *reference* for futu-api invocation idioms (which markets, which params, edge cases) but the code shape can't be lifted wholesale — old shape is in-process; new shape is a gRPC sidecar exposing the same `Broker` service. `restart-futu.ps1` (34 lines) ports cleanly.

## 3. Components

| Path | Purpose |
|---|---|
| `sidecar-futu/futu_sidecar.py` | gRPC server entrypoint. Loads server cert/key/CRL/CA from local files, starts mTLS-required server, runs forever. |
| `sidecar-futu/handlers.py` | Implements `Broker` service (Health, ListManagedAccounts, GetAccountSummary, GetPositions, GetOrders, GetContract, PlaceOrder, CancelOrder, OrderEvent, SearchContracts, **Configure**). `ModifyOrder`/`PlaceBracket` return `UNIMPLEMENTED`. |
| `sidecar-futu/futu_client.py` | Owns `OpenSecTradeContext` + reconnect loop. Holds creds (set via Configure). Wraps every futu-api call in `asyncio.to_thread`. |
| `sidecar-futu/normalize.py` | Proto↔futu-api mapping. Owns full `AssetClass` enum surface; only STOCK/ETF/WARRANT/CBBC mappings populated. |
| `sidecar-futu/sim.py` | `--simulator` branch (default ON). Per-account `_order_event_queues` for direct dispatch, mirroring v0.5.5 SIM fix. |
| `sidecar-futu/pyproject.toml`, `uv.lock` | Separate package: `futu-api` + `grpcio` + `cryptography`. |
| `sidecar-futu/scripts/build-windows.ps1` | PyInstaller → `dist-staging-futu/futu-sidecar.exe`. |
| `proto/broker/v1/broker.proto` (extension) | New `Configure` RPC. |
| `backend/app/services/broker_registry_factory.py` (extension) | `SIDECAR_PORTS["futu"]=18005`. New `SIDECAR_BROKERS={"isa-live":"ibkr",...,"futu":"futu"}`. After registry build, calls Configure on the futu sidecar with creds from `app_secrets`. |
| `backend/app/api/contracts.py` (extension) | `?broker=ibkr\|futu` query param routes search to right sidecar. |
| `frontend/src/styles/global.css` (extension) | JP kanji fix: rename JP face to `"Noto Sans JP"`, add `[lang\|="ja"]` CSS rule. |
| `frontend/public/fonts/NotoSansCJK-JP-400.subset.woff2` (regenerated) | Re-subset to include U+4E00–9FFF (CJK Unified Ideographs) on top of existing hiragana/katakana. |
| `frontend/src/features/orders/ContractSearchInput.tsx` | Passes `?broker=` derived from active account's `broker_id`. |
| `frontend/src/features/orders/TradeTicketModal.tsx` | Field-disable for warrants/CBBC stop-limit (Futu doesn't support). |
| `deploy/nuc/restart-futu-sidecar.ps1` | Operator helper. |
| `deploy/nuc/build-windows-futu.ps1` | Build + sign + stage `futu-sidecar.exe`. |

## 4. Data flow

### 4.1 Discovery (read path)

```
BrokerDiscoverer (every 30s)
  └─ for label in {isa-live, isa-paper, normal-live, normal-paper, futu}:
       └─ asyncio.gather(
            client[label].list_managed_accounts() → upsert broker_accounts
                                                    (broker_id from SIDECAR_BROKERS[label]),
            client[label].get_account_summary(acc) → update last_nlv* per account,
            client[label].get_positions(acc) → upsert positions,
          )
```

Futu sidecar's `ListManagedAccounts` calls `OpenSecTradeContext.get_acc_list()`. Each row maps to one proto `Account`:
- `account_number = str(acc_id)` (Futu's numeric ID as string)
- `mode = LIVE` if `trd_env=REAL` else `PAPER`
- `gateway_label = "futu"` (constant)
- `currency_base = ""` (BASE-tag refresh deferred to Phase 7)
- `acc_type` ignored (not on the wire; logged for diagnostics)

The discoverer's existing soft-delete + resurrect logic (only deletes rows whose `last_seen_via` matches healthy labels; resurrect path nulls cached NLV) applies unchanged.

### 4.2 Configure RPC at startup

**Configure validates only — does NOT do synchronous InitConnect (M3).** Sidecar parses the RSA PEM (`cryptography.hazmat.primitives.serialization.load_pem_private_key`), asserts unlock_pwd_md5 matches `^[0-9a-fA-F]{32}$`, caches the values atomically, returns `ok=true` on validation pass. Returns `ok=false` with `detail="invalid_rsa_pem" | "invalid_unlock_pwd_md5"` on fail. The InitConnect → unlock_trade → register-handlers chain runs as a background task whose progress is observable via `Health.gateway_connected`. Configure is cheap (~ms), so rotation is cheap.

```
Backend lifespan:
  1. build_broker_registry() → 5 BrokerSidecarClients
  2. For each label requiring Configure (currently just "futu"):
       creds_md5 = await config_service.reveal_secret("broker", f"{label}.unlock_pwd_md5")
       rsa_pem  = await config_service.reveal_secret("broker", f"{label}.rsa_priv_pem")
       host     = await config_service.get("broker", f"{label}.opend_host", default="127.0.0.1")
       port     = await config_service.get_int("broker", f"{label}.opend_port", default=11111)
       conn_id  = await config_service.get("broker", f"{label}.connection_id", default="")
       resp = await registry.get_client(label).configure(creds_md5, rsa_pem, host, port, conn_id)
       if resp.ok:
           # H1: only mark configured on response.ok
           # H2: track per (label, sidecar.started_at) so a sidecar restart re-Configures
           registry._configured[label] = current_health_started_at
       else:
           log.error("broker_configure_rejected", label=label, detail=resp.detail)
           # NOT added to _configured; health-probe loop will retry next tick
  3. Start consumer + watchdog (existing).
```

**Health-probe loop integration (H1 + H2):** `BrokerRegistry._configured` is `dict[str, datetime]` (label → sidecar's `Health.started_at` at the time Configure last succeeded). On each successful health probe:

```
if (label not in registry._configured
    or registry._configured[label] != health.started_at):
    # Sidecar restarted, or never configured. Re-Configure.
    re_configure(label)
```

This handles the case where the Windows watchdog respawns the sidecar mid-run — the new sidecar process has a different `started_at`, the cached entry mismatches, and Configure fires again.

**Configure-during-reconnect race (H3):** If Configure arrives while a previous `_init_task` is running (re-Configure for cred rotation), the sidecar:
1. Caches new creds atomically (single assignment).
2. If `self._init_task is not None and not self._init_task.done()`: `self._init_task.cancel(); await self._init_task` (suppress CancelledError).
3. Spawns a new `_init_task` with the fresh creds.

This guarantees only one InitConnect-to-OpenD attempt is ever in flight; OpenD never sees concurrent `InitConnect` from the same client.

**Cred rotation flow:** Operator updates `broker.futu.unlock_pwd_md5` via `POST /api/admin/secrets`. To apply without backend restart, operator hits the new `POST /api/admin/brokers/{label}/reconfigure` endpoint (Phase 6 scope, in `app/api/admin.py`), which forces backend to pull current secrets and re-Configure the sidecar. Without this endpoint, rotation requires backend restart.

### 4.3 Place order (write path)

```
Frontend → POST /api/orders (with broker_id="futu" account)
  → orders_service.place_order(account_id=..., contract=..., side=..., qty=...)
    → registry.get_client("futu").place_order(broker_pb2.PlaceOrderRequest)
      → futu sidecar handler → OpenSecTradeContext.place_order(...)
      → returns broker_order_id (Futu's order_id) + status
    → orders.row INSERT (status=pending_submit)
  → returns OrderResponse

Side channel (already running):
  OrderEventConsumer.stream_for("futu", account_number)
    → futu sidecar handlers.OrderEvent (gRPC stream)
      → TradeOrderHandlerBase callback → kind="status"  OrderEventMessage → queue
      → TradeDealHandlerBase callback  → kind="exec_details" + kind="commission_report"
        OrderEventMessage → queue
      → consumer applies state machine, UPDATEs orders, publishes SSE
```

Futu's `TradeOrderHandlerBase.on_recv_rsp` and `TradeDealHandlerBase.on_recv_rsp` callbacks fire on the futu-api SDK's internal threads. Sidecar bridges them into per-stream `asyncio.Queue`s (same pattern as v0.5.5 SIM dispatch fix — direct queue dispatch avoids cross-loop event-emit bugs).

**Pre-subscribe callbacks are dropped, not buffered (H5).** Sidecar's `_order_event_queues[account_number]: list[asyncio.Queue]` is empty until at least one consumer subscribes via the `OrderEvent` gRPC stream. Callbacks that fire before then have no queue to dispatch into and are silently discarded. The `OrderEventConsumer.reconcile_at_startup()` (existing Phase 5b infra) calls `client.get_orders(account_number)` to snapshot broker-side state and synthesize events for any state-drift relative to local DB — this catches missed pre-subscribe events. **Sidecar must NOT introduce an unbounded pre-subscribe buffer.**

## 5. Order status mapping

Futu `OrderStatus` enum → internal status (compatible with `order_status_rank()` SQL function from 5c CRIT-1):

| Futu status (numeric) | Internal status | rank |
|---|---|---|
| `UNSUBMITTED` (0) | `pending_submit` | 0 |
| `SUBMITTING` (3) | `pending_submit` | 0 |
| `WAITING_SUBMIT` (4) | `submitted` | 1 |
| `SUBMITTED` (5) | `submitted` | 1 |
| `FILLED_PART` (10) | `partial` | 3 |
| `FILLED_ALL` (11) | `filled` | 4 |
| `CANCELLED_PART` (14) | `cancelled` | 5 |
| `CANCELLED_ALL` (15) | `cancelled` | 5 |
| `FAILED` (21) | `rejected` | 5 |
| `DISABLED` (22) | `rejected` | 5 |
| `DELETED` (23) | `expired` | 5 |

The state machine in `OrderEventConsumer._process_event` already enforces `order_status_rank(new) > order_status_rank(current)` — so out-of-order events from Futu (rare but observed; Futu sometimes emits `SUBMITTING` after `SUBMITTED` on reconnect) can't revert state. No code change needed in the consumer.

## 6. Edge cases & invariants

### 6.1 OpenD reconnect + unlock

- Sidecar detects OpenD-down via futu-api `ConnLink` callback or polling failure.
- `Health.gateway_connected` flips to `false` (registry sees label as degraded; `/api/brokers/accounts` flips Futu rows to `connected=false`; tray turns yellow).
- Background reconnect task: 1s/2s/4s/.../30s exponential backoff, capped at 30s.
- On reconnect: re-`InitConnect` with cached RSA private key + connection ID, re-`unlock_trade` with cached MD5 password, re-register `TradeOrderHandlerBase` + `TradeDealHandlerBase`.
- Backend's `OrderEventConsumer.reconcile_at_startup`-style logic handles missed events on the next supervisor iteration (existing infra).

`unlock_trade(unlock_password_md5)` is called per `OpenSecTradeContext` instance — but the password itself is per-Futu-account (one trading password per acc_id, used across all market filters). Future US/HKCC additions reuse the same cached MD5 and call `unlock_trade` separately on each new context (L1).

`order_list_query` reconcile coverage: Futu's `order_list_query` returns same-day orders by default (per docs). Orders stuck >24h in `pending_submit` get force-progressed to `rejected` by the existing 5-min watchdog timer (5b mechanism). Same long-tail behavior as IBKR (L8).

If `Configure` was never called (sidecar started before backend), reconnect fails fast with `Health.gateway_connected=false` until the backend boots and calls `Configure`. Nothing crashes.

### 6.2 Timezone normalization

Futu returns `create_time` / `updated_time` as **HK-local strings** (UTC+8). Sidecar's `normalize.py` converts to UTC `Timestamp` proto before emitting any wire message. Test coverage: parse `"2026-04-29 14:30:00"` → assert UTC timestamp is `2026-04-29 06:30:00Z`.

### 6.3 Unknown enum values

Any unrecognized `SecurityType` or `OrderStatus` from futu-api maps to `*_UNSPECIFIED` and increments `broker_normalize_unknown_total{label="futu", field="..."}` Prometheus counter. WARN log with the raw value. Doesn't block the request; the row reaches the wire with `ASSET_UNSPECIFIED` and the frontend renders a placeholder.

Same handling for `trd_env` (L3): if `trd_env not in {REAL, SIMULATE}`, increment `broker_normalize_unknown_total{label="futu", field="trd_env"}` and skip the account row (don't upsert to `broker_accounts`). Discoverer's existing soft-delete logic does NOT remove the row (`last_seen_via` filter requires the label to be healthy AND have reported the row this tick).

### 6.4 Architectural invariants

1. **Single sidecar per broker family.** Flat dict keyed by `gateway_label` — never nest by broker_id.
2. **`gateway_label = "futu"`** is the constant for this single Futu sidecar; future US/HKCC contexts ride the same label (same sidecar, additional internal contexts). **One `broker_accounts` row per Futu acc_id**, regardless of how many markets the account supports (M2). Multi-market metadata is OUT OF SCOPE for Phase 6 — when US/HKCC contexts ship later they add a `markets: list[Trd_*]` metadata field via Alembic migration. Phase 6 implementations must NOT introduce a per-(account, market) row pattern.
3. **`broker_id` set by BACKEND at upsert time, not by sidecar.** New `SIDECAR_BROKERS` dict alongside `SIDECAR_PORTS`. Keeps proto broker-agnostic. **Cross-checked at every health probe (H4):** `BrokerRegistry._probe_client` asserts `health.broker_id == SIDECAR_BROKERS[label]`. Mismatch → log CRITICAL, mark label degraded, increment `broker_registry_label_mismatch_total{label}` Prometheus counter, fire `BrokerLabelMismatch` page alert.
4. **Configure RPC is idempotent + validation-only + retried-on-(health-recovery OR sidecar-restart).** Configure validates inputs and caches creds; InitConnect runs in a background task. The registry tracks `_configured: dict[str, datetime]` (label → sidecar's `Health.started_at`) so a sidecar restart re-Configures (H2). `_configured.add` is gated on `ConfigureResponse.ok` (H1). Configure-while-in-flight cancels the previous `_init_task` before spawning a new one (H3). Cred rotation flow: `POST /api/admin/brokers/{label}/reconfigure` triggers a re-pull from `app_secrets` and a fresh Configure call (Phase 6 scope).
5. **All Futu credentials flow through `app_secrets` + Configure RPC.** Zero new local secrets, zero new `.env` entries. Local exception only for the gRPC server cert/key/CA/CRL (genuine bootstrap circularity).
6. **Symbol format is broker-native, end-to-end, in Phase 6.** `HK.00700` stays `HK.00700`. Cross-broker symbol normalization is an explicit Phase 7 deliverable. Historical Futu orders carry the `HK.XXXXX` format permanently; Phase 7 normalization must reverse-map for unified queries, not retroactively rewrite `orders.contract_symbol` (L7).
7. **`OrderEventConsumer`, `PendingSubmitWatchdog`, `AccountService`, `BrokerDiscoverer`, `/api/brokers/accounts`, `/api/orders`, cancel — all stay broker-agnostic.** Broker-specific logic lives in `sidecar-futu/normalize.py`.
8. **`order_status_rank()` SQL function (5c CRIT-1) handles Futu state machine transitions.** No new internal statuses in Phase 6.
9. **`--simulator` flag default = ON.** Real Futu order placement requires explicit `--no-simulator`, paralleling the IBKR pattern from v0.5.5.
10. **`futu-api` is sync-only — every call wrapped in `asyncio.to_thread`.** Direct futu-api calls from gRPC handlers are forbidden (would deadlock the asyncio event loop).
11. **Sidecar callbacks fired before any consumer subscribes are dropped, not buffered (H5).** Consumer's `reconcile_at_startup` snapshot via `client.get_orders()` covers the gap. Sidecars must NOT introduce an unbounded pre-subscribe buffer.
12. **Single-worker uvicorn is load-bearing (M4).** The registry's `_configured` dict, `_MODIFY_REPLAY_CACHE` (5c), `_COMMISSION_BUFFER` (5c), and SSE per-client queues all assume one process. Multi-worker requires moving these to Redis (deferred to Phase 9). CI enforces single-worker via `pgrep` assertion on the entrypoint.

## 7. Frontend changes

### 7.1 JP kanji routing fix (deferred TASKS.md item)

Today: TC face's `unicode-range` covers U+4E00–9FFF (CJK Unified Ideographs), so all kanji — Chinese AND Japanese — render from TC glyphs. The JP face only covers hiragana/katakana (U+3040–30FF).

Fix — **two `@font-face` declarations under the same `font-family: "Noto Sans JP"` family** so the kanji file loads lazily only when JP-content rendering actually hits the U+4E00–9FFF range (M6):

```css
/* Hiragana / katakana / kana-extension — small (~50KB), loads first */
@font-face {
  font-family: "Noto Sans JP";
  font-weight: 400;
  font-display: swap;
  src: url("/fonts/NotoSansJP-kana-400.woff2") format("woff2");
  unicode-range: U+3040-309F, U+30A0-30FF, U+31F0-31FF;
}

/* CJK Unified Ideographs — kanji-only (~1-2MB), browser fetches lazily
   when content in this unicode range actually renders */
@font-face {
  font-family: "Noto Sans JP";
  font-weight: 400;
  font-display: swap;
  src: url("/fonts/NotoSansJP-kanji-400.woff2") format("woff2");
  unicode-range: U+4E00-9FFF, U+3400-4DBF, U+F900-FAFF;
}
```

Plus the routing rule: `[lang|="ja"] { font-family: "Noto Sans JP", "Noto Sans", system-ui, sans-serif; }`. The `[lang|="ja"]` matches both `lang="ja"` and `lang="ja-JP"`.

Storybook visual story `frontend/src/components/primitives/Text/CJKText.stories.tsx` renders the same kanji string under `lang="ja"` / `zh-TW` / `zh-CN` for inspectable diff.

**Subset pipeline (operator-side, one-time):**
```bash
# From the source NotoSansJP-Regular.otf (Google Fonts, public):
pyftsubset NotoSansJP-Regular.otf \
  --output-file=NotoSansJP-kana-400.woff2 \
  --flavor=woff2 \
  --unicodes=U+3040-309F,U+30A0-30FF,U+31F0-31FF

pyftsubset NotoSansJP-Regular.otf \
  --output-file=NotoSansJP-kanji-400.woff2 \
  --flavor=woff2 \
  --unicodes=U+4E00-9FFF,U+3400-4DBF,U+F900-FAFF
```

Resulting woff2 files are checked into `frontend/public/fonts/`. Provenance + regeneration command lives in `frontend/public/fonts/README.md`.

### 7.2 `/api/contracts/search` becomes broker-aware

`ContractSearchInput.tsx` reads active account's `broker_id` (already in store via `useActiveStores()`) and passes `?broker=ibkr|futu` to `/api/contracts/search`. Backend route accepts `broker: Literal["ibkr","futu","schwab"]` via Pydantic Query validation; rejects anything else with 422 (L4). `?broker=schwab` returns 503 with `Retry-After` until Schwab ships. Param missing → defaults to legacy "first healthy" behavior so the route stays backward-compatible. ~40 lines total backend + frontend.

### 7.3 TradeTicketModal field-disable

Field-disable map (the `mode` prop landed in 5c) extends: HK warrants/CBBC don't support stop-limit orders, so `Stop-Limit` button is disabled when `accountBroker==='futu' && (assetClass==='WARRANT' || assetClass==='CBBC')`. Same pattern, one more case.

### 7.4 No changes elsewhere

OrdersPage, FillsTable, AccountPicker — all consume `OrderResponse` / `FillResponse` / `AccountResponse` shapes that don't differ by broker. The `broker_id` discriminator is already on the wire (5b). Active-broker filter in OrdersPage works against it as-is.

## 8. Test strategy

- **Backend unit tests** — parametrize existing `test_brokers.py` / `test_account_service.py` / `test_order_event_consumer.py` / `test_pending_submit_watchdog.py` to cover `label="futu"`. New `test_brokers_futu.py` for Configure RPC retry semantics.
- **Sidecar unit tests** — `sidecar-futu/tests/`. Mock `OpenSecTradeContext` via `MagicMock`. Critical paths: Configure idempotency, lazy `InitConnect` on first non-Health request, `place_order` arg translation, `OrderEvent` callback bridging, SIM dispatch, status mapping table, timezone normalization.
- **Sidecar contract tests** — `sidecar-futu/tests/test_handlers_futu_contract.py`, mirrors `sidecar/tests/test_handlers_orders_contract.py`. Spin real grpc server with sidecar handlers, mock futu-api at the boundary, verify wire shape.
- **Mock E2E** — refactor `backend/tests/fixtures/sidecar_servicer.py::FakeBrokerServicer` to be **broker-agnostic** (label parameter routes to per-broker response data factories). New `backend/tests/fixtures/futu_test_data.py` provides HK stock/ETF/warrant/CBBC fixtures with Futu-shape conids, numeric account_numbers, Futu OrderStatus values pre-translated. Status mapping table on the servicer side ensures Futu's `FILLED_ALL` reaches the wire as `filled` (M5). New `backend/tests/integration/test_e2e_futu_chain.py` — preview→place→cancel for each asset class (stocks, ETFs, warrants, CBBC). Runs in `.github/workflows/e2e-mock.yml` on every push.
- **Real-Futu nightly** — DEFERRED to Phase 7. Phase 6 ships mock-E2E only.
- **OpenAPI snapshot lock** — no new HTTP wire shapes. `Configure` is proto-only. The existing `broker_id` Literal (`"ibkr"|"futu"|"schwab"`) covers Futu rows.
- **Frontend tests** — Topbar + AppShell tests already include futu fixtures; verify they keep passing. Add JSDOM-based test asserting `[lang|="ja"]` CSS resolves to `"Noto Sans JP"` font-family.
- **Pester** — none new. `restart-futu-sidecar.ps1` is short enough to skip.

## 9. Chunks (implementation outline)

| Chunk | Scope | Estimate (FT days) |
|---|---|---|
| **A — Proto + wiring shells** | Configure RPC + Health.broker_id + Health.started_at + AssetClass.CBBC + codegen regen + `SIDECAR_BROKERS` dict + label-mismatch alert + app_secrets/app_config seed runbook | 0.5 |
| **B — Sidecar core** | sidecar-futu skeleton + Health (with broker_id + started_at) + Configure (validation-only, in-flight-task cancellation, ok=true gate) + ListManagedAccounts + futu_client + normalize | 1.75 |
| **C — Sidecar read+trade** | Read RPCs + place/cancel + status mapping + OrderEvent stream (drop-pre-subscribe semantics) + SIM mode + timezone normalizer | 3.0 |
| **D — Backend service updates** | Broker-aware contracts.py (Pydantic Literal validation) + SIDECAR_BROKERS-with-mismatch-cross-check + `_configured` dict per (label, started_at) + reconfigure admin endpoint + parametrize tests | 0.75 |
| **E — Tests** | Refactor FakeBrokerServicer broker-agnostic + futu_test_data fixtures + sidecar tests + integration E2E + reconfigure-cycle test | 2.0 |
| **F — Frontend** | JP kanji subset (TWO faces, lazy kanji) + lang CSS + ContractSearchInput broker param (with Schwab=503 stub) + TradeTicketModal field-disable | 1.25 |
| **G — Ops + close-out** | NUC ops scripts + Defender exclusion glob extension + Prometheus metrics+alerts (incl. BrokerLabelMismatch) + operator runbook (RSA gen + OpenD config + app_secrets seed) + USER GATE deploy verify + tag v0.6.0 | 1.25 |
| **Total** | 7 chunks | **~10.5 FT days** |

Plus architect-review pass (~30 min) before plan generation.

## 10. References

- IBKR sidecar topology — memory `phase4_sidecar_topology.md` (mirror this for `phase6_futu_topology.md` post-implementation).
- futu-api documentation — **canonical**: `https://openapi.futunn.com/futu-api-doc/en/`. Save to memory `reference_futu_api_docs.md` (parallel to existing `reference_tws_api_docs.md`).
- 1024-bit RSA requirement — memory `futu_1024_rsa_key.md` (2048-bit fails InitConnect; saved from old Dashboard debugging session).
- Phase 5b/c order infrastructure — memories `phase5b_shipped.md`, `phase5c_shipped.md` (state machine, OrderEventConsumer, PendingSubmitWatchdog, fills/pending_fills, `order_status_rank()` SQL function).
- Reuse reference — `/mnt/c/Dashboard_old/backend/app/brokers/futu.py` (629 lines, sync in-process Futu adapter from the abandoned v1 codebase).

## 11. Architect-review focus areas

When the spec is run through `ARCHITECT-REVIEW` skill, these are the questions worth challenging adversarially:

1. **Configure RPC retry semantics.** The registry tracks `_configured: set[str]`; on health-probe success, calls Configure if not yet configured. What if Configure succeeds but the sidecar's subsequent OpenD `InitConnect` fails? Is there a "configured but not connected" intermediate state that the registry needs to distinguish from "configured and healthy"? Suggested resolution: split `_configured` (Backend has shipped creds) from `Health.gateway_connected` (sidecar has reached OpenD).

2. **`broker_id` collision risk.** Backend sets `broker_id` at upsert time via `SIDECAR_BROKERS[label]`. What if two different sidecars report the same `account_number` for different broker_ids? Today the unique key on `broker_accounts` is `(broker_id, account_number)`. Confirm Alembic schema supports this.

3. **SIM mode default-ON for Futu.** IBKR's `--simulator` defaults ON. For Futu, defaulting ON means an operator needs `--no-simulator` to actually trade. Is this safe given Futu's `trd_env=SIMULATE` already provides a paper-trading mode? Two layers of simulation might be confusing.

4. **OpenD reconnect race with active orders.** If OpenD drops mid-`PlaceOrder` request, the sidecar gets a Python exception. The order may or may not have hit Futu's servers. The HTTP client (orders_service) sees a 5xx; the orders row stays in `pending_submit`. PendingSubmitWatchdog then scans Futu's order list on reconnect. Confirm the reconcile path works for Futu (Futu's `OpenSecTradeContext.order_list_query()` returns a snapshot the watchdog can use).

5. **Multi-context unlock state.** A single OpenD process serving multiple market filters (HK, US, HKCC) — does `unlock_trade()` apply per-context or per-process? If per-context, future US/HKCC additions need separate unlock calls — `Configure` must accept multiple unlock passwords, or the sidecar must hold one per market. Verify against futu-api docs.

6. **PyInstaller binary size.** The IBKR sidecar binary is ~80MB after PyInstaller freeze. `futu-api` plus its deps will add another ~30-50MB. Confirm the NUC has the disk + the Windows Defender exclusion already covers `C:\dashboard\dist-staging-*` (per existing IBKR setup).

7. **Cross-broker symbol normalization deferred to Phase 7.** Confirm Phase 6 doesn't accidentally introduce `HK.00700` → `00700` translation anywhere (especially in `ContractSearchInput` typeahead behavior). The frontend's saved-symbol persistence must not cross-pollinate Futu and IBKR symbols.

8. **JP kanji woff2 size budget.** Re-subsetting `NotoSansCJK-JP-400.subset.woff2` to include U+4E00–9FFF roughly doubles the file. Verify the resulting file is acceptable as a `font-display: swap` resource on first JP-content render; consider whether to shard into a kanji-only secondary face loaded after the hiragana/katakana primary.

## 12. Open questions for architect review (none expected)

All major scope/topology questions resolved during brainstorm Q1–Q4. Implementation details (exact file layout under `sidecar-futu/`, Prometheus metric naming) belong to the plan stage.

## 13. Architect review — applied

Run on `d97a194` (initial spec commit) by the user-scope `ARCHITECT-REVIEW` skill, 2026-04-29. **0 CRITICAL, 5 HIGH, 6 MEDIUM, 8 LOW.** All HIGH + MEDIUM applied inline below; LOWs documented (one-line clarifications applied at the same edit pass per CLAUDE.md project memory `feedback_architect_findings_apply_through_medium.md`).

| ID | Tier | Topic | Resolution |
|---|---|---|---|
| H1 | HIGH | `_configured.add` ungated by `ConfigureResponse.ok` | §4.2 lifespan pseudocode now gates the registry update on `resp.ok`; rejection logs and retries on next health probe. Captured in invariant 6.4#4. |
| H2 | HIGH | Sidecar restart leaves it permanently unconfigured | `Health.started_at` field added to proto. Registry tracks `_configured: dict[str, datetime]` keyed by sidecar's `started_at`; mismatch triggers re-Configure. §4.2 + invariant 6.4#4. |
| H3 | HIGH | Configure-during-reconnect race on in-flight `_init_task` | §4.2 specifies cancel-and-await semantics: Configure caches new creds atomically, cancels any previous `_init_task`, spawns fresh one. Invariant 6.4#4. |
| H4 | HIGH | SIDECAR_BROKERS typo undetectable; backend lies on the wire | `Health.broker_id` field added to proto; `BrokerRegistry._probe_client` cross-checks against `SIDECAR_BROKERS[label]`; mismatch → CRITICAL log + `broker_registry_label_mismatch_total` counter + `BrokerLabelMismatch` page alert. Invariant 6.4#3. |
| H5 | HIGH | Pre-subscribe Futu callbacks: drop vs buffer unspecified | §4.3 explicitly states drop-not-buffer; reconcile snapshot covers gap. New invariant 6.4#11. |
| M1 | MEDIUM | `AssetClass` enum lacks `CBBC` | §2 Proto delta adds `AssetClass.CBBC = 10`. Chunk A scope updated. |
| M2 | MEDIUM | Future multi-market account collisions | Invariant 6.4#2 expanded: one row per acc_id, multi-market metadata deferred to a later phase via Alembic migration; Phase 6 must NOT introduce per-(account, market) row pattern. |
| M3 | MEDIUM | ConfigureResponse semantics ambiguous | §4.2 explicitly: Configure validates only (PEM parse + MD5 regex); InitConnect runs in background; `ok=true` does NOT mean OpenD reachable. |
| M4 | MEDIUM | Single-worker uvicorn assumption not in invariants | New invariant 6.4#12 — explicit. |
| M5 | MEDIUM | Mock servicer refactor scope underestimated | §8 + Chunk E expanded: refactor FakeBrokerServicer broker-agnostic + new `futu_test_data.py` fixture + status-translation table on servicer side. Chunk E bumped 1.5 → 2.0 FT days. |
| M6 | MEDIUM | JP kanji woff2 doubles file size | §7.1 splits into TWO `@font-face` declarations under same family — kana primary (~50KB), kanji-only secondary (~1-2MB) lazy-loaded by browser via unicode-range matching. |
| L1 | LOW | unlock_trade per-context vs per-process | One-line note in §6.1. |
| L2 | LOW | gRPC string vs bytes for RSA priv | One-line proto comment in §2. |
| L3 | LOW | trd_env unknown value handling | §6.3 extension. |
| L4 | LOW | Frontend `?broker=` validation | §7.2 specifies Pydantic `Literal` + 422/503 behavior. |
| L5 | LOW | PyInstaller binary size + Defender exclusion | Chunk G G1 covers `dist-staging-*` glob extension. |
| L6 | LOW | Operator runbook complexity | Chunk G G3 explicit; total estimate bumped to reflect (1.0 → 1.25 FT day). |
| L7 | LOW | Symbol format historical | Invariant 6.4#6 forward-pointer added. |
| L8 | LOW | order_list_query reconcile coverage | §6.1 note added. |

Net total estimate change: 9.0 → 10.5 FT days (+1.5 days for the HIGH+MEDIUM scope expansions, primarily mock-servicer refactor M5 and split-woff2 M6 + new admin reconfigure endpoint H3).
