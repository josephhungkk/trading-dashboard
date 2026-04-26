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
    nlv: str | None = Field(default=None)                 # decimal-as-string; null = no successful refresh yet
    nlv_currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    nlv_at: datetime | None = Field(default=None)         # UTC; null when nlv is null
```

**Wire format invariants (R3, R4, R5):**

- `nlv` is the Decimal serialized via `format(value.quantize(Decimal('1e-8')), 'f')` — fixed-point, no trailing-zero stripping (so `"0.10000000"` stays as-is), no scientific notation. `value.normalize()` is **not** used because `Decimal("0.00000000").normalize()` produces `0E-8` which JS treats as a string mismatch.
- `nlv_currency` MUST match `^[A-Z]{3}$`. Empty strings, padded strings (`"US "`), and lowercase variants are rejected at write time by the discoverer (see §5 skip-write predicate). Pydantic enforces shape at the boundary.
- `nlv_at`/`nlv`/`nlv_currency` are written as a triple — the discoverer only writes them together. Frontend can rely on `nlv_at !== null` implying both other fields are non-null.

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

### 3.3 Contract test updates (R2)

Adding fields to `AccountResponse` will break the existing strict-shape OpenAPI smoke at `backend/tests/api/test_openapi_phase4.py` (which asserts the exact Phase-4 field set). 5a's plan must:

- Update the test to assert *required* keys present and *forbidden* keys absent (`gateway_label`, `account_number`), but allow additional optional keys.
- Update or add similar assertions for `AccountListResponse` envelope (`broker_maintenance` field present and well-typed when active).
- Update any frontend snapshot tests of the same shape (verify by `grep -rn "AccountResponse\|AccountListResponse" frontend/src/services/*.test.ts`).

Treat this as a contract-evolution PR pattern, not a one-time fix; future phases adding fields shouldn't have to re-do this.

## 4. Schema — Alembic 0003

```sql
ALTER TABLE broker_accounts
  ADD COLUMN last_nlv NUMERIC(20, 8),
  ADD COLUMN last_nlv_currency VARCHAR(3),
  ADD COLUMN last_nlv_at TIMESTAMPTZ,
  ADD CONSTRAINT broker_accounts_last_nlv_currency_iso3 CHECK (
    last_nlv_currency IS NULL
    OR last_nlv_currency ~ '^[A-Z]{3}$'
  );
```

All three columns nullable (R11). No backfill — discoverer populates on first tick after deploy. No index initially (22 rows, no query joins); revisit if `broker_accounts` grows past 10k rows.

**Why `VARCHAR(3) + CHECK regex` instead of `CHAR(3)` (R1, R11):** `CHAR(3)` silently right-pads short values (`'US'` → `'US '`) and silently truncates long values; both make the wire-shape contract leaky. `VARCHAR(3) NOT NULL CHECK length(...) = 3` rejects malformed writes at insert time; combined with the discoverer's skip-write predicate (§5) and the Pydantic `pattern` constraint (§3.1) it forms three layers of defense.

**Numeric overflow (R11):** `NUMERIC(20, 8)` allows values up to `999_999_999_999.99999999` — twelve significant digits before the decimal. An IBKR account exceeding $1T would raise `numeric field overflow` on UPDATE. The discoverer wraps each per-account UPDATE in a try/except to log `broker_discover_nlv_overflow` and continue (one bad account doesn't taint the other 21 — see §5 invariants).

`alembic/versions/0003_broker_accounts_nlv.py` follows the existing 0002 pattern (transactional DDL on Postgres → atomic; failure → backend doesn't start → unhealthy container → docker-compose holds nginx behind it; existing image keeps serving).

## 5. Discoverer — `_discover_once` extension

**Re-entrancy guard (R7):** `BrokerDiscoverer` gains an `asyncio.Lock` initialized in `__init__`. `discover_loop`'s tail wraps the call: `if self._tick_lock.locked(): log.warning("broker_discover_iteration_skipped_overlap"); else: async with self._tick_lock: await self._discover_once()`. This prevents two `_discover_once` invocations running concurrently when a tick exceeds the 30s interval (catastrophic write contention + double sidecar load). The change to `discover_loop` is a 4-line wrapper, not a refactor.

**Sidecar reentrancy invariant (R8):** `GetAccountSummary` is read-only against `ib.accountValues()` — no `req*Async` IB API call inside the handler. 22 simultaneous gRPC calls all read the same shared cache; concurrency-safe by construction. The Phase 5b proto (which adds RPCs that issue real IB API requests like `placeOrderAsync`) will need an explicit per-sidecar semaphore — that's not a 5a concern but is documented here so future work doesn't accidentally extend `GetAccountSummary` to call into IB.

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

    # Skip-write predicate (R1, R5): only UPDATE when the summary is genuinely
    # populated. The sidecar's _money_for_tag falls back to "0"/"USD" when the
    # underlying ib.accountValues() row hasn't arrived yet — writing that
    # would make a freshly-connected paper account indistinguishable from a
    # successful $0 fetch. Defensive: require currency matches ^[A-Z]{3}$
    # AND value is a non-empty decimal-string. Empty currencies and bare "0"
    # are treated as "no data this tick" — leave nlv_at NULL, let the next
    # tick try again.
    def _is_populated(summary: base.Summary) -> bool:
        nlv_currency = summary.net_liquidation.currency
        nlv_value = summary.net_liquidation.value
        return (
            len(nlv_currency) == 3
            and nlv_currency.isascii()
            and nlv_currency.isupper()
            and bool(nlv_value)
        )

    def _format_decimal(s: str) -> str:
        # Wire format: fixed-point, 8 fractional digits, no scientific
        # notation. format() with 'f' specifier guarantees no E-notation
        # output. quantize() pins the precision to NUMERIC(20,8).
        d = Decimal(s).quantize(Decimal("1e-8"))
        return format(d, "f")

    nlv_update_count = 0
    nlv_overflow_count = 0
    async with self._session_factory() as session, session.begin():
        for r in results:
            if r is None or isinstance(r, BaseException):
                continue
            label, account_number, summary = r
            if not _is_populated(summary):
                continue
            try:
                await session.execute(nlv_update_stmt, {
                    "broker_id": "ibkr",
                    "account_number": account_number,
                    "nlv": _format_decimal(summary.net_liquidation.value),
                    "currency": summary.net_liquidation.currency,
                })
                nlv_update_count += 1
            except DBAPIError as exc:
                # NUMERIC(20,8) overflow on a single account doesn't taint
                # the other 21. Log and continue.
                if "overflow" in str(exc).lower():
                    nlv_overflow_count += 1
                    log.warning("broker_discover_nlv_overflow",
                                account_number=account_number, error=str(exc))
                else:
                    raise

    log.info(
        "broker_discover_iteration_ok",
        upsert_count=len(rows_seen),
        soft_delete_count=soft_delete_count,
        nlv_update_count=nlv_update_count,
        nlv_overflow_count=nlv_overflow_count,
    )
```

Key invariants:
- **Per-call timeout 10 s** — never blocks the 30 s discover loop on one slow sidecar.
- **`return_exceptions=True`** — one failed RPC doesn't taint the others.
- **Skip-write predicate** — UPDATE only when `currency` matches `^[A-Z]{3}$` AND `value` is non-empty (R1, R5). Empty/fallback summaries leave `nlv_at` NULL so the frontend correctly renders "no data yet" instead of `$0`.
- **UPDATE only on success** — failed accounts retain stale `last_nlv_at` so the frontend's staleness UI flags them naturally.
- **Single transaction, but per-row try/except for overflow (R10, R11)** — a single account exceeding `NUMERIC(20,8)` raises `numeric field overflow` and is logged + skipped without aborting the other 21 UPDATEs.
- **Skip soft-deleted rows** — `WHERE deleted_at IS NULL` guard prevents a race where a row is soft-deleted mid-tick and a stale NLV gets written.
- **Resurrect-from-soft-delete clears NLV (R9):** the existing upsert in `_discover_once` (the `ON CONFLICT (broker_id, account_number) DO UPDATE` clause) gains:
  ```sql
  last_nlv = CASE WHEN broker_accounts.deleted_at IS NOT NULL THEN NULL ELSE broker_accounts.last_nlv END,
  last_nlv_currency = CASE WHEN broker_accounts.deleted_at IS NOT NULL THEN NULL ELSE broker_accounts.last_nlv_currency END,
  last_nlv_at = CASE WHEN broker_accounts.deleted_at IS NOT NULL THEN NULL ELSE broker_accounts.last_nlv_at END
  ```
  When a soft-deleted account is rediscovered, NLV is reset to NULL (frontend renders "no data yet") rather than showing weeks-old stale values.

**Operational metrics (R10):** add `broker_discover_nlv_update_duration_ms` histogram (Prometheus) and `broker_discover_nlv_overflow_total` counter so the operator can see p99 of the UPDATE batch and any overflow incidents. At 22 accounts and ~5ms RTT each on the WG link, expected p99 is ~110ms — revisit batched multi-row UPDATE if this grows beyond 500ms or accounts grow >100.

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

**Shared helper required (R6, R13):** the same maintenance-window envelope is needed by `AccountService.list_accounts` (new in 5a) and by `_classify_sidecar_failure` in `app/api/accounts.py` (existing — Phase 4). Rather than duplicate the cascade and risk the boundary-second race (`active=True, until=null` when `seconds_until_window_ends(now) == 0`), 5a promotes the logic into one helper:

```python
# in app/services/ibkr_maintenance.py — new in 5a:

class BrokerMaintenance(BaseModel):
    active: bool
    window: Literal["weekend", "daily"] | None
    until: datetime | None

def compute_broker_maintenance(now: datetime) -> BrokerMaintenance:
    """Single-evaluation envelope: predicate and `until` are computed
    consistently, with a min-1-second floor to ensure `until > now`
    whenever active=True (avoids the boundary flicker)."""
    if in_weekend_reset(now):
        secs = max(seconds_until_window_ends(now), 1)
        return BrokerMaintenance(active=True, window="weekend",
                                 until=now + timedelta(seconds=secs))
    in_daily, _region = in_daily_reset(now)
    if in_daily:
        secs = max(seconds_until_window_ends(now), 1)
        return BrokerMaintenance(active=True, window="daily",
                                 until=now + timedelta(seconds=secs))
    return BrokerMaintenance(active=False, window=None, until=None)
```

Both call sites import it. The `BrokerMaintenance` Pydantic model lives in the same module so callers don't pull in a fresh definition (single source of truth).

`AccountListResponse` assembly becomes a one-liner:

```python
async def list_accounts(self) -> base.AccountListResponse:
    # ... existing rows fetch ...
    degraded = await self._registry.degraded_labels()
    return AccountListResponse(
        accounts=[_account_response_from_row(r) for r in rows],
        degraded_sidecars=degraded,
        broker_maintenance=compute_broker_maintenance(datetime.now(UTC)),
    )
```

Plan must include: refactor `_classify_sidecar_failure` to use the same helper instead of inline duplicating the cascade (zero behavior change, removes the boundary-race surface).

## 7. Frontend — `RealAccountsService` extension

`toDisplayAccount` mapper grows two responsibilities:

1. Map `r.nlv` (string) → `Account.nlv` (number) via `safeParseDecimal(r.nlv ?? "0").display`. The `lossy` flag on `safeParseDecimal` returns `true` for trailing-zero values like `"0.10000000"` because `Number("0.10000000").toString() === "0.1"`. Per §3.1 wire-format invariants the backend now emits `format(d.quantize(Decimal('1e-8')), 'f')` (8 fractional digits, fixed-point) which means `lossy` will be `true` for almost every real NLV. **The mapper does NOT branch on `lossy` — it always uses `display` for the number.** Tests that previously asserted `lossy === false` for simple values must be revised: lossy is informational only, not an error signal. (R3, R4)
2. Pick `baseCurrency` from `r.nlv_currency` first (authoritative), `r.currency_base` second, `'USD'` last (matches existing fallback). Limited to `Account['baseCurrency']` literal type (`'USD' | 'HKD' | 'GBP' | 'JPY' | 'KRW'`); unknown codes fall through to `'USD'`.

The `Account` interface stays a number for `nlv` (no breaking change to existing components); a parallel optional field `nlvAt: Date | null` is added so the staleness rule can render correctly.

`RealAccountsService.list(mode)` maps the new envelope including `broker_maintenance` and exposes it via a new selector `useFleetMaintenance()` (a Zustand store mirroring `useFleetHealth`'s shape from Phase 4). AccountPicker rows read both `useFleetHealth` (degraded labels — unchanged from Phase 4) and `useFleetMaintenance` (active reset window — new in 5a).

**Re-render cadence (R12):** every 30s discover tick replaces 22 row references on the next account-list poll/SSE message. AccountPicker's row component must `React.memo` on a stable equality check (compare by `account.id` + `nlv` + `nlvAt.getTime()`) so unchanged rows skip reconciliation. **5a does NOT add server-side ETag/hash dedup** — at 22 rows the per-tick cost is negligible. Revisit at >100 accounts (Phase 6 Futu adds <10; Schwab Phase 8 adds <5; remains under threshold for foreseeable phases).

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
| Sidecar | 1 new in `tests/test_handlers_health_summary.py` | `test_concurrent_summaries_do_not_interfere` — fire 22 `GetAccountSummary` calls in parallel against `golden_fake_ib`, assert each returns the right account's NLV (R8 invariant). |
| Sidecar (real) | 6-8 (new file `test_real_ibkr_smoke.py`) | Live paper gateway, nightly cron. |
| Backend migrations | ~3 new in `tests/migrations/test_0003.py` | (R11) `last_nlv_currency` rejects `'US'` (too short), `'usd'` (lowercase), `'USDX'` (truncate-or-error), `'   '` (blank pad); accepts `'USD'`. NULL allowed initially. `last_nlv` rejects `2e30` (overflow), accepts `999999999999.99999999`. |
| Backend services — discover | ~7 new in `tests/services/test_brokers.py` | Fan-out succeeds for 4 healthy + 0 timed-out; one-account-`wait_for(10)`-times-out doesn't taint others; UPDATE skips when summary returns `currency=""` or `value="0"` (R1, R5); UPDATE skips soft-deleted (`deleted_at IS NOT NULL`); first tick after deploy populates all 22 rows; **resurrect-from-soft-delete clears `last_nlv*`** (R9); **overlap guard skips concurrent tick** (R7). |
| Backend services — maintenance helper | ~4 new in `tests/services/test_ibkr_maintenance.py` | `compute_broker_maintenance` boundary tests: 1 second before window opens, exact-second-window-opens, 1 second before window closes, exact-second-window-closes. Verify `until > now` always when `active=True` (R6). Verify weekend→daily handoff Saturday→Sunday returns the right window. |
| Backend API | ~5 new in `tests/api/test_accounts_list.py` | Envelope includes `broker_maintenance`; `nlv*` fields present on `AccountResponse`; `nlv` is null when `last_nlv_at` is null; OpenAPI smoke updated to allow optional fields (R2); maintenance envelope reflects mocked-now via `freezegun` or similar. |
| Backend overflow | 1 new in `tests/services/test_brokers.py` | `broker_discover_nlv_overflow_total` increments when one account's NLV exceeds `NUMERIC(20,8)`; other 21 still UPDATE successfully (R10, R11). |
| Frontend services | ~5 new in `src/services/accounts.test.ts` | `toDisplayAccount` maps `nlv_currency` → `baseCurrency` with fallback chain (3 cases: `'GBP'`, `''`, `null`); null `nlv_at` produces null `nlvAt`; `lossy: true` on `'0.10000000'` does NOT prevent display (R3); `safeParseDecimal('null')` returns `display: 0` not NaN. |
| Frontend stores | ~2 new in `src/stores/global/fleet-maintenance.test.ts` | `useFleetMaintenance` selector returns the active/window/until shape; null window when inactive; `until` parsed as Date object (not raw ISO string). |
| Frontend components | ~6 new in `src/components/patterns/AccountPicker/AccountPicker.test.tsx` | < 2 min normal · 2-30 min dim · > 30 min `'—'` · null `nlvAt` `'—' "no data yet"` · maintenance-active suppresses staleness · row uses `React.memo` (no re-render when other rows update — verified via render-counter mock). |

Coverage gate: 80%+ on backend `app/services/brokers.py` (existing — no regression), on `app/api/accounts.py` (existing), on the new code paths.

## 12. Architect-review checkpoints — addressed

The architect-review pass (2026-04-26, agent `architect`) returned 14 findings. All CRITICAL and HIGH (R1-R8) are folded into the spec above. MEDIUM and LOW are tracked in §13. The original five concern areas all map to applied fixes:

1. **Discoverer fan-out concurrency** → §5 sidecar reentrancy invariant + sidecar test `test_concurrent_summaries_do_not_interfere`.
2. **Decimal-as-string round-trip** → §3.1 wire-format invariants (`format(d.quantize(Decimal('1e-8')), 'f')`); §7 lossy-flag-is-informational note.
3. **Maintenance-window envelope** → §6 `compute_broker_maintenance` shared helper with min-1-second `until` floor.
4. **Concurrent UPDATE serialization** → §5 per-row try/except for overflow; metric `broker_discover_nlv_update_duration_ms`; revisit batched UPDATE at >100 accounts.
5. **NULL-safety on the wire** → §11 frontend tests assert `safeParseDecimal(null ?? "0")` returns `display: 0`.

## 13. Architect review — applied

| ID | Severity | Status | Resolution |
|---|---|---|---|
| R1 | CRITICAL | APPLIED | §3.1 `nlv_currency` Pydantic `pattern=^[A-Z]{3}$`; §4 schema `VARCHAR(3) + CHECK regex`; §5 skip-write predicate `_is_populated`. |
| R2 | CRITICAL | APPLIED | §3.3 contract test updates (allow optional fields, retain forbidden-field assertions on `gateway_label`/`account_number`). |
| R3 | CRITICAL | APPLIED | §3.1 wire format `format(d.quantize(Decimal('1e-8')), 'f')` — fixed-point, no scientific notation; §7 `lossy` flag is informational only. |
| R4 | HIGH | APPLIED | §3.1 explicit "no `.normalize()`" note (avoids `0E-8` for zero values); fixed-point `format(d, 'f')` instead. |
| R5 | HIGH | APPLIED | §5 skip-write predicate filters out `currency=""` and falsy `value` so paper account "no data yet" stays distinct from a real `$0` (which won't pass the predicate either, by design). |
| R6 | HIGH | APPLIED | §6 `compute_broker_maintenance` helper with `max(secs, 1)` floor; both call sites import it. |
| R7 | HIGH | APPLIED | §5 `asyncio.Lock` re-entrancy guard on `_discover_once`; tick-overlap logs `broker_discover_iteration_skipped_overlap`. |
| R8 | HIGH | APPLIED | §5 sidecar reentrancy invariant: `GetAccountSummary` is read-only against `ib.accountValues()` cache, no `req*Async`. New test `test_concurrent_summaries_do_not_interfere` validates 22 concurrent reads. |
| R9 | MEDIUM | APPLIED | §5 upsert clears `last_nlv*` columns when `deleted_at IS NOT NULL` (resurrect-from-soft-delete). New test in §11. |
| R10 | MEDIUM | APPLIED | §5 per-row try/except for `numeric field overflow`; new metric `broker_discover_nlv_update_duration_ms`. Batched multi-row UPDATE deferred until >100 accounts (Phase 6+). |
| R11 | MEDIUM | APPLIED | §4 `VARCHAR(3) + CHECK regex` (rejects `'US'`, `'usd'`, `'USDX'`, blank-pad). §11 migration test row exercises all four. |
| R12 | MEDIUM | DOCUMENTED | §7 explicit `React.memo` requirement; ETag/hash dedup intentionally OUT of 5a (revisit at >100 accounts). New AccountPicker test `row uses React.memo`. |
| R13 | LOW | APPLIED | §6 helper `compute_broker_maintenance` lives in `app/services/ibkr_maintenance.py`; `BrokerMaintenance` Pydantic model lives in same module. |
| R14 | LOW | DOCUMENTED | §15 explicit "5b OrderEvent stream is a separate background task per sidecar, NOT extended off `_discover_once`." |

## 14. Spec self-review

- **Placeholders:** none. Every section has concrete column names, RPC signatures, threshold values, file paths. §13 architect-review-applied table replaces the prior placeholder.
- **Internal consistency:** §3.2 envelope shape ↔ §6 service layer assembly ↔ §7 frontend mapping all reference the same `broker_maintenance: {active, window, until}` triple. The `compute_broker_maintenance` helper is the single source for the cascade. Wire format invariants (§3.1) are referenced from both the discoverer (§5 `_format_decimal`) and the frontend mapper (§7 `safeParseDecimal` lossy-flag clause).
- **Scope check:** sub-phase scoped to NLV + currency + smoke tests + maintenance-window envelope + R1-R14 fixes. Trade execution explicitly OUT (§1, §9 scope statement, §15 deferred items). Single Alembic migration (0003) — clean.
- **Ambiguity:** UI thresholds `< 2 min / 2-30 min / > 30 min / null` enumerated explicitly with mock pseudocode in §8. Maintenance-window override is the only orthogonal cross-cut and it's a single boolean switch. Skip-write predicate (§5) is enumerated explicitly as a Python predicate function.

## 15. Open items deferred to 5b/5c

- Trade execution proto contract (PlaceOrder, CancelOrder, ModifyOrder, OrderEvent stream).
- Per-user audit table (`order_events`, partitioned).
- Kill-switch flag + Cancel-All button.
- Confirmation modal in live mode + nonce.
- Per-account/per-token notional + qty + NLV-percent caps.
- Trade ticket UI (side-panel, Cmd+B, prefill from positions).
- Bracket OCO + algo strategies (5c).
- Frontend trade ticket beyond the Standard 4 order types (5c).

**Architectural note (R14):** the unary-fan-out pattern in §5 (`asyncio.gather(*[GetAccountSummary(...)])`) is intentionally narrow to summary-style RPCs. The Phase 5b `OrderEvent` stream subscription will be a **separate background task per sidecar** (one persistent gRPC server-streaming RPC per sidecar label), NOT extended off `_discover_once`. The stream task lives alongside the discoverer in `BrokerRegistry` lifecycle, not under it. Future readers shouldn't conflate the two patterns: `_discover_once` is for periodic-poll RPCs; `_stream_order_events_loop` (5b) is for push-stream RPCs.
