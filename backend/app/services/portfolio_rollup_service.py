"""Phase 10b.2 §5.1 — PortfolioRollupService.

Per-request orchestrator. Pulls broker_accounts + pnl_intraday + positions,
FX-converts per-account with fault isolation (architect HIGH #4 — partial
200 not whole-rollup 503), returns RollupLive / RollupCurve / RollupDrill.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.portfolio import (
    AssetClassExposure,
    PerAccount,
    RollupLive,
)
from app.services.orders_service import PreviewUnavailable, RedisLike, _fx_rate

log = structlog.get_logger(__name__)

SUPPORTED_BASE = frozenset({"GBP", "USD", "EUR", "HKD", "JPY", "AUD"})
_STALE_THRESHOLD_S = 300.0  # 5 minutes


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
            raise ValueError(f"unsupported base currency: {base_currency}")

        rows = (
            (
                await self._db.execute(
                    text(
                        """
                    SELECT
                      ba.id              AS account_id,
                      ba.broker_id::text AS broker_id,
                      ba.gateway_label   AS alias,
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
            nlv_base = (nlv_native * fx).quantize(Decimal("1e-8"))
            realized_base = (Decimal(r["realized"] or 0) * fx).quantize(Decimal("1e-8"))
            unrealized_base = (Decimal(r["unrealized"] or 0) * fx).quantize(Decimal("1e-8"))

            total_nlv_base += nlv_base
            total_realized += realized_base
            total_unrealized += unrealized_base
            any_account_computed = True

            status: str = "live"
            nlv_age = r["nlv_age_s"]
            if nlv_age is not None and nlv_age > _STALE_THRESHOLD_S:
                stale_accounts.append(account_id)
                status = "stale"

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
                    status=status,  # type: ignore[arg-type]
                )
            )

        # Architect HIGH #4: only 503 when ALL non-initialising accounts failed FX.
        if any_non_initialising and not any_account_computed and fx_stale_accounts:
            raise PreviewUnavailable(503, {"error": "fx_rate_unavailable", "pair": "all"})

        exposure = await self._exposure_by_asset_class(base_currency, total_nlv_base)

        return RollupLive(
            base_currency=base_currency,
            total_nlv_base=total_nlv_base.quantize(Decimal("0.01")),
            total_realized_today_base=total_realized.quantize(Decimal("0.01")),
            total_unrealized_base=total_unrealized.quantize(Decimal("0.01")),
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
                # Per-currency FX failure here downgrades silently — exposure
                # is informational, not a gate decision. Account-level FX
                # failures are still surfaced in fx_stale_accounts above.
                continue
            long_base = (Decimal(r["long_native"] or 0) * fx).quantize(Decimal("1e-8"))
            short_base = (Decimal(r["short_native"] or 0) * fx).quantize(Decimal("1e-8"))
            bucket = per_class.setdefault(
                r["asset_class"], {"long": Decimal(0), "short": Decimal(0)}
            )
            bucket["long"] += long_base
            bucket["short"] += short_base

        exposures: list[AssetClassExposure] = []
        for asset_class, b in sorted(per_class.items()):
            gross = abs(b["long"]) + abs(b["short"])
            pct = (
                (gross / total_nlv_base * 100).quantize(Decimal("0.01"))
                if total_nlv_base != 0
                else Decimal("0")
            )
            exposures.append(
                AssetClassExposure(
                    asset_class=asset_class,
                    long_notional_base=b["long"].quantize(Decimal("0.01")),
                    short_notional_base=b["short"].quantize(Decimal("0.01")),
                    pct_of_nlv=pct,
                )
            )
        return exposures
