from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.combos import ComboOrder, OrderLeg
from app.models.orders import Order
from app.schemas.risk import GateVerdict
from app.services.combos.pnl_envelope import compute_envelope
from app.services.combos.strategy_validator import validate
from app.services.combos.types import ComboContext, LegContext, LegSpec

log = structlog.get_logger(__name__)


class _ComboRiskProtocol(Protocol):
    async def evaluate_combo(self, ctx: ComboContext, mode: str) -> GateVerdict: ...


async def preview(
    db: AsyncSession,
    account_id: str,
    payload: dict[str, Any],
    risk_svc: _ComboRiskProtocol,
    redis: Any,
    mode: str = "preview",
) -> dict[str, Any]:
    legs = [LegSpec(**leg) for leg in payload["legs"]]
    spec = validate(
        payload["strategy_type"],
        legs,
        payload["underlying_symbol"],
        payload["underlying_canonical_id"],
        payload["tif"],
        account_id,
    )
    mids = await _fetch_mids(legs)
    envelope = compute_envelope(spec, mids)
    ctx = ComboContext(
        account_id=account_id,
        mode=mode,
        legs=[
            LegContext(
                leg_idx=i,
                instrument_id=leg.instrument_id,
                side=leg.side,
                qty=leg.qty,
                position_effect=leg.position_effect,
            )
            for i, leg in enumerate(legs)
        ],
        envelope=envelope,
    )
    result = await risk_svc.evaluate_combo(ctx, mode=mode)
    if result.blockers:
        return {
            "risk_blockers": [b.model_dump() for b in result.blockers],
            "risk_warnings": [w.model_dump() for w in result.warnings],
        }

    client_combo_id = f"combo-{uuid4()}"
    nonce = str(uuid4())
    payload_hash = _payload_hash(legs, client_combo_id)
    await redis.set(f"combo_nonce:{nonce}", payload_hash, ex=120)

    return {
        "client_combo_id": client_combo_id,
        "strategy_type": payload["strategy_type"],
        "envelope": {
            "net_debit_credit": str(envelope.net_debit_credit),
            "kind": envelope.kind,
            "max_loss": str(envelope.max_loss) if envelope.max_loss is not None else None,
            "max_profit": str(envelope.max_profit) if envelope.max_profit is not None else None,
            "break_even": [str(b) for b in envelope.break_even],
        },
        "risk_warnings": [w.model_dump() for w in result.warnings],
        "risk_blockers": [],
        "csrf_nonce": nonce,
    }


async def confirm(
    db: AsyncSession,
    nonce: str,
    client_combo_id: str,
    legs_payload: list[dict[str, Any]],
    account_id: str,
    redis: Any,
    broker_client: Any,
    underlying_canonical_id: str,
    strategy_type: str,
    underlying_symbol: str,
    tif: str,
    net_debit_credit: Decimal,
    net_debit_credit_kind: str,
) -> dict[str, Any]:
    stored_hash = await redis.getdel(f"combo_nonce:{nonce}")
    if stored_hash is None:
        raise ValueError("nonce_invalid")
    legs = [LegSpec(**leg) for leg in legs_payload]
    stored_str = stored_hash if isinstance(stored_hash, str) else stored_hash.decode()
    if stored_str != _payload_hash(legs, client_combo_id):
        raise ValueError("payload_drift")

    combo = ComboOrder(
        account_id=UUID(account_id),
        client_combo_id=client_combo_id,
        strategy_type=strategy_type,
        underlying_symbol=underlying_symbol,
        underlying_canonical_id=underlying_canonical_id,
        net_debit_credit=net_debit_credit,
        net_debit_credit_kind=net_debit_credit_kind,
        tif=tif,
        status="pending_submit",
    )
    db.add(combo)
    await db.flush()

    for i, leg in enumerate(legs):
        db.add(
            OrderLeg(
                combo_id=combo.id,
                leg_idx=i,
                instrument_id=leg.instrument_id,
                side=leg.side,
                qty=leg.qty,
                position_effect=leg.position_effect,
                limit_price=leg.limit_price,
            )
        )
    await db.flush()

    if broker_client is None:
        combo.status = "rejected"
        await db.flush()
        raise ValueError("broker_not_configured")

    try:
        response = await broker_client.place_combo(combo, legs)
    except Exception as exc:
        log.exception("combo_place_failed", combo_id=str(combo.id), error=str(exc))
        combo.status = "rejected"
        await db.flush()
        raise

    for i, leg_result in enumerate(response.legs):
        leg = legs[i]
        limit_price = leg.limit_price or Decimal("0")
        broker_order_id = leg_result.broker_order_id or None
        order = Order(
            account_id=UUID(account_id),
            client_order_id=uuid4(),
            broker_order_id=broker_order_id,
            combo_id=combo.id,
            conid=str(leg.instrument_id),
            symbol=leg.symbol,
            side=leg.side.upper(),
            order_type="LIMIT",
            tif=tif if tif in ("DAY", "GTC") else "DAY",
            qty=leg.qty,
            limit_price=limit_price,
            status="submitted",
            notional=abs(leg.qty * limit_price),
            position_effect=leg.position_effect,
        )
        db.add(order)
        await db.flush()
        await db.execute(
            update(OrderLeg)
            .where(OrderLeg.combo_id == combo.id, OrderLeg.leg_idx == i)
            .values(order_id=order.id, broker_order_id=broker_order_id, status="working")
        )

    combo.broker_combo_id = response.broker_combo_id
    combo.status = "working"
    await db.flush()

    return {"combo_id": str(combo.id), "status": combo.status}


def _payload_hash(legs: list[LegSpec], client_combo_id: str) -> str:
    canonical = [
        {
            "leg_idx": i,
            "side": leg.side,
            "symbol": leg.symbol,
            "exchange": leg.exchange,
            "currency": leg.currency,
            "expiry": leg.expiry,
            "strike": str(leg.strike),
            "put_call": leg.put_call,
            "ratio": leg.ratio,
            "qty": str(leg.qty),
            "limit_price": str(leg.limit_price) if leg.limit_price is not None else None,
            "position_effect": leg.position_effect,
        }
        for i, leg in enumerate(legs)
    ]
    return hashlib.sha256(
        json.dumps({"combo_id": client_combo_id, "legs": canonical}, sort_keys=True).encode()
    ).hexdigest()


async def _fetch_mids(legs: list[LegSpec]) -> dict[int, Decimal]:
    return {i: Decimal("5.00") for i in range(len(legs))}
