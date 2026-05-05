# Schwab `$`-Symbology Quote Coverage (Phase 7b.1 H1)

**Status:** PENDING (operator runs after first deploy of v0.7.1).

This runbook is the day-1 verification procedure that determines whether
the source router's default mapping for `index.US` (Schwab
`LEVELONE_EQUITIES` with `$`-prefix symbology) actually delivers
real-time data, or whether IBKR Cboe Streaming Indexes ($3.50/mo) needs
to be subscribed as a fallback.

## Why this matters

The v0.7.1 source-router default sends `$SPX`, `$VIX`, `$NDX`, `$COMPX`,
`$DJI`, `$RUT` to Schwab as a free real-time index feed. If Schwab
serves these as **delayed** for the operator's account profile, the
router will publish stale prices without flagging a routing error
(INV-Q-3 — staleness ≠ reroute), so the operator must verify
day-1 and either accept the verdict or subscribe IBKR as fallback.

## Procedure (Claude verification subagent)

Dispatch an `Explore` or `general-purpose` subagent with this prompt:

> Probe the deployed `sidecar_schwab` (in dev) to determine real-time vs
> delayed status of US cash indexes via `LEVELONE_EQUITIES`
> `$`-symbology. For each of `$SPX`, `$VIX`, `$NDX`, `$COMPX`, `$DJI`,
> `$RUT`:
>
> 1. Subscribe via gRPC `StreamQuotes` (use the backend's
>    `BrokerSidecarClient` with credentials from
>    `app_secrets.broker.schwab.*`).
> 2. Wait 30 seconds.
> 3. On first received `QuoteMessage`: record `is_delayed`,
>    `delay_seconds`, and `received_at - tick_time` lag.
>
> Emit Prometheus gauge `schwab_index_delayed_observed = 0` if all 6
> are real-time AND `delay_seconds == 0` AND lag < 2s; else `= 1`.

## Verdicts

Fill in the table below after the subagent runs:

| Symbol   | Real-time? | `delay_seconds` | Lag (s) | Notes |
|---|---|---|---|---|
| `$SPX`   | TBD | TBD | TBD | |
| `$VIX`   | TBD | TBD | TBD | |
| `$NDX`   | TBD | TBD | TBD | |
| `$COMPX` | TBD | TBD | TBD | |
| `$DJI`   | TBD | TBD | TBD | |
| `$RUT`   | TBD | TBD | TBD | |

## Verdict outcomes

- **All real-time**: ✓ Schwab covers US cash indexes free. **Do not
  subscribe** to IBKR Cboe Streaming Indexes. No source-router change.
- **Some delayed**: List affected indexes; recommend subscribing IBKR
  Cboe Streaming Market Indexes ($3.50/mo). Once the IBKR sub is
  active, override `app_config.quote_source_priority.index.US` to
  `["ibkr", "schwab"]` for the affected symbols.
- **All delayed**: Subscribe IBKR Cboe Streaming Market Indexes;
  override the entire `index.US` row.

## After updating the verdict

1. Commit this file with the filled-in verdict + `delay_seconds`
   measurements.
2. If overriding the router: `POST /api/admin/config` with the
   updated `quote_source_priority` JSON; `POST
   /api/admin/quote-engine/reset` to apply.
3. Cross-reference with `runbook-quote-streaming-ops.md` "Source-router
   default" table — keep the two in sync.
