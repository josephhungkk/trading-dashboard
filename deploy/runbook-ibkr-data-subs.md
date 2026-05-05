# IBKR Data Subscription Cancel/Keep Matrix (Phase 7b.1 H2)

**Status:** PENDING (operator runs after first deploy of v0.7.1).

This runbook verifies which IBKR data subscriptions are still needed
after Phase 7b.1 wires Schwab + Futu + (Phase 7b.2) yfinance as
preferred quote sources. It probes IBKR API-streamability (NOT TWS-
display) for the operator's profile via the dev `sidecar_ibkr`.

## Why this matters

IBKR's TWS price grid shows many subscriptions as "Fee Waived" but the
underlying API does not stream them. Phase 7b.1 only relies on IBKR
for paid market-data bundles where IBKR's API permission is verified
real and reliable.

## Procedure (Claude verification subagent)

Dispatch an `Explore` or `general-purpose` subagent with this prompt:

> Probe IBKR API-streamability (NOT TWS-display) for the operator's
> profile via dev `sidecar_ibkr`. Issue `reqMktData` for:
>
> 1. **LSE UK L1**:
>    `Contract(symbol="VOD", secType="STK", exchange="LSE",
>    currency="GBP")`
> 2. **LSE International L1**:
>    `Contract(symbol="GAZ", secType="STK", exchange="LSEIOB1",
>    currency="USD")`
> 3. **Cboe Streaming Indexes**:
>    `Contract(symbol="SPX", secType="IND", exchange="CBOE",
>    currency="USD")`
> 4. **STOXX Index Data Real-Time**:
>    `Contract(symbol="DAX", secType="IND", exchange="EUREX",
>    currency="EUR")`
> 5. **(Confirm) HKEX L1**:
>    `Contract(symbol="700", secType="STK", exchange="SEHK",
>    currency="HKD")` → expect IBKR `error 200` (no API permission,
>    even though TWS shows it free).
>
> Wait 30s per probe. Record: success / `error 200` (no permission) /
> `no ticks` / `delayed`.

## Probe results

Fill in after the subagent runs:

| Probe                                | Outcome | Verdict |
|---|---|---|
| 1. LSE UK L1 (VOD)                   | TBD     | TBD |
| 2. LSE Intl L1 (GAZ@LSEIOB1)         | TBD     | TBD |
| 3. Cboe Streaming Indexes (SPX@CBOE) | TBD     | TBD |
| 4. STOXX RT Index Data (DAX@EUREX)   | TBD     | TBD |
| 5. HKEX L1 (700@SEHK)                | TBD     | TBD |

## Cancel / Keep / Subscribe matrix

- **Cancel after 7b.1 ships**:
  - US Securities Snapshot
  - US Streaming Add-On
  - OPRA
  - US Futures Value Bundle PLUS

  Schwab covers all of these free.

- **Keep / subscribe**:
  - LSE UK L1 (GBP 1) — verified API-streamable in probe #1.
  - LSE International L1 (GBP 1) — verified API-streamable in probe #2.
  - STOXX Index Data Real-Time (EUR 3) — verified API-streamable in
    probe #4.

- **Don't subscribe**:
  - HKEX L1 ("Fee Waived" but TWS-only — verified by probe #5
    returning `error 200` for API access). Use Futu Lv1 (free,
    API-exposed).

- **Optional**:
  - IBKR Cboe Streaming Indexes ($3.50/mo) — only if Task H1
    verification (`runbook-quote-coverage.md`) finds Schwab
    `$SPX`/`$VIX`/etc. delayed.

## Annual savings

- ~$192/yr from cancelled US bundles.
- ~$50–300/yr more if the operator previously subscribed to
  additional intl bundles now replaced by yfinance (Phase 7b.2).

## After updating the matrix

1. Cancel the no-longer-needed subscriptions in IBKR Account Management.
2. Subscribe the verified-API subs if not already active.
3. Commit this file with the probe results filled in.
4. Re-run `runbook-quote-coverage.md` if any IBKR subscription change
   affects the index.US fallback.
