# Phase 12 — Options Single-Leg Design

**Date:** 2026-05-14
**Version target:** v0.12.x
**Status:** approved for planning (architect reviews 1 + 2 applied 2026-05-14)

---

## Overview

Phase 12 adds single-leg options trading across IBKR, Schwab (chain data only), Alpaca, and Futu HK. It ships:

1. An option chain viewer at `/options/chain` with live bid/ask/IV/Greeks streaming
2. Strike picker + `OptionDetailsSection` injected into the existing `TradeTicketModal`
3. Single-leg place/modify/cancel on IBKR, Alpaca, and Futu HK (Schwab execution deferred — see CRIT-A)
4. An exercise elections page at `/options/events`
5. **Architectural pillar #4**: `meta` JSONB on `instruments` — Pydantic discriminated union for options contract shape (foundation for Phases 13–16)
6. `position_effect` column on `orders` + `RiskContext` — enables BTO/STO/BTC/STC and naked-short detection

Greeks are displayed but **not** wired into the risk gate (deferred to a later phase).

---

## Brokers in scope

| Broker | Chain data | Trade execution | Exercise/Assign |
|--------|-----------|-----------------|-----------------|
| **Schwab** | Primary for USD options (`GET /chains`, `GET /expirationchain`) | **No** (CRIT-A: schwabdev is read-only; 401 upstream) | No |
| **IBKR** | Fallback for USD chain data; primary execution | Yes | Yes (`exerciseOptions`) |
| **Alpaca** | Fallback for USD chain data | Yes | No |
| **Futu HK** | Primary for HKD options (`get_option_chain`, `get_option_expiration_date`) | Yes | No (Futu HK API limitation) |

**CRIT-A note:** Schwab chain data (`GET /chains`, `GET /expirationchain`) is REST read-only and works via schwabdev today. Schwab option execution (`PlaceOrder` for options) requires a separate productionisation chunk once the upstream Schwab 401 issue is resolved. That chunk is deferred to Phase 12.x; this spec covers Phase 12.0 without Schwab execution.

Trade execution source priority for USD: IBKR → Alpaca. HKD: Futu only.

Chain source routing is config-driven — see §Configuration below.

---

## Configuration

### `option_chain_sources`

- **Namespace:** `quote_engine`
- **Key:** `option_chain_sources`
- **Schema:** `dict[str, list[str]]` — maps ISO currency code to ordered list of source IDs
- **Default value stored in `app_config` at first boot:**
  ```json
  {"USD": ["schwab", "ibkr", "alpaca"], "HKD": ["futu"]}
  ```
- **Validation:** on load, assert every source ID exists in `broker_registry`; reject unknown sources with startup warning (fail-open — use remaining valid sources).
- **Invalidation:** pubsub channel `app_config:invalidate:option_chain_sources` (same pattern as `risk_limits`). `OptionChainService` subscribes at lifespan start and reloads in-memory priority list on message.
- **Admin endpoint:** `PUT /api/admin/quote-engine/option-chain-sources` (JWT admin + CSRF nonce). Writes to `app_config`, publishes invalidation.

---

## Data Model

### Migration 0046

Five schema changes. Enum extension must run outside the transaction (MED-7):

```python
# upgrade() preamble — outside main transaction block
with op.get_context().autocommit_block():
    op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'OPTION'")
```

Inside the transaction:

1. **`instruments.meta` column: semantic shift only — no rename (CRIT-C).** The column stays named `meta`; the ORM model stays `Instrument.meta`; `resolve_or_create(meta=...)` stays unchanged. The Pydantic layer (`parse_instrument_meta`) enforces the typed schema on top of the raw JSONB — no column rename required.

2. **Backfill `meta` discriminator (MED-2, idempotent MED-N):**
   ```sql
   UPDATE instruments
   SET meta = jsonb_set(meta, '{asset_class}', to_jsonb(asset_class::text))
   WHERE meta != '{}' AND meta->>'asset_class' IS NULL;
   ```
   Migration is idempotent: the WHERE clause skips rows already backfilled. Followed by a Python validation pass inside `upgrade()`: iterate all non-empty `meta` rows, call `parse_instrument_meta(row.meta)`, abort migration on `ValidationError`.

3. **`position_effect` column on `orders` (CRIT-B):**
   ```sql
   ALTER TABLE orders
   ADD COLUMN position_effect TEXT
       CHECK (position_effect IN ('OPEN', 'CLOSE', 'UNWIND', NULL));
   ```
   `NULL` = equity/non-option (existing rows unchanged). Options orders: `'OPEN'` (BTO/STO) or `'CLOSE'` (BTC/STC). `side` remains `BUY`/`SELL`; `position_effect` is the second dimension. Combined label `BTO`/`STO`/`BTC`/`STC` is derived in the UI and in risk checks — never stored as a composite string.

4. **New `option_greeks` table** — persisted Greeks for held/traded contracts only.

5. **New `exercise_elections` table** — idempotent election log with partial unique index.

### `meta` JSONB — Pydantic discriminated union (HIGH-1, CRIT-C)

`NonOptionDetails` uses a **closed enum** of known non-option asset classes — native Pydantic v2 discriminator, fail-fast on unknown values:

```python
NonOptionAssetClass = Literal["", "STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "CRYPTO", "FOREX"]

class NonOptionDetails(BaseModel):
    """All non-option instruments. Existing {} rows deserialise with asset_class=""."""
    asset_class: NonOptionAssetClass = ""

class OptionDetails(BaseModel):
    asset_class: Literal["OPTION"] = "OPTION"
    underlying_canonical_id: str      # e.g. "stock:SPY:US"
    strike: Decimal                    # e.g. Decimal("450.00")
    expiry: date                       # e.g. date(2025, 1, 17)
    put_call: Literal["C", "P"]
    multiplier: int                    # required — not defaulted (HIGH-E)
    style: Literal["A", "E"]          # required — "A" American, "E" European; no default

# FutureDetails, ForexDetails etc. added additively in Phases 14/15
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails,
    Field(discriminator="asset_class")
]

_adapter = TypeAdapter(InstrumentMeta)

def parse_instrument_meta(raw: dict) -> InstrumentMeta:
    return _adapter.validate_python(raw)  # ValidationError on unknown asset_class
```

`multiplier` is **required with no default** (HIGH-E: multiplier must be provided by the sidecar; a missing multiplier is a hard error, not silently defaulted to 100).

`style` is **required with no default** — callers must know whether the option is American or European from the broker response. Sidecar handlers map broker-native style fields; unknown style raises `ValidationError`.

All consumers use `isinstance(details, OptionDetails)` to branch. The `meta` column name, ORM field, and `resolve_or_create(meta=...)` kwarg are **unchanged** — only the Pydantic model name changes from `contract_details` terminology to `InstrumentMeta`.

### `position_effect` on orders (CRIT-B)

| `side` | `position_effect` | Combined label | Meaning |
|--------|------------------|----------------|---------|
| BUY | OPEN | Buy to Open (BTO) | Open new long option position |
| SELL | OPEN | Sell to Open (STO) | Open new short option position |
| BUY | CLOSE | Buy to Close (BTC) | Close existing short position |
| SELL | CLOSE | Sell to Close (STC) | Close existing long position |
| BUY/SELL | NULL | — | Equity / non-option order (unchanged) |

**Naked-short detection** in `risk_service._check_options_exposure` (new check, HIGH-D): for STO orders, verify existing long position of ≥ qty exists (covered call / married put allowed); if no cover, gate on `OPTION_LEVEL >= 3` from `app_config[options/trading_level]`.

**`RiskContext` additions:**
```python
position_effect: str | None = None   # "OPEN" | "CLOSE" | None
```

### `tax_treatment` on fills and orders (HIGH-F)

Add nullable `tax_treatment TEXT` column to both `orders` and `fills` tables (migration 0046). Values: `'EQUITY'`, `'OPTION_PREMIUM'`, `'OPTION_EXERCISE'`, `'OPTION_ASSIGNMENT'`, `'OPTION_EXPIRY'`. `NULL` = not yet classified (existing rows). Phase 23 (UK CGT) reads this column — cheap now, expensive to backfill later.

### Canonical ID format for option contracts (MED-1)

Colon-separated, consistent with existing `stock:AAPL:US` and `etf:SPY:US` conventions:

```
option:{UNDERLYING_SYMBOL}:{EXCHANGE}:{YYMMDD}:{P|C}:{STRIKE_DECIMAL}
```

Example: `option:SPY:CBOE:250117:C:450.00`

Built deterministically by `InstrumentResolver.find_or_create_option`.

### `option_greeks` table (MED-3)

```sql
CREATE TABLE option_greeks (
    instrument_id  BIGINT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    delta          NUMERIC(12, 6),
    gamma          NUMERIC(12, 6),
    theta          NUMERIC(12, 6),
    vega           NUMERIC(12, 6),
    rho            NUMERIC(12, 6),
    iv             NUMERIC(12, 6),   -- decimal (0.18 = 18%)
    iv_rank        NUMERIC(5, 2),    -- NULL until Phase 18
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON option_greeks (updated_at);
```

**Upsert guard (CRIT-2):** `OptionGreeksService.upsert` refuses to write if `instrument_id` has no row in `positions` AND no `orders.created_at >= today`. Chain-browse callers use ephemeral Redis Greeks only.

**Eviction:** APScheduler 60s, year-round, deletes `updated_at < now() - interval '5 minutes'`.

**Clamping (MED-3):** `GreeksSnapshot.__post_init__` clamps each field to `(-9999.999999, 9999.999999)`, increments `option_greeks_clamped_total{field}` Counter.

**Observability:** `option_greeks_rows_total` Gauge set after each eviction.

### `exercise_elections` table (HIGH-5, HIGH-I)

```sql
CREATE TABLE exercise_elections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key  UUID NOT NULL UNIQUE,
    jwt_subject      TEXT NOT NULL,           -- HIGH-I: bound to authed user
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

CREATE UNIQUE INDEX exercise_elections_one_per_day
    ON exercise_elections (account_id, instrument_id)
    WHERE created_at::date = CURRENT_DATE AND status != 'failed';
```

### Chain browse data (Redis, not Postgres)

Key: `options:chain:{underlying_canonical_id}:{expiry_iso}:{source}`
TTL: exchange-aware (MED-P): 30s during exchange trading hours for the relevant market (NYSE/NASDAQ for USD, HKEX for HKD); 300s outside. `market_calendar.is_open(exchange)` determines the TTL at write time.

Value: JSON list of `OptionChainRow` (strike, put_call, bid, ask, iv, delta, gamma, theta, vega, oi, volume, conid, multiplier). All price fields stored as strings to avoid float64 drift (MED-O — see §Float precision policy).

**Instruments are NOT created for chain browse rows.** `InstrumentResolver.find_or_create_option` is called only on order-intent (strike click) or position sync.

### Float precision policy (MED-O)

All monetary values (strike, bid, ask, premium, notional) travel as `Decimal` in Python and as `TEXT` in Redis cache (serialised via `str(Decimal)`). Proto `double` fields for price data are converted to `Decimal` at the sidecar boundary using `Decimal(str(value))`. Greek values (dimensionless ratios) may use `float` internally but are stored as `NUMERIC(12,6)` in Postgres. The `GreeksSnapshot` dataclass holds `Decimal` fields to avoid silent float64 drift in clamping arithmetic.

---

## Proto Changes (`broker.proto`)

### New RPCs

```protobuf
rpc GetOptionChain(OptionChainRequest) returns (OptionChainResponse);
rpc GetOptionExpirations(OptionExpirationsRequest) returns (OptionExpirationsResponse);
rpc StreamOptionGreeks(OptionGreeksRequest) returns (stream OptionGreeksResponse);  // HIGH-J
rpc ExerciseOption(ExerciseOptionRequest) returns (ExerciseOptionResponse);
```

**HIGH-J — streaming Greeks RPC:** `StreamOptionGreeks` is a server-side streaming RPC (not unary `GetOptionGreeks`). The sidecar streams Greeks updates as the market ticks (IBKR: `reqMktData` tick types 10–13; Schwab: not available; Futu: `get_option_condiction` poll). The backend subscribes once per conid and fans out to Redis `greeks.options.<conid>`. The WS options feed reads from this channel, not from `option_greeks` Postgres table (which is the durable store for held contracts only).

### Key messages

```protobuf
message OptionChainRequest {
  string underlying_symbol = 1;
  string expiry_date        = 2;  // ISO "2025-01-17"
  string currency           = 3;  // "USD" | "HKD"
  int32  strike_count       = 4;  // max 60 USD, 40 HKD
}

message OptionChainRow {
  string conid         = 1;
  string strike        = 2;   // MED-O: string to preserve decimal precision
  string put_call      = 3;   // "C" | "P"
  string bid           = 4;   // string
  string ask           = 5;   // string
  double iv            = 6;   // Greek — float ok
  double delta         = 7;
  double gamma         = 8;
  double theta         = 9;
  double vega          = 10;
  int64  open_interest = 11;
  int64  volume        = 12;
  int32  multiplier    = 13;  // required; sidecar must populate
  string exchange      = 14;
  string style         = 15;  // "A" | "E"; required
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
  repeated string conids    = 1;   // HIGH-J: stream for multiple conids at once
  string          account_id = 2;
}

message OptionGreeksResponse {
  string conid         = 1;   // HIGH-J: identifies which conid this update is for
  double delta         = 2;
  double gamma         = 3;
  double theta         = 4;
  double vega          = 5;
  double rho           = 6;
  double iv            = 7;
  double iv_rank       = 8;   // 0.0 until Phase 18
  int64  fetched_at_ms = 9;
}

message ExerciseOptionRequest {
  string account_id      = 1;
  string conid           = 2;
  int64  qty             = 3;
  string action          = 4;  // "EXERCISE" | "DO_NOT_EXERCISE" | "LAPSE"
  string idempotency_key = 5;  // UUID; required
}

message ExerciseOptionResponse {
  bool   success    = 1;
  string broker_ref = 2;
  string message    = 3;
}
```

### `SymbolRef` — `OptionContractHint` oneof (HIGH-3)

Tag 6 in `SymbolRef` was `source_meta bytes`. Replace with a typed oneof to avoid msgpack-in-proto:

```protobuf
message OptionContractHint {
  string conid      = 1;
  string strike     = 2;   // string decimal
  string expiry_iso = 3;
  string put_call   = 4;
  int32  multiplier = 5;
}

// In SymbolRef, replace source_meta bytes = 6 with:
oneof contract_hint {
  OptionContractHint option_hint = 6;
  // FutureContractHint future_hint = 7;  Phase 14
  // ForexContractHint  forex_hint  = 8;  Phase 15
}
```

**HIGH-J — conid→canonical_id swap:** When a conid subscription migrates to canonical_id (on first position/order event), the WS gateway:
1. Calls `find_or_create_option` to create the `instruments` row
2. Unsubscribes from `greeks.options.<conid>` Redis channel
3. Subscribes to `quote.<source>.<canonical_id>` via `SubscriptionRegistry`
4. Sends a `{type: "canonicalized", conid: "...", canonical_id: "..."}` frame to the FE so it can update its local state map

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
    ) -> OptionChainResponse
    async def subscribe_strike_window(
        self,
        underlying_canonical_id: str,
        expiry: date,
        conids: list[str],               # MED-R: explicit conids, not ambiguous list[str]
    ) -> list[SubscriptionHandle]        # MED-R: typed return
```

`SubscriptionHandle` is a dataclass `(conid: str, canonical_id: str | None, channel: str)` — unambiguous whether a given strike is on the conid path or canonical_id path.

Cache: exchange-aware TTL (MED-P). Singleflight per `(underlying_canonical_id, expiry_iso)` (HIGH-4, MED-M note): in-process `asyncio.Lock` dict. For multi-worker deployments (Phase 24), this becomes a Redis lock — noted as a known single-replica limitation consistent with the project's Phase 24 policy.

**`app/services/options/greeks_service.py`**

```python
class OptionGreeksService:
    async def upsert(self, instrument_id: int, greeks: GreeksSnapshot) -> None
    async def get(self, instrument_id: int) -> GreeksSnapshot | None
    async def evict_stale(self, older_than: timedelta = timedelta(minutes=5)) -> int
    async def start_streaming(self, conids: list[str], account_id: str) -> None
    async def stop_streaming(self, conids: list[str]) -> None
```

`start_streaming` calls `StreamOptionGreeks` sidecar RPC, fans updates to `greeks.options.<conid>` Redis channel, and calls `upsert` for conids that have positions/orders. `stop_streaming` cancels the sidecar stream task.

**`app/services/options/exercise_service.py`**

```python
class ExerciseService:
    async def list_pending(self, account_id: UUID, jwt_subject: str) -> list[ExerciseCandidate]
    async def elect(
        self,
        account_id: UUID,
        jwt_subject: str,              # HIGH-I: bound to authed user
        instrument_id: int,
        action: Literal["EXERCISE", "DO_NOT_EXERCISE", "LAPSE"],
        qty: Decimal,
        csrf_nonce: str,
        idempotency_key: UUID,
    ) -> ExerciseResult
```

`list_pending` uses `market_calendar.next_trading_days(5)` (MED-4/MED-K note: style derived from `OptionDetails.style` on the instrument, not hardcoded by currency). `account_id` is resolved via `AccountService._resolve_account` chokepoint (HIGH-I — same pattern as all other account-touching endpoints). Rate-limited 5/min per `jwt_subject` (HIGH-I — exercise is money-moving).

### New risk checks (`app/services/risk_service.py`) — HIGH-D

New `_check_options_exposure` method, called from `evaluate()` when `ctx.asset_class == OPTION`:

1. **Options trading level gate:** `app_config[options/trading_level]` (integer L1–L4). Default L1 (covered calls + long options only). Checked at order-intent:
   - L1: BTO (long calls/puts) and covered calls (STO with existing long stock ≥ qty×100)
   - L2: L1 + cash-secured puts (STO put with cash reserve ≥ strike×qty×multiplier in account)
   - L3: L2 + naked calls/puts (STO without cover)
   - L4: L3 + uncapped risk strategies (not enforced in Phase 12 — deferred to Phase 13 multi-leg)
2. **Naked short check (CRIT-B):** STO without cover → require L3+; if L < 3, `BLOCK` with `naked_short_not_permitted`.
3. **Cash-secured put reserve:** STO put at L2 → verify available cash ≥ `strike × qty × multiplier × 1.05` (5% buffer). Uses BP check site (same as CRIT-1 fix).
4. **Expiry-day cutoff:** On expiry date, block new OPEN orders after `market_calendar.option_cutoff_time(expiry, exchange)` (e.g. 15:00 ET for US equity options).
5. **0DTE warning:** If `expiry == today`, append `WARN` with `zero_dte_order` code. Not a blocker. FE surfaces as a yellow banner.
6. **Assignment risk warning:** For STO (short options) within 5 trading days of expiry with delta > 0.7 (ITM), append `WARN` with `high_assignment_risk`.

All new checks respect the existing fail-OPEN policy for audit row insertion failures.

### Modified services

**`app/services/instruments.py` — `InstrumentResolver.find_or_create_option` (HIGH-2, CRIT-C)**

Delegates to the existing `resolve_or_create(meta=...)` primitive — no duplication of lock/ON CONFLICT logic:

```python
async def find_or_create_option(
    self,
    db: AsyncSession,
    underlying_canonical_id: str,
    strike: Decimal,
    expiry: date,
    put_call: Literal["C", "P"],
    multiplier: int,         # required — not defaulted
    style: Literal["A", "E"],  # required — not defaulted
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
        multiplier=multiplier, style=style,
    )
    return await self.resolve_or_create(
        db,
        canonical_id=canonical_id,
        asset_class=AssetClass.OPTION,
        primary_exchange=exchange,
        currency=currency,
        meta=details.model_dump(),   # CRIT-C: meta kwarg — column name unchanged
        source=source,
        raw_symbol=conid,
    )
```

**`app/services/orders_service.py` — multiplier-aware notional (CRIT-1)**

Three sites updated. `multiplier` and `position_effect` plumbed onto `RiskContext`:

```python
# RiskContext additions:
multiplier: int = 1                  # 1 for non-options
position_effect: str | None = None   # "OPEN" | "CLOSE" | None

# Site 1 — _native_notional (all 3 branches multiply by ctx.multiplier):
#   LIMIT:  qty * limit_price * multiplier
#   STOP:   qty * stop_price  * multiplier
#   market: qty * mid * 1.05  * multiplier

# Site 2 — risk_service._check_buying_power (~line 380):
#   order_notional = ctx.qty * ctx.price * ctx.multiplier

# Site 3 — RiskContext construction in _evaluate_risk_for_place_order:
#   details = parse_instrument_meta(instrument.meta)
#   multiplier = details.multiplier if isinstance(details, OptionDetails) else 1
#   position_effect = order.position_effect
#   ctx = RiskContext(..., multiplier=multiplier, position_effect=position_effect)
```

**Concentration check (MED-Q):** `risk_service._check_concentration` uses `position.market_value_base`; for option positions this must include the multiplier: `option_exposure = qty * premium * multiplier`. The position sync path must populate `market_value_base` as `qty × last_price × multiplier` for OPTION asset class.

**`app/services/orders_service.py` — contract expiry check (MED-8)**

```python
if isinstance(details, OptionDetails):
    if market_calendar.is_past_expiry(details.expiry, instrument.primary_exchange):
        raise ContractExpiredError(...)
```

**`app/services/telegram/commands.py` — reject options at parser layer (HIGH-G)**

`parse_place_order` in `order_flow.py` must explicitly reject OCC-format symbols:

```python
_OCC_PATTERN = re.compile(r'^[A-Z]{1,5}\d{6}[CP]\d{8}$')

def parse_place_order(text: str) -> ParsedOrder:
    parts = text.split()
    symbol = parts[1].upper()
    if _OCC_PATTERN.match(symbol):
        raise ParseError(
            "Options orders are not supported via Telegram. "
            "Use /place_order SYMBOL SIDE QTY for equity orders only."
        )
    ...
```

### Quote-engine integration (HIGH-6, HIGH-H)

**Subscription budget sizing (HIGH-H):**

| Broker | Known subscription limit | Options WS budget |
|--------|------------------------|-------------------|
| IBKR | ~100 concurrent market data lines (paper); ~300 live | 40 per chain WS connection; 10 connections = 400 max |
| Schwab | No streaming Greeks — chain data only (REST) | N/A for Greeks streaming |
| Alpaca | Options data: rate-limited per plan | 60 per WS connection; 10 connections = 600 max |
| Futu HK | ~100 subscriptions per quote context | 40 per connection; 10 connections = 400 max |

**Budget enforcement:** `SubscriptionRegistry` tracks `options_subs_active_{source}` Gauge. When adding a new option subscription would exceed `OPTION_SUB_BUDGET[source]` (loaded from `app_config[quote_engine/option_sub_budgets]` with safe defaults), the subscription is refused and the `subscription_capped` frame is sent.

**In-process conid map (per WS connection):**
- `dict[conid, OptionContractHint]` — populated on subscribe, torn down entirely on WS close
- TTL: 5 min idle per conid → `stop_streaming` call at sidecar level
- Separate Redis namespace: `greeks.options.<conid>` (not `quote.*.*`)

**On conid→canonical_id migration:**
1. `find_or_create_option` called
2. `stop_streaming([conid])` on `OptionGreeksService`
3. `SubscriptionRegistry.subscribe(canonical_id)` via normal path
4. `{type: "canonicalized", conid, canonical_id}` frame sent to FE

**Observability:** `quote_options_chain_subs_active{source}` Gauge.

### New API endpoints (`app/api/options.py`)

```
GET  /api/options/expirations?symbol=SPY&currency=USD
GET  /api/options/chain?symbol=SPY&expiry=2025-01-17&strikes=20
GET  /api/options/greeks/{instrument_id}
GET  /api/options/exercise             (JWT; account resolved via _resolve_account)
POST /api/options/exercise             (JWT + CSRF + idempotency_key; rate 5/min)
GET  /api/options/events               (JWT; last 30 days)
PUT  /api/admin/quote-engine/option-chain-sources  (JWT admin + CSRF)
PUT  /api/admin/quote-engine/option-sub-budgets    (JWT admin + CSRF)
PUT  /api/admin/options/trading-level              (JWT admin + CSRF)
```

JWT-gated. `/chain`, `/expirations`: rate-limited 10/s per `jwt_subject`. `/exercise` POST: rate-limited 5/min (money-moving). All account-touching endpoints use `_resolve_account` chokepoint (HIGH-I).

### New WebSocket (`app/api/ws_options.py`)

```
WS /ws/options/chain?symbol=SPY&expiry=2025-01-17
```

- Conflated at 2 Hz; connection cap 10
- Per-connection conid map; torn down on close
- `subscription_capped` frame when > broker budget
- 30s heartbeat + `{type: "stale"}` on staleness
- `{type: "canonicalized"}` frame on conid→canonical_id migration (HIGH-J)

---

## Prometheus Metrics

```
option_chain_fetch_seconds{source}               Histogram
option_chain_fetch_total{source, outcome}         Counter   — ok|stale|timeout|error
option_expirations_fetch_total{source, outcome}   Counter
option_greeks_stream_updates_total{source}        Counter   — HIGH-J: streaming, not unary
option_exercise_total{broker, action, outcome}    Counter
option_greeks_rows_total                          Gauge
option_greeks_clamped_total{field}                Counter
quote_options_chain_subs_active{source}           Gauge
option_risk_check_total{check, verdict}           Counter   — HIGH-D: per new check type
```

---

## Frontend

### New files

```
frontend/src/
  routes/
    options.chain.tsx
    options.events.tsx
  features/options/
    OptionChainPage.tsx
    OptionChainToolbar.tsx
    OptionChainTable.tsx               # butterfly layout
    OptionGreeksStrip.tsx              # Δ Γ Θ V IV — reused in table + modal
    OptionExpiryTabs.tsx
    OptionDetailsSection.tsx           # injected into TradeTicketModal above sizing
    OptionEventsPage.tsx
    ExerciseElectionRow.tsx
    hooks/
      useOptionChain.ts                # TanStack Query + WS hybrid
      useOptionExpirations.ts
      useExerciseElections.ts
    types.ts
```

### `OptionChainTable` layout

Butterfly: calls left (green ITM tint) | strike (amber ATM) | puts right (red ITM tint).

Columns: Bid · Ask · IV · Δ · OI | **Strike** | OI · Δ · IV · Bid · Ask

Strike window: default 20, scroll loads +10 up to broker cap (40 HKD / 60 USD). ATM from underlying spot. Click row → `find_or_create_option` (order-intent) → `TradeTicketModal` pre-filled.

**Mobile (LOW-3):** Below `md`, collapses to single-column list (strike + IV + Δ). Tap → vertical detail sheet (put/call toggle + Greeks) → TradeTicketModal.

### `OptionDetailsSection` in `TradeTicketModal`

Rendered when `instrument.asset_class === 'OPTION'`, above sizing section:

- Contract label: `SPY Jan 17 2025 450C`
- Sub-label: `American · ×100 · CBOE · expires in N trading days` (trading days — LOW-1)
- Greeks strip: Δ · Γ · Θ · V · IV (`—` when unavailable — never blocks order)
- Premium line: `Premium 5.18 · Notional per contract $518 · 1 contract = 100 shares SPY`
- Side selector: "Buy to Open / Sell to Open / Buy to Close / Sell to Close" (derived from `position_effect` + `side`)
- 0DTE warning banner if `expiry === today`
- Qty label → "Contracts"

### `OptionEventsPage` (`/options/events`)

1. **Pending elections** — expiring ≤5 trading sessions, intrinsic > 0 (degrades to "expiring within 5 sessions" when spot unavailable); Exercise / DNE / Lapse + `idempotency_key` UUID generated client-side + CSRF nonce
2. **Recent assignments** — last 30 days (pagination deferred Phase 19+)
3. **Recent exercises** — last 30 days

### `useOptionChain` hook

TanStack Query + WS hybrid (same pattern as `usePortfolioRollup`). Handles `canonicalized` frames by updating the local conid→canonical_id map. Falls back to 5s REST poll if WS drops or strikes are capped.

### Navigation

"Options" entry in sidebar between "Trade" and "Portfolio". Primary: `/options/chain`; sub-link: `/options/events`.

---

## Error Handling

| Scenario | Backend | FE |
|----------|---------|-----|
| Chain sidecar timeout/error | Stale cache + `stale:true`; 503 if no cache | "Stale data — last updated X ago" banner |
| IBKR slow chain (up to 5s) | 6s timeout; singleflight coalesces misses | Loading skeleton |
| Expired contract in order | 422 `contract_expired` (exchange-tz aware) | Blocking risk banner |
| 0DTE order | WARN `zero_dte_order` in preview | Yellow banner in TradeTicketModal |
| Naked short without L3 | BLOCK `naked_short_not_permitted` | Blocking risk banner |
| Expiry-day cutoff | BLOCK `option_cutoff_passed` | Blocking risk banner |
| High assignment risk | WARN `high_assignment_risk` | Yellow banner |
| Exercise duplicate | Idempotency key → return original; partial index → 409 | No duplicate broker call |
| Greeks out of range | Clamp + `option_greeks_clamped_total` | Clamped value displayed |
| Subscription over budget | `subscription_capped` frame + REST fallback | "Showing X of Y strikes — polling remainder" |
| OCC symbol in Telegram | Parser `ParseError` | Bot replies with rejection message |
| Multiplier missing in sidecar | `ValidationError` in `parse_instrument_meta` | 500; logged; chain row skipped |

---

## Testing

### Backend

- `test_chain_service.py` — cache hit/miss, stale, source routing, singleflight, exchange-aware TTL, budget cap
- `test_greeks_service.py` — upsert guard (position/order check), eviction year-round, clamping
- `test_exercise_service.py` — trading-day calendar filter, idempotency, partial unique 409, CSRF single-use, `_resolve_account`, rate limit, unsupported broker
- `test_instrument_resolver_option.py` — canonical_id colon format, delegates to `resolve_or_create(meta=...)`, required multiplier/style
- `test_options_risk_checks.py` — naked short L1/L2/L3 gate, cash-secured put reserve, 0DTE WARN, expiry cutoff BLOCK, assignment risk WARN, multiplier-aware notional (×multiplier on all 3 sites)
- `test_options_api.py` — all 9 endpoints, JWT, rate limits, CSRF, stale shape, expiry 422
- `test_ws_options.py` — connect, 2Hz, disconnect teardown, cap frame, canonicalized frame, heartbeat
- `test_telegram_order_flow_options.py` — OCC symbol rejected at parser layer

### Frontend

- `OptionChainTable.test.tsx` — ATM highlight, ITM/OTM shading, row click → modal pre-fill, mobile collapse
- `OptionDetailsSection.test.tsx` — Greeks strip, placeholders, notional × multiplier, trading-days countdown, 0DTE banner
- `OptionEventsPage.test.tsx` — elections, idempotency key, CSRF, spot-unavailable degradation
- `useOptionChain.test.ts` — REST fallback, stale banner, canonicalized frame handling, cap hybrid

Coverage target: 80%+.

---

## Deferred (out of scope for Phase 12.0)

- **Schwab option execution** — blocked by upstream Schwab 401; deferred to Phase 12.x once resolved
- Greeks wired into risk gate / margin model (requires per-broker margin semantics)
- IV rank display (Phase 18 stores 52-week IV history; `iv_rank` ships as NULL)
- Multi-leg combos (Phase 13)
- Options position sizing / Kelly on premium (Phase 19)
- Alpaca exercise (not in Alpaca API)
- Futu HK exercise (not in Futu HK API)
- Phase 24 multi-worker singleflight (Redis lock replaces in-process asyncio.Lock)
- Cursor pagination on `/options/events` (Phase 19+)
- L4 uncapped-risk strategy gate (Phase 13 multi-leg)
