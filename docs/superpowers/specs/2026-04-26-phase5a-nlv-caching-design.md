# Phase 5a — NLV caching + currency + 4.x cleanups — Design

**Tag at end:** `v0.5.0`
**Estimated duration:** ~2 weeks
**Prerequisite:** v0.4.0 shipped (broker layer + sidecars + mTLS + 22 IBKR accounts visible)
**Successor:** Phase 5b (trade execution: read + place + cancel) → Phase 5c (modify + bracket + algos)

## 1. Goal

Make the AccountPicker render real per-account NLV and currency instead of the post-Phase-4 placeholders (`0` / `"USD"`). Bundle the remaining Phase 4.x cleanups (real-IBKR test markers) so the next phase starts from a clean operational state.

Out of scope: trade execution (Phase 5b), order events (Phase 5b), bracket/algos (Phase 5c). Trade execution decisions (kill switch, audit table, confirmation modal, etc.) are deferred to the Phase 5b brainstorm.

## 2. Why

After v0.4.0 the `/api/accounts` endpoint returns 22 real IBKR accounts but every entry has `currency_base: ""` (BASE tag never cached because the per-account `reqAccountUpdates` loop hangs the IB API) and the wire shape carries no NLV at all. The frontend falls back to `nlv: 0` / `baseCurrency: 'USD'`. Visually the dashboard is "live" but every row reads the same neutral placeholder — the user can't tell ISA Live apart from Normal Paper without internal knowledge.

This phase fixes both with one mechanism: a per-account `GetAccountSummary` fan-out on every discover tick. The summary RPC's `Summary.net_liquidation` field carries both NLV (string-decimal) and the account's base currency (3-letter ISO code). One round-trip per account per 30 s populates a cache row that the existing `/api/accounts` route serves inline.

## 3. Wire shape changes

### 3.1 `AccountResponse` (backend → frontend)

Adds three fields:

```python
class AccountResponse(BaseModel):
    id: UUID
    broker_id: Literal["ibkr", "futu", "schwab"]
    alias: str | None
    mode: Literal["live", "paper"]
    currency_base: str = Field(default="", max_length=3)  # unchanged from Phase 4
    display_order: int
    # New in 5a:
    nlv: str | None                 # decimal-as-string; null = no successful refresh yet
    nlv_currency: str | None        # 3-letter ISO; null when nlv is null
    nlv_at: datetime | None         # UTC; null when nlv is null
```

The backend continues to populate `currency_base` from the same source (sidecar's response) so the field stays as a fallback for any consumer not yet aware of `nlv_currency`. New consumers should prefer `nlv_currency` because it's the authoritative source from the same summary call that produced the NLV.

### 3.2 `AccountListResponse` envelope

Grows a `broker_maintenance` field:

```python
class BrokerMaintenance(BaseModel):
    active: bool
    window: Literal["weekend", "daily"] | None
    until: datetime | None  # UTC ISO-8601; null when active=False

class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    degraded_sidecars: list[str]
    broker_maintenance: BrokerMaintenance  # New in 5a
```

Computed server-side once per request from `app/services/ibkr_maintenance.py`'s existing helpers (`in_weekend_reset`, `in_daily_reset`, `seconds_until_window_ends`). Frontend reads `broker_maintenance.active` and suppresses the staleness UI when true. Single source of truth — frontend doesn't duplicate the timezone-aware reset-window logic.

## 4. Schema — Alembic 0003

```sql
ALTER TABLE broker_accounts
  ADD COLUMN last_nlv NUMERIC(20, 8),
  ADD COLUMN last_nlv_currency CHAR(3),
  ADD COLUMN last_nlv_at TIMESTAMPTZ;
```

All three nullable. No backfill — discoverer populates on first tick after deploy. No index initially (22 rows, no query joins on these columns); revisit if `broker_accounts` grows >10k rows in a future broker (unlikely).

`last_nlv_currency` uses `CHAR(3)` to enforce ISO-3-letter-code shape at the column level; sidecar guarantees the format from the IBKR-side response.

`alembic/versions/0003_broker_accounts_nlv.py` follows the existing 0002 pattern (transactional DDL on Postgres → atomic; failure → backend doesn't start → unhealthy container → docker-compose holds nginx behind it; existing image keeps serving).

## 5. Discoverer — `_discover_once` extension

Pseudocode of the new tail of the existing `_discover_once`:

```python
async def _discover_once(self) -> None:
    healthy_clients = await self._registry.healthy_clients()
    # ... existing ListManagedAccounts upsert + soft-delete logic unchanged ...

    # 5a: per-account NLV refresh
    summary_targets = [
        (label, account.account_number)
        for label, account in rows_seen
    ]

    async def _fetch_summary(label: str, account_number: str) -> tuple[str, str, base.Summary] | None:
        client = self._registry._clients.get(label)
        if client is None:
            return None
        try:
            summary = await asyncio.wait_for(
                client.get_account_summary(account_number),
                timeout=10.0,
            )
            return (label, account_number, summary)
        except (asyncio.TimeoutError, BrokerSidecarUnavailable, BrokerSidecarTimeout):
            return None

    results = await asyncio.gather(
        *(_fetch_summary(label, acct) for (label, acct) in summary_targets),
        return_exceptions=True,
    )

    nlv_update_stmt = text(
        """
        UPDATE broker_accounts
           SET last_nlv = CAST(:nlv AS NUMERIC(20, 8)),
               last_nlv_currency = :currency,
               last_nlv_at = now(),
               updated_at = now()
         WHERE broker_id = CAST(:broker_id AS broker_id_enum)
           AND account_number = :account_number
           AND deleted_at IS NULL;
        """
    )

    nlv_update_count = 0
    async with self._session_factory() as session, session.begin():
        for r in results:
            if r is None or isinstance(r, BaseException):
                continue
            label, account_number, summary = r
            await session.execute(nlv_update_stmt, {
                "broker_id": "ibkr",
                "account_number": account_number,
                "nlv": summary.net_liquidation.value,
                "currency": summary.net_liquidation.currency,
            })
            nlv_update_count += 1

    log.info(
        "broker_discover_iteration_ok",
        upsert_count=len(rows_seen),
        soft_delete_count=soft_delete_count,
        nlv_update_count=nlv_update_count,
    )
```

Key invariants:
- **Per-call timeout 10 s** — never blocks the 30 s discover loop on one slow sidecar.
- **`return_exceptions=True`** — one failed RPC doesn't taint the others.
- **UPDATE only on success** — failed accounts retain stale `last_nlv_at` so the frontend's staleness UI flags them naturally.
- **Single transaction** — all UPDATEs commit atomically; either the tick's NLV refresh succeeds or it doesn't.
- **Skip soft-deleted rows** — `WHERE deleted_at IS NULL` guard prevents a race where a row is soft-deleted mid-tick and a stale NLV gets written.

## 6. AccountService.list_accounts — extension

The SELECT in `_AccountRow` mapping gains the three new columns:

```sql
SELECT id, broker_id, account_number, alias, mode, gateway_label,
       currency_base, display_order,
       last_nlv, last_nlv_currency, last_nlv_at
  FROM broker_accounts
 WHERE deleted_at IS NULL
 ORDER BY display_order, account_number;
```

`_account_response_from_row` adds three field assignments. `last_nlv` (Postgres `NUMERIC` → Python `Decimal`) is serialized to its string form for the wire (`str(Decimal(...))`); when null, all three new fields are null in the response.

`AccountListResponse` assembly:

```python
async def list_accounts(self) -> base.AccountListResponse:
    # ... existing rows fetch ...
    degraded = await self._registry.degraded_labels()

    now = datetime.now(UTC)
    if in_weekend_reset(now):
        maintenance = BrokerMaintenance(
            active=True, window="weekend",
            until=now + timedelta(seconds=seconds_until_window_ends(now)),
        )
    else:
        in_daily, _region = in_daily_reset(now)
        if in_daily:
            maintenance = BrokerMaintenance(
                active=True, window="daily",
                until=now + timedelta(seconds=seconds_until_window_ends(now)),
            )
        else:
            maintenance = BrokerMaintenance(active=False, window=None, until=None)

    return AccountListResponse(
        accounts=[_account_response_from_row(r) for r in rows],
        degraded_sidecars=degraded,
        broker_maintenance=maintenance,
    )
```

Note: the same maintenance-window check already exists for the detail-route 503 envelope (`_classify_sidecar_failure` in `app/api/accounts.py`). 5a doesn't refactor that — it just calls the same helpers from a new place. A small refactor to share a `_compute_maintenance_envelope()` helper between the two call sites is a polish task in the implementation plan.

## 7. Frontend — `RealAccountsService` extension

`toDisplayAccount` mapper grows two responsibilities:

1. Map `r.nlv` (string) → `Account.nlv` (number) via `safeParseDecimal(r.nlv ?? "0").display`. Lossy round-trip is acceptable for picker display; precise comparisons should still go through `safeParseDecimal(r.nlv).precise`.
2. Pick `baseCurrency` from `r.nlv_currency` first (authoritative), `r.currency_base` second, `'USD'` last (matches existing fallback). Limited to `Account['baseCurrency']` literal type (`'USD' | 'HKD' | 'GBP' | 'JPY' | 'KRW'`); unknown codes fall through to `'USD'`.

The `Account` interface stays a number for `nlv` (no breaking change to existing components); a parallel optional field `nlvAt: Date | null` is added so the staleness rule can render correctly.

`RealAccountsService.list(mode)` maps the new envelope including `broker_maintenance` and exposes it via a new selector `useFleetMaintenance()` (a Zustand store mirroring `useFleetHealth`'s shape from Phase 4). AccountPicker rows read both `useFleetHealth` (degraded labels — unchanged from Phase 4) and `useFleetMaintenance` (active reset window — new in 5a).

## 8. AccountPicker staleness UI

Per-row rule (TypeScript pseudocode):

```tsx
function nlvCellState(account: Account, maintenance: FleetMaintenance): NlvCellState {
  if (maintenance.active) {
    return {
      variant: 'normal',
      value: account.nlv,
      tooltip: `broker in scheduled maintenance — refreshes when ${maintenance.window} window ends ${formatTime(maintenance.until)}`,
    };
  }
  if (account.nlvAt === null) {
    return { variant: 'placeholder', value: '—', tooltip: 'no data yet' };
  }
  const ageSec = (Date.now() - account.nlvAt.getTime()) / 1000;
  if (ageSec < 120) {
    return { variant: 'normal', value: account.nlv, tooltip: null };
  }
  if (ageSec < 1800) {
    return {
      variant: 'dim',
      value: account.nlv,
      tooltip: `as of ${formatTime(account.nlvAt)} (${Math.round(ageSec / 60)} min ago)`,
    };
  }
  return { variant: 'placeholder', value: '—', tooltip: `stale since ${formatTime(account.nlvAt)}` };
}
```

Thresholds **2 min / 30 min** are hard-coded in this mapper — UI policy, not operator policy. Tunable later by editing the constant + redeploy. Stylelint's `unit-disallowed-list` enforced via existing tokens.

## 9. real_ibkr smoke tests — `sidecar/tests/test_real_ibkr_smoke.py`

New file, ~6-8 tests, all marked `@pytest.mark.real_ibkr`. Connect against `127.0.0.1:4002` (paper gateway) using `ib_async.IB()` directly (no mocks). Tests are read-only and idempotent — no orders placed, no state mutated.

```python
@pytest.mark.real_ibkr
async def test_connect_paper_gateway() -> None:
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=999, timeout=15)
    try:
        assert ib.isConnected()
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
async def test_managed_accounts_returns_at_least_one() -> None:
    # Connect, sleep 0.5, read managedAccounts(), assert len >= 1.


@pytest.mark.real_ibkr
async def test_account_summary_carries_currency() -> None:
    # Connect, reqAccountSummaryAsync(), inspect ib.accountSummary().
    # Pick the first account, find its NetLiquidation row, assert
    # row.currency matches one of {USD, GBP, HKD, JPY, KRW, EUR, CAD}.
    # This is the contract test that proves option-E base-currency works.


@pytest.mark.real_ibkr
async def test_get_positions_round_trips() -> None:
    # Connect, reqPositionsAsync(), assert structure (no exceptions).


@pytest.mark.real_ibkr
async def test_get_orders_empty_list_ok() -> None:
    # Connect, openTrades() — paper account with no open orders should
    # return [] without exception.


@pytest.mark.real_ibkr
async def test_connection_survives_sixty_seconds() -> None:
    # Connect, sleep(60), assert ib.isConnected() still True.
    # Catches connection-drop regressions in ib_async or TWS.
```

CI nightly cron (`.github/workflows/nightly-real-ibkr.yml`) already runs `pytest tests/ -m real_ibkr`. With this file in place, those 6 tests execute against the live paper gateway; the existing exit-5-as-success shim is no longer triggered (real tests get collected).

The 5b trade-execution real_ibkr tests land later as `sidecar/tests/test_real_ibkr_trade.py` — separate file, separate brainstorm.

## 10. Migration sequencing

Standard Phase 4 deploy pattern, no operator step:

1. Push to `main`.
2. `deploy.yml` runs:
   - Generates backend proto stubs (existing step from Phase 4).
   - rsync to VPS.
   - `docker compose -f docker-compose.prod.yml build backend`.
   - `docker compose up -d backend` — backend's `entrypoint.sh` runs `alembic upgrade head` (creates the 3 columns) before uvicorn starts.
   - nginx reload, health probe, Playwright smoke.
3. Discoverer's first tick (within 30 s of backend ready) populates the new columns for all 22 accounts.

Rollback path: if Alembic 0003 fails for any reason, the backend container exits non-zero, docker-compose marks it unhealthy, the previous (Phase 4) image keeps serving via the existing instance. `git revert` + push reverts the migration in the next deploy.

## 11. Test surface

| Layer | New tests | Scope |
|---|---|---|
| Sidecar | 0 (existing handler tests cover GetAccountSummary already) | `test_handlers_health_summary.py` already covers the summary path with `golden_fake_ib`. |
| Sidecar (real) | 6-8 (new file `test_real_ibkr_smoke.py`) | Live paper gateway, nightly cron. |
| Backend migrations | 1 (new test in `tests/migrations/test_0003.py`) | `nlv` columns exist, are nullable, `CHAR(3)` constraint enforced. |
| Backend services | ~5 new in `tests/services/test_brokers.py` | Discoverer fan-out succeeds; one-account-times-out doesn't taint others; UPDATE skips soft-deleted; UPDATE includes both nlv + nlv_currency; first tick after deploy populates all 22 rows. |
| Backend API | ~3 new in `tests/api/test_accounts_list.py` | Envelope includes `broker_maintenance`; `nlv*` fields present on AccountResponse; `nlv` is null when `last_nlv_at` is null. |
| Frontend services | ~3 new in `src/services/accounts.test.ts` | `toDisplayAccount` maps `nlv_currency` → `baseCurrency` with fallback chain; null `nlv_at` produces null `nlvAt`; `safeParseDecimal` lossiness flag preserved. |
| Frontend stores | ~2 new in `src/stores/global/fleet-maintenance.test.ts` | `useFleetMaintenance` selector returns the active/window/until shape; null window when inactive. |
| Frontend components | ~5 new in `src/components/patterns/AccountPicker/AccountPicker.test.tsx` | 2-min / 30-min / null / maintenance-active rendering paths. |

Coverage gate: 80%+ on backend `app/services/brokers.py` (existing — no regression), on `app/api/accounts.py` (existing), on the new code paths.

## 12. Architect-review checkpoints

Topics to flag explicitly in the architect-review pass:

1. **Discoverer fan-out concurrency** — 22 simultaneous `GetAccountSummary` RPCs against the gRPC sidecar fleet (4 sidecars × ≤6 accounts each). Verify the sidecar handler is reentrant (doesn't share state across concurrent RPCs); if not, a per-sidecar semaphore is needed.
2. **Decimal-as-string round-trip** — `Postgres NUMERIC(20,8) → asyncpg Decimal → str(Decimal) → wire → safeParseDecimal → display number`. Does the chain preserve precision through all 4 hops? Edge case: NLV with 8 fractional digits like `100.00000001`.
3. **Maintenance-window envelope** — does the existing `in_weekend_reset` / `in_daily_reset` API correctly handle DST transitions on the 5 timezones (ET, CET, HKT, plus the two derived from them)? Phase 4's tests covered this; ensure the new envelope-emission path doesn't introduce a re-evaluation race.
4. **Concurrent UPDATE serialization** — single transaction wraps 22 UPDATE statements. Postgres serialization should be fine; verify no row-level deadlock via the unique `(broker_id, account_number)` constraint.
5. **NULL-safety on the wire** — frontend receives `nlv: null` when the discoverer hasn't run; `safeParseDecimal(null ?? "0")` returns `display: 0` not NaN. Verify in tests.

## 13. Architect review — applied

To be filled by the architect-review skill before plan-writing. Pre-flagged areas (above §12) are the high-risk surfaces.

## 14. Spec self-review

- **Placeholders:** none. Every section has concrete column names, RPC signatures, threshold values, file paths.
- **Internal consistency:** §3.2 envelope shape ↔ §6 service layer assembly ↔ §7 frontend mapping all reference the same `broker_maintenance: {active, window, until}` triple.
- **Scope check:** sub-phase scoped to NLV + currency + smoke tests + maintenance-window envelope. Trade execution explicitly OUT (§1, §9 scope statement). Single Alembic migration (0003) — clean.
- **Ambiguity:** UI thresholds `< 2 min / 2-30 min / > 30 min / null` enumerated explicitly with mock pseudocode in §8. Maintenance-window override is the only orthogonal cross-cut and it's a single boolean switch.

## 15. Open items deferred to 5b/5c

- Trade execution proto contract (PlaceOrder, CancelOrder, ModifyOrder, OrderEvent stream).
- Per-user audit table (`order_events`, partitioned).
- Kill-switch flag + Cancel-All button.
- Confirmation modal in live mode + nonce.
- Per-account/per-token notional + qty + NLV-percent caps.
- Trade ticket UI (side-panel, Cmd+B, prefill from positions).
- Bracket OCO + algo strategies (5c).
- Frontend trade ticket beyond the Standard 4 order types (5c).
