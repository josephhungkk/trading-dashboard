"""Telegram command handlers — /status /accounts /kill_switch /mute /unmute /help."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.account_kill_switch_service import AccountKillSwitchService
from app.services.telegram.allowlist import AllowlistEntry

log = structlog.get_logger(__name__)

# Matches: <number><unit> where unit ∈ {m, h, d}
_MUTE_RE = re.compile(r"^(\d+)([mhd])$")
_MULTIPLIERS: dict[str, int] = {"m": 60, "h": 3600, "d": 86400}
_MAX_MUTE_SECS = 365 * 86400  # 1 year cap


async def handle_status(msg: Message, *, request_app: Any = None) -> None:
    try:
        evaluator = getattr(getattr(request_app, "state", None), "alerts_evaluator", None)
        if evaluator is None:
            await msg.answer("Alerts evaluator not running.")
            return
        await msg.answer("Evaluator: running")
    except Exception:
        log.exception("telegram.handle_status_failed")
        await msg.answer("Status unavailable.")


async def handle_accounts(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    try:
        rows = await db.execute(
            text(
                "SELECT a.alias, b.label as broker, a.mode, a.currency_base "
                "FROM broker_accounts a JOIN brokers b ON a.broker_id = b.id "
                "WHERE a.deleted_at IS NULL "
                "ORDER BY a.display_order"
            ),
        )
        accounts = rows.fetchall()
        if not accounts:
            await msg.answer("No accounts found.")
            return
        lines = [f"<b>Accounts for {html.escape(entry.label)}:</b>"]
        for acc in accounts:
            lines.append(
                f"• {html.escape(acc.alias)} ({html.escape(acc.broker)})"
                f" [{html.escape(acc.mode)}] {html.escape(acc.currency_base)}"
            )
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
        await msg.answer("Usage: /kill_switch &lt;broker&gt; (e.g. IBKR, FUTU)")
        return
    try:
        rows = await db.execute(
            text(
                "SELECT a.id, a.alias FROM broker_accounts a "
                "JOIN brokers b ON a.broker_id = b.id "
                "WHERE b.label = :broker AND a.deleted_at IS NULL"
            ),
            {"broker": broker_alias},
        )
        accounts = rows.fetchall()
        if not accounts:
            escaped = html.escape(broker_alias)
            await msg.answer(f"No accounts found for broker '{escaped}'.")
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
                outcomes.append(f"✅ {html.escape(acc.alias)}: kill-switch enabled")
            except Exception:
                log.exception(
                    "telegram.kill_switch_account_failed",
                    account_id=acc.id,
                    alias=acc.alias,
                )
                outcomes.append(f"❌ {html.escape(acc.alias)}: failed")
        await msg.answer("\n".join(outcomes))
    except Exception:
        log.exception("telegram.handle_kill_switch_failed")
        await msg.answer("Kill-switch failed.")


async def handle_mute(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Usage: /mute &lt;alert_id&gt; [30m|2h|1d]")
        return
    try:
        alert_id = int(parts[1])
    except ValueError:
        await msg.answer("Usage: /mute &lt;alert_id&gt; [30m|2h|1d]")
        return

    muted_until: datetime | None = None
    if len(parts) >= 3:
        m = _MUTE_RE.match(parts[2])
        if not m:
            await msg.answer("Usage: /mute &lt;alert_id&gt; [30m|2h|1d]")
            return
        secs = int(m.group(1)) * _MULTIPLIERS[m.group(2)]
        if secs == 0 or secs > _MAX_MUTE_SECS:
            await msg.answer("Duration must be between 1m and 365d.")
            return
        muted_until = datetime.now(tz=UTC) + timedelta(seconds=secs)

    try:
        result = await db.execute(
            text(
                "UPDATE alerts SET status='disabled', muted_until=:mu, updated_at=now() "
                "WHERE id=:aid AND jwt_subject=:sub RETURNING id"
            ),
            {"aid": alert_id, "mu": muted_until, "sub": entry.jwt_subject},
        )
        if result.fetchone() is None:
            await db.rollback()
            await msg.answer(f"Alert {alert_id} not found or not yours.")
            return
        await db.commit()
        dur = f" until {muted_until.isoformat()}" if muted_until else " (permanent)"
        await msg.answer(f"Alert {alert_id} muted{dur}.")
    except Exception:
        log.exception("telegram.handle_mute_failed")
        await db.rollback()
        await msg.answer("Mute failed.")


async def handle_unmute(msg: Message, *, entry: AllowlistEntry, db: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Usage: /unmute &lt;alert_id&gt;")
        return
    try:
        alert_id = int(parts[1])
    except ValueError:
        await msg.answer("Usage: /unmute &lt;alert_id&gt;")
        return
    try:
        result = await db.execute(
            text(
                "UPDATE alerts SET status='active', muted_until=NULL, updated_at=now() "
                "WHERE id=:aid AND jwt_subject=:sub RETURNING id"
            ),
            {"aid": alert_id, "sub": entry.jwt_subject},
        )
        if result.fetchone() is None:
            await db.rollback()
            await msg.answer(f"Alert {alert_id} not found or not yours.")
            return
        await db.commit()
        await msg.answer(f"Alert {alert_id} unmuted.")
    except Exception:
        log.exception("telegram.handle_unmute_failed")
        await db.rollback()
        await msg.answer("Unmute failed.")


async def handle_help(msg: Message) -> None:
    await msg.answer(
        "<b>Available commands:</b>\n"
        "/status — evaluator status\n"
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
    tg_chat: Any = None,
) -> None:
    async def _authed(msg: Message) -> AllowlistEntry | None:
        from_user_id = msg.from_user.id if msg.from_user else 0
        entry: AllowlistEntry | None = allowlist.lookup(
            chat_id=msg.chat.id, from_user_id=from_user_id
        )
        if entry is None:
            await msg.answer("Unauthorized.")
        return entry

    @dp.message(Command("help"))
    async def _help(msg: Message) -> None:
        from_user_id = msg.from_user.id if msg.from_user else 0
        if not await rate_limiter.check_read(chat_id=msg.chat.id, from_user_id=from_user_id):
            await msg.answer("Rate limit exceeded. Try again later.")
            return
        await handle_help(msg)

    @dp.message(Command("status"))
    async def _status(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        if not await rate_limiter.check_read(chat_id=msg.chat.id, from_user_id=entry.from_user_id):
            await msg.answer("Rate limit exceeded. Try again later.")
            return
        await handle_status(msg, request_app=request_app)

    @dp.message(Command("accounts"))
    async def _accounts(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        if not await rate_limiter.check_read(chat_id=msg.chat.id, from_user_id=entry.from_user_id):
            await msg.answer("Rate limit exceeded. Try again later.")
            return
        async with db_factory() as db:
            await handle_accounts(msg, entry=entry, db=db)

    @dp.message(Command("kill_switch"))
    async def _ks(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        if not await rate_limiter.check_write(chat_id=msg.chat.id, from_user_id=entry.from_user_id):
            await msg.answer("Rate limit exceeded. Try again later.")
            return
        async with db_factory() as db:
            await handle_kill_switch(msg, entry=entry, db=db, redis=redis)

    @dp.message(Command("mute"))
    async def _mute(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        if not await rate_limiter.check_write(chat_id=msg.chat.id, from_user_id=entry.from_user_id):
            await msg.answer("Rate limit exceeded. Try again later.")
            return
        async with db_factory() as db:
            await handle_mute(msg, entry=entry, db=db)

    @dp.message(Command("unmute"))
    async def _unmute(msg: Message) -> None:
        entry = await _authed(msg)
        if entry is None:
            return
        if not await rate_limiter.check_write(chat_id=msg.chat.id, from_user_id=entry.from_user_id):
            await msg.answer("Rate limit exceeded. Try again later.")
            return
        async with db_factory() as db:
            await handle_unmute(msg, entry=entry, db=db)

    if tg_chat is not None:
        import asyncio as _asyncio

        @dp.message(F.text & ~F.text.startswith("/"))
        async def _chat_msg(msg: Message) -> None:
            entry = await _authed(msg)
            if entry is None:
                return
            from_user_id = entry.from_user_id
            if not await rate_limiter.check_read(chat_id=msg.chat.id, from_user_id=from_user_id):
                await msg.answer("Rate limit exceeded. Try again later.")
                return
            task = _asyncio.create_task(tg_chat.handle(msg))
            task.add_done_callback(
                lambda t: (
                    not t.cancelled()
                    and t.exception() is not None
                    and log.error(
                        "telegram.chat_task_failed",
                        error_class=type(t.exception()).__name__,
                    )
                )
            )
