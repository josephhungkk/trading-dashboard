from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_telegram_command_log_columns_exist(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'telegram_command_log'"
        )
    )
    columns = {row[0] for row in result.fetchall()}

    expected = {
        "id",
        "ts",
        "chat_id",
        "from_user_id",
        "command",
        "args",
        "outcome",
        "latency_ms",
    }
    assert not expected - columns


@pytest.mark.asyncio
async def test_alerts_muted_until_column_exists(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'alerts' AND column_name = 'muted_until'"
        )
    )

    assert result.scalar_one_or_none() == "muted_until"
