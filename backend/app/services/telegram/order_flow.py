"""Telegram trade execution state machine — parse, resolve, preview, confirm, cancel."""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import structlog
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.services.brokers import BrokerSidecarTimeout, BrokerSidecarUnavailable
from app.services.orders_service import (
    PreviewUnavailable,
    _is_regular_trading_hours,
    _preview_payload_hash,
    place_order,
    preview_order,
)
from app.services.telegram.allowlist import AllowlistEntry

log = structlog.get_logger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Z0-9.]{1,16}$")
_OCC_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
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
    if _OCC_PATTERN.match(symbol):
        return None  # options orders not supported via Telegram

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

    conid: str = str(candidates[0].conid)

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


async def _run_preview(
    parsed: ParsedOrder,
    *,
    account_id: str,
    conid: str,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> Any:
    """Call preview_order service. Returns PreviewResponse."""
    request_data = {
        "account_id": account_id,
        "conid": conid,
        "side": parsed.side,
        "order_type": parsed.order_type,
        "tif": parsed.tif,
        "qty": parsed.qty,
        "limit_price": parsed.limit_price,
        "stop_price": parsed.stop_price,
    }
    return await preview_order(
        cfg=cfg,
        db=db,
        redis=redis,
        registry=registry,
        capability=capability,
        request_data=request_data,
        user_key=f"telegram:{entry.from_user_id}",
    )


async def _do_preview_and_write_pending(
    parsed: ParsedOrder,
    account: Any,
    *,
    msg: Message,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> None:
    """Run preview; write pending key or reply with error."""
    t0 = time.monotonic()
    conid = await resolve_instrument(
        parsed.symbol,
        db=db,
        registry=registry,
        broker_label=str(account.gateway_label),
    )
    if conid is None:
        metrics.TELEGRAM_ORDER_ATTEMPTS_TOTAL.labels(result="unknown_symbol").inc()
        await msg.answer(
            f"Unknown or ambiguous symbol <b>{html.escape(parsed.symbol)}</b> — "
            "trade it via the web first to register it."
        )
        return

    try:
        preview = await _run_preview(
            parsed,
            account_id=str(account.id),
            conid=conid,
            entry=entry,
            db=db,
            redis=redis,
            registry=registry,
            capability=capability,
            cfg=cfg,
        )
    except PreviewUnavailable as exc:
        metrics.TELEGRAM_ORDER_PREVIEWS_TOTAL.labels(result="unavailable").inc()
        await msg.answer(f"Preview unavailable: {html.escape(str(exc.payload))}")
        return
    except Exception:
        log.exception("telegram.preview_failed")
        metrics.TELEGRAM_ORDER_PREVIEWS_TOTAL.labels(result="unavailable").inc()
        await msg.answer("Preview failed — try again.")
        return
    finally:
        metrics.TELEGRAM_ORDER_E2E_SECONDS.labels(stage="preview").observe(time.monotonic() - t0)

    if preview.position_sanity.requires_extra_attestation:
        metrics.TELEGRAM_ORDER_PREVIEWS_TOTAL.labels(result="position_sanity_rejected").inc()
        await msg.answer(
            "This order would result in an extreme position change — please confirm via the web."
        )
        return

    if preview.risk_blockers:
        metrics.TELEGRAM_ORDER_PREVIEWS_TOTAL.labels(result="blocked").inc()
        lines = ["❌ <b>Order blocked by risk gate:</b>"]
        for b in preview.risk_blockers:
            code = html.escape(str(b.get("code", "")))
            message = html.escape(str(b.get("message", "")))
            lines.append(f"• {code}: {message}")
        lines.append("\nUse the web to adjust limits or order size.")
        await msg.answer("\n".join(lines))
        return

    warning_lines: list[str] = []
    if preview.risk_warnings:
        metrics.TELEGRAM_ORDER_PREVIEWS_TOTAL.labels(result="warned").inc()
        for w in preview.risk_warnings:
            code = html.escape(str(w.get("code", "")))
            message = html.escape(str(w.get("message", "")))
            warning_lines.append(f"⚠️ WARN: {code}: {message}")
    else:
        metrics.TELEGRAM_ORDER_PREVIEWS_TOTAL.labels(result="ok").inc()

    pending_payload = {
        "account_id": str(account.id),
        "account_alias": str(account.alias),
        "account_mode": str(account.mode),
        "account_gateway_label": str(account.gateway_label),
        "conid": conid,
        "symbol": parsed.symbol,
        "side": parsed.side,
        "qty": parsed.qty,
        "order_type": parsed.order_type,
        "tif": parsed.tif,
        "limit_price": parsed.limit_price,
        "stop_price": parsed.stop_price,
    }
    key = _pending_key(msg.chat.id, entry.from_user_id)
    await redis.set(key, json.dumps(pending_payload), ex=_PENDING_TTL)

    side_e = html.escape(parsed.side)
    sym_e = html.escape(parsed.symbol)
    qty_e = html.escape(parsed.qty)
    otype_e = html.escape(parsed.order_type)
    tif_e = html.escape(parsed.tif)
    alias_e = html.escape(str(account.alias))
    mode_e = html.escape(str(account.mode))
    currency_e = html.escape(str(account.currency))
    notional_e = html.escape(str(getattr(preview, "notional", "?")))
    notional_currency_e = html.escape(str(getattr(preview, "notional_currency", "")))

    lines = [
        "📋 <b>Order Preview</b>",
        f"Symbol: {sym_e}",
        f"Side: {side_e}  Qty: {qty_e}  Type: {otype_e}  TIF: {tif_e}",
        f"Account: {alias_e} [{mode_e}] {currency_e}",
        f"Est. notional: ~{notional_currency_e} {notional_e}",
    ]
    if warning_lines:
        lines.extend(["", *warning_lines])

    if account.mode == "live":
        lines.append("\n⚠️ <b>Live account</b> — reply <code>/confirm LIVE</code> to place.")
    else:
        lines.append("\nReply <code>/confirm</code> to place. Valid for 120s.")

    await msg.answer("\n".join(lines))


async def handle_place_order(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> None:
    """Handle /place_order command."""
    parsed = parse_place_order(msg.text or "")
    if parsed is None:
        metrics.TELEGRAM_ORDER_ATTEMPTS_TOTAL.labels(result="invalid_syntax").inc()
        await msg.answer(
            "Usage: <code>/place_order SYMBOL BUY|SELL QTY [--limit PRICE] "
            "[--stop PRICE] [--tif DAY|GTC]</code>"
        )
        return

    rows = (
        await db.execute(
            text(
                "SELECT a.id, a.alias, b.label as broker, a.mode, a.currency_base as currency, "
                "a.gateway_label "
                "FROM broker_accounts a JOIN brokers b ON a.broker_id = b.id "
                "WHERE a.deleted_at IS NULL "
                "ORDER BY a.display_order LIMIT :limit"
            ),
            {"limit": _MAX_ACCOUNTS + 1},
        )
    ).fetchall()

    if len(rows) == 0:
        metrics.TELEGRAM_ORDER_ATTEMPTS_TOTAL.labels(result="no_accounts").inc()
        await msg.answer("No active accounts found.")
        return

    if len(rows) > _MAX_ACCOUNTS:
        metrics.TELEGRAM_ORDER_ATTEMPTS_TOTAL.labels(result="no_accounts").inc()
        await msg.answer("Too many accounts — please select an account via the web.")
        return

    old_acct_key = _acct_select_key(msg.chat.id, entry.from_user_id)
    old_pending_key = _pending_key(msg.chat.id, entry.from_user_id)
    old_acct = await redis.get(old_acct_key)
    old_pending = await redis.get(old_pending_key)
    if old_acct or old_pending:
        await redis.delete(old_acct_key, old_pending_key)
        if old_pending:
            await msg.answer("Previous unconfirmed order cancelled.")

    metrics.TELEGRAM_ORDER_ATTEMPTS_TOTAL.labels(result="parsed").inc()

    if len(rows) == 1:
        account = rows[0]
        await _do_preview_and_write_pending(
            parsed,
            account,
            msg=msg,
            entry=entry,
            db=db,
            redis=redis,
            registry=registry,
            capability=capability,
            cfg=cfg,
        )
        return

    accounts_json = [
        {
            "id": str(r.id),
            "alias": str(r.alias),
            "broker": str(r.broker),
            "mode": str(r.mode),
            "currency": str(r.currency),
            "gateway_label": str(r.gateway_label),
        }
        for r in rows
    ]
    acct_select_payload = {
        "order": {
            "symbol": parsed.symbol,
            "side": parsed.side,
            "qty": parsed.qty,
            "order_type": parsed.order_type,
            "tif": parsed.tif,
            "limit_price": parsed.limit_price,
            "stop_price": parsed.stop_price,
        },
        "accounts": accounts_json,
    }
    await redis.set(old_acct_key, json.dumps(acct_select_payload), ex=_ACCT_SELECT_TTL)

    lines = ["Multiple accounts — reply with a number:"]
    for i, r in enumerate(rows, 1):
        alias_e = html.escape(str(r.alias))
        broker_e = html.escape(str(r.broker))
        mode_e = html.escape(str(r.mode))
        currency_e = html.escape(str(r.currency))
        lines.append(f"{i}. {alias_e} ({broker_e}) [{mode_e}] {currency_e}")
    await msg.answer("\n".join(lines))


async def handle_account_selection(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> bool:
    """Handle numeric reply for account selection. Returns True if consumed."""
    acct_key = _acct_select_key(msg.chat.id, entry.from_user_id)
    raw = await redis.get(acct_key)
    if raw is None:
        return False

    try:
        data = json.loads(raw)
        accounts = data["accounts"]
        order_data = data["order"]
    except Exception:
        log.warning("telegram.acct_select_corrupted", chat_id=msg.chat.id)
        await redis.delete(acct_key)
        return True

    try:
        idx = int((msg.text or "").strip()) - 1
    except ValueError:
        await msg.answer("Please reply with a number from the list.")
        return True

    if idx < 0 or idx >= len(accounts):
        await msg.answer(
            f"Invalid selection. Please reply with a number between 1 and {len(accounts)}."
        )
        return True

    await redis.delete(acct_key)
    account = accounts[idx]

    parsed = ParsedOrder(
        symbol=order_data["symbol"],
        side=order_data["side"],
        qty=order_data["qty"],
        order_type=order_data["order_type"],
        tif=order_data["tif"],
        limit_price=order_data.get("limit_price"),
        stop_price=order_data.get("stop_price"),
    )

    class _AccountProxy:
        def __init__(self, d: dict[str, Any]) -> None:
            self.id = d["id"]
            self.alias = d["alias"]
            self.broker = d["broker"]
            self.mode = d["mode"]
            self.currency = d["currency"]
            self.gateway_label = d["gateway_label"]

    await _do_preview_and_write_pending(
        parsed,
        _AccountProxy(account),
        msg=msg,
        entry=entry,
        db=db,
        redis=redis,
        registry=registry,
        capability=capability,
        cfg=cfg,
    )
    return True


async def handle_confirm(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
    registry: Any,
    capability: Any,
    cfg: Any,
) -> None:
    """Handle /confirm command — consume pending order and dispatch to broker."""
    key = _pending_key(msg.chat.id, entry.from_user_id)
    raw = await redis.execute_command("GETDEL", key)

    if raw is None:
        metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="expired").inc()
        await msg.answer(
            "No pending order (expired or already confirmed). "
            "If you believe an order was placed, check the web dashboard before retrying."
        )
        return

    try:
        pending = json.loads(raw)
    except Exception:
        log.error("telegram.confirm_payload_corrupted")
        await msg.answer("Internal error — please /place_order again.")
        return

    account_mode = pending.get("account_mode", "paper")

    if account_mode == "live":
        text_upper = (msg.text or "").strip().upper()
        if not text_upper.endswith("LIVE"):
            await redis.set(key, raw, ex=_PENDING_TTL)
            await msg.answer(
                "⚠️ <b>Live account</b> — reply <code>/confirm LIVE</code> to place, "
                "or /cancel_order to cancel."
            )
            return

    t0 = time.monotonic()
    account_id = pending["account_id"]
    conid = pending["conid"]
    side = pending["side"]
    order_type = pending["order_type"]
    tif = pending["tif"]
    qty = pending["qty"]
    limit_price = pending.get("limit_price")
    stop_price = pending.get("stop_price")

    nonce_uuid = str(uuid4())
    payload_hash = _preview_payload_hash(
        account_id=account_id,
        conid=conid,
        side=side,
        order_type=order_type,
        tif=tif,
        qty=qty,
        limit_price=limit_price,
        stop_price=stop_price,
    )
    now = datetime.now(UTC)
    rth_at_mint = _is_regular_trading_hours(now)
    nonce_key = f"nonce:order:{account_id}:{nonce_uuid}"
    nonce_value = json.dumps({"payload_hash": payload_hash, "rth_at_mint": rth_at_mint})
    await redis.set(nonce_key, nonce_value, ex=_NONCE_TTL)

    client_order_id = f"telegram-{uuid4()}"
    request_data: dict[str, Any] = {
        "account_id": account_id,
        "conid": conid,
        "side": side,
        "order_type": order_type,
        "tif": tif,
        "qty": qty,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "nonce": nonce_uuid,
        "client_order_id": client_order_id,
    }

    try:
        order = await place_order(
            cfg=cfg,
            db=db,
            redis=redis,
            registry=registry,
            capability=capability,
            request_data=request_data,
        )
        metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="placed").inc()
        await msg.answer(f"✅ Order placed — ID: <code>{html.escape(str(order.id))}</code>")

    except PreviewUnavailable as exc:
        error = exc.payload.get("error", "") if isinstance(exc.payload, dict) else ""
        if error == "risk_gate_blocked":
            metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="risk_blocked").inc()
            blockers = exc.payload.get("blockers", []) if isinstance(exc.payload, dict) else []
            lines = ["❌ <b>Order blocked by risk gate:</b>"]
            for b in blockers:
                code = html.escape(str(b.get("code", "")))
                message = html.escape(str(b.get("message", "")))
                lines.append(f"• {code}: {message}")
            await msg.answer("\n".join(lines))
        elif error in ("max_notional_exceeded", "daily_notional_exceeded"):
            metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="notional_exceeded").inc()
            await msg.answer(f"❌ {html.escape(error)}: order exceeds notional cap.")
        elif error == "rth_changed":
            metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="rth_changed").inc()
            await msg.answer("Market session changed since preview — please /place_order again.")
        elif error in ("unknown_nonce", "payload_mismatch"):
            log.error("telegram.confirm_nonce_error", error=error)
            metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="nonce_error").inc()
            await msg.answer("Internal error — please /place_order again.")
        elif exc.status_code == 503:
            metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="maintenance").inc()
            detail = html.escape(str(exc.payload.get("detail", "maintenance")))
            await msg.answer(f"Broker maintenance in progress: {detail}")
        else:
            metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="other_error").inc()
            await msg.answer(
                "Order submission failed — check the web dashboard for status before retrying."
            )
    except Exception:
        log.exception("telegram.confirm_unexpected_error")
        metrics.TELEGRAM_ORDER_CONFIRMS_TOTAL.labels(result="other_error").inc()
        await msg.answer(
            "Order submission failed — check the web dashboard for status before retrying."
        )
    finally:
        metrics.TELEGRAM_ORDER_E2E_SECONDS.labels(stage="confirm").observe(time.monotonic() - t0)


async def handle_cancel_order(
    msg: Message,
    *,
    entry: AllowlistEntry,
    redis: Any,
) -> None:
    """Handle /cancel_order — clear any pending state for this user."""
    pending_k = _pending_key(msg.chat.id, entry.from_user_id)
    acct_k = _acct_select_key(msg.chat.id, entry.from_user_id)

    pending_raw = await redis.get(pending_k)
    acct_raw = await redis.get(acct_k)

    await redis.delete(pending_k, acct_k)

    if pending_raw:
        metrics.TELEGRAM_ORDER_CANCELS_TOTAL.labels(stage="pending_order").inc()
    if acct_raw:
        metrics.TELEGRAM_ORDER_CANCELS_TOTAL.labels(stage="acct_select").inc()

    await msg.answer("Pending order cancelled.")


# ---------------------------------------------------------------------------
# Futures roll command handlers (Phase 14)
# ---------------------------------------------------------------------------
from app.services.futures.roll_service import RollService  # noqa: E402


async def handle_confirm_roll(
    msg: Message,
    *,
    entry: AllowlistEntry,
    redis: Any,
    roll_service: RollService,
    db_factory: Any,
) -> None:
    parts = (msg.text or "").strip().split()
    if len(parts) < 2:
        await msg.answer("Usage: /confirm_roll <nonce>")
        return
    nonce = parts[1]
    chat_id = msg.chat.id
    async with db_factory() as db:
        from sqlalchemy import text as sql_text

        acct_row = (
            await db.execute(
                sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :cid LIMIT 1"),
                {"cid": str(chat_id)},
            )
        ).first()
        if acct_row is None:
            await msg.answer("No account linked to this chat.")
            return
        account_id = str(acct_row[0])
    try:
        await roll_service.execute_roll(account_id, nonce)
        await msg.answer("Roll confirmed and submitted.")
    except KeyError:
        await msg.answer("Roll nonce not found or already used.")
    except Exception:
        log.exception("telegram.confirm_roll_unexpected_error")
        await msg.answer("Roll submission failed — check the web dashboard for status.")


async def handle_set_roll_rule(
    msg: Message,
    *,
    entry: AllowlistEntry,
    redis: Any,
    db_factory: Any,
) -> None:
    parts = (msg.text or "").strip().split()
    if len(parts) < 3:
        await msg.answer("Usage: /set_roll_rule <ROOT_SYMBOL> <days_before>")
        return
    root_symbol = parts[1].upper()
    import re as _re

    if not _re.fullmatch(r"[A-Z0-9]{1,10}", root_symbol):
        await msg.answer("Invalid symbol. Use alphanumeric, max 10 chars (e.g. ES, NQ, HSI).")
        return
    try:
        days_before = int(parts[2])
    except ValueError:
        await msg.answer("days_before must be an integer.")
        return
    if not (1 <= days_before <= 90):
        await msg.answer("days_before must be between 1 and 90.")
        return
    chat_id = msg.chat.id
    async with db_factory() as db:
        from sqlalchemy import text as sql_text

        acct_row = (
            await db.execute(
                sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :cid LIMIT 1"),
                {"cid": str(chat_id)},
            )
        ).first()
        if acct_row is None:
            await msg.answer("No account linked to this chat.")
            return
        account_id = str(acct_row[0])
        inst_row = (
            await db.execute(
                sql_text(
                    "SELECT id FROM instruments WHERE asset_class = 'FUTURE'"
                    " AND meta->>'underlying_symbol' = :sym ORDER BY id DESC LIMIT 1"
                ),
                {"sym": root_symbol},
            )
        ).first()
        if inst_row is None:
            await msg.answer(f"No futures instrument found for {html.escape(root_symbol)}.")
            return
        instrument_id = inst_row[0]
        await db.execute(
            sql_text(
                "INSERT INTO futures_roll_rules (account_id, instrument_id, days_before)"
                " VALUES (:aid, :iid, :days)"
                " ON CONFLICT (account_id, instrument_id)"
                " DO UPDATE SET days_before = EXCLUDED.days_before, updated_at = now()"
            ),
            {"aid": account_id, "iid": instrument_id, "days": days_before},
        )
        await db.commit()
    await msg.answer(
        f"Roll rule set: {html.escape(root_symbol)} — roll {days_before} days before expiry."
    )


async def handle_delete_roll_rule(
    msg: Message,
    *,
    entry: AllowlistEntry,
    redis: Any,
    db_factory: Any,
) -> None:
    parts = (msg.text or "").strip().split()
    if len(parts) < 2:
        await msg.answer("Usage: /delete_roll_rule <ROOT_SYMBOL>")
        return
    sym = parts[1].upper()
    import re as _re

    if not _re.fullmatch(r"[A-Z0-9]{1,10}", sym):
        await msg.answer("Invalid symbol. Use alphanumeric, max 10 chars.")
        return
    chat_id = msg.chat.id
    async with db_factory() as db:
        from sqlalchemy import text as sql_text

        acct_row = (
            await db.execute(
                sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :cid LIMIT 1"),
                {"cid": str(chat_id)},
            )
        ).first()
        if acct_row is None:
            await msg.answer("No account linked to this chat.")
            return
        account_id = str(acct_row[0])
        rows = (
            await db.execute(
                sql_text(
                    "SELECT r.instrument_id FROM futures_roll_rules r"
                    " JOIN instruments i ON i.id = r.instrument_id"
                    " WHERE r.account_id = :aid AND i.meta->>'underlying_symbol' = :sym"
                ),
                {"aid": account_id, "sym": sym},
            )
        ).fetchall()
        if len(rows) == 0:
            await msg.answer(f"No roll rule found for {html.escape(sym)}.")
            return
        if len(rows) > 1:
            ids = ", ".join(str(r[0]) for r in rows)
            await msg.answer(
                f"Multiple rules found for {html.escape(sym)} (instrument IDs: {html.escape(ids)})."
                f" Use /delete_roll_rule with a specific instrument ID."
            )
            return
        instrument_id = rows[0][0]
        await db.execute(
            sql_text(
                "DELETE FROM futures_roll_rules WHERE account_id = :aid AND instrument_id = :iid"
            ),
            {"aid": account_id, "iid": instrument_id},
        )
        await db.commit()
    await msg.answer(f"Roll rule deleted for {html.escape(sym)}.")


async def handle_roll_rules_list(
    msg: Message,
    *,
    entry: AllowlistEntry,
    redis: Any,
    db_factory: Any,
) -> None:
    chat_id = msg.chat.id
    async with db_factory() as db:
        from sqlalchemy import text as sql_text

        acct_row = (
            await db.execute(
                sql_text("SELECT id FROM broker_accounts WHERE telegram_chat_id = :cid LIMIT 1"),
                {"cid": str(chat_id)},
            )
        ).first()
        if acct_row is None:
            await msg.answer("No account linked to this chat.")
            return
        account_id = str(acct_row[0])
        rows = (
            await db.execute(
                sql_text(
                    "SELECT i.symbol, r.days_before FROM futures_roll_rules r"
                    " JOIN instruments i ON i.id = r.instrument_id"
                    " WHERE r.account_id = :aid AND r.enabled = true"
                    " ORDER BY i.symbol"
                ),
                {"aid": account_id},
            )
        ).fetchall()
    if not rows:
        await msg.answer("No roll rules configured.")
        return
    lines = ["<b>Your roll rules:</b>"]
    for row in rows:
        lines.append(f"• {html.escape(str(row[0]))}: roll {row[1]} days before expiry")
    await msg.answer("\n".join(lines))
