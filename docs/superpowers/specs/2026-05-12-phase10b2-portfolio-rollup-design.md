# Phase 10b.2 — Multi-account portfolio rollup

**Date:** 2026-05-12
**Predecessor:** Phase 10b.1 — Position-sizing calculator (v0.13.0)
**Successor:** Phase 11 — AI router + Alerts + Telegram (per ROADMAP.md)
**Target tag:** v0.14.0
**ROADMAP deliverable:** Phase 10 #9 — Multi-account portfolio rollup (cross-broker NLV / exposure / Δ)

---

## 1. Goal

Ship a single page at `/portfolio/rollup` that answers, in one view:

> "What does my whole book look like right now and over the last year, across all brokers, in my chosen base currency?"

Plus a drill-down from any asset-class exposure row into the contributing instruments with concentration-cap utilisation from the Phase 10a `risk_limits` table.

This closes the last open Phase 10 deliverable. After this phase, Phase 10 is complete and the gate, sizer, and rollup form the three load-bearing pillars of pre-trade and post-trade portfolio visibility.

---

## 2. Scope

### In scope

1. **NLV rollup** — cross-broker sum of `broker_accounts.last_nlv` per account, FX-converted to a user-selectable base currency (default GBP). Live, intraday curve, 30-day curve, and 1-year+ curve.
2. **Exposure by asset class** — cross-broker `SUM(qty * avg_cost * multiplier * fx_rate)` per position grouped by `instruments.asset_class` and direction (long/short). FX applied at read time using current rates. **Caveat:** exposure is computed at average cost, not at last quote mark — `positions.market_value_base` column does not exist today (see ARCHITECT [CRIT #2] — risk_service uses the same approximation per its Phase 10a.5 B3 comment). Page header renders "Exposure at cost basis" badge to set operator expectation. Adding a real mark-to-market column is deferred to Phase 10b.3 or Phase 24.
3. **P&L attribution per broker/account** — realized (today) + unrealized, sourced from the existing `v_account_intraday_pnl` view (Phase 10a.5).
4. **Drill-down** — click an asset-class row, expand contributing instruments with concentration-cap utilisation (informational, not enforcement).
5. **Realtime push** — WebSocket topic `portfolio.rollup` whenever any sidecar refreshes NLV. FE auto-updates with 500 ms debounce.

### Non-goals (explicit)

- **Strategy-tagged P&L** — defers to Phase 20+ when fills carry `strategy_id` / `bot_id`. Bot engine does not exist before Phase 20.
- **Historical NLV backfill from broker APIs** (FlexQuery, Schwab transaction history, etc.). History accrues forward from deploy day. Operator-facing copy reads "History since YYYY-MM-DD".
- **`account_balances` table decoupling.** Deferred to Phase 24 (recorded in `docs/ROADMAP.md`).
- **Position recompute from quotes × marks.** Broker-reported NLV is canonical for this phase.
- **PWA / offline / CSV export.** Phase 23 covers tax exports; Phase 25 covers PWA.
- **Add/remove account toggle on rollup.** All `broker_accounts WHERE deleted_at IS NULL` are included.

### Adjacent reserved namespace

`/portfolio/*` namespace is reserved. Phase 23 will add `/portfolio/tax`; Phase 20+ will add `/portfolio/attribution`.

---

## 3. Architecture

**Pattern:** Service + REST + WS push (mirrors Phase 10b.1).

```
┌──────────────────────────────────────────────────────────────────────────┐
│  brokers.py:1416                                                         │
│  ─────────────────                                                       │
│  UPDATE broker_accounts SET last_nlv = ...                               │
│       └─→  INSERT INTO account_balance_snapshots ...                     │
│       └─→  redis.publish("portfolio.rollup.dirty", account_id)           │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
   ┌──────────────────────────┐      ┌────────────────────────────┐
   │ account_balance_snapshots│      │  ws_portfolio.py            │
   │ (hypertable, 7d chunks)  │      │  /ws/portfolio/rollup       │
   │   + 1h CAGG (30d retain) │      │  debounce 500ms             │
   │   + 1d CAGG (5y retain)  │      │  JSON frames                │
   └────────────┬─────────────┘      └────────────┬───────────────┘
                │                                  │
                ▼                                  ▼
   ┌──────────────────────────────────────────────────────────┐
   │  PortfolioRollupService (per-request)                    │
   │   .compute_live(base_currency)                           │
   │   .compute_curve(base_currency, window)                  │
   │   .drill_asset_class(asset_class, base_currency)         │
   │  Uses _fx_rate() from orders_service (Redis-cached)      │
   └────────────┬─────────────────────────────────────────────┘
                │
                ▼
   ┌──────────────────────────────────────────────────────────┐
   │  3× REST endpoints in app/api/portfolio.py               │
   │   GET /api/portfolio/rollup                              │
   │   GET /api/portfolio/rollup/curve                        │
   │   GET /api/portfolio/rollup/drill                        │
   └────────────┬─────────────────────────────────────────────┘
                │
                ▼
   ┌──────────────────────────────────────────────────────────┐
   │  FE: /portfolio/rollup (TanStack-Router file-based)      │
   │   Hybrid: REST initial fetch + WS push + poll fallback   │
   └──────────────────────────────────────────────────────────┘
```

---

## 4. Schema (Alembic 0039 + 0040)

### 4.1 `account_balance_snapshots` (Alembic 0039)

```sql
CREATE TABLE account_balance_snapshots (
  account_id    UUID          NOT NULL
                REFERENCES broker_accounts(id) ON DELETE CASCADE,
  ts            TIMESTAMPTZ   NOT NULL,
  nlv           NUMERIC(20,8) NOT NULL,
  currency      CHAR(3)       NOT NULL,
  source_label  TEXT          NOT NULL,
  PRIMARY KEY (account_id, ts),
  CONSTRAINT ck_abs_currency_iso3 CHECK (currency ~ '^[A-Z]{3}$'),
  CONSTRAINT ck_abs_source_label  CHECK (
    source_label ~ '^[a-z0-9-]+$' AND length(source_label) <= 64
  )
);
-- ARCHITECT [CRIT #1]: no nlv >= 0 constraint — margin-call accounts have
-- legitimately negative NLV during settlement / over-leverage. Mirrors
-- broker_accounts.last_nlv which has no such constraint (alembic 0003).
-- ARCHITECT [MED #1]: source_label is dictionary-encoded by TimescaleDB
-- column compression; unbounded text defeats compression. Constrain to
-- lowercase-alphanumeric-hyphen, <= 64 chars (mirrors pnl_intraday).

SELECT create_hypertable(
  'account_balance_snapshots', 'ts',
  chunk_time_interval => INTERVAL '7 days'
);

CREATE INDEX abs_account_ts_idx
  ON account_balance_snapshots (account_id, ts DESC);

SELECT add_retention_policy(
  'account_balance_snapshots', INTERVAL '2 years'
);
```

**Sizing estimate:** 4 brokers × 5 accounts × refresh ~30 s during market hours × 8 h/day × 252 trading days ≈ 4.8 M rows/yr × ~80 bytes = ~400 MB/yr raw, before TimescaleDB compression. 52 chunks/year at 7-day intervals — Timescale sweet spot.

**`source_label`** mirrors `pnl_intraday.source_label` (Phase 10a.5). Records which sidecar instance/gateway wrote the row (e.g., `"ibkr-main-paper"`). Used for debugging duplicate-write or stale-source bugs.

### 4.2 Continuous aggregates (Alembic 0040)

```sql
-- 1h granularity, 30d retention — feeds window=30d
CREATE MATERIALIZED VIEW account_balance_snapshots_1h
WITH (timescaledb.continuous) AS
SELECT
  account_id,
  time_bucket(INTERVAL '1 hour', ts) AS bucket,
  last(nlv, ts)       AS nlv_close,
  last(currency, ts)  AS currency,
  MAX(nlv)            AS nlv_high,
  MIN(nlv)            AS nlv_low,
  first(nlv, ts)      AS nlv_open
FROM account_balance_snapshots
GROUP BY account_id, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
  'account_balance_snapshots_1h',
  start_offset => INTERVAL '7 days',
  end_offset   => INTERVAL '1 hour',
  schedule_interval => INTERVAL '30 minutes'
);

-- ARCHITECT [MED #2]: every CAGG must declare retention explicitly.
SELECT add_retention_policy(
  'account_balance_snapshots_1h', INTERVAL '1 year'
);

-- ARCHITECT [MED #3]: real-time aggregation — UNION recent raw with CAGG
-- so compute_curve(window='30d') doesn't gap between deploy and first
-- scheduled refresh, and between scheduled refreshes thereafter.
ALTER MATERIALIZED VIEW account_balance_snapshots_1h
  SET (timescaledb.materialized_only = false);

-- 1d granularity, 5y retention — feeds window=1y
CREATE MATERIALIZED VIEW account_balance_snapshots_1d
WITH (timescaledb.continuous) AS
SELECT
  account_id,
  time_bucket(INTERVAL '1 day', ts) AS bucket,
  last(nlv, ts)       AS nlv_close,
  last(currency, ts)  AS currency,
  MAX(nlv)            AS nlv_high,
  MIN(nlv)            AS nlv_low,
  first(nlv, ts)      AS nlv_open
FROM account_balance_snapshots
GROUP BY account_id, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
  'account_balance_snapshots_1d',
  start_offset => INTERVAL '90 days',
  end_offset   => INTERVAL '1 day',
  schedule_interval => INTERVAL '6 hours'
);

SELECT add_retention_policy(
  'account_balance_snapshots_1d', INTERVAL '10 years'
);

ALTER MATERIALIZED VIEW account_balance_snapshots_1d
  SET (timescaledb.materialized_only = false);
```

**ARCHITECT [CRIT #3]: CAGG backfill must use autocommit_block.** `refresh_continuous_aggregate(...)` is a PROCEDURE that issues internal `COMMIT` and rejects running inside a transaction. Alembic's `env.py:65` sets `transaction_per_migration=True` which wraps every migration in `BEGIN ... COMMIT`. The Phase 10b.1 alembic 0038 precedent is **load-bearing wrong** — it only worked because `bars_1m` was near-empty on the current deploy. Correct pattern:

```python
# In Alembic 0040 upgrade():
with op.get_context().autocommit_block():
    op.execute("CALL refresh_continuous_aggregate('account_balance_snapshots_1h', NULL, NULL)")
    op.execute("CALL refresh_continuous_aggregate('account_balance_snapshots_1d', NULL, NULL)")
```

(See §15 Footguns for the precedent retraction. Phase 9.6 retro should also revert 0038 — out of scope for this phase but logged.)

### 4.3 Writer hook (code change, no schema)

At `brokers.py:1416` (the canonical `SET last_nlv = ...` UPDATE site, inside the existing per-account `session.begin_nested()` SAVEPOINT at line 1449), the snapshot INSERT must run in its **own inner SAVEPOINT** so its failure does not roll back the NLV UPDATE.

**ARCHITECT [HIGH #1]: two-level SAVEPOINT pattern.** Same-outer-SAVEPOINT placement (the naive pattern) would roll back the NLV UPDATE on INSERT failure, defeating fail-OPEN. SQLAlchemy 2.0 nested SAVEPOINTs are the canonical isolation mechanism:

```python
async with session.begin_nested():            # outer SAVEPOINT: NLV update
    await session.execute(nlv_update_stmt, params)
    try:
        async with session.begin_nested():    # inner SAVEPOINT: snapshot insert
            await session.execute(
                text("""
                    INSERT INTO account_balance_snapshots
                      (account_id, ts, nlv, currency, source_label)
                    VALUES (:id, now(), :nlv, :currency, :label)
                    ON CONFLICT (account_id, ts) DO NOTHING
                """),
                {"id": account_id, "nlv": nlv, "currency": currency, "label": source_label},
            )
    except Exception:
        portfolio_rollup_snapshot_write_errors_total.inc()
        log.exception("portfolio_rollup_snapshot_write_failed", account_id=str(account_id))
```

`ON CONFLICT DO NOTHING` guards against same-microsecond double-writes from two sidecar threads. The next refresh in ~30 s closes any visual gap.

**Fail-OPEN policy:** if the inner SAVEPOINT raises, the exception is caught, metric ticked, but the outer SAVEPOINT (containing the NLV UPDATE) continues. We accept losing one bucket of history rather than blocking an NLV write that gate/sizer depend on. Mirrors Phase 10a's `risk_audit_insert_failures_total` pattern.

**Test:** §11.1 test_balance_snapshot_writer must include "INSERT raises CheckViolation → metric increments, NLV UPDATE still commits, outer transaction still commits."

### 4.4 What is not touched

- `broker_accounts.last_nlv` / `last_nlv_currency` / `last_nlv_at` — unchanged. Risk gate (`risk_service.py`), sizer (`position_sizing_service.py`), and orders (`orders_service.py`) keep reading the existing columns.
- `pnl_intraday` / `v_account_intraday_pnl` — read-only consumers for the P&L slice.
- `positions` (qty, avg_cost, multiplier, currency, instrument_id) — read-only consumer for the exposure slice. Computed at cost basis per ARCHITECT [CRIT #2]; no schema change to `positions`.

---

## 5. Backend services

### 5.1 `PortfolioRollupService` (`app/services/portfolio_rollup_service.py`)

Per-request, DI'd `(db: AsyncSession, redis: RedisLike)`. No singleton state.

```python
class PortfolioRollupService:
    def __init__(self, db: AsyncSession, redis: RedisLike): ...

    async def compute_live(self, base_currency: str) -> RollupLive: ...
    async def compute_curve(
        self, base_currency: str,
        window: Literal["intraday", "30d", "1y"],
    ) -> RollupCurve: ...
    async def drill_asset_class(
        self, asset_class: str, base_currency: str,
    ) -> RollupDrill: ...
```

#### `compute_live`

Single SQL round-trip joining `broker_accounts` + `pnl_intraday` + positions roll-up subquery. Returns:

```python
class RollupLive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_currency: str                                  # ISO-3
    total_nlv_base: Decimal
    total_realized_today_base: Decimal
    total_unrealized_base: Decimal
    history_since: date | None                          # MIN(account_balance_snapshots.ts)::date
    accounts: list[PerAccount]
    exposure_by_asset_class: list[AssetClassExposure]
    fx_rates: dict[str, Decimal]                        # {"USD/GBP": ...} for display
    stale_accounts: list[UUID]                          # last_nlv_at > 5min old
```

FX conversion uses the existing `_fx_rate(redis, src, dst)` helper from `orders_service.py:1904`.

**ARCHITECT [HIGH #4]: Per-account FX fault isolation.** Each account's `nlv_base` is computed independently. If `_fx_rate` raises `PreviewUnavailable(fx_rate_unavailable)` for ONE account's currency pair, that account is marked `fx_stale=true`, its `nlv_base` is set to `None`, and its UUID appended to `fx_stale_accounts`. The endpoint returns **200 with `partial=true`** and excludes null contributions from `total_nlv_base`. Only return 503 when ALL accounts' FX pairs are unavailable. Drift rationale: trade-execution paths (where `_fx_rate` originated) need single-decision 503; rollup view is multi-account and degrades gracefully. Phase 10a.5 effectivity-closure thesis applies.

```python
class RollupLive(BaseModel):
    # ...existing fields...
    partial: bool = False                                # any per-account FX failed
    fx_stale_accounts: list[UUID] = Field(default_factory=list)

class PerAccount(BaseModel):
    # ...existing fields...
    fx_stale: bool = False
    nlv_base: Decimal | None = None                      # None when fx_stale
```

#### `compute_curve`

Reads from one of three sources by `window`:

| `window` | Source | Range |
|---|---|---|
| `intraday` | `account_balance_snapshots` (raw) | last 24h |
| `30d` | `account_balance_snapshots_1h` (CAGG) | last 30 days |
| `1y` | `account_balance_snapshots_1d` (CAGG) | last 365 days |

Returns:

```python
class RollupCurve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_currency: str
    window: Literal["intraday", "30d", "1y"]
    per_account: list[CurvePoint]                       # one row per (account_id, bucket)
    totals: list[BucketTotal]                           # pre-summed (bucket, total_nlv_base)

class CurvePoint(BaseModel):
    account_id: UUID
    bucket: datetime
    nlv_close_base: Decimal
    nlv_high_base: Decimal | None                       # None for intraday raw points
    nlv_low_base: Decimal | None

class BucketTotal(BaseModel):
    bucket: datetime
    total_nlv_base: Decimal
```

**Simplification:** FX conversion uses **current rates** at read time, not per-bucket historical FX. The page-level copy reads "values in current GBP". Exact historical FX defers to Phase 23 (UK CGT tax page needs the same and may add an FX history table).

#### `drill_asset_class`

Reads `risk_limits` for caps (precedence walk: account → broker → global, mirrors `RiskService._resolve_limit`). Joins `positions` filtered by `instruments.asset_class`. Returns:

```python
class RollupDrill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_class: str
    base_currency: str
    instruments: list[InstrumentExposure]

class InstrumentExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: int
    display_name: str
    exchange: str
    total_qty: Decimal
    notional_base: Decimal
    pct_of_nlv: Decimal
    cap_pct: Decimal | None
    utilisation_pct: Decimal | None
    verdict: Literal["ok", "warn", "block"]
```

`verdict` derives from `risk_limits.warn_at_pct` / `max_pct` for the matching asset_class scope. **Read-only — no audit write, no gate evaluate.** Drill is informational visibility, not enforcement.

### 5.2 REST endpoints (`app/api/portfolio.py`)

| Method + path | Auth | CSRF | Returns | Rate limit |
|---|---|---|---|---|
| `GET /api/portfolio/rollup?base=GBP` | JWT | no | `RollupLive` | shared `"portfolio"` bucket |
| `GET /api/portfolio/rollup/curve?base=GBP&window=intraday\|30d\|1y` | JWT | no | `RollupCurve` | shared `"portfolio"` bucket |
| `GET /api/portfolio/rollup/drill?asset_class=equity&base=GBP` | JWT | no | `RollupDrill` | shared `"portfolio"` bucket |

All endpoints validate `base` against `^[A-Z]{3}$`. `window` is a strict Literal union; `asset_class` is a permissive string validated against the `instruments.asset_class` open-set on read (returns `RollupDrill` with `instruments=[]` if no rows match — not an error). Supported base currencies (hard-coded set): `{GBP, USD, EUR, HKD, JPY, AUD}` (ARCHITECT [MED #7]).

**ARCHITECT [HIGH #6]: Rate limiter signature.** The existing `SlidingWindowRateLimiter` at `position_sizing_rate_limiter.py:43` keys on `(jwt_subject, account_id)` — rollup is cross-account so that shape doesn't fit. Spin a fresh instance with key `(jwt_subject, "portfolio")` — a single shared bucket across all three rollup endpoints so a curve fetch can't drown a live rollup poll. **Total cap: 10/s burst per jwt_subject**. Module-level singleton in `app/services/portfolio_rate_limiter.py`; `_reset_portfolio_limiter` autouse fixture in tests (copy from `_reset_limiter`).

### 5.3 Redis pubsub publisher (writer-side)

After the snapshot insert in `brokers.py:1416`:

**ARCHITECT [HIGH #5]: Tracked task set to prevent GC strand.** Naked `asyncio.create_task(...)` is on the project's silent-failure-hunter antipattern list (`codex_defaults.md` pattern). Python's GC can collect the task while in-flight if no strong ref is held, producing `Task was destroyed but it is pending!` warnings.

```python
# In brokers.py BrokerDiscoverer.__init__:
self._publish_tasks: set[asyncio.Task] = set()

# After the inner SAVEPOINT INSERT in §4.3:
task = asyncio.create_task(_publish_dirty(self._redis, account_id))
self._publish_tasks.add(task)
task.add_done_callback(self._publish_tasks.discard)

# In BrokerDiscoverer.stop():
for t in list(self._publish_tasks):
    t.cancel()
await asyncio.gather(*self._publish_tasks, return_exceptions=True)
```

Channel: `portfolio.rollup.dirty`. Payload: `str(account_id)`. **`_publish_dirty` catches all exceptions** — a Redis blip never blocks the NLV write path. **On publish failure**, increment `portfolio_rollup_publish_failures_total` and rely on the WS 30 s heartbeat to mask up to one 30 s window of UI staleness.

---

## 6. WebSocket gateway (`app/api/ws_portfolio.py`)

**Endpoint:** `/ws/portfolio/rollup?base=GBP`.

**Connection lifecycle (in order):**

1. **ARCHITECT [HIGH #2] CSWSH origin check** — compare `ws.headers.get("origin")` against `app.state.cors_origins`. Mismatch → close `WS_1008_POLICY_VIOLATION` reason="origin". Mirrors `ws_quotes.py:156-158`.
2. **Auth via `require_admin_jwt_ws`** — closes `WS_1008_POLICY_VIOLATION` reason="auth" on failure (NOT a custom 4401 — the helper produces 1008 per `ws_auth.py:50,57,61,66`).
3. Accept upgrade; emit initial `snapshot` frame.

**Connection cap:** 20 concurrent (single-user dashboard).

**Frame format:** JSON with explicit `"version": 1` field (ARCHITECT [MED #4]) — FE checks version, unknown version → close WS + revert to REST polling.

**Parallel gateway, not co-opted.** `ws_quotes.py` keeps its MessagePack subprotocol + per-symbol conflation untouched. `ws_portfolio.py` is a net-new file with its own auth, debounce, and frame schema. The two share `require_admin_jwt_ws` but nothing else.

```
client → WS
       ← {"version":1, "type": "snapshot", "payload": <RollupLive>}        (initial)
       ← {"version":1, "type": "snapshot", "payload": <RollupLive>,
          "partial": true, "fx_stale_accounts": [...]}                       (partial FX outage)
       ← {"version":1, "type": "snapshot", "payload": <RollupLive>}        (debounced fire)
       ← {"version":1, "type": "stale", "account_ids": [...]}              (30s heartbeat)
```

**Debounce loop with per-connection compute cache:**

**ARCHITECT [HIGH #3]:** the canonical pattern is `pubsub.listen()` + `asyncio.Event` (used in 4/5 existing pubsub consumers — `orders_sse.py:111`, `config_cache.py:70`; NOT `get_message` polling). Plus a 250 ms per-connection compute cache so a publish burst doesn't trigger N recomputes. Plus `asyncio.wait_for` on every send so a slow client can't backpressure the loop.

```python
_COMPUTE_CACHE_TTL_S = 0.25
_DEBOUNCE_S = 0.5
_SEND_TIMEOUT_S = 2.0

async def _pump(ws, redis, base_currency):
    pubsub = redis.pubsub()
    await pubsub.subscribe("portfolio.rollup.dirty")
    dirty = asyncio.Event()

    async def _listen():
        async for _msg in pubsub.listen():
            dirty.set()

    listener = asyncio.create_task(_listen())
    last_send = 0.0
    last_compute = 0.0
    last_payload: dict | None = None
    try:
        while connected:
            try:
                await asyncio.wait_for(dirty.wait(), timeout=_DEBOUNCE_S)
            except TimeoutError:
                pass
            dirty.clear()
            now = time.monotonic()
            if (now - last_send) < _DEBOUNCE_S:
                continue
            if (now - last_compute) < _COMPUTE_CACHE_TTL_S and last_payload is not None:
                payload = last_payload
            else:
                payload = await service.compute_live(base_currency)
                last_payload = payload
                last_compute = now
            try:
                await asyncio.wait_for(
                    ws.send_json({"version": 1, "type": "snapshot", "payload": payload}),
                    timeout=_SEND_TIMEOUT_S,
                )
            except TimeoutError:
                portfolio_rollup_ws_send_timeout_total.inc()
                await ws.close(code=WS_1011_INTERNAL_ERROR)
                break
            last_send = now
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
```

A burst of 4 sidecars publishing within 500 ms collapses to one snapshot. Matches Phase 7b.1 quote-bus conflation philosophy (4–10/s for quotes; 2/s max for rollup is appropriate for daily-cadence data).

**Heartbeat:** every 30 s the server emits a `stale` message listing accounts whose `last_nlv_at` is >5 min old. FE renders staleness badges next to per-account rows.

**Per-connection compute:** N WS clients = N recomputes per debounce window. Acceptable for single-user dashboard (max ~2 tabs). If the cap is ever raised, add a shared compute cache (Redis-backed, 250 ms TTL) — out of scope.

---

## 7. Frontend

### 7.1 Service module — `frontend/src/services/portfolio/`

Mirrors `frontend/src/services/sizing/` (Phase 10b.1):

```
portfolio/
├── types.ts                     RollupLive, RollupCurve, RollupDrill,
│                                AssetClassExposure, PerAccount, InstrumentExposure
├── api.ts                       fetchJson<T>(...) wrappers
├── useRollupLive.ts             TanStack-Query + WS subscriber hybrid
├── useRollupCurve.ts            TanStack-Query for the curve endpoint
├── useRollupDrill.ts            TanStack-Query, lazy (only fires on drill open)
└── useRollupLive.test.tsx       hook tests
```

#### `useRollupLive` — hybrid REST + WS + poll fallback

1. Initial fetch via `GET /api/portfolio/rollup` for fast first paint.
2. Open WS to `/ws/portfolio/rollup`; on each `snapshot` frame call `queryClient.setQueryData(['portfolio','rollup',base], payload)` so consumers re-render without a refetch.
3. On WS `close` event, enable TanStack `refetchInterval: 10000` until WS reconnects; on reconnect, disable the interval again.

Pattern borrowed from `useQuoteSubscription` in `services/quotes/`. Slow networks / WS-blocked corporate proxies still get a working page at 10 s polling.

### 7.2 Route + page

**Route:** `frontend/src/routes/portfolio.rollup.tsx` (TanStack-Router file-based, per Phase 3 conventions). URL search params: `base` (persisted to localStorage), `window` (not persisted).

**Page:** `frontend/src/features/portfolio/RollupPage.tsx`. Layout (matches the wireframe approved in brainstorm):

```
┌──────────────────────────────────────────────────────────────────────┐
│  Total NLV £142,380     +£284 today (+0.20%)    Open £98,210         │
│  Base [GBP ▾]  ·  History since 2026-05-12                           │
├──────────────────────────────────────────────────────────────────────┤
│  ▸ Intraday  ▸ 30 days  ▸ 1 year                                     │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  klinecharts area chart of total_nlv_base by bucket            │  │
│  └────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────┤
│  Per account                    │  Exposure by asset class           │
│  ─────────────────────────────  │  ─────────────────────────────     │
│  IBKR-main-paper   £82,100      │  Equity   £72,300  ▸ drill         │
│  Schwab-main-paper £38,420      │  Crypto   £18,400  ▸ drill         │
│  Futu-main-paper   £21,860      │  Cash      £7,510                  │
└──────────────────────────────────────────────────────────────────────┘
```

### 7.3 Components (boundary layers per CLAUDE.md)

**Reused (existing):**
- `components/primitives/Card`, `Button`, `Drawer`, `Select`
- `components/patterns/StatCard`, `ChartArea` (klinecharts wrapper from Phase 9)
- `components/patterns/StaleBadge` (Phase 5a NLV cache UI)

**New (`features/portfolio/`):**
- `RollupKpiBar` — header strip
- `RollupCurveChart` — klinecharts area chart with window toggle
- `PerAccountTable`
- `AssetClassExposureList`
- `AssetClassDrillDrawer` — opens as right-side drawer on row click; lists instruments with cap utilisation as horizontal bars; `verdict: warn` tints amber, `verdict: block` tints red. No "fix" buttons — informational only.

### 7.4 Base-currency selector

Bound to a Zustand-scoped store entry (`portfolioRollupBase`). Persisted to `localStorage` via Zustand's persist middleware. Default GBP. Validated against the hard-coded supported set `{GBP, USD, EUR, HKD, JPY, AUD}` client-side before fetch.

**ARCHITECT [MED #7]:** Zustand persist `migrate` callback validates the persisted value on read; resets to GBP if invalid (e.g., user hand-edited localStorage to `'XYZ'`). Prevents an indefinite-422 loop.

```typescript
persist({
  storage: createJSONStorage(() => localStorage),
  version: 1,
  migrate: (state, version) => {
    const SUPPORTED = new Set(['GBP','USD','EUR','HKD','JPY','AUD']);
    if (!SUPPORTED.has((state as any)?.portfolioRollupBase)) {
      return { ...(state as object), portfolioRollupBase: 'GBP' };
    }
    return state;
  },
})
```

### 7.5 Window toggle

Local `useState` in `RollupPage`; not persisted. Reflected in URL search params so deep links work (`/portfolio/rollup?window=30d`).

---

## 8. Observability

Six Prometheus metrics + one gauge:

| Metric | Type | Labels | Emission site |
|---|---|---|---|
| `portfolio_rollup_compute_total` | Counter | `endpoint, base_currency` | REST handlers per successful GET |
| `portfolio_rollup_compute_latency_seconds` | Histogram | `endpoint` | wraps compute path (REST + WS) |
| `portfolio_rollup_fx_unavailable_total` | Counter | `pair` | catches `PreviewUnavailable(503)` |
| `portfolio_rollup_snapshot_writes_total` | Counter | — | writer hook on successful INSERT |
| `portfolio_rollup_snapshot_write_errors_total` | Counter | — | writer hook on exception (never re-raises) |
| `portfolio_rollup_ws_publish_total` | Counter | — | every `redis.publish` |
| `portfolio_rollup_ws_connections` | Gauge | — | WS gateway |

---

## 9. Error matrix

| Surface | Error | HTTP / Frame | UX |
|---|---|---|---|
| REST | invalid base ccy | 422 `invalid_base_currency` | inline toast on selector |
| REST | unknown asset_class | n/a — returns empty list | drawer renders "no data" |
| REST | invalid window | 422 `invalid_window` | curve falls back to intraday |
| REST | FX rate missing | 503 `fx_rate_unavailable {pair}` | KPI bar shows last-known + "FX stale" badge |
| REST | rate limited | 429 `rate_limited` | client backs off 2s |
| REST | DB unavailable | 503 propagated | full-page error boundary |
| WS | auth fail | 4401 close | client surfaces "session expired" |
| WS | server compute fail | `{type:"error", code:"..."}` | client keeps last snapshot + amber banner |
| Writer | INSERT fails | logged, metric ticked, NLV update **succeeds** | next refresh in 30s fills gap |

**Fail policies:**

- **Writer hook → fail-OPEN.** Lose at most one bucket of history; never block an NLV write.
- **REST/WS reader → fail-CLOSED on FX.** 503 the whole compute rather than silently substitute zero.

---

## 10. Security checklist

- **No hardcoded secrets.** No new credentials in this phase.
- **Input validation:** all 3 REST endpoints + 1 WS endpoint validate `base` against `^[A-Z]{3}$`; `window` against Literal union; `asset_class` against open-set with denylist.
- **SQL injection:** all queries via SQLAlchemy bound params; no f-string SQL.
- **XSS:** FE renders only numeric + enum data; `display_name` from `instruments` is escaped by React's default text rendering. No `dangerouslySetInnerHTML`.
- **CSRF:** N/A — all endpoints are GET. No nonce required.
- **AuthN/AuthZ:** every REST + WS endpoint requires JWT via `require_admin_jwt` / `require_admin_jwt_ws`.
- **Rate limiting:** `SlidingWindowRateLimiter` at 10/s burst per `(jwt_subject, route)` on REST; WS connection cap at 20 concurrent.
- **Error messages:** sanitised — `{"error": code}` shape. Raw exception strings logged server-side via `log.exception`, never echoed.
- **Error bodies redact** `account_number`, `gateway_label`, raw `last_nlv` values. **Allowed echoes:** `account_id` UUID and `instrument_id` BIGINT are FE handles per CLAUDE.md "AccountResponse boundary stripping" doctrine and may appear in responses (e.g., `stale_accounts`, `fx_stale_accounts`). ARCHITECT [MED #6] softens the Phase 10b.1 "no IDs in errors" line which was overly strict for this surface.
- **No new `.env` keys.** Runtime config (if any) through `app_config`.

### Logging

`structlog` throughout; bound context per request: `rollup_base`, `rollup_window`, `rollup_endpoint`.

**Never log at INFO or above:**
- Raw NLV amounts (DEBUG only).
- Account IDs without anonymisation (extend `core/logging.py` redaction set if needed).
- FX rates (noise).

---

## 11. Test plan (heavy goldens)

### 11.1 Backend (~28 tests)

| Layer | File | Tests |
|---|---|---|
| Unit — service math | `backend/tests/services/test_portfolio_rollup_service.py` | 12: NLV sum happy path, multi-currency conversion goldens (USD+HKD+GBP positions in one account; 2× HKD/GBP rate stress), missing-FX → 503 raise, intraday curve construction from raw snapshots, 30d curve from 1h CAGG, 1y curve from 1d CAGG, drill-down with all 3 verdicts, drill-down no-cap-set fallback, exposure-by-asset-class with shorts (negative pct), exposure when position has `instrument_id IS NULL` (skipped per gate behaviour), `history_since` returns None when no snapshots exist |
| Unit — writer hook | `backend/tests/services/test_balance_snapshot_writer.py` | 5: happy insert, ON CONFLICT no-op on duplicate ts, exception swallowed + metric ticked, publish on Redis success, publish swallowed on Redis fail |
| Unit — rate limiter | `backend/tests/services/test_portfolio_rate_limiter.py` | 3: burst cap, window expiry, separate buckets per `(subject, route)` |
| Integration — REST | `backend/tests/integration/test_portfolio_rollup_api.py` | 5: GET live shape + auth, GET curve all 3 windows, GET drill, 429 on burst, 503 on FX-unavailable |
| Integration — WS | `backend/tests/integration/test_portfolio_rollup_ws.py` | 3: connect + initial snapshot, debounced republish on pubsub fire, disconnect cleanup |

### 11.2 Frontend (~11 tests)

| File | Tests |
|---|---|
| `useRollupLive.test.tsx` | 4: initial fetch, WS merge, WS-disconnect-to-poll, error surface |
| `useRollupDrill.test.tsx` | 2: lazy fire, cache shape |
| `RollupPage.test.tsx` | 2: render + 3 panels visible, base-ccy selector renders + persists |
| `AssetClassDrillDrawer.test.tsx` | 3: opens on click, closes on Escape, renders verdict colours |

### 11.3 E2E (3 Playwright)

`tests/e2e/phase10b2-rollup.spec.ts`:

1. Page render: navigate to `/portfolio/rollup`, assert KPI bar + curve + 2 lower panels visible.
2. Window toggle: click 30d → URL search-param updates → chart re-renders.
3. Drill: click asset-class row → drawer opens → first instrument row visible.

### 11.4 Multi-currency golden vectors

Pinned fixtures for the trickier conversions (per Q7 — heavy goldens):

| Vector | Setup | Expected |
|---|---|---|
| GV1 — single USD account, base GBP | `last_nlv=10000 USD, FX USD/GBP=0.7912` | `total_nlv_base = 7912.00 GBP` |
| GV2 — USD + HKD account, base GBP | `10000 USD + 50000 HKD, FX USD/GBP=0.7912, HKD/GBP=0.1015` | `total_nlv_base = 7912 + 5075 = 12987.00 GBP` |
| GV3 — base = native currency | `10000 USD, base=USD` | `total_nlv_base = 10000.00 USD, fx_rate=1.0` |
| GV4 — short position in exposure | `-100 AAPL @ 200 USD, base GBP` | `short_notional_base = 15824.00 GBP, pct_of_nlv negative` |
| GV5 — stale account (last_nlv_at > 5min) | one account with `last_nlv_at = now() - 6min` | included in `stale_accounts` list |
| GV6 — FX cache miss all accounts | `_fx_rate` raises for every account currency | endpoint returns **503** `fx_rate_unavailable` |
| GV7 — drill with all 3 verdicts | 3 instruments, util 50% / 85% / 110% | verdicts: ok / warn / block |
| GV8 — drill with no cap | instrument with no `risk_limits` row | `cap_pct=None, utilisation_pct=None, verdict=ok` |
| **GV9** — negative NLV (margin call) | `last_nlv=-1500 USD, base GBP` (ARCHITECT [CRIT #1]) | snapshot inserts; `total_nlv_base` includes -1186.80 GBP contribution |
| **GV10** — partial FX outage | 3 accounts in USD/HKD/GBP; HKD/GBP rate missing (ARCHITECT [HIGH #4]) | response 200, `partial=true`, USD+GBP accounts present with `nlv_base`, HKD account has `fx_stale=true` + `nlv_base=null`, HKD UUID in `fx_stale_accounts`, `total_nlv_base` excludes HKD |
| **GV11** — null NLV (fresh account) | account with `last_nlv=NULL, last_nlv_at=NULL` | account included in `accounts` with `nlv_base=null, status="initialising"`; excluded from `total_nlv_base` and `stale_accounts` |
| **GV12** — weekend gap in curve | `compute_curve(window='30d')` over a Fri-Mon range | gaps in raw between Fri 22:00 UTC and Sun 22:00 UTC; curve buckets are sparse (not interpolated to zero) |

**TZ note:** all CAGG buckets are UTC-day boundaries. FE shifts to user TZ for display via the existing `useTimezone()` hook; daily buckets straddle local midnight as expected.

**Total: ~46 new tests** (was 42; +4 goldens GV9–GV12). Reviewer chain at end of each chunk.

---

## 12. Chunking

**ARCHITECT [MED #5]:** Chunk B split to three sub-chunks (B', B'', B''') to keep each under reviewer-chain working-context limits (~600–800 LOC diff per chunk).

| Chunk | Scope | Commits |
|---|---|---|
| **A** | Schema + writer hook | A1 Alembic 0039 (with source_label CHECK); A2 Alembic 0040 (with autocommit_block + retention + materialized_only=false); A3 writer hook in `brokers.py:1416` with nested-SAVEPOINT + tracked publish task set (Codex; Qwen fallback); A4 writer tests (5); A5 reviewer chain | ~5 |
| **B'** | Schemas + `compute_live` | B'1 Pydantic schemas (RollupLive/Curve/Drill + partial/fx_stale fields); B'2 `compute_live` per-account FX isolation + tests; B'3 reviewer chain | ~3 |
| **B''** | Curve + drill | B''1 `compute_curve` (3 windows) + tests; B''2 `drill_asset_class` + tests; B''3 reviewer chain | ~3 |
| **B'''** | Endpoints + rate limiter + metrics | B'''1 portfolio rate limiter (fresh instance, `(jwt, "portfolio")` key); B'''2 3 REST endpoints + integration tests; B'''3 7 Prometheus metrics; B'''4 reviewer chain | ~4 |
| **C** | WS gateway + pubsub publisher | C1 `ws_portfolio.py` (CSWSH + listen() + 250ms cache + send timeout + heartbeat); C2 Redis publish wired in A3 writer; C3 WS integration tests (3 + CSWSH rejection); C4 reviewer chain | ~4 |
| **D** | Frontend `/portfolio/rollup` route + drill drawer | D1 regenerate `api-generated.ts`; D2 services/portfolio module (with frame version check); D3 hook tests; D4 RollupPage + 4 new components; D5 drill drawer + tests; D6 reviewer chain | ~6 |
| **E** | Playwright + close-out | E1 Playwright; E2 final 5-reviewer chain; E3 close-out (CHANGELOG / CLAUDE.md / TASKS.md / memory) + v0.14.0 tag | ~3 |

**Total: ~28 commits, 7 chunks.** Bigger than Phase 10b.1 (20 commits / 5 chunks) — reflects heavier scope (history schema + WS gateway + drill drawer + heavy goldens).

### Model routing (CLAUDE.md table)

| Task character | Route |
|---|---|
| Alembic migrations (A1, A2) | Qwen (schema-driven, structured) |
| Writer hook touching `brokers.py` (~1900 LOC, multi-site) | Codex (multi-site judgement) |
| New service files + tests (B2–B4, C1) | Qwen (self-contained module writes) |
| FE service module + page (D1–D5) | Qwen for boilerplate; Opus direct for `RollupPage` integration |
| Reviewer chains | spec / python-haiku, code / security / db-sonnet, ARCHITECT-opus once-per-phase |

---

## 13. Deferrals

| Item | Reason | Phase target |
|---|---|---|
| Strategy-tagged P&L attribution | needs `fills.strategy_id` / `bot_id` | Phase 20+ |
| Historical NLV backfill from broker APIs | per-broker work; awkward APIs; scope-doubler | none (operator runbook only) |
| `account_balances` table decoupling | rewrites 5+ services | Phase 24 (recorded in ROADMAP.md) |
| Exact historical FX (per-bucket rates) | needs FX history table | Phase 23 |
| Add/remove account toggle on rollup | YAGNI for personal-use dashboard | none |
| PWA / offline / CSV export | Phase 25 covers PWA; Phase 23 covers tax export | Phase 23/25 |
| Multi-replica WS compute cache | single-replica today | Phase 24 |
| Drill audit (write `risk_decisions` on near-cap) | drill is informational | none |
| Drill drawer "Edit cap" affordance | informational read-only viewport in 10b.2; backend returns `verdict` but FE renders no actionable button (ARCHITECT [MED #8]) | Phase 11 |

---

## 14. Versioning

Per TASKS.md note: 10b.2 ships at **v0.14.0** (10b.1 was v0.13.0). ROADMAP.md natural numbering is already lapped — that's documented and consistent across 10a / 10a.5 / 10b.1 / 10b.2.

---

## 15. Footguns (lessons from Phase 10b.1)

- **`_PORTFOLIO_RATE_LIMITER` is module-level singleton.** Reset via `_reset_limiter` autouse fixture in tests; copy fixture to any new test file that hits the endpoints.
- **`instrument_id` is BIGINT, not UUID.** Same trap as 10b.1.
- **`broker_accounts.gateway_label` is `"isa-paper"` / `"ibkr-paper"`** — use `capability_broker_id(label)` to extract broker_id, not `label.split("-")[0]`.
- **Pre-commit ruff hook rejects Unicode mathematical chars** (×, →). Use ASCII (`*`, `->`).
- **`StrEnum` required** (ruff UP042 rejects `class X(str, Enum)`).
- **`model_config = ConfigDict(extra="forbid")`** is canonical defence-in-depth — apply to every request/response Pydantic model.
- **TimescaleDB CAGG initial backfill must use `op.get_context().autocommit_block()`** (NOT raw `op.execute("CALL refresh_continuous_aggregate(...)")` — see ARCHITECT [CRIT #3] retraction of the Phase 10b.1 alembic 0038 precedent). `refresh_continuous_aggregate` is a PROCEDURE that issues internal COMMIT and rejects running inside a transaction; alembic's `transaction_per_migration=True` makes the naive pattern silently fail when the source hypertable has actual data to backfill.
- **`DROP MATERIALIZED VIEW ... CASCADE`** in CAGG downgrade — same as Alembic 0038.
- **Every CAGG must declare retention explicitly** (`add_retention_policy`) even if cap is high. Documented project-wide pattern (ARCHITECT [MED #2]).
- **`pubsub.listen()` is the canonical consumer pattern**, NOT `pubsub.get_message()` polling — 4/5 existing consumers use `listen()` (orders_sse, config_cache, etc.). The Phase 10b.1 spec implicitly mixed the two; this spec pins `listen()`.

---

## 16. ARCHITECT-REVIEW outcome

Review run 2026-05-12 (opus). 19 findings: 3 CRIT + 6 HIGH + 7 MED + 3 LOW. Per project rule (`feedback_architect_findings_apply_through_medium.md`), CRIT + HIGH + MED applied inline above. Summary:

| Tag | § | Topic | Resolution |
|---|---|---|---|
| CRIT #1 | 4.1 | `nlv >= 0` CHECK rejects margin-call writes | Constraint dropped; GV9 golden added |
| CRIT #2 | 2, 5.1 | `positions.market_value_base` does not exist | Adopt `qty * avg_cost * multiplier` (risk_service approximation); "at cost basis" badge on UI |
| CRIT #3 | 4.2 | Sync CAGG backfill fails inside `transaction_per_migration` TX | `autocommit_block()` pattern; precedent retraction logged for 0038 |
| HIGH #1 | 4.3 | Same-SAVEPOINT writer rolls back NLV on INSERT failure | Two-level nested SAVEPOINT pattern; writer test added |
| HIGH #2 | 6 | WS missing CSWSH origin check; wrong close code | CSWSH check + 1008 close (not 4401) |
| HIGH #3 | 6 | Per-conn compute load + no send timeout + wrong pubsub pattern | 250 ms cache + `listen()` + `asyncio.wait_for` send |
| HIGH #4 | 5.1 | Whole-rollup 503 on single FX miss | Per-account FX fault isolation; 200 partial; GV10 golden |
| HIGH #5 | 5.3 | Naked `create_task` GC-strand risk | Tracked task set + lifespan cancel/gather |
| HIGH #6 | 5.2 | Rate limiter signature mismatch | Fresh instance with `(jwt, "portfolio")` key |
| HIGH #7 | 11.4 | Missing edge-case goldens | GV9 negative-NLV, GV10 partial-FX, GV11 null-NLV, GV12 weekend-gap |
| MED #1 | 4.1 | `source_label` cardinality | CHECK regex + length limit |
| MED #2 | 4.2 | CAGG retention asymmetry | Explicit `add_retention_policy` on both CAGGs |
| MED #3 | 4.2 | CAGG coverage gap deploy→first refresh | `materialized_only = false` real-time aggregation |
| MED #4 | 6 | Frame schema versioning | `"version": 1` on every frame; FE version-check |
| MED #5 | 12 | Chunk B too large for reviewer chain | Split into B'/B''/B''' (7 chunks total) |
| MED #6 | 10 | Account-ID redaction overly strict | Softened — UUIDs are FE handles per CLAUDE.md |
| MED #7 | 7.4 | localStorage corruption indefinite-422 loop | Zustand persist `migrate` callback resets to GBP |
| MED #8 | 13 | Drill drawer cap-edit affordance | Deferred to Phase 11 with explicit row |
| LOW #1 | 3 | Diagram caption inconsistency | Accepted; minor cosmetic |
| LOW #2 | 11.1 | `pytest.mark.asyncio` decoration unstated | Implicit per pytest-asyncio strict mode; noted |
| LOW #3 | 14 | Phase 10 versioning collision residue | TASKS.md already documents the resolved lapping |

LOWs documented; not applied inline per project rule.

---

## 17. Acceptance criteria

The phase closes at v0.14.0 when:

1. All 5 chunks (A–E) shipped with reviewer chain run at each chunk boundary.
2. ~42 new tests passing (BE 28, FE 11, E2E 3).
3. Final 5-reviewer chain across A+B+C+D shows 0 CRIT / 0 HIGH (or all HIGH applied inline).
4. ARCHITECT-REVIEW pass complete with CRIT+HIGH+MED applied inline (per project rule `feedback_architect_findings_apply_through_medium.md`).
5. `/portfolio/rollup` reachable in production; WS subscription confirmed via browser devtools.
6. CHANGELOG / CLAUDE.md / TASKS.md / memory updated.
7. v0.14.0 tagged + pushed to origin/main.
8. Memory file `phase10b2_shipped.md` indexed in MEMORY.md.
