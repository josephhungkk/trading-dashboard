"""Telegram trade execution state machine — parse, resolve, preview, confirm, cancel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.brokers import BrokerSidecarTimeout, BrokerSidecarUnavailable

log = structlog.get_logger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Z0-9.]{1,16}$")
_DECIMAL_10_RE = re.compile(r"^\d+(\.\d{1,10})?$")
_DECIMAL_8_RE = re.compile(r"^\d+(\.\d{1,8})?$")

_PENDING_TTL = 120
_ACCT_SELECT_TTL = 120
_NONCE_TTL = 30
_MAX_ACCOUNTS = 20

_PREFERRED_EXCHANGES = {"SMART", "NASDAQ", "NYSE", "ARCA", "SEHK"}


@dataclass(frozen=True, slots=True)
class ParsedOrder:
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: str
    order_type: Literal["MARKET", "LIMIT", "STOP_LIMIT"]
    tif: Literal["DAY", "GTC"]
    limit_price: str | None
    stop_price: str | None


def parse_place_order(text: str) -> ParsedOrder | None:
    """Parse /place_order command text into ParsedOrder or None on failure."""
    parts = text.split()
    if len(parts) < 4:
        return None

    symbol = parts[1].upper()
    if not _SYMBOL_RE.match(symbol):
        return None

    side_raw = parts[2].upper()
    if side_raw not in ("BUY", "SELL"):
        return None
    side: Literal["BUY", "SELL"] = side_raw  # type: ignore[assignment]

    qty = parts[3]
    if not _DECIMAL_10_RE.match(qty):
        return None

    limit_price: str | None = None
    stop_price: str | None = None
    tif: Literal["DAY", "GTC"] = "DAY"

    i = 4
    while i < len(parts):
        flag = parts[i]
        if flag in ("--limit", "--stop", "--tif"):
            if i + 1 >= len(parts):
                return None
            val = parts[i + 1]
            if flag == "--limit":
                if not _DECIMAL_8_RE.match(val):
                    return None
                limit_price = val
            elif flag == "--stop":
                if not _DECIMAL_8_RE.match(val):
                    return None
                stop_price = val
            elif flag == "--tif":
                if val not in ("DAY", "GTC"):
                    return None
                tif = val  # type: ignore[assignment]
            i += 2
        else:
            return None

    if stop_price is not None and limit_price is None:
        return None

    if stop_price is not None:
        order_type: Literal["MARKET", "LIMIT", "STOP_LIMIT"] = "STOP_LIMIT"
    elif limit_price is not None:
        order_type = "LIMIT"
    else:
        order_type = "MARKET"

    return ParsedOrder(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        tif=tif,
        limit_price=limit_price,
        stop_price=stop_price,
    )


def _pending_key(chat_id: int, from_user_id: int) -> str:
    return f"telegram:order:pending:{chat_id}:{from_user_id}"


def _acct_select_key(chat_id: int, from_user_id: int) -> str:
    return f"telegram:order:acct_select:{chat_id}:{from_user_id}"


async def resolve_instrument(
    symbol: str,
    *,
    db: AsyncSession,
    registry: Any,
    broker_label: str,
) -> str | None:
    """Return conid for symbol, or None if not found/ambiguous/unavailable."""
    row = (
        await db.execute(
            text(
                "SELECT i.conid FROM instruments i "
                "JOIN brokers b ON i.broker_id = b.id "
                "WHERE i.ticker = :symbol AND b.label = :broker_label "
                "LIMIT 1"
            ),
            {"symbol": symbol, "broker_label": broker_label},
        )
    ).fetchone()
    if row is not None:
        return str(row.conid)

    try:
        client = await registry.get_client(broker_label)
    except KeyError:
        log.warning("telegram.resolve_instrument_broker_not_configured", broker_label=broker_label)
        return None

    try:
        contracts = await client.search_contracts(symbol, asset_class="STOCK")
    except BrokerSidecarUnavailable:
        log.warning("telegram.resolve_instrument_broker_unavailable", symbol=symbol)
        return None
    except BrokerSidecarTimeout:
        log.warning("telegram.resolve_instrument_broker_unavailable", symbol=symbol)
        return None

    equity = [c for c in contracts if c.asset_class == "STOCK"]
    if len(equity) == 0:
        return None

    # Ambiguity check: multiple exchanges in equity pool means different economic
    # instruments (e.g. VOD/LSE in GBP vs VOD/NASDAQ in USD). Always reject.
    equity_exchanges = {c.exchange for c in equity}
    if len(equity_exchanges) > 1:
        log.info(
            "telegram.resolve_instrument_ambiguous",
            symbol=symbol,
            exchanges=list(equity_exchanges),
        )
        return None

    # Single exchange: use preferred filter to pick canonical listing, else take all.
    preferred = [c for c in equity if c.exchange in _PREFERRED_EXCHANGES]
    candidates = preferred if preferred else equity

    if len(candidates) == 0:
        return None

    conid = candidates[0].conid

    try:
        await db.execute(
            text(
                "INSERT INTO instruments (ticker, conid, broker_id) "
                "SELECT :symbol, :conid, b.id FROM brokers b WHERE b.label = :broker_label "
                "ON CONFLICT DO NOTHING"
            ),
            {"symbol": symbol, "conid": conid, "broker_label": broker_label},
        )
        await db.commit()
    except Exception:
        log.warning("telegram.resolve_instrument_insert_failed", symbol=symbol)
        await db.rollback()

    return conid
