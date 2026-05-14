"""Write-only helper for telegram_command_log hypertable."""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


async def log_command(
    db: AsyncSession,
    *,
    chat_id: int,
    from_user_id: int | None,
    command: str,
    args: str | None,
    outcome: str,
    latency_ms: int | None = None,
) -> None:
    try:
        await db.execute(
            text(
                "INSERT INTO telegram_command_log"
                " (chat_id, from_user_id, command, args, outcome, latency_ms)"
                " VALUES (:chat_id, :from_user_id, :command, :args, :outcome, :latency_ms)"
            ),
            {
                "chat_id": chat_id,
                "from_user_id": from_user_id,
                "command": command,
                "args": args,
                "outcome": outcome,
                "latency_ms": latency_ms,
            },
        )
        await db.commit()
    except Exception:
        log.warning("telegram.command_log_insert_failed", command=command)
