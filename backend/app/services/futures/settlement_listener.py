"""Settlement listener — 3 broker tasks + shared _record_settlement helper."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text

log = structlog.get_logger(__name__)

_PUBSUB_CHANNEL = "futures.settlement.{account_id}"


async def _record_settlement(
    *,
    db: Any,
    redis: Any,
    telegram: Any,
    event: dict[str, Any],
) -> None:
    account_id = event["account_id"]
    instrument_id = event["instrument_id"]
    symbol = event.get("symbol", "")
    settlement_price = event["settlement_price"]
    cash_delta = event["cash_delta"]
    settlement_type = event["settlement_type"]
    broker_event_id = event.get("broker_event_id") or None
    settled_at = event["settled_at"]

    try:
        await db.execute(
            text(
                "INSERT INTO futures_settlement_events "
                "(account_id, instrument_id, settlement_price, cash_delta, "
                "settlement_type, broker_event_id, settled_at) "
                "VALUES (:account_id, :instrument_id, :settlement_price, :cash_delta, "
                ":settlement_type, :broker_event_id, CAST(:settled_at AS TIMESTAMPTZ)) "
                "ON CONFLICT (account_id, broker_event_id)"
                " WHERE broker_event_id IS NOT NULL DO NOTHING"
            ),
            {
                "account_id": account_id,
                "instrument_id": instrument_id,
                "settlement_price": settlement_price,
                "cash_delta": cash_delta,
                "settlement_type": settlement_type,
                "broker_event_id": broker_event_id,
                "settled_at": settled_at,
            },
        )
        await db.commit()
        from app.core import metrics

        broker = event.get("broker", "unknown")
        metrics.FUTURES_SETTLEMENT_EVENTS_TOTAL.labels(
            broker=broker, settlement_type=settlement_type
        ).inc()
    except Exception as exc:
        log.error("settlement_db_insert_failed", error=str(exc))
        await db.rollback()
        return

    try:
        import json

        channel = _PUBSUB_CHANNEL.format(account_id=account_id)
        await redis.publish(
            channel, json.dumps({"symbol": symbol, "settlement_type": settlement_type})
        )
    except Exception as exc:
        log.warning("settlement_redis_publish_failed", error=str(exc))

    try:
        import html as _html

        cash_sign = "+" if float(cash_delta or 0) >= 0 else ""
        esc_sym = _html.escape(str(symbol))
        esc_price = _html.escape(str(settlement_price))
        esc_delta = _html.escape(str(cash_delta or ""))
        if settlement_type == "PHYSICAL":
            tg_msg = (
                f"⚠ {esc_sym} physical delivery initiated — contact broker to arrange delivery. "
                f"Settlement price: {esc_price}"
            )
        else:
            tg_msg = (
                f"💰 {esc_sym} settled at {esc_price} · "
                f"Cash delta: {cash_sign}{esc_delta} (CASH settlement)"
            )
        await telegram.send_message(text=tg_msg)
    except Exception as exc:
        log.warning("settlement_telegram_notify_failed", error=str(exc))


async def _ibkr_settlement_listener(*, db_factory: Any, redis: Any, telegram: Any, ib: Any) -> None:
    log.info("ibkr_settlement_listener_started")


async def _futu_settlement_poller(
    *, db_factory: Any, redis: Any, telegram: Any, trade_ctx: Any
) -> None:
    log.info("futu_settlement_poller_fired")


async def _schwab_settlement_poller(
    *, db_factory: Any, redis: Any, telegram: Any, schwab_client: Any
) -> None:
    log.info("schwab_settlement_poller_fired")
