# Phase 12 — Options Single-Leg Design

**Date:** 2026-05-14  
**Version target:** v0.12.x  
**Status:** approved for planning

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

Chain source routing is config-driven via `app_config[quote_engine/option_chain_sources]`:

```python
OPTION_CHAIN_SOURCE_PRIORITY = {
    "USD": ["schwab", "ibkr", "alpaca"],
    "HKD": ["futu"],
}
```

---

## Data Model

### Migration 0046

Four changes:

1. **Rename `instruments.meta` → `instruments.contract_details`** — single `op.alter_column` rename, no data change. All existing rows remain valid (`{}` for non-option instruments).
2. **Add `OPTION` to `instrument_asset_class` PG enum.**
3. **New `option_greeks` table** — persisted Greeks for held/traded contracts only (not chain browse data).
4. **New `exercise_elections` table** — audit log of exercise/DNE/lapse elections.

### `contract_details` JSONB — Pydantic discriminated union

```python
class NonOptionDetails(BaseModel):
    """Catch-all for STOCK/ETF/INDEX/WARRANT/CBBC/CRYPTO/FOREX.
    Existing rows have contract_details={} which deserialises to this."""
    asset_class: str = ""   # not OPTION — no further fields needed yet

class OptionDetails(BaseModel):
    asset_class: Literal["OPTION"] = "OPTION"
    underlying_canonical_id: str    # e.g. "SPY-STOCK-USD-NYSE"
    strike: Decimal                  # e.g. Decimal("450.00")
    expiry: date                     # e.g. date(2025, 1, 17)
    put_call: Literal["C", "P"]
    multiplier: int                  # 100 standard US, 50 mini, varies HK
    style: Literal["A", "E"] = "E"  # American / European

def parse_contract_details(raw: dict) -> NonOptionDetails | OptionDetails:
    """Discriminate on asset_class field; default to NonOptionDetails for {}.
    FutureDetails, ForexDetails etc. added in Phases 14/15."""
    if raw.get("asset_class") == "OPTION":
        return OptionDetails.model_validate(raw)
    return NonOptionDetails.model_validate(raw)
```

`InstrumentResolver.parse_contract_details(instrument.contract_details)` returns the union type. All consumers use `isinstance(details, OptionDetails)` to branch. The manual discriminator function (instead of a Pydantic `Annotated` union) is used because `NonOptionDetails` has a non-`Literal` `asset_class` field — the two types are not mutually exclusive on a single literal value.

### Canonical ID format for option contracts

```
{UNDERLYING_SYMBOL}-{YYMMDD}{P|C}{STRIKE_8DIGIT}-OPTION-{CCY}-{EXCHANGE}
```

Example: `SPY-250117C00450000-OPTION-USD-CBOE`

Built deterministically by `InstrumentResolver.find_or_create_option`. The OCC-style segment is human-readable and maps 1:1 to the OCC symbology used by US brokers.

### `option_greeks` table

```sql
CREATE TABLE option_greeks (
    instrument_id  BIGINT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    delta          NUMERIC(8, 6),
    gamma          NUMERIC(8, 6),
    theta          NUMERIC(8, 6),
    vega           NUMERIC(8, 6),
    rho            NUMERIC(8, 6),
    iv             NUMERIC(8, 6),   -- implied vol as decimal (0.18 = 18%)
    iv_rank        NUMERIC(5, 2),   -- 52-week IV percentile, nullable
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON option_greeks (updated_at);
```

Stale eviction: APScheduler job every 60s, evicts rows with `updated_at < now() - interval '5 minutes'` during market hours.

### `exercise_elections` table

```sql
CREATE TABLE exercise_elections (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id     UUID NOT NULL REFERENCES broker_accounts(id),
    instrument_id  BIGINT NOT NULL REFERENCES instruments(id),
    action         TEXT NOT NULL CHECK (action IN ('EXERCISE', 'DO_NOT_EXERCISE', 'LAPSE')),
    qty            NUMERIC(20, 8) NOT NULL,
    broker_ref     TEXT,            -- broker confirmation ref, nullable
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

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
  repeated OptionChainRow calls    = 1;
  repeated OptionChainRow puts     = 2;
  string                  source   = 3;
  int64                   fetched_at_ms = 4;
}

message OptionExpirationsRequest {
  string underlying_symbol = 1;
  string currency          = 2;
}

message OptionExpirationsResponse {
  repeated string expiry_dates = 1;  // ISO dates sorted ascending
}

message ExerciseOptionRequest {
  string account_id = 1;  // UUID
  string conid      = 2;
  int64  qty        = 3;
  string action     = 4;  // "EXERCISE" | "DO_NOT_EXERCISE" | "LAPSE"
}

message ExerciseOptionResponse {
  bool   success   = 1;
  string broker_ref = 2;
  string message   = 3;
}

message OptionGreeksRequest {
  string conid      = 1;
  string account_id = 2;   // some brokers require account context for Greeks
}

message OptionGreeksResponse {
  double delta     = 1;
  double gamma     = 2;
  double theta     = 3;
  double vega      = 4;
  double rho       = 5;
  double iv        = 6;
  double iv_rank   = 7;   // 0 if unavailable
  int64  fetched_at_ms = 8;
}
```

### `SymbolRef.source_meta` (tag 6, reserved bytes)

For option quote subscriptions before an `instrument_id` exists, `source_meta` carries msgpack-encoded `{"conid": "...", "strike": 450.0, "expiry": "2025-01-17", "put_call": "C"}`. The quote gateway resolves to `instrument_id` lazily on first position/order event.

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
    ) -> OptionChainResponse  # Redis cache → sidecar fetch → stale fallback
    async def subscribe_strike_window(
        self,
        underlying: str,
        expiry: date,
        strikes: list[Decimal],
    ) -> list[str]  # canonical_ids or source_meta conids for quote subscriptions
```

Cache: `options:chain:{underlying_canonical_id}:{expiry_iso}:{source}`, TTL 30s/300s.  
Stale-on-error: returns last cached value with `stale=True` if sidecar call fails or times out (3s for Schwab/Alpaca, 6s for IBKR, 5s for Futu HK).  
Registered as lifespan singleton at `app.state.chain_service`.

**`app/services/options/greeks_service.py`**

```python
class OptionGreeksService:
    async def upsert(self, instrument_id: int, greeks: GreeksSnapshot) -> None
    async def get(self, instrument_id: int) -> GreeksSnapshot | None
    async def evict_stale(self, older_than: timedelta) -> int
```

Registered at `app.state.greeks_service`. APScheduler 60s eviction job.

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
        csrf_nonce: str,
    ) -> ExerciseResult
```

`ExerciseCandidate`: long option position with `expiry ≤ today + 5 days` and intrinsic value > 0.  
`elect()`: consumes CSRF nonce → calls `ExerciseOption` RPC on appropriate sidecar → writes `exercise_elections` row.  
IBKR is the only broker that supports exercise; Schwab/Alpaca/Futu HK elections are logged but return `broker_unsupported` in `ExerciseResult`.

### Modified services

**`app/services/instruments.py` — `InstrumentResolver.find_or_create_option`**

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
) -> Instrument
```

- Builds canonical_id deterministically
- `INSERT INTO instruments ... ON CONFLICT (canonical_id) DO NOTHING RETURNING id`; re-SELECTs on conflict
- Upserts `symbol_aliases` row for (source, conid)
- Called on order-intent and position sync only

**`app/services/orders_service.py` — multiplier-aware notional**

- `preview_order` / `place_order` / `modify_order`: detect `asset_class == OPTION`
- Notional = `qty × premium × multiplier` (not `qty × price`)
- Add `contract_expired` 422 check: `contract_details.expiry < date.today()`

### New API endpoints (`app/api/options.py`)

```
GET  /api/options/expirations?symbol=SPY&currency=USD
GET  /api/options/chain?symbol=SPY&expiry=2025-01-17&strikes=20
GET  /api/options/greeks/{instrument_id}
GET  /api/options/exercise                   # list pending elections
POST /api/options/exercise                   # submit election (CSRF nonce in body)
GET  /api/options/events                     # exercise + assignment event log (last 30d)
```

All JWT-gated. `/chain` and `/expirations`: rate-limited 10/s per `jwt_subject` via deque limiter.

### New WebSocket (`app/api/ws_options.py`)

```
WS /ws/options/chain?symbol=SPY&expiry=2025-01-17
```

- Streams live bid/ask/IV/Greeks for visible strike window
- Conflated at 2 Hz
- Subscribes to `quote.*.<canonical_id>` or `quote.*.<conid>` patterns via existing quote engine Redis pub/sub
- Connection cap: 10 (chains are wide; each connection fans to up to 40 symbols)
- 30s heartbeat, `{type: "stale", strikes: [...]}` on staleness

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

Strike window: default 20 strikes (10 each side of ATM). Scroll loads more in increments of 10. ATM computed from last underlying spot price.

### `OptionDetailsSection` in `TradeTicketModal`

Rendered when `instrument.asset_class === 'OPTION'`, inserted above the sizing section:

- Contract label: `SPY Jan 17 2025 450C`
- Sub-label: `American · ×100 · CBOE · expires in N days`
- Greeks strip: Δ · Γ · Θ · V · IV (renders `—` placeholders when unavailable — never blocks order entry)
- Premium line: `Premium 5.18 · Notional per contract $518 · 1 contract = 100 shares SPY`

`Qty` label → "Contracts". `Side` → "Buy to Open / Sell to Open / Buy to Close / Sell to Close" (derived from existing position check).

### `OptionEventsPage` (`/options/events`)

Three sections:
1. **Pending elections** — long options expiring ≤5 days with intrinsic value > 0; Exercise / DNE / Lapse buttons; CSRF nonce via `mintCsrfNonce` (same pattern as admin endpoints)
2. **Recent assignments** — assigned against short positions, last 30 days
3. **Recent exercises** — exercised by user, last 30 days

### `useOptionChain` hook

TanStack Query for initial load + WebSocket push for live bid/ask/IV/Greeks updates (same hybrid pattern as `usePortfolioRollup`). Falls back to 5s REST poll if WS drops. Strike window resets on expiry change.

### Navigation

Add "Options" entry to sidebar between "Trade" and "Portfolio". `/options/chain` as primary, `/options/events` as sub-link.

---

## Error Handling

| Scenario | Backend behaviour | FE behaviour |
|----------|------------------|--------------|
| Chain sidecar timeout / error | Return stale cache with `stale: true`; 503 if no cache | "Stale data — last updated X ago" banner on chain table |
| IBKR slow `reqSecDefOptChain` (up to 5s) | 6s timeout; loading state in response | Loading skeleton on table |
| Expired contract in order | 422 `contract_expired` from `preview_order` | Blocking risk banner in TradeTicketModal |
| Exercise past broker cut-off | `ExerciseService` returns structured error with `reason` | Inline error on election row |
| Greeks unavailable | `GreeksSnapshot` is null | `—` placeholders in strip; order entry unblocked |
| Futu HK chain width | `strike_count` capped at 40 | UI shows cap notice |

---

## Testing

### Backend (`tests/services/options/`)

- `test_chain_service.py` — cache hit/miss, stale fallback, source routing (USD→Schwab, HKD→Futu), TTL expiry, timeout handling per broker
- `test_greeks_service.py` — upsert, stale eviction, missing instrument
- `test_exercise_service.py` — pending candidate filter, election write + RPC call, CSRF nonce consumption, unsupported broker path
- `test_instrument_resolver_option.py` — canonical_id construction, ON CONFLICT upsert, deterministic for same inputs

### Backend (`tests/integration/`)

- `test_options_api.py` — all 6 REST endpoints, JWT gate, rate limit, stale response shape, expired contract 422
- `test_ws_options.py` — WS connect, 2 Hz conflation, disconnect/reconnect, connection cap

### Broker adapter stubs

Mock sidecar stubs for `GetOptionChain`, `GetOptionExpirations`, `ExerciseOption` — one per broker (IBKR, Schwab, Alpaca, Futu).

### Frontend (`features/options/*.test.tsx`)

- `OptionChainTable.test.tsx` — ATM highlight, ITM/OTM shading, row click opens TradeTicketModal pre-filled
- `OptionDetailsSection.test.tsx` — Greeks strip renders, placeholders when null, notional = qty × premium × multiplier
- `OptionEventsPage.test.tsx` — pending elections, Exercise/DNE/Lapse, CSRF nonce flow
- `useOptionChain.test.ts` — REST fallback when WS unavailable, stale banner on `stale: true`

Coverage target: 80%+ per project standard. Greeks-unavailable and stale-chain paths explicitly tested.

---

## Deferred (out of scope for Phase 12)

- Greeks wired into risk gate / margin calculation (Phase 10 extension, TBD)
- IV rank / percentile display (needs 52-week IV history — Phase 18 scanner stores this)
- Multi-leg combos (Phase 13)
- Options position sizing (Kelly on premium — Phase 19)
- Alpaca exercise (not in Alpaca API)
- Futu HK exercise (not in Futu HK API)
