"""Phase 11a-A1: ai_completions hypertable migration test.

Validates the hypertable shape, retention policy presence, compression
policy presence, and the required column set. 90d compression per LOW-5
and 1y retention per spec §6.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ai_completions_is_hypertable(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'ai_completions'"
        )
    )
    assert result.scalar_one() == "ai_completions"


@pytest.mark.asyncio
async def test_ai_completions_columns_present(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'ai_completions'"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    expected = {
        "ts",
        "request_id",
        "jwt_subject",
        "capability",
        "provider",
        "model",
        "host",
        "prompt_tokens",
        "completion_tokens",
        "wall_time_ms",
        "wol_warmup_ms",
        "outcome",
        "error_class",
        "caller",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


@pytest.mark.asyncio
async def test_ai_completions_retention_policy(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "  AND hypertable_name = 'ai_completions'"
        )
    )
    config = result.scalar_one()
    assert "drop_after" in config


@pytest.mark.asyncio
async def test_ai_completions_compression_policy(session: AsyncSession) -> None:
    """LOW-5: 90d compression policy keeps disk usage bounded."""
    result = await session.execute(
        text(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_compression' "
            "  AND hypertable_name = 'ai_completions'"
        )
    )
    config = result.scalar_one()
    assert "compress_after" in config


@pytest.mark.asyncio
async def test_ai_completions_outcome_check_constraint(session: AsyncSession) -> None:
    """Outcome enum is constrained to a known set; INSERTs with garbage
    must fail rather than poison the cost-ledger query surface."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO ai_completions "
                "(ts, request_id, jwt_subject, capability, provider, model, "
                " host, outcome, caller) "
                "VALUES (now(), gen_random_uuid(), 'sub', 'LOCAL_ONLY', "
                "'ollama-nuc', 'qwen2.5:7b', 'nuc', 'bogus_outcome', 'test')"
            )
        )
