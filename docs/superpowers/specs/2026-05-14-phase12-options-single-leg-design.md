# Phase 12 — Options Single-Leg Design

**Date:** 2026-05-14
**Version target:** v0.12.x
**Status:** approved for planning (architect review applied 2026-05-14)

---

## Overview

Phase 12 adds single-leg options trading across IBKR, Schwab, Alpaca, and Futu HK. It ships:

1. An option chain viewer at `/options/chain` with live bid/ask/IV/Greeks streaming
2. Strike picker + `OptionDetailsSection` injected into the existing `TradeTicketModal`
3. Single-leg place/modify/cancel wired through the existing order pipeline
4. An exercise elections page at `/options/events`
5. **Architectural pillar #4**: `contract_details` JSONB on `instruments` with a Pydantic discriminated union — the foundation for Phases 13 (multi-leg), 14 (futures), 15 (forex/crypto), 16 (bonds)

Greeks are displayed but **not** wired into the risk gate (deferred to a later phase).

---

## Brokers in scope

| Broker | Chain data | Trade execution | Exercise/Assign |
|--------|-----------|-----------------|-----------------|
| **Schwab** | Primary for USD options (`GET /chains`, `GET /expirationchain`) | Yes | No (not in Schwab API) |
| **IBKR** | Fallback for USD; primary where Schwab unavailable | Yes | Yes (`exerciseOptions`) |
| **Alpaca** | Fallback for USD (`/v2/options/contracts`) | Yes | No |
| **Futu HK** | Primary for HKD options (`get_option_chain`, `get_option_expiration_date`) | Yes | No (Futu HK API limitation) |

Chain source routing is config-driven — see §Configuration below.

---

## Configuration

### `option_chain_sources` (CRIT-3)

- **Namespace:** `quote_engine`
- **Key:** `option_chain_sources`
- **Schema:** `dict[str, list[str]]` — maps ISO currency code to ordered list of source IDs
- **Default value stored in `app_config` at first boot:**
  ```json
  {"USD": ["schwab", "ibkr", "alpaca"], "HKD": ["futu"]}
  ```
- **Validation:** on load, assert every source ID exists in `broker_registry`; reject unknown sources with a startup warning (fail-open — use remaining valid sources).
- **Invalidation:** pubsub channel `app_config:invalidate:option_chain_sources` (same pattern as `risk_limits`). `OptionChainService` subscribes at lifespan start and reloads in-memory priority list on message.
- **Admin endpoint:** `PUT /api/admin/quote-engine/option-chain-sources` (JWT admin + CSRF nonce). Writes to `app_config`, publishes invalidation.

---

## Data Model

### Migration 0046

Five changes (MED-7: enum extension must run outside transaction):

```python
# upgrade() preamble — outside main transaction
with op.get_context().autocommit_block():
    op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'OPTION'")
```

Then inside the transaction:

1. **Rename `instruments.meta` → `instruments.contract_details`** — `op.alter_column("instruments", "meta", new_column_name="contract_details")`. No data change; existing `{}` rows remain valid.
2. **Backfill `contract_details` discriminator** (MED-2) — one-shot UPDATE:
   ```sql
   UPDATE instruments
   SET contract_details = jsonb_set(contract_details, '{asset_class}', to_jsonb(asset_class::text))
   WHERE contract_details != '{}' AND contract_details->>'asset_class' IS NULL;
   ```
   Followed by a validation pass: any row whose `contract_details` fails `parse_contract_details()` aborts the migration.
3. **New `option_greeks` table** — persisted Greeks for held/traded contracts only.
4. **New `exercise_elections` table** — idempotent election log with partial unique index.

### `contract_details` JSONB — Pydantic discriminated union (HIGH-1)

`NonOptionDetails` uses a **closed enum** of known non-option asset classes, enabling native Pydantic v2 discriminator support and fail-fast on unknown values:

```python
NonOptionAssetClass = Literal["", "STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "CRYPTO", "FOREX"]

class NonOptionDetails(BaseModel):
    """All non-option instruments. Existing {} rows deserialise with asset_class=""."""
    asset_class: NonOptionAssetClass = ""

class OptionDetails(BaseModel):
    asset_class: Literal["OPTION"] = "OPTION"
    underlying_canonical_id: str    # e.g. "SPY-STOCK-USD-NYSE"
    strike: Decimal                  # e.g. Decimal("450.00")
    expiry: date                     # e.g. date(2025, 1, 17)
    put_call: Literal["C", "P"]
    multiplier: int                  # 100 standard US, 50 mini, varies HK
    style: Literal["A", "E"] = "E"  # American / European

# FutureDetails, ForexDetails etc. added additively in Phases 14/15 — no changes to
# existing branches required.
ContractDetails = Annotated[
    NonOptionDetails | OptionDetails,
    Field(discriminator="asset_class")
]
```

`parse_contract_details(raw: dict) -> ContractDetails` uses `TypeAdapter(ContractDetails).validate_python(raw)`. Unknown `asset_class` values raise `ValidationError` (fail-fast — not silently swallowed). All consumers use `isinstance(details, OptionDetails)` to branch.

### Canonical ID format for option contracts (MED-1)

To avoid mixing separators with the existing stock: prefix convention, option canonical IDs use the same colon-separated format:

```
option:{UNDERLYING_SYMBOL}:{EXCHANGE}:{YYMMDD}:{P|C}:{STRIKE_DECIMAL}
```

Example: `option:SPY:CBOE:250117:C:450.00`

This is grep-consistent with `stock:AAPL:US` and `etf:SPY:US`. Built deterministically by `InstrumentResolver.find_or_create_option`. The underlying symbol is the human-readable ticker, not the canonical_id, so the option canonical_id is self-describing.

### `option_greeks` table (CRIT-2, MED-3)

```sql
CREATE TABLE option_greeks (
    instrument_id  BIGINT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    delta          NUMERIC(12, 6),   -- MED-3: widened from (8,6)
    gamma          NUMERIC(12, 6),
    theta          NUMERIC(12, 6),   -- can exceed ±10 for high-multiplier contracts
    vega           NUMERIC(12, 6),
    rho            NUMERIC(12, 6),
    iv             NUMERIC(12, 6),   -- implied vol as decimal (0.18 = 18%)
    iv_rank        NUMERIC(5, 2),    -- 52-week IV percentile; NULL until Phase 18
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON option_greeks (updated_at);
```

**CRIT-2 invariant — `upsert` guard:** `OptionGreeksService.upsert` checks before writing:

```python
# Refuse to persist Greeks for contracts not held or recently traded
exists = await db.scalar(
    select(1).where(
        or_(
            exists(select(Position).where(Position.instrument_id == instrument_id)),
            exists(select(Order).where(
                Order.instrument_id == instrument_id,
                Order.created_at >= date.today()
            ))
        )
    )
)
if not exists:
    return  # silently skip — chain-browse caller gets ephemeral Redis Greeks only
```

**Eviction:** APScheduler job every 60s, year-round (not market-hours-gated), deletes `updated_at < now() - interval '5 minutes'`.

**Observability:** `option_greeks_rows_total` Prometheus Gauge (set after each eviction run).

**MED-3 clamping:** `GreeksSnapshot.__post_init__` clamps each field to `(-9999.999999, 9999.999999)` and increments `option_greeks_clamped_total{field}` Counter on clamp.

### `exercise_elections` table (HIGH-5)

```sql
CREATE TABLE exercise_elections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key  UUID NOT NULL UNIQUE,           -- client-supplied; retries return original row
    account_id       UUID NOT NULL REFERENCES broker_accounts(id),
    instrument_id    BIGINT NOT NULL REFERENCES instruments(id),
    action           TEXT NOT NULL CHECK (action IN ('EXERCISE', 'DO_NOT_EXERCISE', 'LAPSE')),
    qty              NUMERIC(20, 8) NOT NULL,
    status           TEXT NOT NULL DEFAULT 'submitted'
                       CHECK (status IN ('submitted', 'confirmed', 'failed')),
    broker_ref       TEXT,
    error_reason     TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prevent duplicate elections for same contract on same day (partial unique index)
CREATE UNIQUE INDEX exercise_elections_one_per_day
    ON exercise_elections (account_id, instrument_id)
    WHERE created_at::date = CURRENT_DATE AND status != 'failed';
```

CSRF nonce is **single-use** (consumed via `consume_confirmation_nonce` — existing pattern). The `idempotency_key` is client-supplied (UUID in request body). Retries with the same key return the original row without re-calling the sidecar.

### Chain browse data (Redis, not Postgres)

Key: `options:chain:{underlying_canonical_id}:{expiry_iso}:{source}`
TTL: 30s during market hours, 300s outside.
Value: JSON list of `OptionChainRow` objects (strike, put_call, bid, ask, iv, delta, gamma, theta, vega, oi, volume, conid, multiplier).

**Instruments are NOT created for chain browse rows.** An `instruments` row + `symbol_aliases` row is created by `InstrumentResolver.find_or_create_option` only when:
- The user clicks a strike to open TradeTicketModal (order-intent), or
- A position for that contract arrives from a broker sync.

---

## Proto Changes (`broker.proto`)

### New RPCs

```protobuf
rpc GetOptionChain(OptionChainRequest) returns (OptionChainResponse);
rpc GetOptionExpirations(OptionExpirationsRequest) returns (OptionExpirationsResponse);
rpc GetOptionGreeks(OptionGreeksRequest) returns (OptionGreeksResponse);
rpc ExerciseOption(ExerciseOptionRequest) returns (ExerciseOptionResponse);
```

### Key messages

```protobuf
message OptionChainRequest {
  string underlying_symbol = 1;
  string expiry_date        = 2;  // ISO "2025-01-17"
  string currency           = 3;  // "USD" | "HKD"
  int32  strike_count       = 4;  // strikes around ATM (max 60 USD, 40 HKD)
}

message OptionChainRow {
  string conid         = 1;
  double strike        = 2;
  string put_call      = 3;   // "C" | "P"
  double bid           = 4;
  double ask           = 5;
  double iv            = 6;
  double delta         = 7;
  double gamma         = 8;
  double theta         = 9;
  double vega          = 10;
  int64  open_interest = 11;
  int64  volume        = 12;
  int32  multiplier    = 13;
  string exchange      = 14;
}

message OptionChainResponse {
  repeated OptionChainRow calls         = 1;
  repeated OptionChainRow puts          = 2;
  string                  source        = 3;
  int64                   fetched_at_ms = 4;
}

message OptionExpirationsRequest {
  string underlying_symbol = 1;
  string currency          = 2;
}

message OptionExpirationsResponse {
  repeated string expiry_dates = 1;  // ISO dates sorted ascending
}

message OptionGreeksRequest {
  string conid      = 1;
  string account_id = 2;   // some brokers require account context for Greeks
}

message OptionGreeksResponse {
  double delta         = 1;
  double gamma         = 2;
  double theta         = 3;
  double vega          = 4;
  double rho           = 5;
  double iv            = 6;
  double iv_rank       = 7;   // 0.0 if unavailable (Phase 18 provides 52w history)
  int64  fetched_at_ms = 8;
}

message ExerciseOptionRequest {
  string account_id = 1;  // UUID
  string conid      = 2;
  int64  qty        = 3;
  string action     = 4;  // "EXERCISE" | "DO_NOT_EXERCISE" | "LAPSE"
}

message ExerciseOptionResponse {
  bool   success    = 1;
  string broker_ref = 2;
  string message    = 3;
}
```

### `SymbolRef` — `OptionContractHint` oneof (HIGH-3)

The existing `source_meta bytes = 6` field in `SymbolRef` was reserved in Phase 7b.1 as a typed extension point for asset-class contract metadata. Confirm: the reservation comment in `broker.proto` reads `reserved 7 to 15` on `SymbolRef` — tags 7–15 are reserved for future extension, not tag 6. Tag 6 (`source_meta bytes`) is already defined and in use.

Rather than encoding msgpack inside the bytes field, define a proper oneof:

```protobuf
message OptionContractHint {
  string conid      = 1;
  double strike     = 2;
  string expiry_iso = 3;   // "2025-01-17"
  string put_call   = 4;   // "C" | "P"
  int32  multiplier = 5;
}

// Add to SymbolRef:
oneof contract_hint {
  OptionContractHint option_hint = 6;  // replaces source_meta bytes
  // FutureContractHint future_hint = 7;   Phase 14
  // ForexContractHint  forex_hint  = 8;   Phase 15
}
```

This replaces the `source_meta bytes` field with a typed oneof. Phases 13–16 add their hint types at tags 7–15 without changing existing fields.

For option chain quote subscriptions before an `instrument_id` exists, the WS gateway populates `option_hint` in `SymbolRef`. The quote gateway resolves to `instrument_id` lazily on first position/order event.

---

## Backend Services

### New services

**`app/services/options/chain_service.py`**

```python
class OptionChainService:
    async def get_expirations(self, underlying: str, currency: str) -> list[date]
    async def get_chain(
        self,
        underlying: str,
        expiry: date,
        strike_count: int = 20,
    ) -> OptionChainResponse  # Redis cache → singleflight sidecar fetch → stale fallback
    async def subscribe_strike_window(
        self,
        underlying: str,
        expiry: date,
        strikes: list[Decimal],
    ) -> list[str]  # canonical_ids or option_hint conids for quote subscriptions
```

Cache key: `options:chain:{underlying_canonical_id}:{expiry_iso}:{source}`, TTL 30s/300s.

**HIGH-4 — singleflight on cache miss:** `get_chain` uses an in-process `asyncio.Lock` per `(underlying_canonical_id, expiry_iso)` key (same pattern as `wol.py` circuit breaker). Concurrent misses for the same key share one sidecar call; subsequent waiters receive the cached result.

Stale-on-error: returns last cached value with `stale=True` if sidecar call fails or exceeds timeout (3s Schwab/Alpaca, 6s IBKR, 5s Futu HK). 503 only if no cache exists at all.

Registered as lifespan singleton at `app.state.chain_service`.

**`app/services/options/greeks_service.py`**

```python
class OptionGreeksService:
    async def upsert(self, instrument_id: int, greeks: GreeksSnapshot) -> None
    # upsert REFUSES to write if instrument has no position and no order today
    async def get(self, instrument_id: int) -> GreeksSnapshot | None
    async def evict_stale(self, older_than: timedelta = timedelta(minutes=5)) -> int
```

APScheduler 60s eviction job, year-round. Prometheus gauge `option_greeks_rows_total`.

**`app/services/options/exercise_service.py`**

```python
class ExerciseService:
    async def list_pending(self, account_id: UUID) -> list[ExerciseCandidate]
    async def elect(
        self,
        account_id: UUID,
        instrument_id: int,
        action: Literal["EXERCISE", "DO_NOT_EXERCISE", "LAPSE"],
        qty: Decimal,
        csrf_nonce: str,       # single-use via consume_confirmation_nonce
        idempotency_key: UUID, # client-supplied; retries return original row
    ) -> ExerciseResult
```

**MED-4 — exercise candidate filter:**
- `list_pending` uses `market_calendar.next_trading_days(5)` (not raw `date.today() + 5`) to correctly handle NYSE/HKEX holiday calendars.
- Intrinsic value computed from last underlying spot price via quote engine. Degrades to "expiring within 5 trading sessions" filter when spot is unavailable.

`elect()` flow:
1. Check `exercise_elections` for existing row with same `idempotency_key` → return it (idempotent).
2. Check partial unique index (one election per account+instrument per day) → 409 if already submitted.
3. Consume CSRF nonce (single-use).
4. Call `ExerciseOption` RPC on sidecar → write `exercise_elections` row with `status='submitted'`.
5. IBKR is the only supported broker; Schwab/Alpaca/Futu HK log the row with `status='confirmed'` and `broker_ref='broker_unsupported'`.

### Modified services

**`app/services/instruments.py` — `InstrumentResolver.find_or_create_option` (HIGH-2)**

`find_or_create_option` is a thin wrapper around the existing `resolve_or_create` primitive — it does NOT duplicate the in-process lock + ON CONFLICT + select-fallback logic:

```python
async def find_or_create_option(
    self,
    db: AsyncSession,
    underlying_canonical_id: str,
    strike: Decimal,
    expiry: date,
    put_call: Literal["C", "P"],
    multiplier: int,
    exchange: str,
    currency: str,
    source: str,
    conid: str,
) -> Instrument:
    canonical_id = _build_option_canonical_id(
        underlying_canonical_id, expiry, put_call, strike, exchange
    )
    details = OptionDetails(
        underlying_canonical_id=underlying_canonical_id,
        strike=strike, expiry=expiry, put_call=put_call,
        multiplier=multiplier, style="A" if currency == "USD" else "E",
    )
    return await self.resolve_or_create(
        db,
        canonical_id=canonical_id,
        asset_class=AssetClass.OPTION,
        primary_exchange=exchange,
        currency=currency,
        contract_details=details.model_dump(),  # stored in contract_details JSONB
        source=source,
        raw_symbol=conid,
    )
```

Called on order-intent and position sync only — never during chain browse.

**`app/services/orders_service.py` — multiplier-aware notional (CRIT-1)**

All three notional sites must be updated. `multiplier` is plumbed onto `RiskContext`:

```python
# RiskContext gains:
multiplier: int = 1   # 1 for non-options; contract multiplier for options

# Sites to update:
# 1. _native_notional (3 branches):
#    LIMIT:  qty * limit_price * multiplier
#    STOP:   qty * stop_price  * multiplier
#    market: qty * mid * 1.05  * multiplier

# 2. risk_service._check_buying_power (line ~380):
#    order_notional = ctx.qty * ctx.price * ctx.multiplier

# 3. RiskContext construction in _evaluate_risk_for_place_order:
#    multiplier = details.multiplier if isinstance(details, OptionDetails) else 1
#    ctx = RiskContext(..., multiplier=multiplier)
```

`ctx.price` remains the per-share/per-unit premium; `multiplier` is the separate contract size factor. This ensures `max_notional_per_order`, `daily_notional_cap`, and BP checks all see the correct notional.

**`app/services/orders_service.py` — contract expiry check (MED-8)**

```python
# contract_expired check uses exchange-aware calendar, not naive date.today()
if isinstance(details, OptionDetails):
    if market_calendar.is_past_expiry(details.expiry, instrument.primary_exchange):
        raise ContractExpiredError(...)
```

`market_calendar.is_past_expiry(expiry, exchange)` anchors to the exchange's timezone and trading calendar — not server local time.

### New API endpoints (`app/api/options.py`)

```
GET  /api/options/expirations?symbol=SPY&currency=USD
GET  /api/options/chain?symbol=SPY&expiry=2025-01-17&strikes=20
GET  /api/options/greeks/{instrument_id}
GET  /api/options/exercise                   # list pending elections
POST /api/options/exercise                   # submit election (CSRF nonce + idempotency_key in body)
GET  /api/options/events                     # exercise + assignment event log (last 30d)
PUT  /api/admin/quote-engine/option-chain-sources  # admin config (JWT admin + CSRF nonce)
```

All JWT-gated. `/chain` and `/expirations`: rate-limited 10/s per `jwt_subject` (anti-abuse; capacity protected by singleflight).

### New WebSocket (`app/api/ws_options.py`)

```
WS /ws/options/chain?symbol=SPY&expiry=2025-01-17
```

- Streams live bid/ask/IV/Greeks for visible strike window
- Conflated at 2 Hz
- Connection cap: 10; each connection subscribes to up to 40 strikes

**HIGH-6 — Quote-engine integration sub-section:**

For option contracts not yet in `instruments` (chain-browse window), the WS gateway maintains an in-process `dict[conid, OptionContractHint]` per connection. This map is:
- Populated when the WS client sends a `{type: "subscribe", strikes: [...]}` frame
- Torn down completely when the WS connection closes (no Redis subscription leak)
- TTL: 5 min idle per conid → unsubscribe at sidecar level

The quote gateway routes:
1. If `canonical_id` resolves in `instruments` → subscribe via existing `SubscriptionRegistry` path (canonical_id-keyed)
2. If not yet in `instruments` → subscribe via `OptionContractHint` path: sidecar `StreamQuotes` keyed by `conid` in `SymbolRef.option_hint`; published to Redis `quote.options.<conid>` (separate namespace from canonical quotes to avoid collision)

On first `OrderEvent` or position sync for a conid, `find_or_create_option` is called and the subscription migrates from the conid path to the canonical_id path.

**MED-5 — strike window cap:**
- BE enforces max 40 strikes per WS connection (Futu HK) / 60 for USD (Schwab/IBKR)
- If FE requests > cap: BE sends `{type: "subscription_capped", visible: [...conids...], dropped: [...conids...]}` frame
- Excess strikes receive quotes via 5s REST polling fallback (`useOptionChain` hook handles this)
- FE surfaces "Showing X of Y strikes — polling for remainder" notice

**Observability:** `quote_options_chain_subs_active` Prometheus Gauge (tracks live conid subscriptions across all WS connections).

**Heartbeat:** 30s; `{type: "stale", strikes: [...]}` on staleness.

---

## Prometheus Metrics (MED-6)

```
option_chain_fetch_seconds{source}              Histogram  — chain RPC latency per source
option_chain_fetch_total{source, outcome}        Counter    — outcome: ok|stale|timeout|error
option_expirations_fetch_total{source, outcome}  Counter
option_greeks_fetch_seconds{source}             Histogram
option_exercise_total{broker, action, outcome}   Counter    — outcome: ok|broker_unsupported|error
option_greeks_rows_total                         Gauge      — set after each eviction run
option_greeks_clamped_total{field}               Counter    — incremented on out-of-range vendor data
quote_options_chain_subs_active                  Gauge      — live conid WS subscriptions
```

---

## Frontend

### New files

```
frontend/src/
  routes/
    options.chain.tsx                  # /options/chain route
    options.events.tsx                 # /options/events route
  features/options/
    OptionChainPage.tsx
    OptionChainToolbar.tsx             # symbol input + expiry tabs + source badge
    OptionChainTable.tsx               # butterfly layout: calls | strike | puts
    OptionGreeksStrip.tsx              # Δ Γ Θ V IV row (reused in table + modal)
    OptionExpiryTabs.tsx               # horizontal scrollable expiry selector
    OptionDetailsSection.tsx           # injected into TradeTicketModal above sizing
    OptionEventsPage.tsx
    ExerciseElectionRow.tsx            # Exercise / DNE / Lapse buttons + CSRF nonce
    hooks/
      useOptionChain.ts                # TanStack Query + WS hybrid
      useOptionExpirations.ts          # TanStack Query, 5 min stale
      useExerciseElections.ts          # TanStack Query, refetch on focus
    types.ts
```

### `OptionChainTable` layout

Butterfly layout: calls left (green tint for ITM) | strike column (ATM highlighted amber) | puts right (red tint for ITM).

Columns: Bid · Ask · IV · Δ · OI | **Strike** | OI · Δ · IV · Bid · Ask

Click any row → opens `TradeTicketModal` pre-filled with contract (underlying, strike, expiry, put_call, conid). `find_or_create_option` is called at this point (order-intent).

Strike window: default 20 strikes (10 each side of ATM). Scroll loads more in increments of 10, up to the per-broker cap (40 HKD / 60 USD). ATM computed from last underlying spot price.

**LOW-3 — mobile fallback:** Below `md` breakpoint, the butterfly table collapses to a single-column list (strike + IV + Δ). Tapping a row opens a vertical detail sheet (call vs put toggle + full Greeks) before opening TradeTicketModal. Uses the same `<Card>` collapse pattern as the positions table.

### `OptionDetailsSection` in `TradeTicketModal`

Rendered when `instrument.asset_class === 'OPTION'`, inserted above the sizing section:

- Contract label: `SPY Jan 17 2025 450C`
- Sub-label: `American · ×100 · CBOE · expires in N trading days` (LOW-1: trading days, not calendar days)
- Greeks strip: Δ · Γ · Θ · V · IV (renders `—` placeholders when unavailable — never blocks order entry)
- Premium line: `Premium 5.18 · Notional per contract $518 · 1 contract = 100 shares SPY`

`Qty` label → "Contracts". `Side` → "Buy to Open / Sell to Open / Buy to Close / Sell to Close" (derived from existing position check).

### `OptionEventsPage` (`/options/events`)

Three sections:
1. **Pending elections** — long options expiring ≤5 trading sessions with intrinsic value > 0 (degrades to "expiring within 5 sessions" when spot unavailable); Exercise / DNE / Lapse buttons; CSRF nonce via `mintCsrfNonce` + client-generated `idempotency_key` UUID
2. **Recent assignments** — assigned against short positions, last 30 days (LOW-2: cursor pagination deferred to Phase 19+)
3. **Recent exercises** — exercised by user, last 30 days

### `useOptionChain` hook

TanStack Query for initial load + WebSocket push for live bid/ask/IV/Greeks updates (same hybrid pattern as `usePortfolioRollup`). Falls back to 5s REST poll if WS drops or if strikes are capped. Strike window resets on expiry change.

### Navigation

Add "Options" entry to sidebar between "Trade" and "Portfolio". `/options/chain` as primary, `/options/events` as sub-link.

---

## Error Handling

| Scenario | Backend behaviour | FE behaviour |
|----------|------------------|--------------|
| Chain sidecar timeout / error | Return stale cache with `stale: true`; 503 if no cache | "Stale data — last updated X ago" banner on chain table |
| IBKR slow `reqSecDefOptChain` (up to 5s) | 6s timeout; singleflight coalesces concurrent misses | Loading skeleton on table |
| Concurrent chain misses (same key) | Singleflight — one sidecar call, all waiters share result | Transparent to FE |
| Expired contract in order | 422 `contract_expired` from `preview_order` (exchange-tz aware) | Blocking risk banner in TradeTicketModal |
| Exercise past broker cut-off | `ExerciseService` returns structured error with `reason` | Inline error on election row |
| Greeks unavailable | `GreeksSnapshot` is null | `—` placeholders in strip; order entry unblocked |
| Futu HK chain width | `strike_count` capped at 40; cap frame sent to WS | UI shows "Showing X of Y strikes — polling remainder" |
| Strike WS > cap | `subscription_capped` frame; excess on 5s REST poll | FE hybrid fallback; cap notice shown |
| Duplicate exercise election | Idempotency key match → return original row; partial unique index → 409 | No duplicate RPC call to broker |
| `option_greeks` cardinality leak | `upsert` guard refuses write for non-position/non-order contracts | Gauge observable via `option_greeks_rows_total` |
| Greeks field out of range | `GreeksSnapshot.__post_init__` clamps; counter incremented | Clamped value displayed |

---

## Testing

### Backend (`tests/services/options/`)

- `test_chain_service.py` — cache hit/miss, stale fallback, source routing (USD→Schwab, HKD→Futu), TTL expiry, timeout handling per broker, singleflight (concurrent misses share one call)
- `test_greeks_service.py` — upsert accepted for held position, upsert refused for browse-only instrument, stale eviction (year-round, not market-hours-gated), clamping + counter
- `test_exercise_service.py` — pending candidate filter uses trading-day calendar, election idempotency key, partial unique index 409, CSRF nonce single-use, unsupported broker path, intrinsic-value degradation when spot unavailable
- `test_instrument_resolver_option.py` — canonical_id construction (colon format), delegates to `resolve_or_create`, deterministic for same inputs

### Backend (`tests/integration/`)

- `test_options_api.py` — all 7 REST endpoints, JWT gate, rate limit, stale response shape, expired contract 422, admin config endpoint + CSRF
- `test_ws_options.py` — WS connect, 2 Hz conflation, disconnect tears down conid subs, connection cap, subscription_capped frame, heartbeat

### Broker adapter stubs

Mock sidecar stubs for `GetOptionChain`, `GetOptionExpirations`, `GetOptionGreeks`, `ExerciseOption` — one per broker (IBKR, Schwab, Alpaca, Futu).

### Frontend (`features/options/*.test.tsx`)

- `OptionChainTable.test.tsx` — ATM highlight, ITM/OTM shading, row click opens TradeTicketModal pre-filled, mobile collapse below md
- `OptionDetailsSection.test.tsx` — Greeks strip renders, placeholders when null, notional = qty × premium × multiplier, expiry countdown in trading days
- `OptionEventsPage.test.tsx` — pending elections with trading-day filter, Exercise/DNE/Lapse with idempotency key, CSRF nonce flow, spot-unavailable degradation
- `useOptionChain.test.ts` — REST fallback when WS unavailable, stale banner on `stale: true`, subscription_capped hybrid behaviour

Coverage target: 80%+ per project standard.

---

## Deferred (out of scope for Phase 12)

- Greeks wired into risk gate / margin calculation (deferred — requires per-broker margin model)
- IV rank / percentile display (Phase 18 scanner stores 52-week IV history; `iv_rank` column ships as NULL)
- Multi-leg combos (Phase 13)
- Options position sizing / Kelly on premium (Phase 19)
- Alpaca exercise (not in Alpaca API)
- Futu HK exercise (not in Futu HK API)
- Exercise elections for Schwab (not in Schwab API)
- Cursor pagination on `/options/events` assignment/exercise history (Phase 19+)
