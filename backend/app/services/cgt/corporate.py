from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cgt.types import TaxEvent

log = structlog.get_logger(__name__)

_FLEX_CODES: dict[str, str] = {
    "FS": "corp_action_split",
    "RS": "corp_action_consolidation",
    "SD": "corp_action_scrip",
    "RI": "corp_action_rights_subscribed",
    "RL": "corp_action_rights_lapsed",
    "SO": "corp_action_spinoff",
    "TC": "corp_action_takeover_share",
    "CASHMERGER": "corp_action_takeover_cash",
    "DM": "corp_action_demerger",
    "RC": "corp_action_return_of_capital",
    "BS": "corp_action_b_share",
}


async def process(te: TaxEvent, session: AsyncSession) -> None:
    handler = _HANDLERS.get(te.event_type)
    if handler is None:
        log.warning("cgt.corporate.unhandled", event_type=te.event_type, notes=te.notes)
        return
    await handler(te, session)


async def _split(te: TaxEvent, session: AsyncSession) -> None:
    if not te.notes or ":" not in te.notes:
        log.warning("cgt.corporate.split_no_ratio", notes=te.notes)
        return
    num, den = (int(x) for x in te.notes.split(":"))
    ratio = num / den
    await session.execute(
        text("""
            UPDATE s104_pool SET qty = qty * :r, last_updated_at = :t
            WHERE account_id = :a AND instrument_id = :i
        """),
        {"r": ratio, "t": te.executed_at, "a": te.account_id, "i": te.instrument_id},
    )
    log.info("cgt.corporate.split", ratio=f"{num}:{den}", instrument_id=te.instrument_id)


async def _consolidation(te: TaxEvent, session: AsyncSession) -> None:
    if not te.notes or ":" not in te.notes:
        log.warning("cgt.corporate.consolidation_no_ratio", notes=te.notes)
        return
    num, den = (int(x) for x in te.notes.split(":"))
    ratio = num / den
    await session.execute(
        text("""
            UPDATE s104_pool SET qty = qty * :r, last_updated_at = :t
            WHERE account_id = :a AND instrument_id = :i
        """),
        {"r": ratio, "t": te.executed_at, "a": te.account_id, "i": te.instrument_id},
    )
    log.info("cgt.corporate.consolidation", ratio=f"{num}:{den}", instrument_id=te.instrument_id)


_HANDLERS = {
    "corp_action_split": _split,
    "corp_action_consolidation": _consolidation,
}
