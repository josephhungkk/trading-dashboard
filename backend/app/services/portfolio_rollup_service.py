"""Phase 10b.2 §5.1 — PortfolioRollupService.

Per-request orchestrator. Pulls broker_accounts + pnl_intraday + positions,
FX-converts per-account with fault isolation (architect HIGH #4 — partial
200 not whole-rollup 503), returns RollupLive / RollupCurve / RollupDrill.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.portfolio import (
    AssetClassExposure,
    BucketTotal,
    CurvePoint,
    InstrumentExposure,
    PerAccount,
    RollupCurve,
    RollupDrill,
    RollupLive,
)
from app.services.orders_service import PreviewUnavailable, RedisLike, _fx_rate

log = structlog.get_logger(__name__)

SUPPORTED_BASE = frozenset({"GBP", "USD", "EUR", "HKD", "JPY", "AUD"})
_STALE_THRESHOLD_S = 300.0  # 5 minutes

# Review MED: module-level constants for quantize() — avoids re-instantiating
# Decimal literals on every per-account / per-bucket loop.
_QUANTIZE_8DP = Decimal("0.00000001")
_QUANTIZE_2DP = Decimal("0.01")


class PortfolioRollupService:
    """Per-request multi-account rollup compute."""

    def __init__(self, db: AsyncSession, redis: RedisLike | Any) -> None:
        self._db = db
        self._redis = redis

    async def compute_live(self, base_currency: str) -> RollupLive:
        """Cross-broker live snapshot with per-account FX fault isolation.

        Architect HIGH #4: per-account FX failures degrade gracefully —
        the failing account is marked fx_stale and excluded from the
        total; the endpoint returns 200 with `partial=true`. Only when
        EVERY non-initialising account fails FX do we raise 503.
        """
        if base_currency not in SUPPORTED_BASE:
            raise ValueError("unsupported base currency")

        rows = (
            (
                await self._db.execute(
                    text(
                        """
                    -- Review MED: select ba.alias (human-set) not ba.gateway_label
                    -- (internal sidecar label) per CLAUDE.md AccountResponse
                    -- boundary-stripping doctrine. Fall back to gateway_label
                    -- when alias is NULL (legacy rows).
                    SELECT
                      ba.id              AS account_id,
                      ba.broker_id::text AS broker_id,
                      COALESCE(ba.alias, ba.gateway_label) AS alias,
                      ba.currency_base   AS currency_base,
                      ba.last_nlv        AS last_nlv,
                      ba.last_nlv_currency AS last_nlv_currency,
                      ba.last_nlv_at     AS last_nlv_at,
                      v.realized         AS realized,
                      v.unrealized       AS unrealized,
                      EXTRACT(EPOCH FROM (now() - ba.last_nlv_at))::float AS nlv_age_s
                    FROM broker_accounts ba
                    LEFT JOIN v_account_intraday_pnl v ON v.account_id = ba.id
                    WHERE ba.deleted_at IS NULL
                    ORDER BY ba.display_order, ba.gateway_label
                    """
                    )
                )
            )
            .mappings()
            .all()
        )

        history_row = (
            await self._db.execute(text("SELECT MIN(ts)::date AS d FROM account_balance_snapshots"))
        ).first()
        history_since: date | None = history_row[0] if history_row else None

        accounts: list[PerAccount] = []
        fx_rates_used: dict[str, Decimal] = {}
        stale_accounts: list[UUID] = []
        fx_stale_accounts: list[UUID] = []
        total_nlv_base = Decimal("0")
        total_realized = Decimal("0")
        total_unrealized = Decimal("0")
        any_account_computed = False
        any_non_initialising = False

        for r in rows:
            account_id = r["account_id"]
            currency_native = r["currency_base"] or "GBP"

            # Initialising — no NLV yet (e.g. fresh account discovered seconds ago)
            # Review MED: if NLV is present but currency is NULL (legacy / adapter
            # bug), log a warning — silently treating it as "initialising" hides
            # the data inconsistency from ops.
            if r["last_nlv"] is not None and r["last_nlv_currency"] is None:
                log.warning(
                    "portfolio_rollup_account_nlv_without_currency",
                    account_id=str(account_id),
                    broker_id=r["broker_id"],
                )
            if r["last_nlv"] is None or r["last_nlv_currency"] is None:
                accounts.append(
                    PerAccount(
                        account_id=account_id,
                        broker_id=r["broker_id"],
                        alias=r["alias"],
                        currency_native=currency_native,
                        nlv_native=None,
                        nlv_base=None,
                        realized_today_base=None,
                        unrealized_base=None,
                        fx_rate=None,
                        fx_stale=False,
                        nlv_age_s=None,
                        status="initialising",
                    )
                )
                continue

            any_non_initialising = True
            native_ccy = r["last_nlv_currency"]
            try:
                fx = await _fx_rate(self._redis, native_ccy, base_currency)
            except PreviewUnavailable:
                fx_stale_accounts.append(account_id)
                accounts.append(
                    PerAccount(
                        account_id=account_id,
                        broker_id=r["broker_id"],
                        alias=r["alias"],
                        currency_native=native_ccy,
                        nlv_native=Decimal(r["last_nlv"]),
                        nlv_base=None,
                        realized_today_base=None,
                        unrealized_base=None,
                        fx_rate=None,
                        fx_stale=True,
                        nlv_age_s=r["nlv_age_s"],
                        status="fx_stale",
                    )
                )
                continue

            if native_ccy != base_currency:
                fx_rates_used[f"{native_ccy}/{base_currency}"] = fx

            nlv_native = Decimal(r["last_nlv"])
            nlv_base = (nlv_native * fx).quantize(_QUANTIZE_8DP)
            realized_base = (Decimal(r["realized"] or 0) * fx).quantize(_QUANTIZE_8DP)
            unrealized_base = (Decimal(r["unrealized"] or 0) * fx).quantize(_QUANTIZE_8DP)

            total_nlv_base += nlv_base
            total_realized += realized_base
            total_unrealized += unrealized_base
            any_account_computed = True

            # Review HIGH: narrow to the actual Literal values flowing through this
            # branch — "live" or "stale". "initialising" + "fx_stale" are handled in
            # other branches. This removes the # type: ignore mypy was suppressing.
            live_or_stale: Literal["live", "stale"] = "live"
            nlv_age = r["nlv_age_s"]
            if nlv_age is not None and nlv_age > _STALE_THRESHOLD_S:
                stale_accounts.append(account_id)
                live_or_stale = "stale"

            accounts.append(
                PerAccount(
                    account_id=account_id,
                    broker_id=r["broker_id"],
                    alias=r["alias"],
                    currency_native=native_ccy,
                    nlv_native=nlv_native,
                    nlv_base=nlv_base,
                    realized_today_base=realized_base,
                    unrealized_base=unrealized_base,
                    fx_rate=fx,
                    fx_stale=False,
                    nlv_age_s=nlv_age,
                    status=live_or_stale,
                )
            )

        # Architect HIGH #4: only 503 when ALL non-initialising accounts failed FX.
        if any_non_initialising and not any_account_computed and fx_stale_accounts:
            raise PreviewUnavailable(503, {"error": "fx_rate_unavailable", "pair": "all"})

        exposure = await self._exposure_by_asset_class(base_currency, total_nlv_base)

        return RollupLive(
            base_currency=base_currency,
            total_nlv_base=total_nlv_base.quantize(_QUANTIZE_2DP),
            total_realized_today_base=total_realized.quantize(_QUANTIZE_2DP),
            total_unrealized_base=total_unrealized.quantize(_QUANTIZE_2DP),
            history_since=history_since,
            accounts=accounts,
            exposure_by_asset_class=exposure,
            fx_rates=fx_rates_used,
            stale_accounts=stale_accounts,
            fx_stale_accounts=fx_stale_accounts,
            partial=bool(fx_stale_accounts),
        )

    async def _exposure_by_asset_class(
        self, base_currency: str, total_nlv_base: Decimal
    ) -> list[AssetClassExposure]:
        """Architect CRIT #2: positions.market_value_base doesn't exist.

        Compute at cost basis: qty * avg_cost * multiplier, FX-converted.
        Mirrors risk_service.py:284-287 approximation. UI surfaces this
        as "Exposure at cost basis" badge. Phase 10b.3 / Phase 24 may
        add real mark-to-market.
        """
        rows = (
            (
                await self._db.execute(
                    text(
                        """
                    -- Review HIGH: JOIN broker_accounts and filter deleted_at
                    -- IS NULL so stale positions from soft-deleted accounts
                    -- don't inflate exposure. positions table has FK to
                    -- broker_accounts(id) but no auto-filter on deleted_at.
                    SELECT
                      i.asset_class::text AS asset_class,
                      p.currency          AS native_ccy,
                      SUM(CASE WHEN p.qty >= 0
                          THEN p.qty * p.avg_cost * COALESCE(p.multiplier, 1)
                          ELSE 0 END)     AS long_native,
                      SUM(CASE WHEN p.qty <  0
                          THEN p.qty * p.avg_cost * COALESCE(p.multiplier, 1)
                          ELSE 0 END)     AS short_native
                    FROM positions p
                    JOIN instruments i ON i.id = p.instrument_id
                    JOIN broker_accounts ba ON ba.id = p.account_id
                       AND ba.deleted_at IS NULL
                    WHERE p.instrument_id IS NOT NULL
                    GROUP BY i.asset_class, p.currency
                    """
                    )
                )
            )
            .mappings()
            .all()
        )

        per_class: dict[str, dict[str, Decimal]] = {}
        for r in rows:
            try:
                fx = await _fx_rate(self._redis, r["native_ccy"], base_currency)
            except PreviewUnavailable:
                # Per-currency FX failure here downgrades silently from the
                # response shape (exposure is informational, not a gate
                # decision) but is logged so ops can spot a USD-wide outage
                # silently zeroing out US equity exposure (review MED).
                log.info(
                    "portfolio_rollup_exposure_fx_unavailable",
                    native_ccy=r["native_ccy"],
                    base_currency=base_currency,
                    asset_class=r["asset_class"],
                )
                continue
            long_base = (Decimal(r["long_native"] or 0) * fx).quantize(_QUANTIZE_8DP)
            short_base = (Decimal(r["short_native"] or 0) * fx).quantize(_QUANTIZE_8DP)
            bucket = per_class.setdefault(
                r["asset_class"], {"long": Decimal(0), "short": Decimal(0)}
            )
            bucket["long"] += long_base
            bucket["short"] += short_base

        exposures: list[AssetClassExposure] = []
        for asset_class, b in sorted(per_class.items()):
            gross = abs(b["long"]) + abs(b["short"])
            pct = (
                (gross / total_nlv_base * 100).quantize(_QUANTIZE_2DP)
                if total_nlv_base != 0
                else Decimal("0")
            )
            exposures.append(
                AssetClassExposure(
                    asset_class=asset_class,
                    long_notional_base=b["long"].quantize(_QUANTIZE_2DP),
                    short_notional_base=b["short"].quantize(_QUANTIZE_2DP),
                    pct_of_nlv=pct,
                )
            )
        return exposures

    async def compute_curve(
        self,
        base_currency: str,
        window: Literal["intraday", "30d", "1y"],
    ) -> RollupCurve:
        """Time-series curve. intraday = raw last 24h; 30d = 1h CAGG; 1y = 1d CAGG.

        FX applied at read time using CURRENT rates — spec §5.1 caveat "values
        in current GBP". Per-bucket historical FX deferred to Phase 23.

        Per-currency FX failures downgrade silently: rows whose native currency
        has no FX rate are skipped from both per_account points and bucket
        totals. The curve is informational (not gate-load-bearing), so partial
        coverage is preferred over a 503 for the whole window.
        """
        if base_currency not in SUPPORTED_BASE:
            raise ValueError("unsupported base currency")

        if window == "intraday":
            source_sql = """
                -- Raw hypertable, last 24h. nlv_close = nlv, no high/low (single sample).
                SELECT
                  abs_rows.account_id AS account_id,
                  abs_rows.ts         AS bucket,
                  abs_rows.nlv        AS nlv_close,
                  abs_rows.currency   AS currency,
                  NULL::NUMERIC(20,8) AS nlv_high,
                  NULL::NUMERIC(20,8) AS nlv_low
                FROM account_balance_snapshots abs_rows
                JOIN broker_accounts ba ON ba.id = abs_rows.account_id
                   AND ba.deleted_at IS NULL
                WHERE abs_rows.ts > now() - INTERVAL '24 hours'
                ORDER BY abs_rows.account_id, abs_rows.ts
            """
        elif window == "30d":
            source_sql = """
                SELECT
                  cagg.account_id AS account_id,
                  cagg.bucket     AS bucket,
                  cagg.nlv_close  AS nlv_close,
                  cagg.currency   AS currency,
                  cagg.nlv_high   AS nlv_high,
                  cagg.nlv_low    AS nlv_low
                FROM account_balance_snapshots_1h cagg
                JOIN broker_accounts ba ON ba.id = cagg.account_id
                   AND ba.deleted_at IS NULL
                WHERE cagg.bucket > now() - INTERVAL '30 days'
                ORDER BY cagg.account_id, cagg.bucket
            """
        elif window == "1y":
            source_sql = """
                SELECT
                  cagg.account_id AS account_id,
                  cagg.bucket     AS bucket,
                  cagg.nlv_close  AS nlv_close,
                  cagg.currency   AS currency,
                  cagg.nlv_high   AS nlv_high,
                  cagg.nlv_low    AS nlv_low
                FROM account_balance_snapshots_1d cagg
                JOIN broker_accounts ba ON ba.id = cagg.account_id
                   AND ba.deleted_at IS NULL
                WHERE cagg.bucket > now() - INTERVAL '365 days'
                ORDER BY cagg.account_id, cagg.bucket
            """
        else:
            # Review HIGH: don't echo the raw input into the error message —
            # the REST handler maps ValueError → 422 with the message body.
            # Pydantic Literal validation upstream rejects bad inputs first.
            raise ValueError("invalid window")

        rows = (await self._db.execute(text(source_sql))).mappings().all()

        # Per-currency FX cache to avoid N round-trips on Redis for the same pair.
        fx_cache: dict[str, Decimal | None] = {}

        async def _get_fx(native_ccy: str) -> Decimal | None:
            if native_ccy not in fx_cache:
                try:
                    fx_cache[native_ccy] = await _fx_rate(self._redis, native_ccy, base_currency)
                except PreviewUnavailable:
                    fx_cache[native_ccy] = None
                    log.info(
                        "portfolio_rollup_curve_fx_unavailable",
                        native_ccy=native_ccy,
                        base_currency=base_currency,
                        window=window,
                    )
            return fx_cache[native_ccy]

        per_account: list[CurvePoint] = []
        bucket_totals: dict[datetime, Decimal] = {}

        for r in rows:
            fx = await _get_fx(r["currency"])
            if fx is None:
                continue
            close_base = (Decimal(r["nlv_close"]) * fx).quantize(_QUANTIZE_8DP)
            high_base = (
                (Decimal(r["nlv_high"]) * fx).quantize(_QUANTIZE_8DP)
                if r["nlv_high"] is not None
                else None
            )
            low_base = (
                (Decimal(r["nlv_low"]) * fx).quantize(_QUANTIZE_8DP)
                if r["nlv_low"] is not None
                else None
            )
            per_account.append(
                CurvePoint(
                    account_id=r["account_id"],
                    bucket=r["bucket"],
                    nlv_close_base=close_base,
                    nlv_high_base=high_base,
                    nlv_low_base=low_base,
                )
            )
            bucket_totals[r["bucket"]] = bucket_totals.get(r["bucket"], Decimal(0)) + close_base

        totals = [
            BucketTotal(bucket=b, total_nlv_base=v.quantize(_QUANTIZE_2DP))
            for b, v in sorted(bucket_totals.items())
        ]
        return RollupCurve(
            base_currency=base_currency,
            window=window,
            per_account=per_account,
            totals=totals,
        )

    async def _compute_total_nlv_base(self, base_currency: str) -> Decimal:
        """Shared helper: cross-broker SUM of NLV in base_currency.

        Used by drill_asset_class to get a denominator for pct_of_nlv WITHOUT
        re-emitting compute_live's per-account warnings / metrics (review
        HIGH: drill was double-firing compute_live's structlog/metric side
        effects on every page load).

        Per-currency FX failures degrade silently — the drill view is
        informational, not gate-load-bearing. Initialising accounts (null
        NLV) are excluded.
        """
        rows = (
            (
                await self._db.execute(
                    text(
                        """
                        SELECT ba.last_nlv_currency AS native_ccy,
                               SUM(ba.last_nlv) AS sum_native
                        FROM broker_accounts ba
                        WHERE ba.deleted_at IS NULL
                          AND ba.last_nlv IS NOT NULL
                          AND ba.last_nlv_currency IS NOT NULL
                        GROUP BY ba.last_nlv_currency
                        """
                    )
                )
            )
            .mappings()
            .all()
        )
        total = Decimal("0")
        for r in rows:
            try:
                fx = await _fx_rate(self._redis, r["native_ccy"], base_currency)
            except PreviewUnavailable:
                continue
            total += (Decimal(r["sum_native"]) * fx).quantize(_QUANTIZE_8DP)
        return total.quantize(_QUANTIZE_2DP)

    async def drill_asset_class(self, asset_class: str, base_currency: str) -> RollupDrill:
        """Per-instrument exposure for an asset_class with cap utilisation.

        Reads risk_limits with the same precedence walk as
        RiskService._resolve_limit (account → broker → global). Drill is
        read-only / informational — no audit, no gate evaluate (spec §2 #4 +
        §13 deferral). Multi-account dashboard: drill aggregates positions
        across ALL non-deleted accounts (matches the cross-broker
        concentration model in §10a B5).

        Cap resolution: drill is cross-account so we take the **global**
        scope cap as the displayed cap_pct. Account- and broker-scoped caps
        exist in risk_limits but they're contextual to a specific trade —
        the drill view is informational and uses the broadest cap as the
        red-line reference.
        """
        if base_currency not in SUPPORTED_BASE:
            raise ValueError("unsupported base currency")

        # Review HIGH: use the lightweight _compute_total_nlv_base helper
        # instead of compute_live, which would re-emit per-account structlog
        # warnings + (future) metric counters on every drill page load.
        total_nlv_base = await self._compute_total_nlv_base(base_currency)

        # Global concentration cap (broadest scope). Returns None if no row.
        cap_row = (
            await self._db.execute(
                text(
                    """
                    SELECT limit_value, warn_at_pct
                    FROM risk_limits
                    WHERE scope_type = 'global'
                      AND scope_id IS NULL
                      AND limit_kind = 'max_position_concentration_pct'
                      AND is_active = true
                    LIMIT 1
                    """
                )
            )
        ).first()
        cap_pct: Decimal | None = Decimal(cap_row[0]) if cap_row is not None else None
        warn_at_pct: Decimal | None = (
            Decimal(cap_row[1]) if cap_row is not None and cap_row[1] is not None else None
        )

        # Per-instrument exposure within the requested asset class. Group by
        # instrument; cost basis approximation per CRIT #2.
        rows = (
            (
                await self._db.execute(
                    text(
                        """
                    -- Review HIGH: split long/short legs before SUM so a
                    -- perfectly hedged instrument (e.g. +50 in account A,
                    -- -50 in account B) doesn't net to 0 notional and hide
                    -- gross concentration. abs() the pct_of_nlv at the end.
                    SELECT
                      p.instrument_id     AS instrument_id,
                      i.display_name      AS display_name,
                      i.primary_exchange  AS exchange,
                      p.currency          AS native_ccy,
                      SUM(p.qty)          AS total_qty,
                      SUM(CASE WHEN p.qty >= 0
                          THEN p.qty * p.avg_cost * COALESCE(p.multiplier, 1)
                          ELSE 0 END) AS long_native,
                      SUM(CASE WHEN p.qty <  0
                          THEN p.qty * p.avg_cost * COALESCE(p.multiplier, 1)
                          ELSE 0 END) AS short_native
                    FROM positions p
                    JOIN instruments i ON i.id = p.instrument_id
                    JOIN broker_accounts ba ON ba.id = p.account_id
                       AND ba.deleted_at IS NULL
                    WHERE i.asset_class::text = :ac
                      AND p.instrument_id IS NOT NULL
                    GROUP BY p.instrument_id, i.display_name, i.primary_exchange, p.currency
                    """
                    ),
                    {"ac": asset_class},
                )
            )
            .mappings()
            .all()
        )

        instruments: list[InstrumentExposure] = []
        for r in rows:
            try:
                fx = await _fx_rate(self._redis, r["native_ccy"], base_currency)
            except PreviewUnavailable:
                log.info(
                    "portfolio_rollup_drill_fx_unavailable",
                    native_ccy=r["native_ccy"],
                    asset_class=asset_class,
                )
                continue
            # Review HIGH: long + short are summed separately so a hedged
            # instrument doesn't net to zero notional and hide gross
            # concentration risk. notional_base shows the signed net (long
            # minus the absolute short value); pct_of_nlv uses gross exposure.
            long_base = (Decimal(r["long_native"] or 0) * fx).quantize(_QUANTIZE_8DP)
            short_base = (Decimal(r["short_native"] or 0) * fx).quantize(_QUANTIZE_8DP)
            notional_base = long_base + short_base
            gross_base = abs(long_base) + abs(short_base)
            pct_of_nlv = (
                (gross_base / total_nlv_base * 100).quantize(_QUANTIZE_2DP)
                if total_nlv_base != 0
                else Decimal("0")
            )
            utilisation_pct: Decimal | None
            verdict: Literal["ok", "warn", "block"]
            if cap_pct is None:
                utilisation_pct = None
                verdict = "ok"
            else:
                utilisation_pct = (pct_of_nlv / cap_pct * 100).quantize(_QUANTIZE_2DP)
                if pct_of_nlv >= cap_pct:
                    verdict = "block"
                elif warn_at_pct is not None and pct_of_nlv >= (
                    cap_pct * warn_at_pct / Decimal("100")
                ):
                    verdict = "warn"
                else:
                    verdict = "ok"
            instruments.append(
                InstrumentExposure(
                    instrument_id=int(r["instrument_id"]),
                    display_name=r["display_name"] or "",
                    exchange=r["exchange"] or "",
                    total_qty=Decimal(r["total_qty"] or 0),
                    notional_base=notional_base,
                    pct_of_nlv=pct_of_nlv,
                    cap_pct=cap_pct,
                    utilisation_pct=utilisation_pct,
                    verdict=verdict,
                )
            )

        return RollupDrill(
            asset_class=asset_class,
            base_currency=base_currency,
            instruments=instruments,
        )
