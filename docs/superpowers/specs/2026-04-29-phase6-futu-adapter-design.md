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
```

IBKR sidecars implement `Configure` as a no-op-returning-OK so the proto stays universal.

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

```
Backend lifespan:
  1. build_broker_registry() → 5 BrokerSidecarClients
  2. For each label requiring Configure (currently just "futu"):
       creds_md5 = await config_service.reveal_secret("broker", f"{label}.unlock_pwd_md5")
       rsa_pem  = await config_service.reveal_secret("broker", f"{label}.rsa_priv_pem")
       host     = await config_service.get("broker", f"{label}.opend_host", default="127.0.0.1")
       port     = await config_service.get_int("broker", f"{label}.opend_port", default=11111)
       conn_id  = await config_service.get("broker", f"{label}.connection_id", default="")
       await registry.get_client(label).configure(creds_md5, rsa_pem, host, port, conn_id)
       registry._configured.add(label)  # tracked per-label
  3. Start consumer + watchdog (existing).
```

Configure is idempotent. The registry's health-probe loop calls Configure if `label not in self._configured` AND health is OK — covers the case where the sidecar bounced after backend boot.

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

If `Configure` was never called (sidecar started before backend), reconnect fails fast with `Health.gateway_connected=false` until the backend boots and calls `Configure`. Nothing crashes.

### 6.2 Timezone normalization

Futu returns `create_time` / `updated_time` as **HK-local strings** (UTC+8). Sidecar's `normalize.py` converts to UTC `Timestamp` proto before emitting any wire message. Test coverage: parse `"2026-04-29 14:30:00"` → assert UTC timestamp is `2026-04-29 06:30:00Z`.

### 6.3 Unknown enum values

Any unrecognized `SecurityType` or `OrderStatus` from futu-api maps to `*_UNSPECIFIED` and increments `broker_normalize_unknown_total{label="futu", field="..."}` Prometheus counter. WARN log with the raw value. Doesn't block the request; the row reaches the wire with `ASSET_UNSPECIFIED` and the frontend renders a placeholder.

### 6.4 Architectural invariants

1. **Single sidecar per broker family.** Flat dict keyed by `gateway_label` — never nest by broker_id.
2. **`gateway_label = "futu"`** is the constant for this single Futu sidecar; future US/HKCC contexts ride the same label (same sidecar, additional internal contexts).
3. **`broker_id` set by BACKEND at upsert time, not by sidecar.** New `SIDECAR_BROKERS` dict alongside `SIDECAR_PORTS`. Keeps proto broker-agnostic.
4. **Configure RPC is idempotent + retried-on-health-recovery.**
5. **All Futu credentials flow through `app_secrets` + Configure RPC.** Zero new local secrets, zero new `.env` entries. Local exception only for the gRPC server cert/key/CA/CRL (genuine bootstrap circularity).
6. **Symbol format is broker-native, end-to-end, in Phase 6.** `HK.00700` stays `HK.00700`. Cross-broker symbol normalization is an explicit Phase 7 deliverable.
7. **`OrderEventConsumer`, `PendingSubmitWatchdog`, `AccountService`, `BrokerDiscoverer`, `/api/brokers/accounts`, `/api/orders`, cancel — all stay broker-agnostic.** Broker-specific logic lives in `sidecar-futu/normalize.py`.
8. **`order_status_rank()` SQL function (5c CRIT-1) handles Futu state machine transitions.** No new internal statuses in Phase 6.
9. **`--simulator` flag default = ON.** Real Futu order placement requires explicit `--no-simulator`, paralleling the IBKR pattern from v0.5.5.
10. **`futu-api` is sync-only — every call wrapped in `asyncio.to_thread`.** Direct futu-api calls from gRPC handlers are forbidden (would deadlock the asyncio event loop).

## 7. Frontend changes

### 7.1 JP kanji routing fix (deferred TASKS.md item)

Today: TC face's `unicode-range` covers U+4E00–9FFF (CJK Unified Ideographs), so all kanji — Chinese AND Japanese — render from TC glyphs. The JP face only covers hiragana/katakana (U+3040–30FF).

Fix:
- Rename JP `@font-face` to `font-family: "Noto Sans JP"` (separate family).
- Re-subset `NotoSansCJK-JP-400.subset.woff2` via `pyftsubset` to include U+4E00–9FFF on top of existing hiragana/katakana. ~2x file size, only loaded for `:lang(ja)` content.
- Add CSS rule: `[lang|="ja"] { font-family: "Noto Sans JP", "Noto Sans", system-ui, sans-serif; }`.
- Storybook visual story `frontend/src/components/primitives/Text/CJKText.stories.tsx` rendering the same kanji string under `lang="ja"` / `zh-TW` / `zh-CN` for inspectable diff.

### 7.2 `/api/contracts/search` becomes broker-aware

`ContractSearchInput.tsx` reads active account's `broker_id` (already in store via `useActiveStores()`) and passes `?broker=ibkr|futu` to `/api/contracts/search`. Backend routes to the right sidecar. ~30 lines total backend + frontend.

### 7.3 TradeTicketModal field-disable

Field-disable map (the `mode` prop landed in 5c) extends: HK warrants/CBBC don't support stop-limit orders, so `Stop-Limit` button is disabled when `accountBroker==='futu' && (assetClass==='WARRANT' || assetClass==='CBBC')`. Same pattern, one more case.

### 7.4 No changes elsewhere

OrdersPage, FillsTable, AccountPicker — all consume `OrderResponse` / `FillResponse` / `AccountResponse` shapes that don't differ by broker. The `broker_id` discriminator is already on the wire (5b). Active-broker filter in OrdersPage works against it as-is.

## 8. Test strategy

- **Backend unit tests** — parametrize existing `test_brokers.py` / `test_account_service.py` / `test_order_event_consumer.py` / `test_pending_submit_watchdog.py` to cover `label="futu"`. New `test_brokers_futu.py` for Configure RPC retry semantics.
- **Sidecar unit tests** — `sidecar-futu/tests/`. Mock `OpenSecTradeContext` via `MagicMock`. Critical paths: Configure idempotency, lazy `InitConnect` on first non-Health request, `place_order` arg translation, `OrderEvent` callback bridging, SIM dispatch, status mapping table, timezone normalization.
- **Sidecar contract tests** — `sidecar-futu/tests/test_handlers_futu_contract.py`, mirrors `sidecar/tests/test_handlers_orders_contract.py`. Spin real grpc server with sidecar handlers, mock futu-api at the boundary, verify wire shape.
- **Mock E2E** — extend `backend/tests/fixtures/sidecar_servicer.py::FakeBrokerServicer` with a label override so tests spin a "futu" mock servicer. New `backend/tests/integration/test_e2e_futu_chain.py` — preview→place→cancel for each asset class (stocks, ETFs, warrants, CBBC). Runs in `.github/workflows/e2e-mock.yml` on every push.
- **Real-Futu nightly** — DEFERRED to Phase 7. Phase 6 ships mock-E2E only.
- **OpenAPI snapshot lock** — no new HTTP wire shapes. `Configure` is proto-only. The existing `broker_id` Literal (`"ibkr"|"futu"|"schwab"`) covers Futu rows.
- **Frontend tests** — Topbar + AppShell tests already include futu fixtures; verify they keep passing. Add JSDOM-based test asserting `[lang|="ja"]` CSS resolves to `"Noto Sans JP"` font-family.
- **Pester** — none new. `restart-futu-sidecar.ps1` is short enough to skip.

## 9. Chunks (implementation outline)

| Chunk | Scope | Estimate (FT days) |
|---|---|---|
| **A — Proto + wiring shells** | Configure RPC + codegen regen + `SIDECAR_BROKERS` dict + app_secrets/app_config seed runbook | 0.5 |
| **B — Sidecar core** | sidecar-futu skeleton + Health/Configure/ListManagedAccounts + futu_client + normalize | 1.5 |
| **C — Sidecar read+trade** | Read RPCs + place/cancel + status mapping + OrderEvent stream + SIM mode + timezone normalizer | 3.0 |
| **D — Backend service updates** | Broker-aware contracts.py + SIDECAR_BROKERS at upsert + parametrize tests | 0.5 |
| **E — Tests** | Mock servicer extension + sidecar tests + integration E2E | 1.5 |
| **F — Frontend** | JP kanji subset regen + lang CSS + ContractSearchInput broker param + TradeTicketModal field-disable | 1.0 |
| **G — Ops + close-out** | NUC ops scripts + Prometheus alerts + operator runbook + USER GATE deploy verify + tag v0.6.0 | 1.0 |
| **Total** | 7 chunks | **~9 FT days** |

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
