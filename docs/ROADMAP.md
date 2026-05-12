# Roadmap

**Status:** locked 2026-04-30. Single source of truth for Phase 7 → v1.0.0.

End-state: a self-hosted personal trading dashboard covering every asset class supported by IBKR + Futu + Schwab, every relevant order type, with multi-source streaming quotes, charting, AI-augmented alerts/scanner, autonomous self-refining bots (parameter-tuning + LLM-feedback ceiling, no raw RL), UK CGT tax-lot accounting, and PWA mobile shipping at v1.0.0.

## Scope decisions

| Decision | Locked value | Rationale |
|---|---|---|
| Mobile | **PWA only** | Personal use; iOS 16.4+ has Web Push for installed PWAs; saves App-Store-trading-app approval gauntlet + ~3 phases of RN work |
| Self-refining bot ceiling | **Parameter tuning + LLM-suggested refinements + shadow-mode promotion** | Industry reality — production trading firms don't deploy raw RL on alpha; non-stationary markets + reward-shaping pathologies + bankruptcy risk on a single account |
| Tax accounting | **UK Section 104 pool + same-day + 30-day rules + HMRC SA108 export** | User is UK tax resident; US FIFO/LIFO is not applicable |
| Quote source ≠ trade venue | **Decoupled** | Schwab provides free real-time L1 + L2 + 1m bars + movers + fills feed for US; Futu HK API has no US quotes; routing is per-asset/market in `app_config` |
| Order types | **DB-driven enum + per-broker capability map** | Pydantic `Literal` doesn't scale to 20+ order types × 3 brokers |
| Bot worker | **Separate Docker service** | Bot crash ≠ API crash; own connection pool; enables multi-worker uvicorn later |

## Versioning policy

**Pattern: `0.x.y.z`** (locked 2026-05-12 during Phase 10b.2 close-out)

- `x` = phase version, computed as **ROADMAP §N + 2** for §7 onwards. This offset reflects the historical lap (Phase 8c shipped at v0.10.0, Phase 9 at v0.11.0, etc.) and keeps the "Tag" column below an accurate map from now on.
- `y` = chunk / sub-phase within the umbrella phase (chunks A/B/C/… AND sub-phases like 10a.5, 10b.1, 10b.2 all count as `y` bumps).
- `z` = task / iteration / re-issue under a chunk (e.g. a retro-applied reviewer-fix or operator hot-patch).

**Sub-phases never bump `x`.** When Phase 10 split into 10a / 10a.5 / 10b.1 / 10b.2 they shipped as v0.12.0 / v0.12.1 / v0.12.2 / v0.12.3 — all under x=12. Same will hold for Phase 11's sub-phases (v0.13.x), Phase 14 Futures sub-phases (v0.16.x), and so on.

**1.0.0** ships when ROADMAP §25 (PWA mobile) is complete. The intermediate Tag column below shows the FIRST tag in each phase's `x` window; sub-phases land at `0.x.{1,2,3,…}`.

See `memory/feedback_sub_phase_versioning.md` for the case-by-case decision rule and `CHANGELOG.md` for the per-tag commit log.

## Phases

| # | Tag (first y) | Theme | Headline deliverables |
|---|---|---|---|
| **7a** | 0.7.0 ✅ | **Schwab connect — data + read-only** | `sidecar_schwab/` on VPS as docker-compose service. OAuth + manual re-auth UI for the 7-day refresh-token wall + opt-in Tier-2 Playwright auto-refresher (feature-flagged). `Configure` RPC, `ListAccounts`, account-summary/positions/orders read-only. `account_hash` column on `broker_accounts` (Alembic 0008). Trade execution + StreamQuotes UNIMPLEMENTED. *Does not yet save IBKR data fees — that arrives with 7b.* |
| **7b** | 0.7.1 ✅ | **Streaming quote engine + IBKR/Futu/Schwab/Coinbase/OANDA sources** | Subscription registry (refcount), Redis quote bus `quote.<source>.<canonical_id>`, frontend WebSocket gateway with conflation (4–10/s), `instruments` + `symbol_aliases` schema (Alembic 0009), stale detection. IBKR + Futu + Schwab streamers wired in one phase. Coinbase WS + OANDA practice WS as additional sources (data-only prep for Phase 15). Quote-source-router with config-driven priority. **Saves IBKR data fees from v0.7.1.** |
| **7b.1.5** | 0.7.2 ✅ | **Instruments seed + admin alias endpoint** *(mini-phase)* | Alembic 0010 adds `symbol`/`primary_exchange`/`canonical_id` to `positions` + creates `watchlist_entries` table. Boot-time `seed_instruments_from_positions(session)` resolves canonical_ids from positions ∪ orders ∪ watchlists. Admin endpoint `POST /api/admin/instruments` for operator-driven alias creation when lazy creation surfaces `NO_INSTRUMENT`. Replaces 7b.1 Task A5 (deferred — plan-vs-schema mismatch). |
| **7c** | 0.7.3 ✅ | **Alpaca adapter (data + read-only)** | `sidecar_alpaca/` gRPC sidecar using `alpaca-py` SDK. API-key auth (no OAuth dance). Read-only `Configure`/`ListManagedAccounts`/`GetAccountSummary`/`GetPositions`/`GetOrders` mirror of `sidecar_schwab`. `StreamQuotes` wired to free real-time IEX feed (`stream.data.alpaca.markets/v2/iex`) — registers `alpaca` source in the open-set enum (already designed-for in 7b.1). US equity + crypto in scope; options scaffolded, trade execution deferred to Phase 8. |
| **8** | 0.8.0 ✅ | **Schwab + Alpaca trade + order-type expansion + Futu Modify/Bracket** | Schwab `PlaceOrder`/`CancelOrder`/`ModifyOrder`/`OrderEvent`. STOP_LIMIT, TRAIL/TRAIL_LIMIT, IOC/FOK/GTD, OCO non-bracket, MOC/MOO/LOC/LOO across IBKR + Futu + Schwab. Futu Modify + Bracket (deferred from Phase 6). Alpaca `PlaceOrder` (US equity + crypto). |
| **8c** | 0.10.0 ✅ | **Crypto trade execution** *(historical lap; this was the first off-by-one against `x = §N + 2`)* | IBKR Paxos crypto orders. Slotted in early as v0.10.0. |
| **9** | 0.11.0 ✅ | **Charting v1 + bar aggregator + historical store** *(historical lap continues)* | TimescaleDB hypertable on PG-18, klinecharts integration, 1s/1m/5m/15m/1h/1d bars, drag-handle stop/TP edit on the chart, historical backfill from broker APIs (Schwab CHART_EQUITY → free 1m US bars). |
| **10** | 0.12.0 ✅ | **Risk engine + position-sizing + multi-account rollup** | PDT counter (US accts), buying-power calc, position concentration limits, pre-trade margin check, max daily loss, account-level kill switch. Position-sizing calculator (Kelly, fixed-fractional, vol-targeting). Multi-account portfolio rollup (cross-broker aggregate NLV / exposure / per-asset-class delta). Pre-trade gate becomes mandatory chokepoint. **Shipped across 4 sub-phases:** 10a (v0.12.0 risk gate), 10a.5 (v0.12.1 effectivity), 10b.1 (v0.12.2 sizing — originally tagged v0.13.0, retagged 2026-05-12), 10b.2 (v0.12.3 rollup). |
| **11** | 0.13.0 | **AI router + Alerts + Telegram** | Ollama router (NUC light + heavy-box WoL with 30s warmup cache), `services/ai/` module any subsystem can call. Price/condition alerts engine. Telegram bot (notifications + admin commands). Prompt-cost tracking. |
| 12 | 0.14.0 | Options — single-leg | Option chain viewer, strike/expiry pickers, on-demand strike-window subscribe, Greeks display, exercise/assign events on IBKR + Schwab + Futu-US. Polymorphic contract via JSONB `contract_details`. |
| 13 | 0.15.0 | Multi-leg option combos | Spread / straddle / strangle / collar / butterfly / condor / iron-condor ticket. Net-debit/credit preview. Schwab `complexOrderStrategyType` + IBKR combo legs. |
| 14 | 0.16.0 | Futures | CME on IBKR + Schwab; HKFE (HSI/HHI) on Futu. Contract-month roll UI. Settlement events. Tick-size/multiplier per contract. |
| 15 | 0.17.0 | Forex + Crypto | IBKR IDEALPRO FX. IBKR Paxos crypto. Coinbase WS as free crypto data source (data-only). 24/7 maintenance handling. Decimal qty (not integer). |
| 16 | 0.18.0 | Bonds + Mutual Funds + CFD | CUSIP search, accrued-interest, T+2 settlement. Mutual-fund EOD NAV ordering. CFD on IBKR (ex-US jurisdictions only). |
| 17 | 0.19.0 | IBKR algos | Adaptive, TWAP, VWAP, Arrival, Iceberg / Hidden / Reserve. Algo parameter UI. |
| 18 | 0.20.0 | **Universe scanner + News/filings + Earnings-event handling** | Rule-based scanner (RSI, breakout, volume, mcap, fundamentals) + LLM commentary on candidates. Schwab `SCREENER_EQUITY` feed. SEC EDGAR (US) + RNS (HK) filings ingest. Earnings calendar with auto-flat / auto-pause hooks for bots. |
| 19 | 0.21.0 | Backtesting harness | Replay historical bars through strategy code, PnL/drawdown/Sharpe/MAR report, walk-forward, Monte Carlo. |
| 20 | 0.22.0 | Bot engine v1 — rule-based | Strategy plugin model (Python files), bot lifecycle (create/start/stop/version), per-bot risk caps, paper-mode-by-default. Bot worker is a separate Docker service. |
| 21 | 0.23.0 | Bot engine v2 — LLM-in-loop | LLM-as-analyst on bot decisions, parameter-tuning loop with human approval, shadow-mode strategy promotion, perf-attribution per bot. |
| 22 | 0.24.0 | Bot engine v3 — autonomous, self-refining | Multi-bot orchestration, nightly retrain, LLM-driven strategy generation with guardrails, auto-promotion rules. **No raw RL.** |
| **23** | 0.25.0 | **UK CGT awareness + per-bot attribution + cgt-calc handoff** | Real-time Section 104 pool tracker (mirrors `fills`), same-day + 30-day b&b matcher, pre-trade gate "would trigger b&b" warning, live £3k allowance gauge, "Tax" page (Section 104 positions + per-bot/per-strategy/per-asset PnL), year-end RAW-CSV export consumable by [`KapJI/capital-gains-calculator`](https://github.com/KapJI/capital-gains-calculator), optional admin-page subprocess invocation of `cgt-calc` for in-place PDF. **Contingency:** if cgt-calc proves unfit at Phase 23 start (current bug investigation pending; tracked as a side task), scope expands to include an in-house Section 104 calculation engine. |
| 24 | 0.26.0 | Infra hardening | PG client-cert auth (drops `.env` plaintext password). Multi-worker uvicorn (Redis-backed nonce / replay / commission stores). ClickHouse for tick history if TimescaleDB outgrows the volume. |
| **25** | **1.0.0** | **PWA mobile + v1.0 ship** | Service worker, install-to-home-screen, FCM / Web Push notifications, mobile-only chart UX, offline order queue, biometric lock via WebAuthn. |

## Pacing

At the v0.4 → v0.6 cadence (~1 week per phase, occasional split into a/b/c like Phase 5): **~6.5 months to v1.0.0** (20 phases between Phase 7a and v1.0.0).

Larger phases that may split during their own brainstorm: 8 (broker × order-type expansion), 13 (multi-leg combos), 18 (scanner + filings + earnings is three streams), 21 + 22 (bot engine generations).

## Architectural pillars (lock these — they ripple)

1. **Quote source ≠ trade venue.** Bus topic is `quote.<source>.<canonical_id>`. Source enum is open-set. **Phase 7a.**
2. **`instruments` + `symbol_aliases(source, symbol)` schema.** Single canonical id per security; per-source name resolution. **Phase 7a.**
3. **OrderType + TimeInForce are DB-driven enums + per-broker capability map**, not Python `Literal`. **Phase 8.**
4. **Polymorphic contract via JSONB `contract_details`.** Option strike/expiry, future contract_month, forex pair, etc. **Phase 12.**
5. **Bot worker is a separate Docker service** with its own connection pool. Communicates via Redis pub/sub + Postgres. Bot crash ≠ API crash; enables multi-worker uvicorn later. **Phase 20.**
6. **AI router is `services/ai/`**, decoupled from bots. Anyone (alerts, scanner, bots, trade ticket) can request completion. **Phase 11.**
7. **Bar aggregator + historical store land in Phase 9, not 7.** Schwab CHART_EQUITY gives free 1m US bars; the aggregator handles non-Schwab sources + sub-1m bars.
8. **Risk engine before bots.** Phase 10 ships before Phase 20. Bots cannot bypass the pre-trade gate.
9. **Self-refinement ceiling = parameter tuning + LLM-suggested refinements + shadow-mode promotion** (Phase 21–22). **No raw RL.**
10. **Mobile is PWA-only.** Phase 25 ships as v1.0.0. No React Native phase.

## Quote source routing matrix (default; user-overridable in `app_config`)

| Asset / Market | Primary | Fallback | Free? |
|---|---|---|---|
| US equity / ETF | Schwab | Alpaca IEX (Phase 7c) / IBKR (paid) | ✓ |
| US options L1 + chain | Schwab | Alpaca options (Phase 7c+) / IBKR (paid) | ✓ |
| US futures | Schwab | IBKR | ✓ |
| US bonds | Schwab REST | IBKR | ✓ |
| HK equity / ETF / warrant / CBBC | Futu | IBKR | ✓ |
| A-shares (Stock Connect) | Futu (paid Lv2) | IBKR (paid) | ✗ |
| Global ex-US/HK equity | IBKR | Twelve Data (future-add) | ✗ |
| Forex | IBKR IDEALPRO | OANDA practice (future-add) | partial |
| Crypto | IBKR Paxos | Coinbase WS (Phase 15) / Alpaca crypto (Phase 7c) | ✓ via Coinbase / Alpaca |

Source enum is open-set — Alpaca / Coinbase / OANDA / Polygon / Finnhub / Twelve Data are all designed-for from Phase 7a but only wired when their asset phase lands.

## Out of scope (post-v1.0)

- Raw reinforcement-learning bots
- Native React Native app (PWA covers personal use)
- Paper-trading simulation engine (broker-side paper accounts remain canonical)
- Multi-tenant / customer-facing — this is a single-user dashboard
- Options market-making / HFT-grade latency

## Deferred backlog assignments  *(updated 2026-05-08, post Phase 9.6 close-out)*

Items that surfaced during Phases 7–9 but were either (a) blocked by
operator action / production-traffic windows or (b) better-fit in a
later phase. Each is anchored to its target phase below.

### Phase 10 (Risk engine + position-sizing + multi-account rollup)

- **FE/BE capabilities runtime-shape mismatch** — pre-trade gate reads
  the capability matrix; reconciling the FE hook (expects
  `BrokerCapabilitiesResponse` dict) vs BE `list_capabilities` (returns
  flat list / asset_class-grouped dict) belongs here since the risk
  engine consumes the same shape. Documented in
  `frontend/src/services/capabilities/types.ts`.
- **Two-tick guard before BrokerDiscoverer position wipe** — defensive
  measure flagged by security review. A single buggy sidecar response
  shouldn't open a window where pre-trade position-based caps are wrong.
- **`place_order` / `modify_order` extraction (>50 LOC each)** — risk
  engine wiring touches both functions heavily; natural extraction
  trigger.

### Phase 18 (Universe scanner + News/filings + Earnings)

- **Phase 7b on-demand quote subscribe for preview** — same per-symbol
  fan-out concern as the scanner.
- **Phase 7b periodic BASE-tag refresh** — same family.

### Phase 24 (Infra hardening)

- **`account_balances` table decoupling** — `broker_accounts.last_nlv` /
  `last_nlv_currency` / `last_nlv_at` are read by 5+ services (brokers
  discoverer, orders_service, risk_service, position_sizing_service,
  sizing API). Future additions (cash-by-currency, buying-power
  components, margin used) won't bolt cleanly onto `broker_accounts`.
  Move to a dedicated `account_balances` current-state table alongside
  the Phase 10b.2 `account_balance_snapshots` history hypertable. Deferred
  from Phase 10b.2 to avoid touching 5+ already-shipped services in a
  single phase (10a/10a.5/10b.1 audited them and shipped clean).
- **Phase 9 Task 18 CAGGs** (10 continuous aggregates 5s/10s/15s/30s/45s
  + 5m/15m/30m/1h/1d) — needs production `bars_1s` traffic to validate
  refresh cadence and storage-vs-compute trade-off. Originally Phase 9
  Chunk B-bis; if ops gives the prod-traffic window before Phase 24
  starts, ship as a `feat(phase9-bis):` standalone; otherwise Phase 24
  consumes it alongside the multi-worker uvicorn rework.
- **Phase 9 24-hour storage actuals** — same prerequisite (production
  traffic window).
- **`_last_position_tick_at` multi-replica concern** — single-replica
  today; explicitly in scope for Phase 24's multi-worker uvicorn
  refactor.

### Operator runbook (not phase-owned)

- **`positions.symbol` / `primary_exchange` backfill** — operator runs
  a one-off re-discovery round when convenient.
