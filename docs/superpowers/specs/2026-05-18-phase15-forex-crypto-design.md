# Phase 15 — Forex + Crypto Design

**Version:** v0.15.0 (15a Forex) + v0.15.1 (15b Crypto)
**Date:** 2026-05-18
**Status:** Approved

---

## 1. Scope

Phase 15 adds two new asset classes to the trading dashboard:

- **15a (v0.15.0):** IBKR IDEALPRO FX — MKT, LMT, and full RFQ (Request-for-Quote) flow; `ForexCalendar` (24/5); `/forex` workspace page; FX mode in TradeTicketModal.
- **15b (v0.15.1):** IBKR Paxos crypto — open-set instrument-registry driven; Coinbase WS as free L1+L2 data source; `CryptoCalendar` (24/7 + configurable maintenance windows); `/crypto` workspace page; crypto mode in TradeTicketModal.

No trading via Coinbase — execution is IBKR Paxos only. Coinbase is data-only.

---

## 2. Data Model & Schema

### 2.1 Alembic 0051 (Phase 15a)

- Confirm `FOREX` present in `instrument_asset_class` PG enum (originally seeded in alembic 0009); add if missing.
- **`ForexInstrumentResolver`** (new, `app/services/forex/instrument_resolver.py`): **read-only** — resolves `(base_currency, quote_currency)` → `Instrument` row via DB lookup + Redis cache key `forex:instrument:{base}{quote}` (60 min TTL). Returns `None` on cache miss; does not write.
- **`_ensure_forex_instrument(db, pair)`** helper in `rfq_service.py`: called at `request_quote` time before `ForexInstrumentResolver.resolve()`. If the instrument row does not yet exist, calls the IBKR sidecar `GetContract` RPC (or a new `ListForexPairs` RPC — see §4.1) to fetch pair metadata, upserts `instruments` with `ForexDetails` meta, then invalidates the resolver cache. This is a deliberate write-side helper separate from the read-only resolver — same separation as `seed_instruments_from_positions` (writer) vs `InstrumentResolver` (reader) elsewhere in the codebase.

- New table `forex_rfq_quotes`:

```sql
CREATE TABLE forex_rfq_quotes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
    instrument_id   BIGINT NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    bid             NUMERIC(20,8) NOT NULL,
    ask             NUMERIC(20,8) NOT NULL,
    ttl_seconds     INT NOT NULL,
    broker_quote_id TEXT,
    side            TEXT CHECK (side IN ('BUY', 'SELL')),
    notional        NUMERIC(20,8),
    notional_currency TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending','accepting','accepted','expired','rejected')),
    reject_reason   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX forex_rfq_quotes_broker_quote_id_idx
    ON forex_rfq_quotes (broker_quote_id) WHERE broker_quote_id IS NOT NULL;
CREATE INDEX forex_rfq_quotes_account_status_idx
    ON forex_rfq_quotes (account_id, status, expires_at);
```

Note: `instrument_id` replaces bare `canonical_id TEXT` — the resolver guarantees canonical form before write. `status` adds `accepting` intermediate state (H3). `reject_reason` captures broker-side rejection detail.

- Add `forex_max_notional_per_trade NUMERIC(20,8)` column to `risk_limits` (nullable; NULL = no cap). This is a deliberate denormalization from the `limit_kind` row convention for hot-path lookup — the same approach taken by Phase 13's `max_combo_loss_native` / `max_combo_net_delta` columns (alembic 0049), where per-feature numeric caps were promoted to typed columns rather than `scope_type+limit_kind` rows.
  - **Scope applicability:** readable at `scope_type IN ('global', 'account')`. NULL at broker scope. Resolution order: account row (if set) → global row (if set) → no cap. Matches the per-account-override pattern used by `max_daily_loss_*`. Resolved in `_check_forex_exposure` via `_resolve_limit(account_id, broker_id, 'forex_max_notional_per_trade')` (same helper used by existing checks).
- `ForexDetails` discriminated-union arm added to `app/services/options/types.py` `InstrumentMeta`:

```python
class ForexDetails(BaseModel):
    asset_class: Literal["FOREX"] = "FOREX"
    base_currency: str            # e.g. "EUR"
    quote_currency: str           # e.g. "USD"
    pip_size: Decimal             # e.g. 0.0001
    contract_size: Decimal | None = None  # None for IDEALPRO spot (notional-based, not lot-based)
    trading_hours: str            # human-readable, e.g. "Sun 17:00 – Fri 17:00 ET"
```

`contract_size` is optional because IDEALPRO spot FX is notional-based, not lot-based. FX futures (if added later) may populate it. The FX notional input in §5.2 does NOT divide by `contract_size`.

### 2.2 Alembic 0052 (Phase 15b)

- Confirm `CRYPTO` present in `instrument_asset_class` PG enum; add if missing.
- `CryptoDetails` discriminated-union arm:

```python
class CryptoDetails(BaseModel):
    asset_class: Literal["CRYPTO"] = "CRYPTO"
    base_asset: str          # e.g. "BTC"
    quote_asset: str         # e.g. "USD"
    min_qty: Decimal         # e.g. 0.00001
    qty_step: Decimal        # e.g. 0.00001
    min_notional: Decimal | None  # e.g. 1.00 USD
```

- New **hypertable** `crypto_order_book_snapshots` (written by `CoinbaseWsAdapter` every 60s, top-10 levels per side; live book lives in Redis stream `crypto:book:{canonical_id}` and hash `crypto:book:snap:{canonical_id}`):

```sql
CREATE TABLE crypto_order_book_snapshots (
    instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    source        TEXT NOT NULL DEFAULT 'coinbase',
    level         INT NOT NULL,
    side          TEXT NOT NULL CHECK (side IN ('bid', 'ask')),
    price         NUMERIC(20,8) NOT NULL,
    qty           NUMERIC(20,8) NOT NULL,
    captured_at   TIMESTAMPTZ NOT NULL
);
SELECT create_hypertable('crypto_order_book_snapshots', 'captured_at');
-- 7-day retention
SELECT add_retention_policy('crypto_order_book_snapshots', INTERVAL '7 days');
-- 1h CAGG: top-3 levels per side (OHLC of price, avg qty)
CREATE MATERIALIZED VIEW crypto_order_book_1h
    WITH (timescaledb.continuous) AS
    SELECT time_bucket('1 hour', captured_at) AS bucket,
           instrument_id, source, side, level,
           first(price, captured_at) AS price_open,
           last(price, captured_at)  AS price_close,
           avg(qty)                  AS qty_avg
    FROM   crypto_order_book_snapshots
    WHERE  level <= 3
    GROUP BY bucket, instrument_id, source, side, level;
```

Volume estimate: 20 pairs × 2 sides × 10 levels × 1/min ≈ 576k rows/day. With 7-day retention: ~4M rows max. Hypertable + retention policy keeps storage bounded without manual pruning.

### 2.3 Updated InstrumentMeta Union

After Phase 15b, `InstrumentMeta` in `app/services/options/types.py` becomes:

```python
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails | FutureDetails | ForexDetails | CryptoDetails,
    Field(discriminator="asset_class"),
]
```

---

## 3. Session Model (24/7 Handling)

### 3.1 ForexCalendar

Added to `app/services/market_calendar.py` (or extracted to `_forex_calendar.py` if file exceeds 800 lines):

- **Schedule:** 24/5 — opens Sunday 17:00 ET, closes Friday 17:00 ET.
- **Intraday reset gap:** 17:00–17:15 ET each weekday. Orders during this window: BLOCK with `reason=session_gap`, `retry_after=<17:15 ET as UTC ISO>`.
- **API:**
  - `is_forex_session_open(now: datetime | None = None) -> bool`
  - `next_forex_session_open(now: datetime | None = None) -> datetime` — returns Sunday 17:00 ET if weekend, or 17:15 ET same day if in daily gap.

### 3.2 CryptoCalendar

Added to `app/services/market_calendar.py` (or `_crypto_calendar.py`):

- **Schedule:** 24/7 with operator-configurable maintenance blackout windows from `app_config[crypto/maintenance_windows]` — JSON array of `{start_utc: "HH:MM", duration_minutes: int, days: ["mon","tue","wed","thu","fri","sat","sun"]}`.
- **API:**
  - `is_crypto_session_open(now: datetime | None = None) -> bool`
  - `next_crypto_session_open(now: datetime | None = None) -> datetime`
- Config key missing or empty array → always open (safe default).

### 3.3 Risk Gate Integration

`EvaluationContext.asset_class` already exists as `str | None` in `risk_service.py:102` — no schema change needed. The new dispatch arms `_check_forex_exposure` and `_check_crypto_exposure` are added to `risk_service.evaluate()` as additional `elif ctx.asset_class == "FOREX"` / `"CRYPTO"` branches (string literal style, matching existing `ctx.asset_class == "OPTION"` at line 877). FOREX/CRYPTO asset classes bypass `exchange_calendars` entirely (no IDEALPRO or Paxos entry exists in that library).

**`EvaluationContext` gains one new optional field** (required by C1 — crypto concentration check):

```python
account_nlv_base: Decimal | None = None
# Populated by orders_service.preview_order / place_order before calling
# RiskService.evaluate(). Source: Redis key account:nlv:{account_id}:{base_ccy}
# written by BalanceSnapshotWriter on each NLV tick (5s TTL approximation).
# None → concentration check is skipped (logged at INFO; no fail-open counter
# incremented — this is a data-availability condition, not an error).
```

`orders_service` already reads `account.currency_base` before `evaluate()` — the NLV Redis lookup is one additional `await redis.get(f"account:nlv:{account_id}:{base_ccy}")` call at that same point, coerced to `Decimal | None`.

---

## 4. IDEALPRO FX — Backend (Phase 15a)

### 4.1 Proto Additions (`proto/broker/v1/broker.proto`)

```protobuf
// Phase 15a — IDEALPRO FX RFQ
message FxQuoteRequest {
  string account_id = 1;
  string base_currency = 2;
  string quote_currency = 3;
  string notional = 4;           // decimal string
  string notional_currency = 5;  // "base" or "quote"
}

message FxQuoteResponse {
  string broker_quote_id = 1;
  string bid = 2;
  string ask = 3;
  int32  ttl_seconds = 4;
  string expires_at = 5;         // ISO8601 UTC
}

message FxAcceptRequest {
  string account_id = 1;
  string broker_quote_id = 2;
  string side = 3;               // "BUY" or "SELL"
  string qty = 4;                // decimal string
}

message FxAcceptResponse {
  string order_id = 1;
  string fill_price = 2;
  string status = 3;
}

message FxCancelRequest {
  string account_id = 1;
  string broker_quote_id = 2;
}

message FxMidRate {
  string base_currency = 1;
  string quote_currency = 2;
  string mid = 3;
  string timestamp = 4;          // ISO8601 UTC
}

// RPCs
rpc RequestFxQuote(FxQuoteRequest) returns (FxQuoteResponse);
rpc AcceptFxQuote(FxAcceptRequest) returns (FxAcceptResponse);
rpc CancelFxQuote(FxCancelRequest) returns (google.protobuf.Empty);
rpc StreamFxRates(google.protobuf.Empty) returns (stream FxMidRate);
```

`StreamFxRates` pushes live mid-rate updates; the sidecar handler publishes to `fx:mid:{base}:{quote}` Redis key — consumed by the existing `_fx_rate()` helper in `orders_service.py` and `position_sizing_service.py`.

### 4.2 `app/services/forex/rfq_service.py` (new)

- `request_quote(account_id, pair, notional, notional_currency)`:
  1. Calls `_ensure_forex_instrument(db, pair)` to guarantee an `instruments` row exists.
  2. Calls `ForexInstrumentResolver.resolve(pair)` to get the `instrument_id`.
  3. Calls sidecar `RequestFxQuote` RPC.
  4. Persists `forex_rfq_quotes` row (`status=pending`, `instrument_id` FK).
  5. Stores CSRF nonce in Redis key `forex:rfq:nonce:{broker_quote_id}` (TTL = `ttl_seconds`).
  6. Returns `FxQuoteResponse`.

- `accept_quote(account_id, broker_quote_id, side, qty)` — **three-state transition with separate sessions**:
  1. **Session 1:** `SELECT ... FOR UPDATE` where `status='pending'` and `expires_at > now()`. Raises `QuoteExpiredError` (→ HTTP 409) if not found or expired. `UPDATE status='accepting'`. **Explicit `await db.commit()`** — row is visible to concurrent callers; a second accept on the same quote will find `status='accepting'` and be rejected. This is a deliberate departure from the standard request-scoped session, justified because the sidecar RPC is the long-pole and must not hold a row lock.
  2. **Sidecar RPC** (outside any DB transaction): calls `AcceptFxQuote`. May take 1–5s.
  3. **Session 2 (fresh):** On RPC success: insert an `orders` row (account_id, instrument_id, side, qty, price=fill_price, status='filled', client_order_id=`rfq-{broker_quote_id}`) and `UPDATE forex_rfq_quotes SET status='accepted', order_id=<new_order_id>`. On RPC failure or timeout: `UPDATE forex_rfq_quotes SET status='rejected', reject_reason=<broker error>`. Both committed in one TX. The inserted `orders` row flows through the existing `order_event_consumer` fills pipeline for P&L and audit — no consumer-side changes needed.

- `cancel_quote(account_id, broker_quote_id)` — guard `status IN ('pending', 'accepting')`, set `status='rejected'`, calls `CancelFxQuote` sidecar RPC.
- APScheduler sweep job (every 5s): `UPDATE forex_rfq_quotes SET status='expired' WHERE status='pending' AND expires_at < now()`. 5s frequency matches typical IDEALPRO TTL (3–10s); `GET /api/forex/quotes` also computes effective status in SELECT (`CASE WHEN status='pending' AND expires_at < now() THEN 'expired' ELSE status END`) so listing is never stale regardless of sweep timing.

**`forex_rfq_quotes` schema addition (M2):** add `order_id UUID REFERENCES orders(id) ON DELETE SET NULL` column (populated on accept success; NULL until then).

### 4.3 `app/api/forex.py` (new)

| Method | Path | Auth | Rate limit |
|---|---|---|---|
| POST | `/api/forex/quote` | JWT | 10/min per account |
| POST | `/api/forex/quote/{broker_quote_id}/accept` | JWT + CSRF nonce | 10/min per account |
| DELETE | `/api/forex/quote/{broker_quote_id}` | JWT | 20/min per account |
| GET | `/api/forex/quotes` | JWT | — |
| GET | `/api/forex/pairs` | JWT | — |

`accept` endpoint: validates `X-Csrf-Nonce` header matches nonce consumed via GETDEL from Redis key `forex:rfq:nonce:{broker_quote_id}` (TTL = quote `ttl_seconds`, set at `request_quote` time). This is a **single-use CSRF** — not the two-key futures roll pattern. Single key is sufficient because `broker_quote_id` is broker-issued and globally unique, so no per-pair de-dupe lock is needed.

### 4.4 Risk Gate `_check_forex_exposure`

Called from `risk_service.evaluate()` when `ctx.asset_class == AssetClass.FOREX`. **Fail-OPEN on infrastructure errors** (DB/Redis failures) — same policy as Phase 14 `_check_futures_exposure`. Failures increment `forex_risk_check_failures_total`.

- BLOCK: `not is_forex_session_open()` → `session_closed` + `retry_after`.
- BLOCK: `notional > risk_limits.forex_max_notional_per_trade` (if set) → `forex_notional_exceeded`.
- WARN: open position in same pair on same account → `consolidation_suggested`.

### 4.5 Prometheus Metrics (Phase 15a)

```
forex_rfq_requests_total{pair}
forex_rfq_accepts_total{pair, outcome}       # outcome: filled|expired|rejected
forex_rfq_expired_total{pair}
forex_quote_stream_updates_total{pair}
forex_risk_blocks_total{reason}
forex_risk_check_failures_total              # fail-open infrastructure errors
forex_rfq_latency_seconds{stage}            # stage: request|accept
```

---

## 5. IDEALPRO FX — Frontend (Phase 15a)

### 5.1 Types & API (`src/services/forex/`)

- `types.ts`: `FxPair`, `FxQuote` (bid, ask, ttl_seconds, expires_at, broker_quote_id, status, side), `FxQuoteRequest`, `FxAcceptRequest`, `FxPosition`, `FxTrade`.
- `api.ts`: `requestQuote`, `acceptQuote` (sends `X-Csrf-Nonce`), `cancelQuote`, `listQuotes`, `listPairs` — all `credentials: 'include'`.

### 5.2 TradeTicketModal — FX Mode

- New `tradeMode` value `'fx'` alongside `'single'` / `'combo'`. Shown when `asset_class === 'FOREX'`.
- `FxTicketSection` (new, `src/features/forex/FxTicketSection.tsx`):
  - Pair display (base/quote), notional input with currency toggle (base or quote).
  - "Get Quote" button → `requestQuote` → renders `FxQuoteDisplay`.
  - `FxQuoteDisplay`: bid/ask with spread, countdown timer via `useInterval` (1s tick), amber badge when TTL < 5s, red + "Quote expired — refresh" when TTL = 0 (no modal close).
  - "Buy" / "Sell" confirm buttons → `mintCsrfNonce()` + `acceptQuote`.

### 5.3 `/forex` Workspace Page

`src/features/forex/ForexPage.tsx` — four-panel responsive grid (tabs on mobile):

1. **Pair browser** — searchable list from `/api/forex/pairs` (sourced from `app_config[forex/enabled_pairs]` JSON array, defaulting to IDEALPRO majors: EURUSD, USDJPY, GBPUSD, AUDUSD, USDCAD, USDCHF, NZDUSD — same config-driven pattern as `coinbase/subscribed_pairs`); live mid-rate from WS quote feed (`quote.ibkr.<canonical_id>`); click selects pair for rate chart + RFQ panel.
2. **Rate chart** — klinecharts wired to `forex` quote source; timeframe selector (1m/5m/1h/1d).
3. **Positions + P&L** — open FX positions table (unrealised P&L per pair); "Trades" tab with fills history and realised P&L per pair.
4. **RFQ panel** — pair + notional input; active quotes list with TTL countdowns; accepted/expired quote history.

Route: `src/routes/forex.tsx` (TanStack Router file-based).

---

## 6. IBKR Paxos Crypto — Backend (Phase 15b)

### 6.1 Proto Additions

```protobuf
// Phase 15b — Paxos Crypto
message CryptoAsset {
  string symbol = 1;
  string base_asset = 2;
  string quote_asset = 3;
  string min_qty = 4;
  string qty_step = 5;
  string min_notional = 6;   // may be empty string if none
  bool   available_24h = 7;
}

message ListCryptoAssetsRequest { string account_id = 1; }
message ListCryptoAssetsResponse { repeated CryptoAsset assets = 1; }

rpc ListCryptoAssets(ListCryptoAssetsRequest) returns (ListCryptoAssetsResponse);
// PlaceCryptoOrder reuses existing PlaceOrder RPC — asset_class=CRYPTO routes to Paxos in sidecar.
// StreamCryptoPositions reuses existing StreamPositions RPC.
```

### 6.2 `app/services/crypto/crypto_service.py` (new)

- `list_assets(account_id)` — calls sidecar `ListCryptoAssets`, upserts `instruments` rows with `CryptoDetails` meta, Redis-caches result 5 min.
- `resolve_crypto_instrument(symbol, broker_id)` — instrument registry lookup → `list_assets` fallback if not found (mirrors `resolve_instrument` pattern from Telegram order flow).
- Crypto orders flow through existing `orders_service.place_order` unchanged. `DECIMAL_10_PATTERN` already accepts up to 10 decimal places; crypto qty validation against `CryptoDetails.qty_step` precision happens at the risk gate (§6.4 `invalid_qty_precision`).
- **Sidecar PlaceOrder dispatch:** The existing IBKR sidecar `PlaceOrder` handler (`sidecar_ibkr/handlers.py:561`) resolves the contract via `_resolve_contract(request.conid)` — conid qualification handles secType automatically for STK, FUT, OPT. For **FOREX** and **CRYPTO**, conid-based resolution is also correct: the IBKR conid for a EURUSD spot or BTC.USD pair already encodes secType=CASH / CRYPTO in the IBKR contract database. **No asset_class branch is needed in PlaceOrder** — the sidecar only needs to be extended in:
  - **`GetContract` / pair search** (`sidecar_ibkr/handlers.py:1463+`): the `_resolve_contract` fallback path (line 1507) uses `Contract(secType=request.asset_class or "STK")`. This must map `"FOREX"` → `secType="CASH"` + `exchange="IDEALPRO"` and `"CRYPTO"` → `secType="CRYPTO"` + `exchange="PAXOS"`. **This is the actual sidecar change** — scoped to 15a Chunk C and 15b Chunk B respectively.
  - `GetContract` asset_class → secType map addition is a ~5-line change to the existing `_resolve_contract` fallback branch.

### 6.3 `app/api/crypto.py` (new)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/crypto/assets` | JWT | 5-min cache |
| GET | `/api/crypto/positions` | JWT | account-scoped |
| GET | `/api/crypto/trades` | JWT | cursor pagination |
| GET | `/api/crypto/book/{canonical_id}` | JWT | top-20 snapshot from Redis hash `crypto:book:snap:{canonical_id}` |

### 6.4 Risk Gate `_check_crypto_exposure`

Called when `ctx.asset_class == AssetClass.CRYPTO`. **Fail-OPEN on infrastructure errors** — same policy as Phase 14. Failures increment `crypto_risk_check_failures_total`.

- BLOCK: `not is_crypto_session_open()` → `session_closed` + `retry_after`.
- BLOCK: qty precision exceeds `CryptoDetails.qty_step` → `invalid_qty_precision`.
- BLOCK: notional < `CryptoDetails.min_notional` (if set) → `below_min_notional`.
- WARN: single crypto asset > 20% of per-account NLV — denominator is `ctx.account_nlv_base` (populated by `orders_service` from `account:nlv:{account_id}:{base_ccy}` Redis key before calling `evaluate()`; see §3.3). If `ctx.account_nlv_base is None`, skip this check silently (log INFO). Scope is per-account (not cross-broker). → `concentration_warning`.
- WARN: current time in 00:00–04:00 UTC → `wide_spread_advisory` (low-liquidity hours).

### 6.5 Prometheus Metrics (Phase 15b)

```
crypto_assets_list_total{broker, outcome}
crypto_order_attempts_total{asset, side}
crypto_risk_blocks_total{reason}
crypto_risk_check_failures_total             # fail-open infrastructure errors
crypto_position_stream_updates_total{broker}
crypto_instrument_resolve_total{outcome}
```

---

## 7. Coinbase WS Adapter (Phase 15b)

### 7.1 `app/services/crypto/coinbase_ws.py` (new)

- Endpoint: `wss://advanced-trade-ws.coinbase.com/` (Coinbase Advanced Trade WS — public channels, no auth).
- Subscriptions from `app_config[coinbase/subscribed_pairs]` (JSON array of product_ids, e.g. `["BTC-USD","ETH-USD"]`).
- **`ticker` channel (L1):** publishes `quote.coinbase.<canonical_id>` to Redis pub/sub (same shape as Alpaca/Schwab quote bus). Also writes `fx:mid:{base}:{quote}` for stablecoin pairs.
- **`level2` channel (L2):** applies incremental deltas to in-process `OrderBook` instances via `book_manager.py`. Publishes:
  - Incremental deltas → `XADD crypto:book:{canonical_id} MAXLEN ~ 1000 {side, price, qty, seq}` (stream is for downstream WS-gateway consumers only — see §7.3).
  - Full top-N snapshot (N=100) → Redis hash `crypto:book:snap:{canonical_id}` every 5s.
- **Sequence number tracking:** Coinbase Advanced Trade WS L2 channel uses a sequence field — field name MUST be verified against Coinbase Advanced Trade WS docs at impl time (historically `sequence` for L2 deltas; `sequence_num` appears on other message types — do not assume). The adapter tracks the last seen sequence value per pair. **Guard:** if the sequence field is absent or `None` on a message, log a WARNING and skip gap detection for that message (do NOT re-subscribe in a loop). On confirmed gap (`received_seq != last_seq + 1` where both are non-None): drop the in-memory `OrderBook`, unsubscribe and re-subscribe — Coinbase replays a fresh snapshot on re-subscribe. Increment `coinbase_book_sequence_gap_total{canonical_id}` counter.
- **Bounded book depth:** `OrderBook` keeps only top-N price levels per side (N=100). Levels outside top-N are discarded on `apply_delta` (not stored). The snapshot API serves top-20 — deeper levels are dead weight.
- **Recovery path:** on reconnect or sequence gap, the book recovers from Coinbase's initial snapshot message (sent automatically by Coinbase on subscribe), NOT from Redis stream replay. The Redis stream is for downstream consumers only and MUST NOT be used as a source of truth for book reconstruction.
- Reconnect: bounded backoff `[1s, 2s, 5s, 15s, 30s]`.
- Lifespan: started alongside existing quote engine adapters in `app/main.py`.

### 7.2 `app/services/crypto/book_manager.py` (new)

```python
MAX_BOOK_DEPTH = 100  # keep only top-100 levels per side

@dataclass
class OrderBook:
    bids: dict[Decimal, Decimal]  # price → qty; bounded to top-MAX_BOOK_DEPTH
    asks: dict[Decimal, Decimal]
    last_seq: int = 0

    def apply_delta(self, side: str, price: Decimal, qty: Decimal, seq: int) -> None:
        book = self.bids if side == "bid" else self.asks
        if qty == 0:
            book.pop(price, None)
        else:
            book[price] = qty
        # Evict levels beyond MAX_BOOK_DEPTH after update
        if side == "bid" and len(book) > MAX_BOOK_DEPTH:
            for p in sorted(book)[:len(book) - MAX_BOOK_DEPTH]:
                del book[p]
        elif side == "ask" and len(book) > MAX_BOOK_DEPTH:
            for p in sorted(book, reverse=True)[:len(book) - MAX_BOOK_DEPTH]:
                del book[p]
        self.last_seq = seq

    def snapshot(self, depth: int = 20) -> dict:
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
        return {"bids": bids, "asks": asks}
```

One `OrderBook` per subscribed pair, held in-process. Book is reset (cleared) and rebuilt from Coinbase's snapshot message on subscribe or sequence-gap recovery — never from the Redis stream.

### 7.3 WS Gateway Extension

- New subscription type `crypto_book:{canonical_id}`.
- On subscribe: send initial snapshot from `crypto:book:snap:{canonical_id}` Redis hash.
- Then: consume `crypto:book:{canonical_id}` Redis stream (XREAD, blocking), push incremental deltas to subscriber.
- Conflation: max 2 book updates/s per subscriber (same pattern as quote conflation).

### 7.4 Prometheus Metrics

```
coinbase_ws_messages_total{channel, outcome}
coinbase_ws_reconnects_total
coinbase_book_publish_total{canonical_id}
coinbase_book_sequence_gap_total{canonical_id}  # sequence gap → book reset + re-subscribe
coinbase_book_lag_seconds                        # histogram: receipt → Redis XADD
```

---

## 8. Crypto Frontend (Phase 15b)

### 8.1 Shared Component: `FractionalQtyInput`

`src/components/patterns/FractionalQtyInput.tsx` — shared by FX notional input and crypto qty input:
- Props: `value`, `onChange`, `step` (Decimal string), `min`, `max`, `decimals` (default 8).
- Validates input against `step` on blur; shows inline error if precision exceeds `decimals`.

### 8.2 Types & API (`src/services/crypto/`)

- `types.ts`: `CryptoAsset`, `CryptoPosition`, `CryptoTrade`, `OrderBookLevel` (price, qty, side), `OrderBookSnapshot` (bids, asks, captured_at, seq).
- `api.ts`: `listAssets`, `listPositions`, `listTrades`, `getBookSnapshot`, `subscribeOrderBook(canonical_id, onSnapshot, onDelta)` — WS subscription returning unsubscribe function.

### 8.3 `OrderBookDisplay.tsx`

`src/features/crypto/OrderBookDisplay.tsx`:
- Top-10 bid/ask depth table with size bars (width ∝ cumulative qty at that level).
- Updates via WS deltas; `useRef` + manual DOM update for size bars (avoids React reconcile cost at 2/s).
- Spread indicator between bid/ask.
- Amber "stale" badge if last update > 5s ago.

### 8.4 TradeTicketModal — Crypto Mode

- `asset_class=CRYPTO` detected → `CryptoDetailsSection` injected (pattern mirrors `FutureDetailsSection`): shows base/quote assets, min_qty, qty_step, 24h price from Coinbase feed.
- Qty input replaced with `FractionalQtyInput` (`step=CryptoDetails.qty_step`, 8 decimal places).
- Standard MKT/LMT flow — no RFQ step (Paxos is direct execution, not RFQ).

### 8.5 `/crypto` Workspace Page

`src/features/crypto/CryptoPage.tsx` — four-panel responsive grid (tabs on mobile):

1. **Asset browser** — list from `/api/crypto/assets`; live last price from quote bus; 24h change % from Coinbase ticker; click selects asset for order book + trade panel.
2. **L2 order book** — `OrderBookDisplay` for selected asset, sourced from Coinbase WS via Redis stream.
3. **Positions + P&L** — open Paxos positions table (unrealised P&L); "Trades" tab with fills history and realised P&L per asset.
4. **Trade panel** — quick MKT/LMT entry (`FractionalQtyInput`), opens TradeTicketModal in crypto mode for full review.

Route: `src/routes/crypto.tsx`.

---

## 9. Chunk Breakdown & Subagent Routing

### Phase 15a — Forex (v0.15.0)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0051: `forex_rfq_quotes` (with `order_id FK`, `reject_reason`, `accepting` status), `ForexDetails` meta, `forex_max_notional_per_trade` in `risk_limits`; `ForexDetails` + `EvaluationContext.account_nlv_base` in `options/types.py` + `risk_service.py` | **Qwen** |
| B | `ForexCalendar` + `CryptoCalendar` in `market_calendar.py` (or siblings); `_check_forex_exposure` in `risk_service.py`; `ForexInstrumentResolver` (read-only, `app/services/forex/instrument_resolver.py`) | **Qwen** |
| C | Proto additions (4 RPCs + messages); `rfq_service.py` (incl. `_ensure_forex_instrument` upsert helper + three-state accept with separate sessions); `app/api/forex.py`; `sidecar_ibkr/handlers.py` `_resolve_contract` FOREX secType branch; Prometheus metric definitions | **Codex** |
| D | APScheduler TTL sweep job (5s); Prometheus counter wiring; `forex` lifespan hook in `main.py` | **Qwen** |
| E | FE: `services/forex/types.ts` + `api.ts`; `FxTicketSection`; `FxQuoteDisplay`; `FractionalQtyInput` (ships complete-and-tested in `src/components/primitives/` per FE boundary table; consumed by 15b Chunk F without re-implementation); TradeTicketModal FX mode toggle | **Codex** |
| F | FE: `ForexPage.tsx` (4 panels); `routes/forex.tsx`; klinecharts forex source wiring; BE+FE integration tests (RFQ three-state flow, `_ensure_forex_instrument`, session-gap BLOCK, TTL sweep, FX modal countdown) | **Codex** |

Reviewer chain per chunk: spec-compliance + python-reviewer / typescript-reviewer (haiku); code-reviewer + security-reviewer + database-reviewer (sonnet); ARCHITECT-REVIEW once at phase close (opus).

### Phase 15b — Crypto (v0.15.1)

| Chunk | Content | Route |
|---|---|---|
| A | Alembic 0052: confirm CRYPTO enum, `CryptoDetails` meta arm, `crypto_order_book_snapshots` hypertable + 7d retention + 1h CAGG | **Qwen** |
| B | `ListCryptoAssets` proto RPC + message; `crypto_service.py`; `app/api/crypto.py` (4 endpoints); `sidecar_ibkr/handlers.py` `_resolve_contract` CRYPTO secType branch | **Codex** |
| C | `coinbase_ws.py`; `book_manager.py` (`OrderBook` dataclass, `apply_delta`, `snapshot`) | **Qwen** |
| D | `_check_crypto_exposure` in `risk_service.py`; `CryptoCalendar` integration; Prometheus metrics | **Qwen** |
| E | WS gateway extension (`crypto_book:` subscription type); Redis stream consumer; lifespan wiring | **Codex** |
| F | FE: `services/crypto/types.ts` + `api.ts`; `OrderBookDisplay.tsx`; `CryptoDetailsSection`; TradeTicketModal crypto mode | **Codex** |
| G | FE: `CryptoPage.tsx` (4 panels); `routes/crypto.tsx`; integration tests (BE: RFQ flow, Coinbase WS mock, risk gate checks; FE: modal crypto mode, order book rendering) | **Codex** |

---

## 10. Deferred

- Coinbase authenticated channels (private order flow via Coinbase) — no trading via Coinbase, data-only.
- OANDA practice WS as FX data fallback (ROADMAP §7b "future-add") — deferred to Phase 18+.
- FX options (currency options on IDEALPRO) — Phase 12 option chain already handles OPTION asset class; wiring FX options is a Phase 16+ extension.
- Crypto options (IBKR crypto options if/when available) — deferred post-v1.0.
- L2 order book for non-crypto asset classes — Phase 18+ (scanner phase).
- Crypto staking / earn features — out of scope for v1.0.
- `forex_rfq_quotes` monthly retention policy — add when v1.0 prod traffic warrants it (Phase 24 infra hardening).

## 11. Architect Review Findings Applied (2026-05-18)

0 CRIT · 4 HIGH · 6 MED applied inline. 3 LOW + 2 INFO noted.

- **H1** — RFQ nonce clarified as single-use CSRF (not two-key); justified by broker-issued unique `broker_quote_id`. §4.3 updated.
- **H2** — `ForexInstrumentResolver` split into read-only resolver + `_ensure_forex_instrument` upsert helper; "mirrors ContractResolver" claim corrected (ContractResolver is read-only; the upsert is a separate concern). §2.1, §4.2, §9 Chunk B updated.
- **H3** — Three-state RFQ transition (`pending → accepting → accepted | rejected`) with `reject_reason` column added. §4.2 and §2.1 updated.
- **H4** — Coinbase L2: sequence number tracking + gap-triggered resubscribe; bounded book depth (N=100); recovery from Coinbase snapshot (not Redis stream); `coinbase_book_sequence_gap_total` metric added. §7.1, §7.2, §7.4 updated.
- **M1** — `DECIMAL_10_PATTERN` corrected to "10 decimal places". §6.2 updated.
- **M2** — `FractionalQtyInput` note added to 15a Chunk E (ships complete in `components/patterns/`, consumed by 15b). §9 updated.
- **M3** — APScheduler sweep changed to 5s; `GET /api/forex/quotes` computes effective status in SELECT. §4.2 updated.
- **M4** — Fail-OPEN policy + `forex_risk_check_failures_total` / `crypto_risk_check_failures_total` counters added to §4.4 and §6.4.
- **M5** — Concentration check denominator specified: per-account `ctx.account_nlv_base`, base-currency converted via `_fx_rate`, at evaluate-time, 5s Redis cache. §6.4 updated.
- **M6** — Sidecar dispatch branches (`AssetClass.CRYPTO → ib_async.Crypto(exchange='PAXOS')`, `AssetClass.FOREX → ib_async.Forex(exchange='IDEALPRO')`) explicitly specified in §6.2, assigned to their respective chunks.
- **L1** — `ForexDetails.contract_size` made `Decimal | None = None`; notional input does not divide by it. §2.1 updated.
- **L2** — `forex_max_notional_per_trade` denormalization noted (same pattern as `combo_max_loss_pct`). §2.1 updated.
- **L3** — Redis key `crypto:book:snap:{canonical_id}` used consistently in §6.3 and §7.1. §6.3 updated.
- **I1** — 15a tags v0.15.0 and ships before 15b starts; 15b tags v0.15.1. Sequencing preserved as designed.
- **I2** — `crypto_order_book_snapshots` writer specified: `CoinbaseWsAdapter` writes every 60s, top-10 levels per side. §2.2 updated.

### §11.2 Pass-2 Architect Review Findings Applied (2026-05-18)

1 CRIT · 3 HIGH · 5 MED applied inline. 3 LOW + 2 INFO noted.

- **C1** — `EvaluationContext.account_nlv_base: Decimal | None` added; sourced from Redis key `account:nlv:{account_id}:{base_ccy}` by `orders_service` before `evaluate()`; None → skip concentration check (log INFO). §3.3 and §6.4 updated.
- **H1** — `combo_max_loss_pct` citation replaced with correct Phase 13 column names (`max_combo_loss_native`, `max_combo_net_delta`, alembic 0049); scope applicability bullet added. §2.1 updated.
- **H2** — `ForexInstrumentResolver` split: read-only resolver + `_ensure_forex_instrument` upsert helper; chunk routing updated (resolver in Chunk B, upsert in Chunk C via `rfq_service`). §2.1, §4.2, §9 updated.
- **H3** — `forex_max_notional_per_trade` scope applicability specified: global/account scopes only; resolution order: account → global → no cap; resolved via `_resolve_limit`. §2.1 updated.
- **M1** — Sidecar `_resolve_contract` FOREX `secType=CASH`/`IDEALPRO` branch added to Chunk C (15a); CRYPTO `secType=CRYPTO`/`PAXOS` branch added to Chunk B (15b). `PlaceOrder` itself needs no asset-class branch — conid qualification handles secType automatically. §6.2, §9 updated.
- **M2** — `accept_quote` inserts an `orders` row on accept success; `forex_rfq_quotes.order_id UUID FK` added to schema. Fills flow through existing `order_event_consumer` pipeline unchanged. §2.1, §4.2 updated.
- **M3** — Three-state transaction model spelled out: Session 1 sets `accepting` + explicit `await db.commit()`; sidecar RPC outside DB TX; Session 2 commits `accepted`/`rejected` + orders row in one TX. §4.2 updated.
- **M4** — Coinbase L2 sequence field name must be verified at impl time; guard added (None → log WARNING + skip gap detection, do not re-subscribe in a loop). §7.1 updated.
- **M5** — `crypto_order_book_snapshots` converted to hypertable with 7-day retention + 1h CAGG (top-3 levels). §2.2 updated.
- **L1** — Asset_class string-literal style (`"FOREX"`, `"CRYPTO"`) used in dispatch branches to match existing `risk_service.py:877` style. §3.3 updated.
- **L2** — `/api/forex/pairs` data source specified: `app_config[forex/enabled_pairs]`, defaults to IDEALPRO majors. §5.3 updated.
- **L3** — `FractionalQtyInput` moved to `src/components/primitives/` (primitive layer, no domain composition). §9 Chunk E updated.
