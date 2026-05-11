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

### 2.1 Fixed-fractional (% of NLV)

```
qty = floor((NLV * risk_pct / 100) / price_in_base)
```

Inputs: `risk_pct` (0 < x < 100), `price` (last trade or limit).
Use case: "spend 2% of account on this trade." Simplest, safest default.
Edge cases: NLV=0 → `qty=0`; price<=0 → 422.

### 2.2 Fixed-risk-per-trade (% of NLV at stop distance)

```
risk_per_share = abs(entry - stop) * fx_to_base
qty = floor((NLV * risk_pct / 100) / risk_per_share)
```

Inputs: `risk_pct`, `entry`, `stop`. Side determines whether `stop < entry`
(BUY) or `stop > entry` (SELL).
Use case: "lose at most 1% of NLV if stop hits." The "real" risk-based
sizing — worst-case loss is *exactly* `risk_pct * NLV`.
Edge cases: `entry == stop` → 422 (zero-distance); stop on wrong side of
entry → 422.

### 2.3 Vol-targeted (target annualized portfolio vol)

```
asset_vol = ATR14_daily / price * sqrt(252)   # daily ATR → annualized
qty = floor((target_vol_pct / 100 * NLV) / (asset_vol * price_in_base))
```

Inputs: `target_vol_pct` (e.g., 15), `price`, plus either ATR14 fetched
server-side OR manual `vol_override_pct`. Vol override always wins when present.
Use case: "this position contributes X% annualized vol to the portfolio."
The professional approach; ties size to the asset's actual price variability.
Edge cases: ATR unavailable (newly-listed, no subscription) AND no override
→ 422 with `error: atr_unavailable, hint: "enter manual vol or pick a
different method"`. User then either supplies override or switches to
fixed-fractional.

## 3. Backend Architecture

### 3.1 Service: `app/services/position_sizing_service.py` (new)

```python
class PositionSizingService:
    def __init__(
        self,
        db: AsyncSession,
        redis: RedisLike,
        risk_service: RiskService,
        atr_service: ATRService,
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
3. FX-convert price/entry/stop from asset currency → account.currency_base.
4. Dispatch on method to one of three pure-math functions (testable in isolation).
5. Call `RiskService.evaluate(ctx_with_suggested_qty, mode="preview")` to
   get the gate verdict on the *suggested* order. ALLOW/WARN/BLOCK preserved.
6. Return `SizingResult { suggested_qty, base_currency_notional, method_breakdown,
   risk_verdict: GateVerdict }`.

The service is constructed per-request (no singleton; matches the Phase 10a
RiskService pattern). Lifespan does NOT create a singleton.

### 3.2 ATR service: `app/services/atr_service.py` (new)

```python
class ATRService:
    async def compute_atr14(
        self,
        instrument_id: UUID,
        asof_date: date,
    ) -> Decimal | None: ...
```

- Reads from existing `bars_1d` table (Phase 9 charting).
- Standard ATR(14) formula: SMA of true range over 14 daily bars ending at `asof_date`.
- Caches results in Redis: `atr14:{instrument_id}:{asof_date}` → TTL 6 hours
  (daily refresh; bars_1d is the canonical source).
- Returns `None` if fewer than 14 daily bars exist for the instrument
  (newly listed, never subscribed).
- Vendor-data fan-out NOT triggered here — only consumes existing local data.
  If you want ATR for an unsubscribed instrument, subscribe it first via the
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

| Method | Path | Auth | CSRF | Purpose |
|---|---|---|---|---|
| `POST` | `/api/risk/position-size` | JWT | no | Compute one sizing. Returns `SizingResult`. |
| `GET` | `/api/risk/sizing-defaults/{account_id}` | JWT | no | Read per-account defaults from `app_config`. |
| `PUT` | `/api/admin/sizing-defaults/{account_id}` | JWT (admin) | yes | Update per-account defaults. |

Defaults are stored in `app_config` namespace `risk_sizing` under keys
`{account_id}.method`, `{account_id}.fixed_fractional.risk_pct`,
`{account_id}.risk_per_trade.risk_pct`, `{account_id}.vol_targeted.target_vol_pct`.
This mirrors the per-gateway daily-cap layout from Phase 5c. Caches via
existing `ConfigService` pubsub invalidation.

### 3.5 Risk-gate integration

Sizer calls `RiskService.evaluate(ctx, mode="preview")` with a synthetic
`OrderContext` constructed from the *suggested* qty. If gate returns BLOCK,
the FE shows the standard BLOCK banner alongside the suggested qty (e.g.,
"Suggested 100 shares — would BLOCK: BP buffer breach"). User can either
reduce risk_pct manually or switch to a smaller-output method. **No
sizing-specific "auto-shrink to fit" magic** — the user must consciously
adjust. Auto-shrink hides constraints and ships footguns.

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
- BE unit (`backend/tests/services/test_atr_service.py`): ATR14 math against
  fixture bars_1d data; missing-data → `None`; cache hit/miss paths.
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
| **A — BE backbone** | `atr_service`, `position_sizing_service` (3 method functions), schemas, unit tests | ~6 | atr_service depends on bars_1d (Phase 9) — verify table is populated for test fixtures |
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
`OrderContext` with **all** the fields the real gate expects:

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
    rth=True,                    # default; if FE knows RTH state, pass it
    request_kind="position_size", # NEW — for audit trail
)
```

The `request_kind="position_size"` value lets `risk_decisions` audit rows
distinguish sizer-driven evaluations from real preview/place_order events.
Add this to the `request_kind` CHECK constraint via Alembic migration
`0038_add_position_size_to_request_kind.py`. The sizer-driven audit rows are
useful for "show me how often the user's suggested sizes got blocked" telemetry
and can be filtered out of the operator's risk-decisions feed by default.

Failure mode: if the gate raises (sidecar timeout, DB error), the sizer returns
HTTP 503 — DON'T silently fall back to "ALLOW with no verdict" because that
hides risk constraints from the operator. Better to show "Risk gate unreachable
— refresh to retry" than a false-confident green check.

## 7. Open Issues / Pre-architect-review Concerns

1. **Synthetic OrderContext fields:** does the sizer have enough information
   to construct a valid OrderContext? `rth` and `order_type` are guesses. The
   architect review should validate whether these affect gate outcomes (e.g.,
   PDT counter behavior under "market" vs "limit"). If yes, FE must pass them
   explicitly.

2. **Race between sizer gate eval and place_order:** the gate result on
   sizer call ≠ the gate result on place_order if state changes between
   them (filled_today increments, kill switch flips). User sees ALLOW on
   sizing but BLOCK on place. **Acceptable**: the user is responsible for
   the time window; we don't claim sizer suggestions are guaranteed valid.
   FE messaging should make this clear: "Risk gate at suggestion time"
   not just "Risk gate".

3. **Configuring per-method defaults is a lot of admin keys** (3 methods × ~2 fields × N accounts). Consider whether the `app_config` namespace
   structure scales, or whether sizing-defaults should be its own table
   (`account_sizing_defaults`). For Phase 10b.1, stick with `app_config`
   for consistency with Phase 10a admin keys; revisit only if it becomes
   awkward.

4. **`request_kind` constraint migration is data-affecting** — needs proper
   Alembic up/down. `down` removes "position_size" rows AND restores the
   prior CHECK constraint. Risk-decisions audit rows from sizer evaluations
   would be lost in a downgrade — document this in the migration comment.

5. **Should the standalone page persist its inputs across navigation?**
   Probably yes (TanStack Router search params for account_id / instrument_id
   / entry / stop). Easy to forget. Add to the spec self-review.

## 8. Tests + Coverage Targets

- Backend: ≥80% line coverage on `position_sizing_service.py` and `atr_service.py`.
- Frontend: ≥80% on new sizing hooks; new components covered by Vitest +
  Playwright.
- The 3 method math functions are pure — they should have 100% coverage trivially.

## 9. Versioning

Phase 10b.1 ships as **v0.13.0**. ROADMAP.md's "Phase 10" originally reserved
v0.10.0, but versioning was already lapped (v0.12.0 shipped Phase 10a, v0.12.1
shipped Phase 10a.5). v0.13.0 follows naturally. Phase 10b.2 will be v0.13.1
or v0.14.0 depending on its scope.
