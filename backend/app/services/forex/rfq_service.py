"""Phase 15a FOREX RFQ service."""

from __future__ import annotations

import json
import secrets
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import structlog
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.ids import uuid7
from app.services.forex.instrument_resolver import ForexInstrumentResolver
from app.services.risk_service import EvaluationContext, RiskService

log = structlog.get_logger(__name__)


def _split_pair(pair: str) -> tuple[str, str]:
    normalized = pair.replace("/", "").upper()
    if len(normalized) != 6 or not normalized.isalpha():
        raise HTTPException(status_code=422, detail="invalid_pair")
    return normalized[:3], normalized[3:]


def _get_field(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


async def _call_sidecar(sidecar: Any, method: str, **kwargs: Any) -> Any:
    fn = getattr(sidecar, method, None)
    if fn is None:
        snake = "".join([f"_{c.lower()}" if c.isupper() else c for c in method]).lstrip("_")
        fn = getattr(sidecar, snake, None)
    if fn is None:
        raise AttributeError(f"sidecar missing {method}")
    return await fn(**kwargs)


async def _ensure_forex_instrument(
    db: AsyncSession,
    redis: Any,
    sidecar: Any,
    pair: str,
) -> dict[str, Any]:
    base, quote = _split_pair(pair)
    resolver = ForexInstrumentResolver(db, redis)
    resolved = await resolver.resolve(base, quote)
    if resolved is not None:
        return resolved

    await _call_sidecar(
        sidecar,
        "RequestFxQuote",
        pair=f"{base}{quote}",
        notional="1",
        notional_currency="base",
    )
    meta = {
        "base_currency": base,
        "quote_currency": quote,
        "pip_size": "0.0001",
        "contract_size": None,
        "trading_hours": "Sun 17:00 - Fri 17:00 ET",
    }
    result = await db.execute(
        text(
            """
            INSERT INTO instruments (
                canonical_id, asset_class, primary_exchange, currency, display_name, meta
            )
            VALUES (
                :canonical_id, 'FOREX', 'IDEALPRO', :currency, :display_name, CAST(:meta AS jsonb)
            )
            ON CONFLICT (canonical_id) DO UPDATE
               SET meta = EXCLUDED.meta,
                   updated_at = now()
            RETURNING id, canonical_id, meta->>'conid' AS conid, asset_class, meta
            """
        ),
        {
            "canonical_id": f"forex:{base}{quote}",
            "currency": quote,
            "display_name": f"{base}/{quote}",
            "meta": json.dumps(meta),
        },
    )
    await db.commit()
    await resolver.invalidate(base, quote)
    row = result.mappings().one()
    return dict(row)


async def request_quote(
    db: AsyncSession,
    redis: Any,
    sidecar: Any,
    account_id: str | UUID,
    pair: str,
    notional: str | Decimal,
    notional_currency: str,
) -> dict[str, Any]:
    base, quote = _split_pair(pair)
    instrument = await _ensure_forex_instrument(db, redis, sidecar, f"{base}{quote}")
    resp = await _call_sidecar(
        sidecar,
        "RequestFxQuote",
        account_id=str(account_id),
        pair=f"{base}{quote}",
        notional=str(notional),
        notional_currency=notional_currency,
    )
    broker_quote_id = str(_get_field(resp, "broker_quote_id", "brokerQuoteId"))
    ttl_seconds = int(_get_field(resp, "ttl_seconds", "ttlSeconds", default=10))
    result = await db.execute(
        text(
            """
            INSERT INTO forex_rfq_quotes (
                account_id, instrument_id, bid, ask, ttl_seconds, broker_quote_id,
                notional, notional_currency, status, expires_at
            )
            VALUES (
                :account_id, :instrument_id, :bid, :ask, :ttl_seconds, :broker_quote_id,
                :notional, :notional_currency, 'pending',
                now() + (:ttl_seconds * interval '1 second')
            )
            ON CONFLICT (broker_quote_id) WHERE broker_quote_id IS NOT NULL DO NOTHING
            RETURNING *
            """
        ),
        {
            "account_id": str(account_id),
            "instrument_id": instrument["id"],
            "bid": str(_get_field(resp, "bid")),
            "ask": str(_get_field(resp, "ask")),
            "ttl_seconds": ttl_seconds,
            "broker_quote_id": broker_quote_id,
            "notional": str(notional),
            "notional_currency": notional_currency,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="duplicate_broker_quote_id")
    await db.commit()
    await redis.set(f"forex:rfq:nonce:{broker_quote_id}", secrets.token_hex(16), ex=ttl_seconds)
    metric = getattr(metrics, "forex_rfq_requests_total", None)
    if metric is not None:
        metric.labels(pair=f"{base}{quote}").inc()
    return dict(row)


async def accept_quote(
    db: AsyncSession,
    redis: Any,
    sidecar: Any,
    risk_svc: RiskService,
    account_id: str | UUID,
    broker_quote_id: str,
    side: str,
    qty: str | Decimal,
) -> dict[str, Any]:
    del redis
    qty_dec = Decimal(str(qty))
    account_uuid = UUID(str(account_id))
    result = await db.execute(
        text(
            """
            SELECT q.*, i.canonical_id, i.meta->>'conid' AS conid
              FROM forex_rfq_quotes q
              JOIN instruments i ON i.id = q.instrument_id
             WHERE q.account_id = :aid
               AND q.broker_quote_id = :bqid
               AND q.status = 'pending'
               AND q.expires_at > now()
             FOR UPDATE
            """
        ),
        {"aid": str(account_id), "bqid": broker_quote_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=409, detail="quote_expired_or_not_found")

    fill_side = side.upper()
    price = Decimal(row["ask"] if fill_side == "BUY" else row["bid"])
    ctx = EvaluationContext(
        account_id=account_uuid,
        broker_id="ibkr",
        instrument_id=row["instrument_id"],
        side=fill_side.lower(),  # type: ignore[arg-type]
        qty=qty_dec,
        price=price,
        order_type="MARKET",
        time_in_force="IOC",
        request_id=str(row["request_id"]),
        currency_base="USD",
        symbol=row["canonical_id"],
        asset_class="FOREX",
        notional=qty_dec * price,
    )
    verdict = await risk_svc.evaluate(ctx, "place_order")
    if verdict.final_verdict == "block":
        reason = verdict.blockers[0].message if verdict.blockers else "risk_gate_blocked"
        await db.execute(
            text(
                "UPDATE forex_rfq_quotes SET status = 'rejected', reject_reason = :reason "
                "WHERE id = :id"
            ),
            {"id": row["id"], "reason": reason},
        )
        await db.commit()
        raise HTTPException(status_code=422, detail="risk_gate_blocked")

    await db.execute(
        text("UPDATE forex_rfq_quotes SET status = 'accepting' WHERE id = :id"),
        {"id": row["id"]},
    )
    await db.commit()

    try:
        resp = await _call_sidecar(
            sidecar,
            "AcceptFxQuote",
            account_id=str(account_id),
            broker_quote_id=broker_quote_id,
            side=fill_side,
            qty=str(qty_dec),
        )
    except Exception as exc:
        await db.execute(
            text(
                "UPDATE forex_rfq_quotes SET status = 'rejected', reject_reason = :reason "
                "WHERE id = :id"
            ),
            {"id": row["id"], "reason": str(exc)},
        )
        await db.commit()
        log.warning("forex.rfq.accept_rpc_failed", broker_quote_id=broker_quote_id, exc=str(exc))
        raise HTTPException(status_code=502, detail="broker_rpc_failed") from exc

    fill_price = Decimal(str(_get_field(resp, "fill_price", "fillPrice", default=price)))
    order_id = uuid7()
    client_order_source = f"rfq-{broker_quote_id}"
    client_order_id = uuid5(NAMESPACE_URL, client_order_source)
    await db.execute(
        text(
            """
            INSERT INTO orders (
                id, account_id, client_order_id, conid, symbol, side, order_type, tif,
                qty, limit_price, stop_price, notional, status, filled_qty
            )
            VALUES (
                :id, :account_id, :client_order_id, :conid, :symbol, :side, 'MARKET', 'IOC',
                :qty, NULL, NULL, :notional, 'pending_submit', 0
            )
            """
        ),
        {
            "id": order_id,
            "account_id": str(account_id),
            "client_order_id": client_order_id,
            "conid": row["conid"] or row["canonical_id"],
            "symbol": row["canonical_id"],
            "side": fill_side,
            "qty": str(qty_dec),
            "notional": str(qty_dec * fill_price),
        },
    )
    await db.execute(
        text(
            "UPDATE forex_rfq_quotes SET status = 'accepted', order_id = :order_id WHERE id = :id"
        ),
        {"id": row["id"], "order_id": order_id},
    )
    await db.commit()
    metric = getattr(metrics, "forex_rfq_accepts_total", None)
    if metric is not None:
        pair = str(row["canonical_id"]).split(":")[-1]
        metric.labels(pair=pair, outcome="success").inc()
    return {"order_id": str(order_id), "fill_price": str(fill_price), "status": "accepted"}


async def cancel_quote(
    db: AsyncSession,
    sidecar: Any,
    account_id: str | UUID,
    broker_quote_id: str,
) -> None:
    result = await db.execute(
        text(
            """
            SELECT id FROM forex_rfq_quotes
             WHERE account_id = :aid
               AND broker_quote_id = :bqid
               AND status IN ('pending', 'accepting')
             FOR UPDATE
            """
        ),
        {"aid": str(account_id), "bqid": broker_quote_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=409, detail="quote_not_cancellable")
    await db.execute(
        text("UPDATE forex_rfq_quotes SET status = 'rejected' WHERE id = :id"),
        {"id": row["id"]},
    )
    await db.commit()
    try:
        await _call_sidecar(
            sidecar,
            "CancelFxQuote",
            account_id=str(account_id),
            broker_quote_id=broker_quote_id,
        )
    except Exception:
        log.info("forex.rfq.cancel_best_effort_failed", broker_quote_id=broker_quote_id)
