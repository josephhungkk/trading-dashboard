from __future__ import annotations

import pytest
import sqlalchemy.exc
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


@pytest.mark.asyncio
async def test_telegram_command_log_is_hypertable(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'telegram_command_log'"
        )
    )
    assert result.scalar_one_or_none() == "telegram_command_log"


@pytest.mark.asyncio
async def test_outcome_check_constraint_rejects_bad_value(session: AsyncSession) -> None:
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await session.execute(
            text(
                "INSERT INTO telegram_command_log (ts, chat_id, command, outcome) "
                "VALUES (now(), 1, '/test', 'bad_value')"
            )
        )
        await session.flush()


@pytest.mark.asyncio
async def test_telegram_command_log_idx_id_exists(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'telegram_command_log' AND indexname = 'idx_tg_cmd_id'"
        )
    )
    assert result.scalar_one_or_none() == "idx_tg_cmd_id"
