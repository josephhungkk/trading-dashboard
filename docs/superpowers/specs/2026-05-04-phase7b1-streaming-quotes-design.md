# Phase 7b.1 — Streaming quote engine (IBKR + Futu + Schwab broker streamers + Redis bus + FE WS gateway + instruments schema) — design

**Status:** architect-reviewed 2026-05-04. CRIT + HIGH + MED applied inline; LOWs deferred to plan (see §12).
**Target tag:** v0.7.1
**Architect-review distribution:** 3 CRIT + 7 HIGH + 11 MED + 5 LOW.
**Architectural pillars set in this phase:**
- (1) **Quote source decoupled from trade venue.** Bus topic = `quote.<source>.<canonical_id>`. Source enum is open-set. (Roadmap pillar #1, originally tagged Phase 7a; lands here.)
- (2) **`instruments` + `symbol_aliases(source, raw_symbol)` schema.** Single canonical id per security; per-source name resolution. (Roadmap pillar #2, lands here.)
- (3) **Broker streamers live inside their sidecars; backend is fan-in/fan-out only.** Backend never opens a broker socket; gRPC `StreamQuotes` is the bus between sidecar and backend.

This phase is **part 1** of the Phase 7b roadmap row. Part 2 (Phase 7b.2 → v0.7.2) ships Coinbase + OANDA + yfinance via a new `sidecar_market_data/` container. Phase 7b.1 ships the broker streamers + the engine + the FE gateway + the instruments schema; Phase 7b.2 adds non-broker sources without altering any 7b.1 interface.

## 1. Goal

Replace the frontend's `MockQuotesService` with a real-time, multi-source streaming quote engine. After this phase ships:

- Watchlist, positions, and orders pages show live ticks for US (Schwab), UK (IBKR LSE paid), HK (Futu free Lv1), and US cash indexes (Schwab `$`-symbology) at <1 s glass-to-glass.
- Backend operates a single `QuoteEngine` that fans in ticks over gRPC bidirectional streams from each sidecar, fans out to Redis pub/sub `quote.<source>.<canonical_id>` for cross-worker broadcast (load-bearing in Phase 24), and serves a frontend-facing WebSocket gateway `/ws/quotes` with MessagePack frames + per-connection conflation (focused 10 Hz, background 4 Hz).
- A new `instruments` + `symbol_aliases` schema (Alembic 0009) underpins per-source symbol resolution; rows are written on first observation (grow-on-demand) and seeded for all symbols held in `positions` / `orders` at boot.
- A `quote_source_priority` map in `app_config` drives source selection per `<asset_class>.<country>`; first healthy source wins.
- IBKR US market-data subscriptions can be cancelled day-1 (Schwab covers US streaming free). IBKR LSE UK + LSE International L1 (GBP 2/mo total) and IBKR STOXX Index Data Real-Time (EUR 3/mo) become the only paid IBKR data subs for the operator's profile, both verified API-streamable in this phase. **Saves $192–960/yr in IBKR data fees vs. typical-IBKR-only baseline.**

This phase **does not** ship Coinbase / OANDA / yfinance (see Phase 7b.2), bar aggregation / TimescaleDB hypertable (Phase 9), option / future / bond / mutual-fund streaming (Phases 12 / 14 / 16), or L2 depth (Phase 13).

## 2. Non-goals (explicitly deferred)

| Surface | Phase | Reason |
|---|---|---|
| Coinbase WS source, OANDA practice WS source, yfinance source | 7b.2 | All three live behind a new `sidecar_market_data/` container that speaks the same gRPC `StreamQuotes` RPC; landing them together is a clean follow-up release without any 7b.1 interface change. |
| Bar aggregator + TimescaleDB hypertable + 1m/5m/15m/1h/1d historical store | 9 | Roadmap pillar #7. Charting v1 phase. 7b.1 streams ticks; aggregator turns the same ticks into bars later. |
| Option L1 streaming (Schwab `LEVELONE_OPTIONS`, IBKR option `reqMktData`, Futu US options) | 12 | Polymorphic `contract_details` JSONB locks at Phase 12; option chain/strike/expiry UX is a phase. |
| Future L1 streaming (Schwab `LEVELONE_FUTURES`, `CHART_FUTURES`, IBKR futures) | 14 | Contract-month roll UX is a phase. |
| Bond + mutual-fund quotes | 16 | EOD NAV / REST snapshot — no streaming protocol exists upstream. |
| L2 depth / order-book book-views | 13 (combos backdrop) or later | UI surface is its own feature; not unblocked by 7b.1. |
| Schwab `SCREENER_EQUITY` (movers / scanner feed) | 18 | Belongs with universe scanner. |
| Multi-worker uvicorn cross-worker quote routing (active reader role) | 24 | Engine publishes to Redis bus already, but cross-worker readers are infra hardening phase. |
| L2 HK depth via Futu paid Lv2 / IBKR HK Lv2 | post-7b.1, separate brainstorm | Operator preference + cost question; trivial to wire once a Lv2 source is paid. |
| 13 sources × wired all together | per-phase as needed | Source enum is open-set in proto from 7b.1; only IBKR/Futu/Schwab wired in 7b.1. Twelve Data, Finnhub Free, EODHD, Tradier, Alpaca, Polygon, Binance designed-for, wired by demand. |

## 3. Architecture

### 3.1 Topology

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Browser tabs / panes                                                       │
│   RealQuotesService ─── WebSocket(MessagePack) ──┐                         │
│   sub / unsub / focus / ping                     │                         │
└──────────────────────────────────────────────────┼─────────────────────────┘
                                                   │ wss + CF Access JWT
                                                   ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Backend (FastAPI, single uvicorn worker in 7b.1; Phase 24 = N workers)     │
│                                                                            │
│  /ws/quotes ─► WSConnection ─► WSConflator (per-conn, 10 Hz outer loop)    │
│                                ├─ pending: dict[canonical_id, QuoteMsg]    │
│                                ├─ focused_symbol (None default)            │
│                                └─ rate caps: 10/s focused, 4/s background  │
│                                                                            │
│  WSConnection ─► SubscriptionRegistry (engine-wide refcount)               │
│                  ├─ per_ws: dict[ws_id, set[canonical_id]]                 │
│                  ├─ global_refs: dict[canonical_id, int]                   │
│                  └─ routes: dict[canonical_id, source_id]                  │
│                                                                            │
│  SubscriptionRegistry ─► SourceRouter (config-driven priority + health)    │
│  SourceRouter ─► SidecarStream (one per source × sidecar instance)         │
│                                                                            │
│  QuoteEngine ─► Redis pub: quote.<source>.<canonical_id>                   │
│              ─► in-process notify_subscribers() (skips Redis round-trip)   │
│              ─► Redis sub: same topic (load-bearing in Phase 24)           │
│              ─► StaleDetector (1 Hz sweep, asset-class threshold)          │
│              ─► InstrumentResolver (instruments + symbol_aliases tables)   │
└────────┬────────┬────────┬─────────────────────────────────────────────────┘
         │ gRPC bidi (mTLS)         │ gRPC bidi (in-net td-net)
         ▼        ▼        ▼        ▼
   ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────────────┐
   │ ibkr×4 │ │ futu   │ │ schwab │ │ market_data     │   (Phase 7b.2)
   │ 18001-4│ │ 18005  │ │ :9090  │ │  port 7b.2-set  │
   │ mTLS   │ │ mTLS   │ │ td-net │ │ in-net or mTLS  │
   └───┬────┘ └───┬────┘ └───┬────┘ └────────┬────────┘
       │          │          │               │
       ▼          ▼          ▼               ▼
  reqMktData  Subscribe   LEVELONE_         Coinbase WS
  (STK + IND) (QUOTE +    EQUITIES WS       OANDA WS
   exchanges:  K_QUOTE)    + $-index sym    yfinance REST poll
   LSE,        SubType.    via LEVEL_       (15-min delayed)
   CBOE-IND,   K_1M*       ONE_EQUITIES
   etc.)       *for Ph9
```

\* `K_1M` Futu subscription is not used in 7b.1 — Phase 9 will wire it for charting.

### 3.2 Source enum (proto, open-set)

```protobuf
enum QuoteSource {
  QUOTE_SOURCE_UNSPECIFIED = 0;
  IBKR         = 1;
  FUTU         = 2;
  SCHWAB       = 3;
  COINBASE     = 4;   // Phase 7b.2
  OANDA        = 5;   // Phase 7b.2
  YFINANCE     = 6;   // Phase 7b.2
  FINNHUB      = 7;   // Phase 18 (free tier as primary)
  TWELVE_DATA  = 8;   // designed-for, unwired
  ALPACA       = 9;   // designed-for, unwired
  POLYGON      = 10;  // designed-for, unwired
  BINANCE      = 11;  // designed-for, unwired
  EODHD        = 12;  // Phase 9 (charting historical bars)
  TRADIER      = 13;  // Phase 12 conditional (US options chains)
}
```

Open-set: adding new entries never breaks proto compat; client enums silently take the last-defined value if unaware.

### 3.3 Source-router default priority (overrides original roadmap matrix)

Per the operator profile (HK + UK + US active, EU/JP/AU/CA monitoring, IBKR data subs minimized):

| Asset / Market | Primary | Fallback | Real-time? | Cost |
|---|---|---|---|---|
| `stock.US` / `etf.US` | Schwab | IBKR (only if subscribed) → yfinance | ✓ | $0 |
| `stock.UK` | IBKR (LSE UK + LSE Intl L1, paid) | yfinance delayed | ✓ | GBP 2/mo |
| `stock.HK` / `etf.HK` / `warrant.HK` / `cbbc.HK` | Futu Lv1 | yfinance delayed | ✓ | $0 (Lv1 free) |
| `stock.EU` / `stock.JP` / `stock.AU` / `stock.CA` | yfinance | — | delayed 15 min | $0 |
| `index.US` ($SPX/$VIX/$NDX/$COMPX/$DJI/$RUT) | Schwab via `LEVELONE_EQUITIES` `$`-symbology | IBKR Cboe Streaming Indexes (paid) → yfinance | ✓ likely; verify in 7b.1 | $0 / $3.50 if Schwab fails |
| `index.EU` (DAX, EuroStoxx 50, CAC) | IBKR STOXX Index Data Real-Time (paid) | yfinance delayed | ✓ | EUR 3/mo |
| `index.HK` (HSI / HSCEI / HHI) | Futu (free with HK Lv1) | — | ✓ | $0 |
| `index.UK` (FTSE 100 / 250) | yfinance ^FTSE / ^FTMC | — | delayed | $0 |
| `index.JP` (Nikkei 225) | yfinance ^N225 | — | delayed | $0 |
| `forex.GLOBAL` | OANDA (Phase 7b.2) | IBKR IDEALPRO (free) | ✓ | $0 |
| `crypto.GLOBAL` | Coinbase (Phase 7b.2) | IBKR Paxos (free) | ✓ | $0 |

**Total real-time spend at v1.0 floor (this user's profile, 7b.1 close-out):** GBP 2 + EUR 3 ≈ **$5.75 / month**, dropping to **$2.55** if Schwab covers US indexes (likely). Adds **+$20 EODHD** in Phase 9 if global ex-US/HK charting is wanted.

### 3.4 Data flow (single tick, end-to-end)

```
1. Browser opens WS  ─►  /ws/quotes  ─►  WSConnection mounts (auth via CF Access JWT
                                          forwarded by nginx; existing Phase 2 verifier)
2. FE sends             { v:1, op:"sub", symbols:["stock:AAPL:US"] }
   (msgpack-encoded)
3. Backend WSConnection ─► SubscriptionRegistry.add(ws_id, ["stock:AAPL:US"])
   - returns the diff: ["stock:AAPL:US"] (refs went 0→1; new global sub)
4. SubscriptionRegistry ─► InstrumentResolver.resolve_or_create("stock:AAPL:US")
   - returns Instrument{asset_class=STOCK, primary_exchange="NASDAQ", currency="USD"}
5. SubscriptionRegistry ─► SourceRouter.route(instrument)
   - returns SourceId.SCHWAB (first healthy in ["schwab","ibkr","yfinance"])
6. SubscriptionRegistry ─► SidecarStream[SCHWAB].add([SymbolRef{...}])
   - emits a Subscribe message on the persistent bidi gRPC stream to schwab-sidecar:9090
7. schwab-sidecar Streamer.add([SymbolRef])
   - dedupes against its own internal refcount
   - if first ref: SUBS LEVELONE_EQUITIES {AAPL} on the live Schwab WebSocket
   - if symbol uses $-prefix index symbology: same SUBS, just different raw_symbol
8. Schwab WS streamer ─► QuoteMessage emitted on the gRPC stream
9. Backend SidecarStream ─► QuoteEngine._on_quote(q)
   - InstrumentResolver lookup (already cached after step 4)
   - QuoteEngine.cache.set(canonical_id, q)
   - Redis publish quote.schwab.stock:AAPL:US
   - in-process notify_subscribers(q) → every WSConflator with this canonical_id pending
10. WSConflator stores q in `pending["stock:AAPL:US"]`; outer 10 Hz loop drains
11. Outer loop checks rate cap (focused = 10/s, background = 4/s):
    - if min_gap satisfied → encode { v:1, op:"q", sym:"stock:AAPL:US", q:{...} } as
      msgpack, send via WS; clear pending; record last_sent
    - else: keep latest in pending; older ticks silently dropped (canonical conflation)
12. FE RealQuotesService.onMessage(msgpack-decoded frame) ─► Quotes Zustand store
    - watchlist row re-renders (React; NumericCell flashes per existing UI)
```

### 3.5 Cardinality + cost envelope

| Dimension | Phase 7b.1 expected | Phase 7b.2 add | Phase 9 add | Hard limit |
|---|---|---|---|---|
| Active WS connections (one user, ≤ 8 tabs) | ≤ 8 | same | same | 50 (alert) |
| Subscriptions per WS | ≤ 200 (watchlist + positions) | same | + 50 chart panes | 1000 (cardinality cap) |
| Engine-wide unique symbols | ≤ 500 | + 50 (forex/crypto) | + 100 (chart symbols) | 5000 (alert) |
| Upstream broker subs | == engine symbols (1:1 after dedup) | same | same | per-source: Schwab ≤ 500, Futu ≤ 500, IBKR ≤ 100/gateway |
| Bus messages/sec at peak | ~50 (US active hours) | + 30 (crypto 24/7) | + 100 (chart fan-out) | 2000 (Redis sustained) |
| FE WS frames/sec at peak | ~10 (4-10 Hz × focused) | same | same | OS WS limits |

Phase 7b.1 stays well within personal-scale budgets. Cardinality cap at 1000 subs/WS is a defensive guard — UI never approaches it.

## 4. Data model

### 4.1 `instruments` + `symbol_aliases` (Alembic 0009)

```sql
-- migration 0009_phase7b_instruments_symbol_aliases.py

CREATE TYPE instrument_asset_class AS ENUM (
  'STOCK',
  'ETF',
  'INDEX',
  'WARRANT',
  'CBBC',
  'FOREX',     -- 7b.2 wired
  'CRYPTO'     -- 7b.2 wired
  -- 'OPTION' added Phase 12; 'FUTURE' added Phase 14; 'BOND' added Phase 16
);

CREATE TABLE instruments (
  id                BIGSERIAL PRIMARY KEY,
  canonical_id      TEXT NOT NULL UNIQUE,
  asset_class       instrument_asset_class NOT NULL,
  primary_exchange  TEXT NOT NULL,        -- "NASDAQ", "LSE", "HKEX", "CBOE", "OANDA"
  currency          CHAR(3) NOT NULL,     -- "USD", "GBP", "HKD", "EUR"
  display_name      TEXT,
  meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
                                          -- room for asset-class extensions:
                                          -- {"isin":"US0378331005","cusip":"037833100","sector":"Technology"}
                                          -- Phase 12 adds {"option": {...}}
                                          -- Phase 14 adds {"future": {...}}
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX instruments_asset_class_idx ON instruments(asset_class);
CREATE INDEX instruments_exchange_idx    ON instruments(primary_exchange);

CREATE TABLE symbol_aliases (
  source            TEXT NOT NULL,        -- matches QuoteSource enum lowercased: "schwab", "futu", "ibkr", ...
  raw_symbol        TEXT NOT NULL,        -- "$SPX" (schwab), "SPX" (ibkr+CBOE+IND), "^GSPC" (yfinance)
  instrument_id     BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
                                          -- e.g. {"exchange":"CBOE","sec_type":"IND","conid":416843}
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, raw_symbol)
);

CREATE INDEX symbol_aliases_instrument_idx ON symbol_aliases(instrument_id);
```

**Canonical id format** (mirrors dashboard_old's `canonical_key`):

```
<asset_class>:<primary_symbol>:<country>
```

Examples:

| canonical_id | meaning |
|---|---|
| `stock:AAPL:US` | Apple, US-listed |
| `stock:0700:HK` | Tencent, HKEX |
| `stock:VOD:UK` | Vodafone, LSE UK |
| `etf:SPY:US` | SPDR S&P 500 ETF |
| `idx:SPX:US` | S&P 500 cash index |
| `idx:VIX:US` | CBOE VIX |
| `idx:HSI:HK` | Hang Seng Index |
| `idx:DAX:DE` | DAX (Phase 7b.1 if EU index sub paid) |
| `warrant:14841:HK` | HK warrant (synthetic example id) |
| `cbbc:67890:HK` | HK CBBC (synthetic example id) |

**Symbol alias examples** (all `conid` values are synthetic placeholders):

| canonical_id | source | raw_symbol | meta |
|---|---|---|---|
| `stock:AAPL:US` | schwab | `AAPL` | `{}` |
| `stock:AAPL:US` | ibkr | `AAPL` | `{"exchange":"NASDAQ","sec_type":"STK","conid":265598}` |
| `stock:AAPL:US` | yfinance | `AAPL` | `{}` |
| `idx:SPX:US` | schwab | `$SPX` | `{}` |
| `idx:SPX:US` | ibkr | `SPX` | `{"exchange":"CBOE","sec_type":"IND"}` |
| `idx:SPX:US` | yfinance | `^GSPC` | `{}` |
| `stock:0700:HK` | futu | `HK.00700` | `{}` |
| `stock:0700:HK` | ibkr | `700` | `{"exchange":"SEHK","sec_type":"STK","conid":1241836}` |
| `stock:VOD:UK` | ibkr | `VOD` | `{"exchange":"LSE","sec_type":"STK","currency":"GBP","conid":12345}` |

**Bootstrap policy:** grow-on-demand. No 10k-row preload. On first observation:

1. `InstrumentResolver.resolve_or_create(canonical_id)` looks up `instruments`; on miss, creates a row with best-effort `asset_class` / `primary_exchange` / `currency` parsed from the canonical_id format and the source's contract metadata.
2. Source-specific symbol mapping helpers populate `symbol_aliases` (e.g. `schwab.symbol_for(canonical_id) -> "$SPX"`).
3. Boot-time seed: `instruments_seed.py` writes rows for every canonical_id found in `positions` + `orders` + `watchlists` so existing held tickers resolve immediately on first quote.

**Concurrency-safe creation (CRIT-3 mitigation)**: two simultaneous ticks for the same novel symbol must NOT race or duplicate. Two-layer guard:

- **In-process layer (hot path, sub-microsecond)**: `InstrumentResolver` holds `dict[canonical_id, asyncio.Lock]` (lazy-created); each `resolve_or_create` call enters the symbol-keyed lock. Repeated calls for the same id serialize through one lock, second waiter sees a cache hit on the post-INSERT read.
- **DB layer (durable)**: SQL pinned to `INSERT INTO instruments (canonical_id, asset_class, primary_exchange, currency, display_name, meta) VALUES (...) ON CONFLICT (canonical_id) DO NOTHING RETURNING id`. If `RETURNING` returns no row, follow with `SELECT id FROM instruments WHERE canonical_id = $1`. Same shape for `symbol_aliases`: `INSERT ... ON CONFLICT (source, raw_symbol) DO NOTHING`. Wrap in `async with session.begin():` (NOT `s.begin_nested()` per memory `feedback_pytest_session_begin_commits.md`).
- The in-process lock dict caps at the global subscription limit (5000 entries); old entries TTL-evicted at 1 h since last access.

**Legacy-data seed fallback (MED-8 mitigation)**: boot seed iterates `positions` / `orders` / `watchlists` and:
- (a) if the row has a `canonical_id` column from a forward-compatible Phase 4-7a migration → use it directly;
- (b) else infer via `InstrumentResolver.from_legacy(broker_id, raw_symbol, exchange, currency)` which attempts the standard `<asset_class>:<symbol>:<country>` mapping;
- (c) inference failures (missing exchange, unknown asset class, ambiguous symbol) log `quote_seed_skipped_total{reason}` and continue boot — never fatal. The held position simply gets `op:"err", code:"NO_INSTRUMENT"` until operator manually creates the alias via admin endpoint.

**Dual-listing canonical_id rule (HIGH-5 mitigation)**: when two listings share `(asset_class, symbol, country)` but differ by `primary_exchange` (rare: e.g. NASDAQ + NYSE for the same ticker), the canonical_id format extends to `<asset_class>:<symbol>:<country>:<exchange>` for the SECOND-and-subsequent observation. The first observation wins the bare form (`stock:AAPL:US`); a later observation on a different exchange is registered as `stock:AAPL:US:NYSE`. The UNIQUE constraint on `canonical_id` prevents the conflict by construction. Documented in `deploy/runbook-quote-streaming-ops.md` under "symbol resolution: dual listings".

**UK pence guard (port from dashboard_old `scale_gbx_if_needed`)**: when `meta.exchange == "LSE"` and quote `currency == "GBp"` (penny denomination), divide by 100 and store as GBP. Sidecar performs this normalization before emitting QuoteMessage; same invariant as IBKR adapter's avg_cost normalization in Phase 4.

### 4.2 Proto extensions

```protobuf
// Added to existing service Broker (proto/broker/v1/broker.proto)

service Broker {
  // ... existing 12 RPCs unchanged ...
  rpc StreamQuotes(stream StreamQuotesRequest) returns (stream QuoteMessage);
}

message StreamQuotesRequest {
  oneof op {
    Subscribe   subscribe   = 1;
    Unsubscribe unsubscribe = 2;
    Heartbeat   heartbeat   = 3;
    Resync      resync      = 4;   // gRPC-only reconnect — see HIGH-1 mitigation §5.2.4
  }

  message Subscribe   { repeated SymbolRef symbols = 1; }
  message Unsubscribe { repeated SymbolRef symbols = 1; }
  message Heartbeat {
    google.protobuf.Timestamp client_time = 1;
    int32 tick_count_received = 2;  // backend-side counter for FE-debug
  }
  // Sidecar reconciles its upstream-broker refcount against `expected`; only diff
  // propagates to broker socket. Used when gRPC reconnects but sidecar process
  // didn't restart (Health.started_at unchanged).
  message Resync { repeated SymbolRef expected = 1; }
}

message SymbolRef {
  string canonical_id = 1;          // engine-side handle, e.g. "stock:AAPL:US"
  string raw_symbol   = 2;          // sidecar's native symbol, e.g. "AAPL", "$SPX", "HK.00700"
  AssetClass asset_class = 3;       // hint
  string exchange     = 4;          // hint, e.g. "NASDAQ", "CBOE", "SEHK", "LSE"
  string currency     = 5;          // hint, currency for UK pence guard
  bytes  source_meta  = 6;          // opaque, sidecar-specific (e.g. IBKR conid as bytes)
  // Reserved for Phase 12/14 extensions (option strike/expiry/right; futures
  // contract month/multiplier) without breaking 7b.1 wire format. Phase 12 will
  // graft `oneof contract_extra { OptionContractDetail option = 7;
  // FutureContractDetail future = 8; }` (MED-11).
  reserved 7 to 15;
}

message QuoteMessage {
  string canonical_id = 1;
  google.protobuf.Timestamp tick_time   = 2;   // broker-stamped if available, else sidecar wall-clock
  google.protobuf.Timestamp received_at = 3;   // sidecar wall-clock
  string source = 4;                            // matches QuoteSource enum lowercased

  // Decimal-as-string. Never float across the wire. Empty string = unset for that tick.
  string last       = 10;
  string bid        = 11;
  string ask        = 12;
  string volume     = 13;          // session total
  string day_high   = 14;
  string day_low    = 15;
  string open       = 16;
  string prev_close = 17;
  string change_pct = 18;          // computed sidecar-side for FE consistency
  string change     = 19;          // last - prev_close, computed sidecar-side

  // Staleness + delay
  bool   is_delayed     = 30;
  int32  delay_seconds  = 31;       // 900 for yfinance 15-min, 0 for real-time

  // Optional debug
  bytes  raw_payload    = 90;       // off by default, on under SIDECAR_TRACE_QUOTES env var
}
```

**RPC direction (CRIT-1)**: `StreamQuotes` is a **sidecar-side server-implemented RPC** — same as `Health` / `GetPositions` / `GetOrders`. **Backend is the gRPC client** and dials each sidecar over the existing channel (mTLS for IBKR/Futu over WG; in-net td-net for Schwab). Subscribe/Unsubscribe/Resync/Heartbeat travel **client→server** on the request side; QuoteMessage travels **server→client** on the response side. This is the inverse of `service BackendCallback` (Phase 7a) where the sidecar dials the backend; do not confuse the two. Each sidecar's existing gRPC server gains exactly one new method.

**Why bidirectional streaming**: one persistent gRPC call per (backend × sidecar instance); subs/unsubs are messages on the request side, ticks flow on the response side. Minimizes connection churn (200-symbol watchlist diff = 1 message, not 200 reconnects), gives gRPC flow control end-to-end backpressure, and matches the upstream broker socket semantics 1:1 (Schwab `LOGIN/SUBS/ADD/UNSUBS`, Futu `subscribe()`, IBKR `reqMktData`/`cancelMktData`). Decided in brainstorm (Q5) over server-streaming + per-symbol streams.

### 4.3 Frontend WS gateway frame schema (MessagePack-encoded)

**Client → Server frames:**

```jsonc
{ "v": 1, "op": "sub",   "symbols": ["stock:AAPL:US","idx:SPX:US"] }
{ "v": 1, "op": "unsub", "symbols": ["stock:AAPL:US"] }
{ "v": 1, "op": "focus", "symbol":  "stock:AAPL:US" }   // null to clear
{ "v": 1, "op": "ping",  "t": 1714824000123 }            // client_ms
```

**Server → Client frames:**

```jsonc
{ "v": 1, "op": "ack",   "sub": 5, "unsub": 0 }                            // refcount diff
{ "v": 1, "op": "snap",  "sym": "stock:AAPL:US", "q": { ... } }            // immediate cached snapshot
{ "v": 1, "op": "q",     "sym": "stock:AAPL:US", "q": { ... } }            // conflated tick
{ "v": 1, "op": "stale", "sym": "stock:AAPL:US", "since_ms": 6500 }
{ "v": 1, "op": "err",   "code": "NO_SOURCE", "msg": "...", "sym": "..." } // optional sym
{ "v": 1, "op": "pong",  "t": 1714824000123 }                              // echoed client_ms
```

**`q` payload** (matches FE `Quote` interface, decimals as JS numbers):

```jsonc
{
  "last": 213.45, "bid": 213.40, "ask": 213.46,
  "change": 1.23, "changePct": 0.0058,
  "volume": 38291842, "dayHigh": 214.10, "dayLow": 211.20,
  "open": 211.50, "prevClose": 212.22,
  "asOf": 1714824001456, "isDelayed": false, "delaySec": 0,
  "source": "schwab"
}
```

**Wire-format choice** (decided in brainstorm Q3): MessagePack chosen over plain JSON for ~30–40% smaller frames on numeric-heavy ticks, schema-versioned via `v` field. FE adds `@msgpack/msgpack`; backend uses `msgpack-python` (already a transitive dep via Schwab streamer; pin explicit).

**Conflation** (decided Q4): focused 10 Hz, background 4 Hz. Default focused = none → all subs at 4 Hz. FE elevates one symbol on chart/trade-ticket mount.

**Snapshot semantics (MED-9 corrected)**: a `sub` op delivers the latest cached tick immediately as `op:"snap"` *only* if `now() - received_at < quote_stale_threshold_seconds[asset_class]`. Above that threshold, no snapshot is sent — FE waits for first live tick (renders `—` placeholder). This avoids the contradiction where a 30-s-old US-stock snapshot would be served then immediately flagged stale (5 s threshold). The cache itself retains 60 s for cross-WS-reconnect snapshots; the served-snapshot gate is the per-asset-class stale threshold. Tested in `test_ws_conflator.py` and `test_quote_engine_e2e.py`.

**Field mapping (MED-1) — proto `QuoteMessage` → FE `q` payload**:

| Proto field | FE field | Conversion |
|---|---|---|
| `last` (decimal-string) | `last` (number) | `Number(s)` if ≤ JS-safe; else send as string + FE renders fallback |
| `bid`, `ask`, `day_high`, `day_low`, `open`, `prev_close` | same names (camelCase: `dayHigh`, `dayLow`, `prevClose`) | same conversion |
| `volume` (decimal-string, may be large) | `volume` (number) | `BigInt(s) → Number` truncation safe up to 2^53 |
| `change` | `change` (number) | same |
| `change_pct` | `changePct` (number) | same |
| `tick_time`, `received_at` | merged into single `asOf` (number, ms epoch) | use `received_at` (sidecar wall-clock) |
| `is_delayed` | `isDelayed` | direct bool |
| `delay_seconds` | `delaySec` | direct int |
| `source` | `source` | direct string |
| `raw_payload`, `source_meta` | — | **stripped at engine boundary (M22, INV-Q-2)** |

WS gateway converts decimal-string fields to JS-safe numbers at the boundary. Decimals exceeding JS-safe range (>2^53-1) are sent as strings instead — current scope (equity quotes < $1M, volume < 9 quadrillion) never hits this.

## 5. Components

### 5.1 Sidecar streamers

#### 5.1.1 `sidecar_schwab/streamer.py` (port from dashboard_old `services/quotes/providers/schwab_streamer.py` ≈ 95% reuse)

- Native WebSocket client for Schwab's `streamerSocketUrl` (resolved via `GET /trader/v1/userPreference` at boot — already cached by Phase 7a's Configure RPC; refresh via existing `_sync_tokens()`).
- Login frame using `streamerInfo.schwabClientCustomerId` / `schwabClientCorrelId` / `accessToken`.
- Subscribe service `LEVELONE_EQUITIES` with field mask `0=key,1=bid,2=ask,3=last,8=volume,12=close,28=high,29=low,30=open,33=last_size`.
- `$`-prefix index symbols (`$SPX`, `$VIX`, `$NDX`, `$COMPX`, `$DJI`, `$RUT`) routed through the SAME service. **7b.1 verification subagent task probes these symbols on day-1 to confirm real-time vs delayed status.** If delayed, fall back to IBKR Cboe Streaming Indexes ($3.50/mo).
- `ADD` / `UNSUBS` for incremental subscription changes (Schwab's native delta protocol).
- Reconnect on close with exponential backoff `min(2^n, 60s)`; replay current symbol set on each reconnect using one `SUBS`. Frame parse failures logged + dropped, loop continues.
- Heartbeat: Schwab sends NOTIFY frames; sidecar emits gRPC `Heartbeat` every 30 s with last-tick-received timestamp.
- `_sync_tokens()` integration (CRIT-2 mitigation): on access-token refresh (via BackendCallback per Phase 7a), **proactively force a streamer reconnect within ≤2 s** — Schwab's streamer authenticates by `accessToken` in the LOGIN frame and does NOT re-auth mid-stream. Schwab disconnects with `30/3` once the original token expires (~30 min TTL), causing a silent multi-minute gap with all subscribed symbols going dark. To avoid this:
  1. Sidecar holds an `asyncio.Event tokens_refreshed` set by `_sync_tokens()` after writing new headers.
  2. Streamer's main loop awaits a race: `done, pending = await asyncio.wait([recv_frame_task, tokens_refreshed.wait_task], return_when=FIRST_COMPLETED)`.
  3. If `tokens_refreshed` wins: cancel current WS, reconnect with new credentials, replay current symbol set.
  4. Emit `quote_token_rotation_reconnect_total{source="schwab"}` (counter) and `quote_token_rotation_gap_seconds` (histogram, p95 < 2 s expected).
- Token-refresh-driven reconnect is the only proactive close; all other reconnects are reactive (WS-level error / Schwab-side close).

#### 5.1.2 `sidecar_futu/streamer.py` (port from dashboard_old `services/quotes/providers/futu.py` ≈ 95% reuse)

- Reuses the existing OpenD connection from Phase 6 (`futu_client.py`).
- `subscribe(code_list, [SubType.QUOTE])` — adds canonical L1 best-bid-ask for HK stocks/ETFs/warrants/CBBC.
- Index subscription for HSI / HSCEI / HHI via the same `subscribe()` call with index symbol (e.g. `HK.800000` for HSI). Verify Futu free Lv1 includes these — **assertion test in golden trace.**
- `unsubscribe()` mirror.
- Internal refcount per `code`: only first ref calls OpenD `subscribe`, only last `unref` calls `unsubscribe`. Avoids double-subscribing.
- Quote callback (`set_handler(QuoteHandlerBase)`) emits `QuoteMessage` on the gRPC stream.
- `K_1M` (1-min bar) subscription support is **not wired in 7b.1**; Phase 9 will add.

#### 5.1.3 `sidecar_ibkr/streamer.py` (IBKR sidecar; port from dashboard_old `services/quotes/providers/ibkr.py` ≈ 70% reuse; rewrite to ib_async if old code used IBPy/raw ibapi). Note: the IBKR sidecar lives at `sidecar_ibkr/`, not `sidecar_ibkr/`.

- Per-gateway sidecar maintains its own subscription set (4 sidecars × independent universes).
- `reqMktData(contract, generic_tick_list, snapshot=False)` for STK + IND (cash indexes via `secType="IND"` `exchange="CBOE"` for SPX/VIX/etc.; LSE UK via `exchange="LSE"` for UK stocks).
- Tick types of interest: `tickPrice` (1=bid, 2=ask, 4=last, 6=high, 7=low, 9=close, 14=open), `tickSize` (0=bidSize, 3=askSize, 5=lastSize, 8=volume).
- UK pence guard: when contract `currency == "GBP"` AND tick prices are quoted in `GBp`, divide by 100 (mirrors Phase 4 IBKR adapter's avg_cost normalization).
- `cancelMktData(reqId)` mirror.
- Internal refcount + reqId pool (IBKR limits ~100 concurrent market data lines per gateway connection).
- **Verification subagent task (7b.1 close-out)**: probe per-exchange API exposure for LSE UK / LSE International / Cboe Streaming Indexes / STOXX Index Data; document in `deploy/runbook-ibkr-data-subs.md`. Some "Fee Waived" subs (notably HK L1) are TWS-display-only and not API-streamable; runbook captures the cancel/keep/subscribe matrix for the operator's profile.
- IBKR market-data subscription state is NOT in our control (operator manages via Client Portal); sidecar surfaces "No market data permissions" tick errors as `quote_resolve_misses_total{source="ibkr",reason="entitlement"}` and reports per-symbol via gRPC error frames; engine routes affected symbols to the next-priority source (yfinance).

### 5.2 Backend `QuoteEngine`

#### 5.2.1 `app/services/quotes/registry.py` (~200 lines, new)

Two-level refcount as designed in §3.1.

```python
class SubscriptionRegistry:
    per_ws: dict[WSConnId, set[CanonicalId]]
    global_refs: dict[CanonicalId, int]
    routes: dict[CanonicalId, SourceId]
    _lock: asyncio.Lock
    _sub_rate_limiter: dict[WSConnId, deque[float]]  # 100 sub frames / minute cap

    async def add(self, ws_id: WSConnId, symbols: list[CanonicalId]) -> SubscribeDiff:
        # Cap enforcement (HIGH-6): partial-success semantics
        # - per-WS cap: len(per_ws[ws_id]) + len(new) ≤ quote_engine_subscription_cap_per_ws (default 1000)
        # - global cap: number of unique CanonicalIds ≤ quote_engine_subscription_cap_global (default 5000)
        # - rate-limit: ≤100 sub frames/minute per ws_id
        # Rejected symbols returned in SubscribeDiff{rejected=[...], rejected_reason=[...]}
        # Successfully-added symbols still subscribe (partial success).
        ...
    async def remove(self, ws_id: WSConnId, symbols: list[CanonicalId]) -> UnsubscribeDiff: ...
    async def remove_ws(self, ws_id: WSConnId) -> UnsubscribeDiff: ...
    def get_active(self) -> set[CanonicalId]: ...    # for SidecarStream replay on reconnect
    def get_active_for(self, source: SourceId) -> set[CanonicalId]: ...  # per-source replay
```

`SubscribeDiff` / `UnsubscribeDiff` carry the `set[CanonicalId]` that became 0→1 / 1→0 globally; only those propagate upstream. Rejected symbols (cap or rate-limit hit) are returned to the WS gateway, which forwards them as `{op:"err", code:"CAP_EXCEEDED", count_rejected:N, sym:"first-rejected"}` to the FE while still acknowledging the accepted ones. Metrics: `quote_subscription_cap_rejected_total{cap_kind}` (kinds: `per_ws`, `global`, `rate_limit`).

#### 5.2.2 `app/services/quotes/router.py` (~200 lines, new)

```python
class SourceRouter:
    def __init__(self, config_service: ConfigService, source_health: SourceHealthMap): ...
    async def route(self, instrument: Instrument) -> SourceId | None: ...
    async def reroute(self, canonical_id: CanonicalId, current: SourceId,
                      reason: RerouteReason) -> SourceId | None: ...
```

- Reads `quote_source_priority` from `app_config` via `ConfigService.get(...)`; cached + invalidated via Redis pub/sub (existing Phase 2 surface).
- `reroute()` triggered on source down event, emits `Unsubscribe` to old + `Subscribe` to new transparently to FE. Gap visible only as a missed tick or two; no UI churn.
- **Source health (HIGH-7 corrected)**: `stream_alive AND time_since_any_tick < health_window` where `health_window = max(5 × min_stale_threshold_in_subscribed_set, 60s)`. The minimum 60 s prevents false-down on quiet symbols (after-hours, idle warrants). **Per-symbol staleness (rendered as `op:"stale"` to FE) is a separate UI signal — does NOT drive reroute.** Reroute triggers only on (a) stream-level `AioRpcError`, or (b) sustained zero-ticks across the *entire* subscribed set for `health_window`.
- New gauge `quote_source_health_state{source}` enum (0=down, 1=degraded, 2=healthy) for explicit observability. Health-flip events drive `quote_route_changes_total{from,to,asset_class}`.

#### 5.2.3 `app/services/quotes/engine.py` (~300 lines, port + ~50% rewrite of dashboard_old `engine.py`)

Glues registry + router + sidecar-stream tasks + Redis bus + WS subscribers. `_on_quote()` is the hot path.

```python
class QuoteEngine:
    async def start(self, config_service, redis, sidecar_clients): ...
    async def stop(self): ...

    async def subscribe(self, ws_id: WSConnId, symbols: list[CanonicalId]) -> int:
        # returns count subscribed; emits Subscribe upstream for the diff
    async def unsubscribe(self, ws_id: WSConnId, symbols: list[CanonicalId]) -> int: ...
    async def disconnect_ws(self, ws_id: WSConnId): ...

    async def _on_quote(self, q: QuoteMessage):
        # 1. raw_payload + source_meta strip (M22 boundary, MED-2)
        #    unless OPERATOR_TRACE_QUOTES=1 (backend-side env, distinct from sidecar trace flag)
        # 2. resolve instrument (cached after first lookup)
        # 3. update last_seen + cache; cache key uses received_at (sidecar wall-clock)
        # 4. clear stale flag if set
        # 5. publish to Redis bus with publisher_worker_id envelope (HIGH-4)
        # 6. notify in-process subscribers (per-WS conflators) — non-blocking on_quote()
        # — must be O(K) where K = subscribers for this canonical_id, NOT O(N)
        ...

    async def _stale_sweep_loop(self):
        # 1 Hz sweep over self.last_seen; uses received_at (sidecar wall-clock)
        # — NOT tick_time (broker-side, may be 15-min behind for delayed sources)
        # emits {op:"stale", since_ms} frame for late symbols
        ...
```

**Engine invariants:**

- **`INV-Q-1` (HIGH-4 — single-worker loopback suppression)**: in single-worker mode (`uvicorn --workers 1`, the 7b.1 default), the engine **publishes to Redis but does NOT subscribe to its own publishes**. Every Redis message envelope carries `publisher_worker_id` (uuid set at lifespan startup); the worker ignores any subscribed message whose `publisher_worker_id` matches its own. In Phase 24 multi-worker, this same envelope lets each worker subscribe to all topics *except* those it published itself — preventing double-delivery to local conflators. The in-process `notify_subscribers()` path is the ONLY delivery to local conflators. Documented + tested in `test_quote_engine_e2e.py` (assertion: in single-worker mode, every tick delivered exactly once to each subscribed conflator).
- **`INV-Q-2` (M22 boundary — MED-2)**: engine's first action in `_on_quote()` is `q.raw_payload = b''; q.source_meta = b''` unless `OPERATOR_TRACE_QUOTES=1` env var is set on the **backend** (separate from sidecar-side `SIDECAR_TRACE_QUOTES`). Stripping happens before cache, before Redis publish, before in-process notify — so audit consumers, future bots, and FE never see internal sidecar bytes. Test: `test_quote_engine_e2e.py` asserts both fields are empty in all WS frames + Redis bus messages by default.
- **`INV-Q-3` (HIGH-7 — staleness signals do NOT drive reroute)**: per-symbol staleness (rendered `op:"stale"` to FE) is a UI signal only. Reroute decisions consult source-aggregate health (see §5.2.2).
- **`INV-Q-4` (CRIT-2 — proactive reconnect on token rotation)**: the sidecar's `tokens_refreshed` Event is the single ordering primitive between `BackendCallback` token writes and the streamer's WS reconnect.

#### 5.2.4 `app/services/quotes/upstream/sidecar_stream.py` (~250 lines, new)

Per-source × per-sidecar persistent bidi gRPC stream task.

```python
class SidecarStream:
    def __init__(self, source: SourceId, channel: grpc.aio.Channel,
                 engine: QuoteEngine, registry: SubscriptionRegistry): ...
    async def run(self):
        # outer loop: connect → run inner stream → on close, backoff + reconnect
        backoff = 1.0
        while not self._stopping:
            try:
                async for resp in self._channel.StreamQuotes(self._request_iter()):
                    await self._engine._on_quote(resp)
                backoff = 1.0
            except (grpc.aio.AioRpcError, ConnectionError) as e:
                self._metrics.reconnect_total.labels(source=self._source).inc()
                await asyncio.sleep(min(backoff, 60))
                backoff *= 2

    async def _request_iter(self) -> AsyncIterator[StreamQuotesRequest]:
        # 1. on (re)connect: detect sidecar restart vs gRPC-only reconnect via Health.started_at
        #    - sidecar restart (started_at delta): yield Subscribe(symbols=registry.get_active_for(source))
        #      sidecar treats current upstream-side refcount as empty
        #    - gRPC-only reconnect (started_at unchanged): yield Resync(expected=registry.get_active_for(source))
        #      sidecar reconciles its own upstream refcount against `expected`; only diff propagates
        # 2. then drain self._pending_changes queue
        # 3. periodic heartbeat every 30s
        ...

    async def add(self, symbols: list[SymbolRef]): ...     # queue Subscribe
    async def remove(self, symbols: list[SymbolRef]): ...  # queue Unsubscribe
```

**Replay-on-reconnect idempotence (HIGH-1)**: distinguishing the two reconnect cases is required because IBKR's `reqMktData` is NOT idempotent against duplicate requests:

- **Case A — sidecar restart** (sidecar process exited; new `Health.started_at`): sidecar's upstream broker socket is freshly logged in (no prior subs). Backend sends `Subscribe(full_active_set)` — a clean replay. Schwab `LOGIN/SUBS` works fine; Futu `subscribe` works fine; IBKR `reqMktData` works fine because all reqIds are fresh.
- **Case B — gRPC-only reconnect** (backend disconnected, sidecar upstream WS still alive; same `Health.started_at`): the broker socket already has the subs. Backend sends `Resync(expected=full_active_set)`. Sidecar diffs `expected` against its own upstream-side refcount (which it maintains independently per §5.1.x), then issues only the delta upstream — no duplicate `reqMktData`, no `LEVELONE_EQUITIES SUBS` for already-subscribed symbols.
- Each sidecar maintains its own upstream-side refcount independent of any one gRPC client connection. Reconciliation is set-diff-based.

**IBKR gateway selection within `IBKR` source (MED-6 mitigation)**: there are 4 SidecarStream instances (one per gateway sidecar — isa-live, isa-paper, normal-live, normal-paper). Selection is config-driven via `app_config.ibkr_gateway_quote_assignment`:

```jsonc
{
  "ibkr_gateway_quote_assignment": {
    "stock.UK":  "isa-live",
    "stock.US":  "isa-live",
    "index.US":  "isa-live",
    "index.EU":  "isa-live",
    "_default":  "isa-live"
  },
  "ibkr_gateway_quote_fallback": ["normal-live"]
}
```

- Default = isa-live (operator's primary entitlement holder). Fallback to normal-live if primary in maintenance (per Phase 4 503 envelope).
- Paper sidecars (isa-paper, normal-paper) are NEVER quote sources — paper accounts have no real-time market-data subscriptions on IBKR.
- `SourceRouter.route()` returns `(SourceId.IBKR, gateway_label)`; the SidecarStream registry indexes by the tuple. Test in `test_source_router.py`: each `(asset_class, country)` maps to expected gateway, fallback works on primary-down event.

### 5.3 Backend WS gateway

#### 5.3.1 `app/api/ws_quotes.py` (~350 lines, new)

```python
@router.websocket("/ws/quotes")
async def ws_quotes(
    ws: WebSocket,
    cf_jwt: CFAccessJWT = Depends(require_admin_jwt_ws),
    engine: QuoteEngine = Depends(get_quote_engine),
):
    ws_id = uuid4()
    conflator = WSConflator(ws, focused_default=None)
    await ws.accept(subprotocol="msgpack-v1")

    consumer_task = asyncio.create_task(conflator.run(rate_focused=10, rate_background=4))
    engine.attach(ws_id, conflator)

    try:
        while True:
            raw = await ws.receive_bytes()
            frame = msgpack.unpackb(raw, raw=False)
            await _handle_client_frame(ws_id, frame, engine, conflator)
    except WebSocketDisconnect:
        pass
    finally:
        consumer_task.cancel()
        await engine.disconnect_ws(ws_id)
```

**CF Access JWT verification (HIGH-2 corrected)**: CF Access does NOT forward `Sec-WebSocket-Protocol` payloads as auth. It injects its own `Cf-Access-Jwt-Assertion` request header on every request including WebSocket upgrades. The new `require_admin_jwt_ws` dep reads `ws.headers["cf-access-jwt-assertion"]` from the upgrade request, validates via existing Phase 2 `CFAccessVerifier`, and either accepts (101) or rejects (401) the upgrade *before* `ws.accept()` fires. The `Sec-WebSocket-Protocol` field is reserved exclusively for `msgpack-v1` content negotiation. Dev-bypass over WG (10.10.0.1) honored as in HTTP via existing nginx bypass policy. Required test coverage in `test_ws_auth.py`:
- Upgrade with valid CF JWT → 101 + `subprotocol="msgpack-v1"` accepted.
- Upgrade with invalid / missing JWT → 401 *during* upgrade (not after accept).
- Upgrade over 10.10.0.1 with no JWT → 101 (dev-bypass).
- Upgrade with valid JWT but `Sec-WebSocket-Protocol` lacking `msgpack-v1` → 426 Upgrade Required.

#### 5.3.2 `WSConflator` (in same module)

```python
class WSConflator:
    pending: dict[CanonicalId, QuoteMessage]
    last_sent: dict[CanonicalId, float]            # monotonic
    focused_symbol: CanonicalId | None
    rate_focused: float = 10.0                      # Hz
    rate_background: float = 4.0                    # Hz

    async def run(self):
        # 10 Hz outer loop drains pending per rate cap
        outer_period = 1.0 / max(self.rate_focused, self.rate_background)
        while True:
            await asyncio.sleep(outer_period)
            await self._drain_once()

    async def _drain_once(self):
        now = time.monotonic()
        out = []
        for sym in list(self.pending.keys()):
            rate = self.rate_focused if sym == self.focused_symbol else self.rate_background
            min_gap = 1.0 / rate
            if now - self.last_sent.get(sym, 0.0) >= min_gap:
                out.append(self.pending.pop(sym))
                self.last_sent[sym] = now
        for q in out:
            try:
                await asyncio.wait_for(self._send(self._frame("q", q)), timeout=2.0)
            except asyncio.TimeoutError:
                self._metrics.send_timeout_total.inc()
                await self._close_and_let_fe_reconnect()
                return  # abort drain; engine.disconnect_ws() runs in finally

    def on_quote(self, q: QuoteMessage):     # called by QuoteEngine.notify_subscribers — NON-BLOCKING
        self.pending[q.canonical_id] = q     # latest only; older ticks dropped (canonical conflation)
        # `pending` is bounded at subscription_cap_per_ws (≤1000) by construction:
        # each canonical_id has at most one pending entry. New ticks for the same
        # symbol replace the previous; older ticks are dropped silently.

    def set_focus(self, sym: CanonicalId | None):
        prev = self.focused_symbol
        self.focused_symbol = sym
        # nothing else — next drain naturally promotes/demotes
```

**Slow-client isolation (HIGH-3 mitigation)**:

- `WSConflator.on_quote(q)` is **non-blocking** by contract — only writes to a dict. `QuoteEngine._on_quote()` calls every relevant conflator's `on_quote()` synchronously and never awaits send.
- `_drain_once` wraps `await self._send(...)` in `asyncio.wait_for(..., timeout=2.0)`. On timeout: increment `quote_ws_send_timeout_total`, close the WS (FE will reconnect), let `engine.disconnect_ws()` clean up subscriptions in the endpoint's `finally` block.
- This isolates one slow / mobile / paused client from the rest. A stalled tab does NOT stall other tabs or the engine fan-out.

### 5.4 Frontend `RealQuotesService`

#### 5.4.1 `frontend/src/services/quotes.ts` (~250 lines, replaces `MockQuotesService`)

```ts
export class RealQuotesService implements QuotesService {
  private ws: WebSocket | null = null;
  private subscriptions = new Map<string, Set<(q: Quote) => void>>();
  private snapshots = new Map<string, Quote>();
  private focused: string | null = null;
  private reconnectBackoffMs = 1000;
  private pendingFrames: Frame[] = [];

  getSnapshot(symbol: string): Quote | undefined {
    return this.snapshots.get(symbol);
  }

  subscribe(symbols: string[], cb: (q: Quote) => void): () => void {
    for (const s of symbols) {
      if (!this.subscriptions.has(s)) {
        this.subscriptions.set(s, new Set());
        this.send({ v: 1, op: 'sub', symbols: [s] });
      }
      this.subscriptions.get(s)!.add(cb);
    }
    return () => { /* unref + send unsub */ };
  }

  setFocus(symbol: string | null) {
    if (this.focused === symbol) return;
    this.focused = symbol;
    this.send({ v: 1, op: 'focus', symbol });
  }

  setTickingEnabled(on: boolean): void {
    if (!on) this.disconnect(); else this.connect();
  }

  private connect() {
    this.ws = new WebSocket(WS_URL, ['msgpack-v1']);
    this.ws.binaryType = 'arraybuffer';
    this.ws.onmessage = (e) => this.onMessage(decode(new Uint8Array(e.data as ArrayBuffer)));
    this.ws.onclose = () => this.scheduleReconnect();
    this.ws.onopen = () => this.replaySubscriptions();
  }

  private scheduleReconnect() {
    setTimeout(() => this.connect(), Math.min(this.reconnectBackoffMs *= 2, 30_000));
  }

  // ...
}
```

#### 5.4.2 `frontend/src/services/ws.ts` (real impl replacing stub)

```ts
export function connectWs(): WebSocket {
  const url = `${import.meta.env.VITE_API_BASE.replace(/^http/, 'ws')}/ws/quotes`;
  return new WebSocket(url, ['msgpack-v1']);
}
```

#### 5.4.3 Hook + integrations

- `useFocusedSymbol(symbol)` hook: on mount → `quotes.setFocus(symbol)`; on unmount → `quotes.setFocus(null)`. Used by Trade ticket and (future Phase 9) Chart pane.
- Existing `useWatchlistTicker()` hook already calls `subscribe()` + per-row callbacks; no API change needed — it was designed against `MockQuotesService` interface, which `RealQuotesService` matches verbatim.

### 5.5 Configuration

New `app_config` keys (Phase 2 admin API can edit at runtime):

```jsonc
{
  "quote_source_priority": {
    "stock.US":    ["schwab","ibkr","yfinance"],
    "stock.UK":    ["ibkr","yfinance"],
    "stock.HK":    ["futu","yfinance"],
    "stock.JP":    ["yfinance"],
    "stock.AU":    ["yfinance"],
    "stock.CA":    ["yfinance"],
    "stock.EU":    ["yfinance"],
    "etf.US":      ["schwab","ibkr","yfinance"],
    "etf.HK":      ["futu","yfinance"],
    "index.US":    ["schwab","ibkr","yfinance"],
    "index.EU":    ["ibkr","yfinance"],
    "index.UK":    ["yfinance"],
    "index.HK":    ["futu"],
    "index.JP":    ["yfinance"],
    "warrant.HK":  ["futu"],
    "cbbc.HK":     ["futu"]
  },
  "quote_stale_threshold_seconds": {
    "stock.US": 5, "stock.UK": 10, "stock.HK": 10,
    "stock.EU": 30, "stock.JP": 30, "stock.AU": 30, "stock.CA": 30,
    "etf.US": 5, "etf.HK": 10,
    "index.US": 5, "index.EU": 10, "index.UK": 30, "index.HK": 10, "index.JP": 30,
    "warrant.HK": 10, "cbbc.HK": 10,
    "_default_delayed_source": 1500
  },
  "quote_ws_focus_rate_hz": 10,
  "quote_ws_background_rate_hz": 4,
  "quote_engine_subscription_cap_per_ws": 1000,
  "quote_engine_subscription_cap_global": 5000,
  "quote_subscription_warmup_ms": 5000,
  "ibkr_gateway_quote_assignment": {
    "stock.UK": "isa-live", "stock.US": "isa-live",
    "index.US": "isa-live", "index.EU": "isa-live",
    "_default": "isa-live"
  },
  "ibkr_gateway_quote_fallback": ["normal-live"]
}
```

**Stale-detection time base (MED-3)**: stale check uses `received_at` (sidecar wall-clock) NOT `tick_time` (broker-stamped, may be 15-min behind on yfinance / IBKR delayed feeds). The `is_delayed` flag is a UI badge only — does not enter the stale calculation. `_default_delayed_source = 1500 s` covers the case where a delayed source itself stops emitting (e.g. yfinance REST poll dies); applies to `(asset_class, country)` combos not in the explicit per-class table when `is_delayed=true`.

## 6. Tests

| Tier | Files / harness | Coverage target | Notes |
|---|---|---|---|
| Unit | `backend/tests/unit/test_quotes_canonical.py` (port from old) | `canonical_key` deterministic across asset classes + edge cases (Lv2 underscores, GBp variants) | ~74 lines port |
| Unit | `backend/tests/unit/test_subscription_registry.py` | refcount transitions: 0→1 / 1→0 / WS disconnect bulk-sweep / cap enforcement | ~150 lines new |
| Unit | `backend/tests/unit/test_source_router.py` | priority walk, health flip, reroute decisions, missing-config fallback | ~120 lines new |
| Unit | `backend/tests/unit/test_ws_conflator.py` | 10/4 Hz cap math, focus elevation/demotion, latest-only conflation, drain race | ~150 lines new |
| Unit | `backend/tests/unit/test_stale_detector.py` | per-asset-class threshold; un-stale on resumed flow; `is_delayed=true` exemption | ~80 lines new |
| Unit | `backend/tests/unit/test_instrument_resolver.py` | resolve-or-create, alias write-on-first-observation, GBX guard hook | ~100 lines new |
| Sidecar golden trace | `sidecar_schwab/tests/test_streamer.py` | recorded `LEVELONE_EQUITIES` WS session + `$SPX` index session → asserts QuoteMessage bytes | port ~85% from old (~170 lines) |
| Sidecar golden trace | `sidecar_futu/tests/test_streamer.py` | recorded SubType.QUOTE callback for HK stock + HSI index → QuoteMessage | ~150 lines new (no existing trace) |
| Sidecar golden trace | `sidecar_ibkr/tests/test_streamer.py` (IBKR sidecar dir) | recorded `tickPrice` + `tickSize` events for AAPL + VOD (LSE GBp guard) + SPX (IND) | ~180 lines new |
| Integration | `backend/tests/integration/test_quote_engine_e2e.py` | fake gRPC sidecar server emits scripted ticks → backend QuoteEngine → `/ws/quotes` MessagePack frames → asserts FE-visible frame sequence (snap → q × N → stale → q resumed → ack on unsub) | ~300 lines new |
| Integration | `backend/tests/integration/test_alembic_0009.py` | upgrade + downgrade round-trip; partial index sanity | ~80 lines new |
| Integration | `backend/tests/integration/test_quote_resolve_loop.py` | concurrent first-observation writes don't deadlock or duplicate rows | ~80 lines new |
| Integration | `backend/tests/integration/test_ws_auth.py` | CF Access JWT pass + dev-bypass over WG + dev-bypass-fail | ~100 lines new |
| E2E | `tests/e2e/streaming-quotes.spec.ts` | open watchlist → confirm tick updates within 1s → focus row → confirm 10 Hz vs 4 Hz delta in DevTools | ~120 lines new |
| Real-Schwab smoke (gated) | `.github/workflows/nightly-real-schwab.yml` extension | `CI_USE_REAL_SCHWAB=1` subscribes `$SPX/$VIX/AAPL`, asserts real-time vs delayed + first-tick latency < 5s | extends existing nightly |
| Load (cardinality stress) (MED-5) | `backend/tests/load/test_quote_engine_cardinality.py` | 1000 subs across 10 fake WS connections; fake gRPC sidecar emits 100 ticks/s; assert engine `_on_quote` p99 latency < 5 ms, no memory growth over 60 s, exactly K conflator notifications per tick (K = subscribers for that canonical_id), no missed deliveries. `[load]` pytest mark, gated off default CI, exercised in nightly. | ~200 lines new |
| Concurrency (race-on-create) (CRIT-3) | `backend/tests/integration/test_quote_resolve_loop.py` (already listed; **strengthened**) | ≥50 concurrent `resolve_or_create` for the same novel canonical_id; assert exactly one `instruments` row + one `symbol_aliases` row + zero exceptions. Mix in same-symbol unique-violation paths via deliberate retry. | strengthened from prior |
| Token rotation reconnect (CRIT-2) | `sidecar_schwab/tests/test_streamer_token_rotation.py` | simulate `tokens_refreshed` Event firing mid-stream → assert WS reconnects within 2 s, `quote_token_rotation_gap_seconds` p95 < 2 s, no tick drops beyond the natural reconnect gap | ~120 lines new |

**Coverage target:** ≥ 85% lines on the new code (backend services/quotes/* + sidecar streamer integrations + ws_quotes endpoint + RealQuotesService FE).

## 7. Deployment + ops

### 7.1 Migration order

1. Alembic 0009 (instruments + symbol_aliases) on PG-18 (NUC-native). Must run before any sidecar starts emitting ticks because resolve-or-create assumes the tables exist.
2. Backend deploy ships with new `/ws/quotes` endpoint, new `quote_source_priority` defaults seeded by lifespan startup (only if missing — operator overrides preserved).
3. Sidecar deployments (NUC IBKR + Futu via PyInstaller; Schwab in-cluster via docker-compose.prod.yml) ship the new `streamer.py` + the gRPC `StreamQuotes` handler. They register the RPC unconditionally; backend opens the bidi stream at lifespan startup.
4. Backend `BrokerRegistry.start_quote_streams()` opens N gRPC streams (4 IBKR + 1 Futu + 1 Schwab = 6 in 7b.1) at lifespan; each stream replays `Subscribe(active_set_for_source)` from registry on first open. At fresh boot the active set is the seed of held-position symbols; future operator-induced subs grow it.
5. Frontend deploy lands `RealQuotesService`. Mock service can be re-enabled via `VITE_QUOTES_USE_MOCK=true` for local dev / Storybook / E2E without a backend.

### 7.2 Operator runbooks (Phase 7b.1 close-out)

- `deploy/runbook-quote-coverage.md` — output of the verification subagent task: per-asset-class real-time vs delayed status across Schwab + IBKR + Futu, including the `$SPX/$VIX/...` real-time/delayed verdict that gates whether IBKR Cboe Streaming Indexes is needed.
- `deploy/runbook-ibkr-data-subs.md` — cancel/keep/subscribe matrix for the operator's IBKR account, including the API-streamability verification per exchange. Must call out HK L1's TWS-only nature explicitly.
- `deploy/runbook-quote-streaming-ops.md` — how to add a new source (proto enum entry + sidecar streamer + symbol_aliases mapping helper); how to debug a stuck stream (`docker compose logs -f schwab-sidecar`, gRPC channel state); how to manually reset the engine without backend restart (`POST /api/admin/quote-engine/reset`).

### 7.3 Backwards-compatibility / rollback

- Sidecar `StreamQuotes` RPC returns `UNIMPLEMENTED` until the streamer wiring per-sidecar is verified by golden-trace tests. Ship in two waves if needed (Schwab first, then Futu, then IBKR ×4).
- Backend `QuoteEngine` falls back to `MockQuotesService`-style behavior (warns operators) if `quote_engine_enabled=false` in `app_config`. Toggle defaults to `true` after the verification subagent confirms day-1 stability.
- Frontend `RealQuotesService` falls back to `MockQuotesService` on persistent WS reconnect failures (3 consecutive >30s gaps) with a banner: "Live quotes unavailable — showing simulated data." No automatic re-enable; operator must reload.

## 8. Observability

### 8.1 Metrics (new in `app/core/metrics.py`)

```
quote_subscriptions_active{source}              gauge
quote_ticks_total{source,asset_class}           counter   # asset_class for cardinality cap
quote_ticks_dropped_total{source,reason}        counter   # reasons: conflation, stale, route_unknown
quote_stream_reconnect_total{source}            counter
quote_stream_uptime_seconds{source}             gauge
quote_route_changes_total{from,to,asset_class}  counter
quote_stale_active_count{asset_class}           gauge
quote_ws_connections_active                     gauge
quote_ws_frames_sent_total{op}                  counter
quote_ws_frame_bytes_sent                       histogram
quote_engine_loop_lag_seconds                   histogram   # tick-in to notify-out latency
quote_resolve_misses_total{source,reason}       counter     # reasons: alias_unknown, entitlement, contract_not_found
quote_uk_pence_normalizations_total             counter     # informational; non-zero is normal
quote_ws_focus_changes_total                    counter
quote_token_rotation_reconnect_total{source}    counter     # CRIT-2
quote_token_rotation_gap_seconds                histogram   # CRIT-2
quote_ws_send_timeout_total                     counter     # HIGH-3
quote_subscription_cap_rejected_total{cap_kind} counter     # HIGH-6 — kinds: per_ws, global, rate_limit
quote_source_health_state{source}               gauge       # HIGH-7 — 0=down, 1=degraded, 2=healthy
quote_instruments_created_total{asset_class}    counter     # MED-10
quote_seed_skipped_total{reason}                counter     # MED-8 — reasons: missing_exchange, unknown_asset_class, ambiguous_symbol
schwab_index_delayed_observed                   gauge       # 0/1 from §7b.1 verification subagent
```

`canonical_id` is intentionally NOT a label dimension — would explode cardinality; per-symbol traces use logs / debug payloads instead.

### 8.2 Alerts (`deploy/prometheus/alerts.yml` new `phase7b_quotes` group, 14 alerts)

| Alert | Expr | Severity | Window |
|---|---|---|---|
| `QuoteSourceDown` | `quote_stream_uptime_seconds{source} == 0` | warning | 2 m |
| `QuoteStreamReconnectFlapping` | `rate(quote_stream_reconnect_total[15m]) * 60 > 6` | warning | 15 m |
| `QuoteStaleHighRate` | `quote_stale_active_count / quote_subscriptions_active > 0.10` | warning | 5 m |
| `QuoteRouteChurnHigh` | `rate(quote_route_changes_total[10m]) * 60 > 1` | warning | 10 m |
| `QuoteEngineLag` | `histogram_quantile(0.95, sum(rate(quote_engine_loop_lag_seconds_bucket[5m])) by (le)) > 0.25` | page | 5 m |
| `QuoteWSConflationDropHigh` | `rate(quote_ticks_dropped_total{reason="conflation"}[5m]) / rate(quote_ticks_total[5m]) > 0.5` | warning | 5 m |
| `QuoteResolveMissHigh` | `rate(quote_resolve_misses_total[5m]) * 60 > 1` | warning | 5 m |
| `QuoteSchwabIndexDelayed` | `schwab_index_delayed_observed == 1` (one-shot post-7b.1 verification gauge) | info | 1 m |
| `QuoteWSConnectionsHigh` | `quote_ws_connections_active > 50` | info | 5 m |
| `QuoteUKPenceUnitMismatch` | `quote_uk_pence_normalizations_total{exchange="LSE"} == 0 AND quote_subscriptions_active{source="ibkr",exchange="LSE"} > 0` (no penny normalizations seen but LSE subs active for >10 min) | warning | 10 m |
| `QuoteTokenRotationGapHigh` *(CRIT-2)* | `histogram_quantile(0.95, sum(rate(quote_token_rotation_gap_seconds_bucket[15m])) by (le)) > 5` for >2 events | warning | 15 m |
| `QuoteWSSendTimeoutHigh` *(HIGH-3)* | `rate(quote_ws_send_timeout_total[5m]) * 60 > 1` | warning | 5 m |
| `QuoteSubscriptionCapHit` *(HIGH-6)* | `quote_subscriptions_active{source!="all"} / 5000 > 0.80` (sustained) | warning | 5 m |
| `QuoteInstrumentsTableGrowthAnomaly` *(MED-10)* | `rate(quote_instruments_created_total[1h]) * 3600 > 50` | warning | 1 h |

### 8.3 Logs

structlog fields on every quote event (structured JSON):

```jsonc
{
  "event": "quote.tick",
  "source": "schwab",
  "canonical_id": "stock:AAPL:US",
  "tick_time": "2026-05-04T15:23:00.123Z",
  "received_at": "2026-05-04T15:23:00.456Z",
  "engine_lag_ms": 333,
  "is_delayed": false,
  "ws_subscribers": 3
}
```

Per redaction rule (Phase 2 logging.py): no auth tokens / API keys / refresh tokens leak via the `raw_payload` field — the sidecar trace flag is operator-only and respects existing redaction processors.

## 9. Open risks + mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Schwab `$`-symbology returns delayed (not real-time) quotes for cash indexes | medium | Day-1 verification subagent task probes; if delayed, trigger `QuoteSchwabIndexDelayed` alert + recommend IBKR Cboe Streaming Indexes $3.50/mo in runbook. Engine routes affected indexes to IBKR fallback automatically once subscribed. |
| IBKR LSE / STOXX subs are TWS-display-only, not API-streamable | high | Day-1 verification subagent probes via `reqMktData` smoke; if unavailable, triggers route fallback to yfinance delayed for affected exchanges + runbook documents the limitation. Operator can revisit Twelve Data Pro $79/mo or Polygon for paid intl real-time. |
| Symbol alias collisions across sources (e.g. `SPX` means S&P 500 cash on IBKR but a different ticker elsewhere) | medium | `symbol_aliases` PRIMARY KEY is `(source, raw_symbol)` not `raw_symbol` alone — collisions are impossible by construction. Alias write-on-first-observation logs a warning if the same (source, raw_symbol) maps to a new instrument_id. |
| Bidi gRPC stream stalls on slow consumer (backend Redis blocked → sidecar can't write) | medium | gRPC flow control naturally pauses sidecar; if pauses sustain >10 s, alert `QuoteEngineLag` fires. Engine drops oldest pending Redis publishes after 1000-message buffer. |
| Frontend WS gateway scales beyond one uvicorn worker (Phase 24 prep) | low (this phase) | Engine publishes to Redis bus already; cross-worker reader role added in Phase 24. 7b.1 single-worker is fine for personal-scale (≤8 tabs, ≤500 symbols). |
| MessagePack frame schema evolution breaks old FE clients | low | `v` field on every frame; FE rejects unknown `v`; backend supports `v=1` only in 7b.1. Phase 12 adds `v=2` if option chains require new frame ops; coexists. |
| Bootstrap race: WSConnection mounts before SidecarStream connects | medium | **Engine returns immediate `{op:"ack"}` for the sub** — never blocks the FE. The symbol's first tick may arrive after `quote_subscription_warmup_ms` (default 5000); FE renders `—` placeholder until first `op:"snap"` or `op:"q"`. If a source is `state=down` at sub time, FE receives `{op:"err", code:"SOURCE_DOWN", retry_in_ms:5000, sym:"..."}` AND the engine still enqueues the sub so it activates on source recovery — no FE retry loop required. (MED-7) |
| Refresher Tier-2 (Phase 7a) renews Schwab token mid-stream → streamer needs to rotate auth | medium | Phase 7a's `_sync_tokens()` already updates header in-process; new ticks use new auth on next gRPC frame. Tested in `test_token_rotation_atomicity.py` extension. |
| `instruments` rows leak (created on observation but never cleaned) | low | Cleanup is out of scope for 7b.1; rows are small (~120 B). Phase 9 charting or Phase 24 infra hardening adds a `last_observed_at` TTL sweep if needed. |
| UK pence guard fires on a non-LSE GBP ticker | low | Guard is gated on `exchange == "LSE"` AND `currency in {"GBp","GBX"}`. Other GBP-quoted tickers (e.g. some warrants on different LSE tiers) keep their original units. Alert `QuoteUKPenceUnitMismatch` catches missed normalizations. |

## 10. Phase 7b.1 chunk plan

| Chunk | Tasks | Dependencies | Est. lines | Codex / Claude split |
|---|---|---|---|---|
| **A — Proto + alembic** | Add `StreamQuotes` RPC + `SymbolRef` + `QuoteMessage` (with `change` field, `Resync` op variant, `reserved 7-15`) to broker.proto; codegen via buf; Alembic 0009 (instruments + symbol_aliases) with `INSERT … ON CONFLICT DO NOTHING RETURNING` shape; `instruments_seed.py` boot helper with legacy fallback; in-process `asyncio.Lock` dict for resolve-or-create races. **Test guidance:** Alembic 0009 tests use the outer-transaction fixture from memory `feedback_pytest_session_begin_commits.md`; runs against the dev-only PG schema (NEVER pytest from `backend/` against prod `DATABASE_URL` per `feedback_pytest_prod_db_wipe.md`). | none | ~280 | Codex source; Claude tests (incl. concurrency stress) + commit |
| **B — Backend QuoteEngine core** | `services/quotes/{base,registry,router,engine}.py`; `services/quotes/upstream/sidecar_stream.py`; `InstrumentResolver` with two-layer creation guard; SubscriptionRegistry with cap + rate-limit; SourceRouter with health window + IBKR gateway selection; engine invariants `INV-Q-1` through `INV-Q-4` | A | ~750 | Codex source; Claude unit tests + integration scaffolding + commit |
| **C — sidecar_schwab streamer** | port `schwab_streamer.py` from old → `sidecar_schwab/streamer.py`; wire `StreamQuotes` RPC handler; `$`-symbology integration; `tokens_refreshed` Event + proactive reconnect on token rotation; sidecar-side upstream-refcount for `Resync` reconciliation | A | ~520 | Codex source (dashboard_old port); Claude golden-trace test + token-rotation reconnect test + commit |
| **D — sidecar_futu streamer** | port `futu.py` from old → `sidecar_futu/streamer.py`; wire RPC handler; HSI/HSCEI/HHI index inclusion test (assert Futu free Lv1 covers them); sidecar-side upstream-refcount | A | ~420 | Codex source (port); Claude golden-trace + commit |
| **E — sidecar_ibkr streamer** | port `ibkr.py` from old → 4 sidecars `streamer.py`; wire RPC handler; LSE GBp guard (gated on `exchange=="LSE"` AND `currency in {"GBp","GBX"}`); sidecar-side upstream-refcount + reqId pool; verification subagent task (Claude-driven, read-only IBKR API probes — no code generation) | A | ~430 | Codex source (port + ib_async rewrite); Claude golden-trace + Claude-driven verification subagent + commit |
| **F — Backend WS gateway** | `api/ws_quotes.py` + `WSConflator` with `asyncio.wait_for` timeout + slow-client isolation; `require_admin_jwt_ws` dep reading `Cf-Access-Jwt-Assertion` header; subprotocol-only `msgpack-v1` negotiation; Redis bus pub with `publisher_worker_id` envelope | B | ~480 | Codex source; Claude WS auth tests (CF JWT + dev-bypass + 426) + integration + commit |
| **G — Frontend RealQuotesService** | replace `MockQuotesService`; real `connectWs()`; `useFocusedSymbol` hook; reconnect logic with bounded `pendingFrames` cap (≤100, drop-oldest); MessagePack codec via `@msgpack/msgpack`; `op:"err"` handling for `CAP_EXCEEDED`/`SOURCE_DOWN`/`SOURCE_STARTING` | F | ~420 | Codex source; Claude FE tests + Storybook + Playwright + commit |
| **H — Verification + runbooks + close-out** | day-1 verification subagent (Schwab `$SPX/$VIX/$NDX/$COMPX/$DJI/$RUT` real-time vs delayed probe, IBKR LSE UK / LSE Intl / STOXX API-streamability via `reqMktData` smoke); 3 runbooks; metric/alert wiring; CHANGELOG + TASKS + CLAUDE.md updates; v0.7.1 tag | A-G | ~400 | Claude (runbooks + verification are docs/probes, not source code) |

**Total estimate:** ~3,200 lines new + ~1,800 ported = ~5,000 lines touched. Comparable to Phase 5b (~3,500 new) and Phase 6 (~2,800 new) at ~1.5–2 weeks pace.

## 11. Architectural pillars set in this phase

- **Quote source decoupled from trade venue.** Bus topic `quote.<source>.<canonical_id>` is the contract; consumers read by topic, not by broker. Phase 8+ trade execution doesn't care which source priced the symbol.
- **`instruments` + `symbol_aliases(source, raw_symbol)` schema.** Single canonical id per security. Asset-class-specific `meta` JSONB column allows extension (option strike, futures contract month) without further migrations through Phase 16.
- **Streamers live in sidecars.** Pattern locked: any new source (Coinbase, OANDA, yfinance in 7b.2; Twelve Data, Finnhub, Polygon, etc. later) ships as a sidecar process implementing the same `StreamQuotes` RPC. Backend never opens a market-data socket directly.
- **Bidirectional gRPC streaming for fan-in.** One persistent stream per source × sidecar instance; subscribe/unsubscribe are messages. Pattern reused in Phase 24's multi-worker uvicorn (workers shard subscription ownership over Redis).
- **WS frames as MessagePack with `v`-versioned schema.** Proven once; Phase 9 charting + Phase 12 options reuse the same frame envelope by adding new `op` values.
- **Conflation is per-WS-connection, focus-aware.** Phase 9 charting's "1m bar update on focused symbol" rides on the same `focus` op; no new mechanism needed.

## 12. Out-of-scope / explicitly punted

| Surface | Phase | Notes |
|---|---|---|
| Coinbase / OANDA / yfinance | 7b.2 | `sidecar_market_data/` ships them as one bundle |
| Bar aggregator + TimescaleDB hypertable | 9 | Roadmap pillar #7 |
| Option L1 streaming (chains, Greeks, IV) | 12 | Polymorphic `contract_details` lands then |
| Future L1 streaming + bars | 14 | Contract-month roll UX |
| Bonds + mutual funds | 16 | EOD NAV / REST snapshot — no streaming protocol |
| L2 depth / order-book book-views | post-7b.1 | Frontend depth viewer is its own feature |
| Schwab `SCREENER_EQUITY` (movers feed) | 18 | Universe scanner |
| Multi-worker uvicorn cross-worker quote routing | 24 | Engine already publishes to Redis; cross-worker reader is infra hardening |
| HK Lv2 depth (Futu paid Lv2 or IBKR HK Lv2) | post-7b.1 separate brainstorm | Operator preference; trivial to wire once paid |
| Twelve Data / Alpaca / Polygon / Binance / Tradier wiring | per-phase as needed | Source enum entries land in 7b.1; streamers added by demand |
| Architect-review LOWs (deferred from 2026-05-04 review) | tracked in plan / TASKS.md | (1) Schwab field-mask constants module (`level_one_fields.py`); (2) `RealQuotesService.pendingFrames` cap to ≤100 drop-oldest (handled in chunk G implementation); (3) line-count estimate may run 30-50% over (acknowledged, no design impact); (4) `hypothesis` fuzz on canonical_id parser (Phase 9 hardening); (5) M22 boundary-strip pillar restatement (style polish; INV-Q-2 already encodes the rule). |

---

**End of design — pending architect review (CRIT + HIGH + MED to be applied inline before final user approval).**
