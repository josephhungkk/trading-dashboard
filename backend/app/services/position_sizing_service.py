"""Phase 10b.1 position-sizing orchestrator.

Per-request service. Loads account+instrument, FX-converts asset prices
to account.currency_base via the existing ``_fx_rate`` helper, dispatches
to the appropriate pure-math function in ``position_sizing_math``, calls
``RiskService.evaluate(ctx, mode='preview')`` for the verdict, and
returns a SizingResult.

No side-effects: spec drift 1 (see plan §"Spec Drift Notice").
RiskService.evaluate is read-only against Redis; PDT mint and audit live
in orders_service, which the sizer never invokes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.risk import Side
from app.schemas.sizing import (
    FixedFractionalInputs,
    MethodBreakdown,
    RiskPerTradeInputs,
    SizingInputs,
    SizingMethod,
    SizingResult,
    VolTargetedInputs,
)
from app.services.orders_service import RedisLike, _fx_rate, capability_broker_id
from app.services.position_sizing_math import (
    compute_fixed_fractional,
    compute_risk_per_trade,
    compute_vol_targeted,
)
from app.services.risk_service import EvaluationContext, RiskService
from app.services.volatility_service import VolatilityService

if TYPE_CHECKING:
    from app.services.brokers import BrokerRegistry
    from app.services.config import ConfigService


class PositionSizingService:
    """Per-request orchestrator. Constructs RiskService internally after
    loading the account, since the gate's margin check needs a per-account
    sidecar client that we can only resolve from the broker_registry once
    the account's ``gateway_label`` is known.
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: RedisLike,
        config: ConfigService,
        broker_registry: BrokerRegistry,
        vol_service: VolatilityService,
    ) -> None:
        self._db = db
        self._redis = redis
        self._config = config
        self._registry = broker_registry
        self._vol = vol_service

    async def compute(
        self,
        *,
        account_id: UUID,
        instrument_id: int,
        method: SizingMethod,
        inputs: SizingInputs,
        side: Side,
    ) -> SizingResult:
        account = await self._load_account(account_id)
        instrument = await self._load_instrument(instrument_id)

        asset_currency = str(instrument["currency"]).strip()
        base_currency = str(account["currency_base"])
        fx_rate = await _fx_rate(self._redis, asset_currency, base_currency)
        nlv_base = Decimal(account["last_nlv"])
        gateway_label = str(account["gateway_label"])
        broker_id = capability_broker_id(gateway_label)

        # Build a per-request RiskService once we have the gateway_label so
        # the gate's margin check can talk to the correct sidecar.
        sidecar = await self._registry.get_client(gateway_label)
        risk = RiskService(
            db=self._db,
            redis=cast(Any, self._redis),
            config=cast(Any, self._config),
            sidecar=cast(Any, sidecar),
        )

        qty, notional_base, breakdown = await self._dispatch(
            method=method,
            inputs=inputs,
            side=side,
            instrument_id=instrument_id,
            nlv_base=nlv_base,
            fx_rate=fx_rate,
            account_currency=base_currency,
        )

        ctx = EvaluationContext(
            account_id=account_id,
            broker_id=broker_id,
            instrument_id=instrument_id,
            side=side,
            qty=qty,
            price=Decimal(breakdown.price_base),
            order_type="market",
            time_in_force="day",
            request_id=f"sizer-{uuid4()}",
            currency_base=base_currency,
        )
        verdict = await risk.evaluate(ctx, mode="preview")

        return SizingResult(
            suggested_qty=qty,
            base_currency_notional=notional_base,
            method=method,
            breakdown=breakdown,
            risk_verdict=verdict,
        )

    async def _dispatch(
        self,
        *,
        method: SizingMethod,
        inputs: SizingInputs,
        side: Side,
        instrument_id: int,
        nlv_base: Decimal,
        fx_rate: Decimal,
        account_currency: str,
    ) -> tuple[Decimal, Decimal, MethodBreakdown]:
        if method == SizingMethod.fixed_fractional:
            assert isinstance(inputs, FixedFractionalInputs)
            price_base = (inputs.price * fx_rate).quantize(Decimal("1e-8"))
            qty, notional = compute_fixed_fractional(
                nlv_base=nlv_base, price_base=price_base, risk_pct=inputs.risk_pct
            )
            return (
                qty,
                notional,
                MethodBreakdown(
                    nlv_base=nlv_base,
                    fx_rate=fx_rate,
                    price_base=price_base,
                    account_currency=account_currency,
                ),
            )

        if method == SizingMethod.risk_per_trade:
            assert isinstance(inputs, RiskPerTradeInputs)
            entry_base = (inputs.entry * fx_rate).quantize(Decimal("1e-8"))
            stop_base = (inputs.stop * fx_rate).quantize(Decimal("1e-8"))
            qty, notional, risk_per_share = compute_risk_per_trade(
                nlv_base=nlv_base,
                entry_base=entry_base,
                stop_base=stop_base,
                side=side,
                risk_pct=inputs.risk_pct,
            )
            return (
                qty,
                notional,
                MethodBreakdown(
                    nlv_base=nlv_base,
                    fx_rate=fx_rate,
                    price_base=entry_base,
                    account_currency=account_currency,
                    risk_per_share_base=risk_per_share,
                ),
            )

        if method == SizingMethod.vol_targeted:
            assert isinstance(inputs, VolTargetedInputs)
            price_base = (inputs.price * fx_rate).quantize(Decimal("1e-8"))
            vol_source: str = "n/a"
            atr14: Decimal | None = None
            realized_vol: Decimal | None = None
            if inputs.vol_override_pct is not None:
                asset_vol = inputs.vol_override_pct / Decimal(100)
                vol_source = "override"
            else:
                est = await self._vol.compute(
                    instrument_id=instrument_id,
                    asof_date=date.today(),
                )
                if est is None:
                    raise ValueError("realized_vol_unavailable")
                asset_vol = est.realized_vol14_annualized
                realized_vol = est.realized_vol14_annualized
                atr14 = est.atr14
                vol_source = "realized"
            qty, notional = compute_vol_targeted(
                nlv_base=nlv_base,
                price_base=price_base,
                target_vol_pct=inputs.target_vol_pct,
                asset_vol_annualized=asset_vol,
            )
            return (
                qty,
                notional,
                MethodBreakdown(
                    nlv_base=nlv_base,
                    fx_rate=fx_rate,
                    price_base=price_base,
                    account_currency=account_currency,
                    atr14=atr14,
                    realized_vol14_annualized=realized_vol,
                    vol_source=vol_source,  # type: ignore[arg-type]
                ),
            )

        raise ValueError(f"unknown method: {method}")

    async def _load_account(self, account_id: UUID) -> dict[str, Any]:
        stmt = text(
            """
            SELECT id, gateway_label, mode, currency_base, last_nlv, last_nlv_currency
            FROM broker_accounts WHERE id = :id
            """
        )
        row = (await self._db.execute(stmt, {"id": account_id})).mappings().first()
        if row is None:
            raise ValueError(f"account not found: {account_id}")
        if row.get("last_nlv") is None:
            raise ValueError(
                f"account {account_id} has no last_nlv — sizing requires a populated NLV"
            )
        return dict(row)

    async def _load_instrument(self, instrument_id: int) -> dict[str, Any]:
        stmt = text(
            """
            SELECT id, display_name, currency FROM instruments WHERE id = :id
            """
        )
        row = (await self._db.execute(stmt, {"id": instrument_id})).mappings().first()
        if row is None:
            raise ValueError(f"instrument not found: {instrument_id}")
        return dict(row)
