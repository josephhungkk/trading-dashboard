import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.orchestrator.retrain import NightlyRetrainJob


def _make_db_factory(bot_ids):
    """Return a context-manager-yielding factory."""
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[(bid,) for bid in bot_ids]))
    )

    @asynccontextmanager
    async def factory():
        yield db

    return factory


@pytest.mark.asyncio
async def test_retrain_skips_paused_bots() -> None:
    """Bots with status != 'running' are excluded."""
    tuner = AsyncMock()
    telegram = AsyncMock()
    job = NightlyRetrainJob(
        db_factory=_make_db_factory([]),
        param_tuner_factory=lambda db: tuner,
        telegram=telegram,
    )
    await job.run()
    tuner.trigger.assert_not_called()
    telegram.send.assert_called_once()


@pytest.mark.asyncio
async def test_retrain_parallel_fan_out_semaphore() -> None:
    """3 bots with semaphore=1 => sequential (max one concurrent)."""
    import uuid

    bot_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    call_order: list[str] = []

    async def fake_trigger(bot_id, *args, **kwargs):
        call_order.append(f"start:{bot_id}")
        await asyncio.sleep(0.01)
        call_order.append(f"end:{bot_id}")
        return None

    tuner = AsyncMock()
    tuner.trigger = AsyncMock(side_effect=fake_trigger)
    telegram = AsyncMock()

    job = NightlyRetrainJob(
        db_factory=_make_db_factory(bot_ids),
        param_tuner_factory=lambda db: tuner,
        telegram=telegram,
        max_parallel=1,
    )
    await job.run()
    assert tuner.trigger.call_count == 3
    # With semaphore=1: end of bot N happens before start of bot N+1
    for i in range(len(bot_ids) - 1):
        end_idx = call_order.index(f"end:{bot_ids[i]}")
        start_next = call_order.index(f"start:{bot_ids[i + 1]}")
        assert end_idx < start_next


@pytest.mark.asyncio
async def test_retrain_posts_telegram_report() -> None:
    """Telegram report is sent after all bots processed."""
    import uuid

    bot_id = uuid.uuid4()
    tuner = AsyncMock()
    tuner.trigger = AsyncMock(return_value=None)
    telegram = AsyncMock()

    job = NightlyRetrainJob(
        db_factory=_make_db_factory([bot_id]),
        param_tuner_factory=lambda db: tuner,
        telegram=telegram,
    )
    await job.run()
    telegram.send.assert_called_once()
    msg = telegram.send.call_args[0][0]
    assert "retrain" in msg.lower() or "bot" in msg.lower()
