# Streaming Quotes Operator Guide (Phase 7b.1)

End-to-end operations runbook for the streaming-quote engine shipped in
v0.7.1. Scope: backend `app/services/quotes/`, `sidecar_*/streamer.py`,
`/ws/quotes`, the `instruments` / `symbol_aliases` schema, and the
config-driven source router.

## Adding a new source

1. Add proto enum entry in `QuoteSource` (next int).
2. Add per-source streamer in `sidecar_<source>/streamer.py` (or add to
   the shared `sidecar_market_data/` for non-broker, free, public data
   like Coinbase / OANDA / yfinance).
3. Add `symbol_aliases` mapping helper for any symbology the upstream
   uses (e.g. Schwab's `$`-prefix indexes; Futu's `HK.` prefix).
4. Add to `app_config.quote_source_priority` for relevant
   `<asset_class>.<country>` keys.
5. Add Prometheus uptime + reconnect alerts to
   `deploy/prometheus/alerts.yml` under the `phase7b_quotes` group.
6. Update the source-router default table at the bottom of this runbook.

## Debugging a stuck stream

1. `docker compose logs -f schwab-sidecar` (in-cluster) or
   `journalctl -u futu-sidecar` / `journalctl -u ibkr-sidecar-*` on the
   NUC.
2. `redis-cli pubsub channels 'quote.*'` — verify that the engine is
   actually publishing to the bus. Empty channel set with active
   subscribers in `/ws/quotes` means the upstream is down or the
   sidecar isn't forwarding ticks.
3. `curl https://dashboard.kiusinghung.com/metrics | grep
   quote_stream_uptime_seconds` — non-zero means the gRPC stream is
   open. Compare with `quote_sidecar_<source>_reconnect_total` for
   recent flaps.
4. Check `schwab_sidecar_token_drift_seconds` (Phase 7a) — if drifted
   over the alert threshold, force a refresh via
   `BackendCallback.RequestTokenRefresh`. The streamer will pick up
   the new tokens via the `tokens_refreshed: asyncio.Event` (CRIT-2).

## Manually resetting the engine

`POST /api/admin/quote-engine/reset` (admin JWT required) drops all
conflators, replays subscriptions from scratch. Use when you've
changed the source-router config or when a sidecar's upstream-side
refcount has gone out of sync with the engine's `SubscriptionRegistry`.

## Symbol resolution: dual listings

`canonical_id` format is `<asset_class>:<symbol>:<country>`. When two
listings share `(asset_class, symbol, country)` but differ by
`primary_exchange`, the format extends to
`<asset_class>:<symbol>:<country>:<exchange>` for the second-and-
subsequent. First observation wins the bare form. The UNIQUE
constraint on `instruments.canonical_id` prevents the conflict by
construction. (HIGH-5.)

## UK pence guard

`sidecar_ibkr` divides LSE GBp (penny) prices by 100 before emitting
`QuoteMessage`. The decision is canonical-id-derived (country=UK,
asset_class=stock), NOT runtime
`ticker.contract.exchange == "LSE"` — SMART routing reports `"SMART"`
at runtime, defeating the latter. Verify via
`quote_uk_pence_normalizations_total{exchange="LSE"}` — non-zero is
normal. If the metric is zero after >10 min of LSE subscriptions, the
`QuoteUKPenceUnitMismatch` alert fires.

## Token rotation gap (Schwab)

Schwab access-token TTL is 30 min. `sidecar_schwab` proactively
reconnects on token refresh (CRIT-2): the streamer's main `recv`
loop races a frame read against the `tokens_refreshed: asyncio.Event`
and short-circuits to `_reconnect_with_new_creds()` when the event
fires. Gap should be <2s end-to-end; alert
`QuoteTokenRotationGapHigh` fires if p95 >5s.

## Subscription cap reached

Default per-WS cap = 1000, global cap = 5000, sub-frame rate-limit =
100/min/WS. Operator can raise via app_config:
- `quote_engine_subscription_cap_per_ws`
- `quote_engine_subscription_cap_global`
- `quote_engine_sub_rate_limit_per_min`

Alert `QuoteSubscriptionCapHit` fires at 80% of either cap. Partial-
success semantics on cap rejection: the engine accepts up to the cap
and returns `op:"err", code:"CAP"` for the rejected tail in the same
frame. (HIGH-6.)

## Operator-trace mode

`OPERATOR_TRACE_QUOTES=1` (env on backend container) disables the
M22 boundary strip — `raw_payload` and `source_meta` will be present
on `QuoteMessage` reaching the WS gateway. Use only for short
debugging sessions; bandwidth roughly doubles. (INV-Q-2 / MED-2.)

## Source-router default (v0.7.1)

| `<asset_class>.<country>` | Primary | Fallback | Notes |
|---|---|---|---|
| stock.US, etf.US | schwab | ibkr (paid bundles) | Schwab covers free |
| stock.UK | ibkr | yfinance (delayed) | LSE GBP 2/mo |
| stock.HK, etf.HK, warrant.HK, cbbc.HK | futu | — | Lv1 free |
| index.US ($SPX/$VIX/$NDX/$COMPX/$DJI/$RUT) | schwab `LEVELONE_EQUITIES` `$`-symbology | ibkr Cboe Streaming Indexes (paid $3.50/mo) | H1 verifies real-time |
| index.EU (DAX/EuroStoxx) | ibkr STOXX Index Data | — | EUR 3/mo |
| index.HK (HSI/HSCEI/HHI) | futu | — | Free |
| stock.EU, stock.JP, stock.AU, stock.CA | yfinance (Phase 7b.2) | — | Delayed ~15min |
| crypto.* | (Phase 7b.2 coinbase) | — | Free WS |
| forex.* | (Phase 7b.2 oanda) | — | Practice WS |

Override at `app_config.quote_source_priority.<asset_class>.<country>`.
Schema: `[<source_id_1>, <source_id_2>, ...]`. SourceRouter walks
left-to-right, skipping any source whose health window says
`UNHEALTHY` (≥3 errors in last 60s). Per-symbol staleness does NOT
trigger reroute (INV-Q-3).
