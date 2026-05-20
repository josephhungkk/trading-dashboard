import pytest
import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_generated_strategies_table(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1 FROM generated_strategies LIMIT 0"))


@pytest.mark.asyncio
async def test_generated_strategies_sandbox_status_check(session: AsyncSession) -> None:
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO generated_strategies"
                    " (name, source_code, source_hash, generation_prompt, prompt_hash,"
                    "  llm_model, sandbox_status)"
                    " VALUES ('t', 'c', 'h', 'p', 'ph', 'gpt-4', 'invalid_value')"
                )
            )


@pytest.mark.asyncio
async def test_bot_strategy_provenance_table(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1 FROM bot_strategy_provenance LIMIT 0"))


@pytest.mark.asyncio
async def test_generated_strategies_prompt_hash_index(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename='generated_strategies'"
            " AND indexname='generated_strategies_prompt_hash_idx'"
        )
    )
    assert result.scalar_one_or_none() == "generated_strategies_prompt_hash_idx"


@pytest.mark.asyncio
async def test_bots_strategy_class_column(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='bots' AND column_name='strategy_class'"
        )
    )
    assert result.scalar_one_or_none() == "strategy_class"
