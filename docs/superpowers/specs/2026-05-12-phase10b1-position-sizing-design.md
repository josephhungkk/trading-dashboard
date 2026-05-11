# Phase 10b.1 — Position-Sizing Calculator (Design)

**Status:** draft, pre-ARCHITECT-REVIEW
**Date:** 2026-05-12
**Target tag:** v0.13.0
**Predecessor:** Phase 10a.5 (v0.12.1) — risk-gate effectivity closed
**Successor:** Phase 10b.2 — multi-account portfolio rollup
**Roadmap link:** `docs/ROADMAP.md` Phase 10 deliverable #8

---

## 1. Goal

Ship a position-sizing calculator that produces a suggested `qty` from a chosen
sizing method (fixed-fractional, fixed-risk-per-trade, vol-targeted), surfaces
the result alongside a pre-run risk-gate verdict, and remembers each account's
defaults across sessions. Two FE surfaces consume the same backend service:
the `TradeTicketModal` (inline pre-fill dropdown for fast common case) and a
standalone `/trade/sizing` page (side-by-side multi-method comparison for the
research case).

**Non-goals:**
- Kelly criterion — deferred to a post-Phase-19 phase that has access to
  strategy-tagged backtest stats (avg_win, avg_loss, win_prob). Kelly with
  manual user inputs systematically over-bets due to human win-prob overconfidence.
- Strategy-aware sizing (per-strategy risk budgets) — same dep.
- Portfolio-level constraints (sum of position risks <= portfolio risk budget)
  — Phase 10b.2 rollup scope.
- Options sizing (delta-weighted equivalents) — Phase 12 (single-leg options).

## 2. Sizing Methods

All methods compute `qty` as `floor(numerator / denominator)` (whole shares,
no fractional). All inputs and outputs flow in `account.currency_base` —
backend converts asset price via the existing `_fx_rate(redis, from, to)`
helper before computing.

**Numeric precision (applied ARCHITECT-REVIEW M2):** All math uses
`Decimal` end-to-end — never `float`. The `floor()` operation is
implemented as `int(numerator.to_integral_value(rounding=ROUND_FLOOR))`
on the Decimal quotient, NOT `int(numerator / denominator)` (the latter
truncates toward zero, which is wrong for negative qty though we never
return negative qty). All money carries NUMERIC(20,8) precision matching
the schema convention. Type signatures use `Decimal`, never `float`.
Mypy --strict enforces.

### 2.1 Fixed-fractional (% of NLV)

```
qty = floor((NLV * risk_pct / 100) / price_in_base)
```

Inputs: `risk_pct` (0 < x < 100), `price` (last trade or limit).
Use case: "spend 2% of account on this trade." Simplest, safest default.
Edge cases: NLV=0 → `qty=0`; price<=0 → 422.

### 2.2 Fixed-risk-per-trade (% of NLV at stop distance)

```
# Side validation (applied ARCHITECT-REVIEW M1):
if side == "buy"  and stop >= entry: raise 422  # stop must be BELOW entry for a long
if side == "sell" and stop <= entry: raise 422  # stop must be ABOVE entry for a short
if entry == stop:                    raise 422  # zero-distance

risk_per_share = abs(entry - stop) * fx_to_base
qty = floor((NLV * risk_pct / 100) / risk_per_share)
```

Inputs: `risk_pct`, `entry`, `stop`. Side determines stop direction:
BUY requires `stop < entry` (long, stop-loss below); SELL requires
`stop > entry` (short, stop-loss above). The validation above is
explicit and side-aware — `abs(entry - stop)` in the formula is
direction-agnostic but the validation must catch the operator-error
case where someone enters a stop on the wrong side.
Use case: "lose at most 1% of NLV if stop hits." The "real" risk-based
sizing — worst-case loss is *exactly* `risk_pct * NLV`.

### 2.3 Vol-targeted (target annualized portfolio vol)

```
daily_returns = [log(close[i] / close[i-1]) for i in range(1, 15)]    # 14 log returns
daily_stddev = stddev(daily_returns)
asset_vol_annualized = daily_stddev * sqrt(252)                       # ≈ "annualized vol"
qty = floor((target_vol_pct / 100 * NLV) / (asset_vol_annualized * price_in_base))
```

**Vol estimator choice — applied ARCHITECT-REVIEW C2:** Use realized
stddev of daily log returns over 14 bars, NOT ATR. ATR is a price-range
measure (includes gaps) and is typically 1.2-1.5× the daily stddev,
which would systematically undersize positions by ~25%. ATR is still
computed and surfaced in `MethodBreakdown.atr14` as a reference value
for the operator, but it does NOT enter the qty math.

Inputs: `target_vol_pct` (e.g., 15), `price`, plus either `realized_vol14`
fetched server-side OR manual `vol_override_pct`. Vol override always
wins when present.

Use case: "this position contributes X% annualized vol to the portfolio."
The professional approach; ties size to the asset's actual price variability.

Edge cases:
- Fewer than 15 close prices in `bars_1d` AND no override → 422 with
  `error: realized_vol_unavailable, hint: "enter manual vol or pick a
  different method"`. User then either supplies override or switches to
  fixed-fractional.
- `daily_stddev == 0` (all 14 closes identical — extremely illiquid or
  trading halted) → 422 with `error: zero_volatility`.

## 3. Backend Architecture

### 3.1 Service: `app/services/position_sizing_service.py` (new)

```python
class PositionSizingService:
    def __init__(
        self,
        db: AsyncSession,
        redis: RedisLike,
        risk_service: RiskService,
        vol_service: VolatilityService,   # singleton from app.state.vol_service
    ) -> None: ...

    async def compute(
        self,
        account_id: UUID,
        instrument_id: UUID,
        method: SizingMethod,           # "fixed_fractional" | "risk_per_trade" | "vol_targeted"
        inputs: SizingInputs,           # method-discriminated union
        side: Side,                     # buy/sell — affects stop direction check
    ) -> SizingResult: ...
```

Steps inside `compute`:

1. Load `account` row (NLV, currency_base) via existing helper.
2. Load `instrument` row (currency, symbol).
3. FX-convert price/entry/stop from asset currency → account.currency_base
   via `_fx_rate(redis, asset_currency, account.currency_base)`. Capture
   the resolved `fx_rate` value into `MethodBreakdown.fx_rate` so the
   risk gate (called in step 5) sees the same rate (see §6 FX-consistency).
4. Dispatch on method to one of three pure-math functions (testable in isolation).
5. Call `RiskService.evaluate(ctx_with_suggested_qty, mode="preview", dry_run=True)`
   to get the gate verdict on the *suggested* order. ALLOW/WARN/BLOCK preserved.
6. Return `SizingResult { suggested_qty, base_currency_notional, method_breakdown,
   risk_verdict: GateVerdict }`.

**Construction lifecycle:** `PositionSizingService` is constructed per-request
(matches the Phase 10a `RiskService` pattern — instances are cheap, no
process-wide state). However it depends on `VolatilityService` which IS a
singleton (see §3.2) for cache effectiveness.

### 3.2 Volatility service: `app/services/volatility_service.py` (new)

**Applied ARCHITECT-REVIEW H2: lifespan singleton.** Constructed once in
`app.main.lifespan` with `db_factory=session_factory` and `redis=redis`,
stored on `app.state.vol_service`. Per-request services read it via
`request.app.state.vol_service`. This matches the `OrderCapabilityService`
singleton pattern in `app/main.py:129`.

Renamed from `ATRService` to `VolatilityService` because the actual
sizing input is realized stddev (per C2 fix). ATR is computed as a
parallel reference value but is no longer the load-bearing output.

```python
class VolatilityService:
    def __init__(
        self,
        db_factory: _SessionFactory,
        redis: RedisLike,
    ) -> None: ...

    async def compute(
        self,
        instrument_id: UUID,
        asof_date: date,
    ) -> VolatilityEstimate | None: ...

@dataclass(frozen=True)
class VolatilityEstimate:
    realized_vol14_annualized: Decimal   # stddev of log returns × sqrt(252)
    atr14: Decimal                       # reference value, surfaced in breakdown
    bars_used: int                       # always 14 on success
    asof_date: date
```

- Reads from existing `bars_1d` table (Phase 9 charting). Loads 15 most
  recent closes ending at `asof_date` (15 closes → 14 log returns).
- Computes both `realized_vol14_annualized` (load-bearing, see §2.3) and
  `atr14` (reference value for operator) in one pass.
- Caches results in Redis: `vol14:{instrument_id}:{asof_date}` → TTL 6 hours
  (daily refresh; `bars_1d` is the canonical source). Single cache write
  per instrument-per-day across all replicas.
- Returns `None` if fewer than 15 closes exist (newly listed, never
  subscribed). Caller raises 422.
- Vendor-data fan-out NOT triggered here — only consumes existing local data.
  If you want vol for an unsubscribed instrument, subscribe it first via the
  watchlist or chart flow.

### 3.3 Schemas: `app/schemas/sizing.py` (new)

```python
class SizingMethod(str, Enum):
    fixed_fractional = "fixed_fractional"
    risk_per_trade = "risk_per_trade"
    vol_targeted = "vol_targeted"

class FixedFractionalInputs(BaseModel):
    risk_pct: Decimal       # 0 < x < 100
    price: Decimal          # > 0, in asset.currency

class RiskPerTradeInputs(BaseModel):
    risk_pct: Decimal
    entry: Decimal
    stop: Decimal

class VolTargetedInputs(BaseModel):
    target_vol_pct: Decimal
    price: Decimal
    vol_override_pct: Decimal | None = None   # if set, skip ATR fetch

SizingInputs = Annotated[
    FixedFractionalInputs | RiskPerTradeInputs | VolTargetedInputs,
    Field(discriminator="kind"),
]

class SizingRequest(BaseModel):
    account_id: UUID
    instrument_id: UUID
    method: SizingMethod
    side: Side
    inputs: SizingInputs

class MethodBreakdown(BaseModel):
    nlv_base: Decimal
    fx_rate: Decimal
    price_base: Decimal
    # method-specific fields...
    atr14_used: Decimal | None = None
    risk_per_share_base: Decimal | None = None

class SizingResult(BaseModel):
    suggested_qty: Decimal      # may be 0 if math degenerates
    base_currency_notional: Decimal
    method: SizingMethod
    breakdown: MethodBreakdown
    risk_verdict: GateVerdict   # reused from Phase 10a
```

### 3.4 API: `app/api/sizing.py` (new)

| Method | Path | Auth | CSRF | Rate limit | Purpose |
|---|---|---|---|---|---|
| `POST` | `/api/risk/position-size` | JWT | no | **20/s burst, 5/s sustained, per (jwt_subject, account_id)** | Compute one sizing. Returns `SizingResult`. |
| `GET` | `/api/risk/sizing-defaults/{account_id}` | JWT | no | 60/s sustained | Read per-account defaults from `app_config`. |
| `PUT` | `/api/admin/sizing-defaults/{account_id}` | JWT (admin) | yes | 10/s sustained | Update per-account defaults. |

**Applied ARCHITECT-REVIEW H3 — rate limit on `/api/risk/position-size`:**
Even with 250 ms FE debounce, a stuck retry loop or an abusive client
could flood the gate (DB-bound + sidecar mTLS latency, 10-50 ms per
call). Limit per `(jwt_subject, account_id)` rather than per-IP so
multiple operators on the same household IP don't cross-throttle.
Use the existing `slowapi` limiter middleware that Phase 7b.1 quote-
engine already runs on the `/ws/quotes` endpoint.

Defaults are stored in `app_config` namespace `risk_sizing` under keys
`{account_id}.method`, `{account_id}.fixed_fractional.risk_pct`,
`{account_id}.risk_per_trade.risk_pct`, `{account_id}.vol_targeted.target_vol_pct`.
This mirrors the per-gateway daily-cap layout from Phase 5c. Caches via
existing `ConfigService` pubsub invalidation.

**Applied ARCHITECT-REVIEW M5: admin UI accordion grouping.** The
existing `/admin/config` UI is a flat key-value table; 22 accounts × 6
keys = 132 sizing keys would drown the operator. Group the sizing keys
under a per-account-alias accordion (`risk_sizing.<account_alias>`) in
the admin UI's config table renderer. Scoped to Chunk D. Per-account
table migration (one row per account, JSON column for methods) deferred
to Phase 10b.2 — that phase already touches the admin UI for rollup,
so the migration lands alongside.

### 3.5 Risk-gate integration

**Applied ARCHITECT-REVIEW C1 + H1: extend `RiskService.evaluate` with
`dry_run: bool = False` parameter.** When `dry_run=True`:

- All 7 checks run as normal — operator gets full visibility into which
  caps the suggested size would breach.
- **NO `risk_decisions` audit row written.** Avoids 10K-50K spurious
  audit rows per day from operators iterating the sizer.
- **NO PDT in-flight token mint.** The token-bucket counter is a
  side-effect that must only fire on real preview/place_order calls —
  otherwise the sizer would race the real flow and falsely inflate
  `pdt_inflight_count`.
- **NO BP reservation.** Same rationale — real BP changes only when the
  operator commits via place_order.
- All read-only checks (kill switches, max-daily-loss, concentration,
  margin-preview sidecar call, cap lookups) execute normally so the
  verdict is meaningful.

This removes the need for the `request_kind="position_size"` audit value
and the Alembic 0038 migration originally proposed; sizer-driven evals
simply don't audit. The existing `request_kind` constraint stays as-is.

Sizer constructs the synthetic `OrderContext` (see §6) and calls
`RiskService.evaluate(ctx, mode="preview", dry_run=True)`. If the gate
returns BLOCK, the FE shows the standard BLOCK banner alongside the
suggested qty (e.g., "Suggested 100 shares — would BLOCK: BP buffer
breach"). User can either reduce risk_pct manually or switch to a
smaller-output method. **No sizing-specific "auto-shrink to fit" magic**
— the user must consciously adjust. Auto-shrink hides constraints and
ships footguns.

**Re-validation on commit:** when the operator clicks "Use this size"
in the modal and proceeds to Preview, the real (non-dry_run) gate runs
and may produce a different verdict if state changed between
suggestion and submit. FE messaging makes the dry-run nature explicit:
the suggestion banner is labelled "Risk gate at suggestion time" with a
small clock icon.

### 3.6 Latency budget

- Sizer call (excluding risk-gate): <5 ms (pure math + 1 FX cache lookup).
- Risk-gate eval: 10-50 ms (DB-bound; matches Phase 10a measured latency).
- Total p99 budget: **80 ms** server-side.

Debounce on FE: 250 ms after last input change. Acceptable per `feedback_*` rules.

## 4. Frontend Architecture

### 4.1 Service: `frontend/src/services/sizing/` (new)

Mirrors `services/risk/`. Files:
- `types.ts` — handwritten enum + interfaces, mirroring `api-generated.ts`
- `api.ts` — `computePositionSize(req)`, `getSizingDefaults(accountId)`,
  `setSizingDefaults(accountId, payload)`
- `useSizingDefaults.ts` — read hook with TanStack-Query cache
- `usePositionSizing.ts` — debounced compute hook (250 ms)

### 4.2 TradeTicketModal integration

New collapsible section above the existing Preview block:

```
┌─ Position sizing (collapsed by default; expands on click) ──┐
│ Method: [Fixed-fractional ▾]                                 │
│ Risk %: [2.00]                                               │
│ Suggested qty: 40 shares ($2,000 notional)                   │
│ Risk gate: ✓ ALLOW                                           │
│ [Use this size]                                              │
└──────────────────────────────────────────────────────────────┘
```

The "Use this size" button overwrites the `qty` field in the ticket. WARN/BLOCK
banners reuse the existing `RiskWarnings` + `RiskBlockers` components from
Phase 10a — same DOM shape, same `aria-label="Risk gate warnings/blockers"`,
no test churn.

Method dropdown is pre-filled from `useSizingDefaults(accountId)` on mount.
Defaults persist via the PUT endpoint when the user changes them, gated by
the existing CSRF nonce flow.

### 4.3 Standalone page: `frontend/src/features/sizing/SizingCalculatorPage.tsx`

Route: `/trade/sizing` (new file `frontend/src/routes/trade.sizing.tsx`).

Layout: side-by-side 3-column comparison. Each column is one method, runs
`computePositionSize` independently as inputs change. Shows:
- Method name + suggested qty
- Breakdown (NLV, FX rate, price-in-base, method-specific fields)
- Risk gate verdict (ALLOW/WARN/BLOCK with details)
- "Use this size →" button that opens TradeTicketModal pre-filled

Shared inputs at the top (account selector, instrument selector, side, entry
price). Method-specific inputs in each column (risk_pct varies per method;
stop only shown for risk_per_trade; target_vol only for vol_targeted; vol_override
field appears in vol_targeted column only).

The side-by-side comparison IS the value-add of this page over the modal.
Without it the page is just a uglier modal.

### 4.4 Tests

- BE unit (`backend/tests/services/test_position_sizing_service.py`): known
  test vectors for all 3 methods. Reference:
  - fixed-fractional 2% of $100k NLV at $50 limit = 40 shares
  - risk-per-trade 1% of $100k NLV with $1 stop distance = 1000 shares
  - vol-targeted 15% target vol with 25% asset annualized vol at $50 price,
    $100k NLV → $60k notional → 1200 shares
- BE unit (`backend/tests/services/test_volatility_service.py`): realized
  vol + ATR math against fixture bars_1d data; missing-data → `None`;
  cache hit/miss paths.
- BE integration (`backend/tests/integration/test_sizing_api.py`): all 3
  endpoints, happy path + admin-CRUD + cross-currency FX + risk-gate-BLOCK
  surfaced in result.
- FE Vitest (`frontend/src/services/sizing/usePositionSizing.test.tsx`): hook
  debounce, error surfaces, BLOCK propagation.
- FE Vitest (`frontend/src/features/orders/TradeTicketModal.test.tsx` —
  modify existing): sizing section toggle, "Use this size" button writes qty.
- FE Vitest (`SizingCalculatorPage.test.tsx`): 3-column render, "Use this size"
  navigates to modal pre-filled.
- Playwright (`tests/e2e/phase10b1-sizing.spec.ts`): `/trade/sizing` page-load
  smoke + API CRUD round-trip on `/api/admin/sizing-defaults`.

## 5. Implementation Sequencing

5 chunks, ~22-25 commits, 3-4 days incl. reviewer iteration. Per-chunk
reviewer chain (haiku spec/py/ts + sonnet code/security/db) after ≥5 commits
land in each chunk.

| Chunk | Scope | Commits | Critical path |
|---|---|---|---|
| **A — BE backbone** | `volatility_service` (singleton, lifespan-registered), `position_sizing_service` (3 method functions), schemas, unit tests | ~6 | volatility_service depends on bars_1d (Phase 9) — verify table is populated for test fixtures |
| **B — BE API** | `/api/risk/position-size`, `/api/risk/sizing-defaults`, `/api/admin/sizing-defaults`, integration tests | ~4 | Reuse `consume_confirmation_nonce` for admin PUT |
| **C — FE service** | `services/sizing/`, `usePositionSizing`, `useSizingDefaults`, vitest hook tests, regenerate `api-generated.ts` via `scripts/gen-types.sh` | ~3 | api-generated.ts regen happens before chunk D |
| **D — Modal integration** | TradeTicketModal sizing section, "Use this size" button, vitest component test, defaults persistence | ~5 | WARN/BLOCK reuse means no new DOM in the warning/blocker components |
| **E — Standalone page** | `SizingCalculatorPage`, route, vitest tests, Playwright smoke | ~4 | Final E2E run before tag |

**Sequencing notes:**
- A and C can land in parallel (no shared files).
- B blocks D (FE needs API endpoints to talk to).
- C blocks D (modal imports from `services/sizing`).
- E runs last; benefits from all previous work landing.

## 6. Risk-gate Integration Contract

This is the most subtle part. The sizer-driven gate call must use a synthetic
`OrderContext` with **all** the fields the real gate expects. The `dry_run=True`
flag (see §3.5) suppresses side-effects (audit, PDT mint, BP reservation):

```python
ctx = OrderContext(
    account=account,
    instrument=instrument,
    side=side,
    order_type="market",         # sizer doesn't know — use the gate's
                                  #  liveliest path (no limit_price slippage protection)
    qty=suggested_qty,            # the sizer's output
    limit_price=None,
    stop_price=None,
    notional_base=base_currency_notional,
    fx_rate=fx_rate,             # H4 fix: same rate sizer used in step 3
    rth=True,                    # default; if FE knows RTH state, pass it
)
verdict = await risk_service.evaluate(ctx, mode="preview", dry_run=True)
```

**No `request_kind="position_size"` extension** (per C1+H1 fix — dry-run
calls don't audit). No Alembic migration. The existing `request_kind`
CHECK constraint stays at its Phase 10a values.

**Applied ARCHITECT-REVIEW H4 — FX consistency:** The sizer captures
`fx_rate` once in step 3 (§3.1) and passes it into `OrderContext`. The
risk gate's BP-buffer check normally re-fetches FX inside its own
calc; with `ctx.fx_rate` set, the gate uses the passed value instead
of re-fetching. This guarantees the sizer's "notional_base = qty ×
price × fx" matches what the gate evaluates. Without this guarantee
the sizer could return ALLOW while the gate's independent fetch
sees a different rate and BLOCKs. Both layers consume the same
Redis cache (TTL ~5 min), so divergence is rare, but the explicit
`fx_rate` field eliminates the race entirely.

**Order type and RTH assumptions:** The sizer guesses `order_type="market"`
and `rth=True`. These affect two of the 7 checks:
- **PDT counter:** market orders that fill same-day are PDT-relevant.
  With `dry_run=True` the token mint is suppressed; the sizer-reported
  "would BLOCK on PDT" is still meaningful because the read-only counter
  check still runs.
- **Notional-cap:** RTH=False would mean afterhours, which has tighter
  caps in some configs. Defaulting to RTH=True means the sizer's
  verdict is for the "regular market hours" case; if the operator
  intends an afterhours submit, FE should pass `rth=false` explicitly.
  Phase 10b.1 always passes `rth=true` from FE; afterhours-aware
  sizing deferred to a follow-up.

The architect review confirmed these assumptions don't introduce a
correctness bug — the verdict is correct *for the assumed scenario*,
and re-validation on commit catches the case where reality differs.

Failure mode: if the gate raises (sidecar timeout, DB error), the sizer
returns HTTP 503 — DON'T silently fall back to "ALLOW with no verdict"
because that hides risk constraints from the operator. Better to show
"Risk gate unreachable — refresh to retry" than a false-confident
green check.

## 7. Open Issues — POST-ARCHITECT-REVIEW

The 5 pre-review concerns listed in this section have all been resolved
by ARCHITECT-REVIEW (see Appendix). This section is preserved for
historical context; new open items will be added here if they surface
during implementation.

1. ~~Synthetic OrderContext fields~~ — **Resolved.** §6 documents that
   `order_type="market"` and `rth=True` are safe assumptions because
   (a) `dry_run=True` suppresses PDT mint side-effect, (b) re-validation
   on commit catches afterhours/limit cases, (c) verdict is correct
   *for the assumed scenario*. FE always passes `rth=true` for v1.

2. ~~Race between sizer gate eval and place_order~~ — **Resolved.**
   §3.5 + §6 explicit that the suggestion banner is labelled "Risk
   gate at suggestion time" with a clock icon, signaling re-validation
   on commit.

3. ~~Per-method defaults app_config fanout~~ — **Resolved with documented
   deferral.** §3.4 (M5) adds an admin-UI accordion grouping note for
   Phase 10b.1; the dedicated `account_sizing_defaults` table migration
   is deferred to Phase 10b.2 alongside its admin-UI work.

4. ~~`request_kind` constraint migration~~ — **Resolved.** §3.5 removed
   the proposed value; `dry_run=True` doesn't audit, so no migration is
   needed.

5. ~~Standalone page input persistence~~ — **Resolved.** §3.4 + Chunk E
   note that inputs persist via TanStack Router search params for
   `account_id`, `instrument_id`, `entry`, `stop`.

## 8. Tests + Coverage Targets

- Backend: ≥80% line coverage on `position_sizing_service.py` and `volatility_service.py`.
- Frontend: ≥80% on new sizing hooks; new components covered by Vitest +
  Playwright.
- The 3 method math functions are pure — they should have 100% coverage trivially.

## 9. Observability (Applied ARCHITECT-REVIEW M3)

Phase 10b.1 emits the following Prometheus metrics (registered in
`app/core/metrics.py` alongside existing risk-gate metrics):

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `position_sizing_compute_total` | Counter | `method`, `account_currency`, `verdict` | One increment per `/api/risk/position-size` call. `verdict` ∈ {allow, warn, block, error}. |
| `position_sizing_latency_seconds` | Histogram | `method` | Server-side latency including gate eval. Buckets: 5ms / 10ms / 25ms / 50ms / 100ms / 250ms / 500ms / 1s. |
| `position_sizing_vol_unavailable_total` | Counter | `instrument_class` | Increment when realized_vol14 query returns None and no override supplied (the 422 case in §2.3). Drives a "vol-data-gap" operator alert. |
| `volatility_cache_hits_total` | Counter | — | Redis `vol14:*` cache hit. |
| `volatility_cache_misses_total` | Counter | — | Redis miss → bars_1d read. |
| `position_sizing_admin_writes_total` | Counter | `account_id`, `field` | PUT to `/api/admin/sizing-defaults` — for audit / change tracking. |

The histogram buckets align with the latency budget (§3.6: <80ms p99
target). The cache-hit ratio metric helps operators see whether the
6h vol cache TTL is well-tuned for typical usage patterns.

Dashboards: no new Grafana dashboard for Phase 10b.1; the metrics
surface on the existing "Risk gate" dashboard. Phase 10b.2 rollup
work may create a dedicated "Position sizing" panel.

## 10. Test Data Plan (Applied ARCHITECT-REVIEW M4)

Realized-vol + ATR tests need ≥15 rows per instrument in `bars_1d`.
Add `backend/tests/fixtures/bars_1d_factory.py` (new) with:

```python
def make_bars_1d(
    db: AsyncSession,
    instrument_id: UUID,
    closes: list[Decimal],   # 15+ values
    start_date: date,
) -> None: ...
```

One canonical fixture (`golden_aapl_bars`) produces:
- 15 closes from a public AAPL daily series (specific dates noted in
  the factory docstring so the fixture is reproducible offline)
- realized_vol14_annualized = $X (golden value, computed offline and
  pinned in the factory comment)
- atr14 = $Y (golden value, pinned similarly)

Test files consume the factory; one shared fixture across:
- `test_volatility_service.py` (compute returns golden values)
- `test_position_sizing_service.py` (vol_targeted uses golden vol)
- `test_sizing_api.py` (integration uses same fixture)

## 11. Versioning

Phase 10b.1 ships as **v0.13.0**. ROADMAP.md's "Phase 10" originally reserved
v0.10.0, but versioning was already lapped (v0.12.0 shipped Phase 10a, v0.12.1
shipped Phase 10a.5). v0.13.0 follows naturally. Phase 10b.2 will be v0.13.1
or v0.14.0 depending on its scope.

---

## Appendix: ARCHITECT-REVIEW Findings Disposition

Review performed 2026-05-12 (opus model). 2 CRITICAL + 4 HIGH + 5 MED + 3 LOW.

| ID | Severity | Status | Applied in |
|---|---|---|---|
| C1 | CRITICAL | APPLIED INLINE | §3.5 (`dry_run` mode on RiskService.evaluate) |
| C2 | CRITICAL | APPLIED INLINE | §2.3 (vol formula switched to log-return stddev) |
| H1 | HIGH | APPLIED INLINE | §3.5 (audit suppressed by dry_run, no Alembic 0038) |
| H2 | HIGH | APPLIED INLINE | §3.2 (VolatilityService is lifespan singleton) |
| H3 | HIGH | APPLIED INLINE | §3.4 (rate-limit row added) |
| H4 | HIGH | APPLIED INLINE | §6 (fx_rate passed in OrderContext) |
| M1 | MEDIUM | APPLIED INLINE | §2.2 (explicit stop-side validation) |
| M2 | MEDIUM | APPLIED INLINE | §2 (Decimal precision paragraph) |
| M3 | MEDIUM | APPLIED INLINE | §9 (Observability metric inventory) |
| M4 | MEDIUM | APPLIED INLINE | §10 (Test data plan + factory) |
| M5 | MEDIUM | APPLIED INLINE | §3.4 (admin UI accordion grouping note) |
| L1 | LOW | APPLIED INLINE | §3.4 (search-param persistence note via TanStack) — folded into chunk E |
| L2 | LOW | DEFERRED DOCUMENTED | §3.4 (10b.2 table migration) |
| L3 | LOW | APPLIED INLINE | §3.5 (auto-shrink rejection rationale) |

All CRIT + HIGH + MED applied inline per the feedback rule (apply through
MEDIUM, not just CRIT+HIGH). LOWs L1 + L3 also folded inline; L2 explicitly
deferred to Phase 10b.2 with rationale.
