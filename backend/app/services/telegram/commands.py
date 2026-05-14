"""Telegram command handlers — /status /accounts /kill_switch /mute /unmute /help."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.account_kill_switch_service import AccountKillSwitchService
from app.services.telegram.allowlist import AllowlistEntry

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

_MUTE_RE = re.compile(r"^(\d+)([mhd])$")
_MULTIPLIERS: dict[str, int] = {"m": 60, "h": 3600, "d": 86400}


async def handle_status(msg: Message, *, request_app: Any = None) -> None:
    try:
        evaluator = request_app.state.alerts_evaluator if request_app else None
        if evaluator is None:
            await msg.answer("Alerts evaluator not running.")
            return
        await msg.answer("Evaluator: running")
    except Exception:
        await msg.answer("Status unavailable.")


async def handle_accounts(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    try:
        rows = await db.execute(
            text(
                "SELECT a.alias, b.label as broker, a.mode, a.currency_base "
                "FROM accounts a JOIN brokers b ON a.broker_id = b.id "
                "WHERE a.jwt_subject = :sub AND a.deleted_at IS NULL "
                "ORDER BY a.display_order"
            ),
            {"sub": entry.jwt_subject},
        )
        accounts = rows.fetchall()
        if not accounts:
            await msg.answer("No accounts found.")
            return
        lines = [f"<b>Accounts for {entry.label}:</b>"]
        for acc in accounts:
            lines.append(f"• {acc.alias} ({acc.broker}) [{acc.mode}] {acc.currency_base}")
        await msg.answer("\n".join(lines))
    except Exception:
        log.exception("telegram.handle_accounts_failed")
        await msg.answer("Could not retrieve accounts.")


async def handle_kill_switch(
    msg: Message,
    *,
    entry: AllowlistEntry,
    db: AsyncSession,
    redis: Any,
) -> None:
    parts = (msg.text or "").split()
    broker_alias = parts[1].upper() if len(parts) > 1 else ""
    if not broker_alias:
        await msg.answer("Usage: /kill_switch <broker> (e.g. IBKR, FUTU)")
        return
    try:
        rows = await db.execute(
            text(
                "SELECT a.id, a.alias FROM accounts a "
                "JOIN brokers b ON a.broker_id = b.id "
                "WHERE UPPER(b.label) = :broker AND a.jwt_subject = :sub AND a.deleted_at IS NULL"
            ),
            {"broker": broker_alias, "sub": entry.jwt_subject},
        )
        accounts = rows.fetchall()
        if not accounts:
            await msg.answer(f"No accounts found for broker '{broker_alias}'.")
            return
        ks = AccountKillSwitchService(db=db, redis=redis)
        outcomes = []
        for acc in accounts:
            try:
                await ks.toggle(
                    acc.id,
                    is_enabled=False,
                    reason="telegram:/kill_switch",
                    by=f"telegram:{entry.label}",
                )
                outcomes.append(f"✅ {acc.alias}: kill-switch enabled")
            except Exception:
                outcomes.append(f"❌ {acc.alias}: failed")
        await msg.answer("\n".join(outcomes))
    except Exception:
        log.exception("telegram.handle_kill_switch_failed")
        await msg.answer("Kill-switch failed.")


async def handle_mute(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Usage: /mute <alert_id> [30m|2h|1d]")
        return
    try:
        alert_id = int(parts[1])
    except ValueError:
        await msg.answer("Usage: /mute <alert_id> [30m|2h|1d]")
        return

    muted_until: datetime | None = None
    if len(parts) >= 3:
        m = _MUTE_RE.match(parts[2])
        if not m:
            await msg.answer("Usage: /mute <alert_id> [30m|2h|1d]")
            return
        secs = int(m.group(1)) * _MULTIPLIERS[m.group(2)]
        muted_until = datetime.now(tz=UTC) + timedelta(seconds=secs)

    try:
        await db.execute(
            text(
                "UPDATE alerts SET status='disabled', muted_until=:mu, updated_at=now() "
                "WHERE id=:aid AND jwt_subject=:sub"
            ),
            {"aid": alert_id, "mu": muted_until, "sub": entry.jwt_subject},
        )
        await db.commit()
        dur = f" until {muted_until.isoformat()}" if muted_until else " (permanent)"
        await msg.answer(f"Alert {alert_id} muted{dur}.")
    except Exception:
        log.exception("telegram.handle_mute_failed")
        await msg.answer("Mute failed.")


async def handle_unmute(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Usage: /unmute <alert_id>")
        return
    try:
        alert_id = int(parts[1])
    except ValueError:
        await msg.answer("Usage: /unmute <alert_id>")
        return
    try:
        await db.execute(
            text(
                "UPDATE alerts SET status='active', muted_until=NULL, updated_at=now() "
                "WHERE id=:aid AND jwt_subject=:sub"
            ),
            {"aid": alert_id, "sub": entry.jwt_subject},
        )
        await db.commit()
        await msg.answer(f"Alert {alert_id} unmuted.")
    except Exception:
        log.exception("telegram.handle_unmute_failed")
        await msg.answer("Unmute failed.")


async def handle_help(msg: Message) -> None:
    await msg.answer(
        "<b>Available commands:</b>\n"
        "/status — evaluator status + active alert count\n"
        "/accounts — list your accounts\n"
        "/kill_switch &lt;broker&gt; — enable kill-switch for broker accounts\n"
        "/mute &lt;id&gt; [30m|2h|1d] — mute an alert (permanent if no duration)\n"
        "/unmute &lt;id&gt; — restore a muted alert\n"
        "/help — this message"
    )


def register_handlers(
    dp: Dispatcher,
    *,
    allowlist: Any,
    rate_limiter: Any,
    db_factory: Any,
    redis: Any,
    request_app: Any = None,
) -> None:
    async def _authed(msg: Message) -> AllowlistEntry | None:
        from_user_id = msg.from_user.id if msg.from_user else 0
        entry = allowlist.lookup(chat_id=msg.chat.id, from_user_id=from_user_id)
        if entry is None:
            await msg.answer("Unauthorized.")
        return entry

    @dp.message(Command("help"))
    async def _help(msg: Message) -> None:
        await handle_help(msg)

    @dp.message(Command("status"))
    async def _status(msg: Message) -> None:
        if await _authed(msg) is None:
            return
        await handle_status(msg, request_app=request_app)

    @dp.message(Command("accounts"))
    async def _accounts(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        async with db_factory() as db:
            await handle_accounts(msg, entry=entry, db=db)

    @dp.message(Command("kill_switch"))
    async def _ks(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        async with db_factory() as db:
            await handle_kill_switch(msg, entry=entry, db=db, redis=redis)

    @dp.message(Command("mute"))
    async def _mute(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        async with db_factory() as db:
            await handle_mute(msg, entry=entry, db=db)

    @dp.message(Command("unmute"))
    async def _unmute(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        async with db_factory() as db:
            await handle_unmute(msg, entry=entry, db=db)
