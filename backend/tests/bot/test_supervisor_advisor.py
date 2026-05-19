import importlib.util
import inspect
import json
import multiprocessing
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.bot.supervisor import BotSupervisor, _handle_pause_cmd

pytestmark = pytest.mark.no_db


def test_stop_reason_advisor_auto_pause_column_exists():
    assert text is not None
    migration_path = (
        Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0063_advisor.py"
    )
    spec = importlib.util.spec_from_file_location("alembic_0063_advisor", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    source = inspect.getsource(migration.upgrade)

    assert "advisor_auto_pause" in source


@pytest.mark.asyncio
async def test_pause_propagates_reason_to_status_frame():
    bot_id = uuid4()
    redis = AsyncMock()

    await _handle_pause_cmd(
        bot_id=bot_id,
        reason="advisor_auto_pause",
        redis=redis,
        db=AsyncMock(),
    )

    redis.publish.assert_awaited_once()
    channel, payload = redis.publish.await_args.args
    assert channel == f"bot:status:{bot_id}"
    frame = json.loads(payload)
    assert frame["status"] == "paused"
    assert frame["reason"] == "advisor_auto_pause"


@pytest.mark.asyncio
async def test_pause_propagates_default_reason_manual():
    bot_id = uuid4()
    redis = AsyncMock()

    await _handle_pause_cmd(
        bot_id=bot_id,
        reason="manual",
        redis=redis,
        db=AsyncMock(),
    )

    _, payload = redis.publish.await_args.args
    frame = json.loads(payload)
    assert frame["reason"] == "manual"


@pytest.mark.asyncio
async def test_dispatch_pause_sends_reason_to_child():
    bot_id = uuid4()
    supervisor = BotSupervisor(redis=AsyncMock(), db=AsyncMock())
    queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=20)
    supervisor._child_queues[bot_id] = queue

    await supervisor._dispatch_command(
        bot_id,
        {"cmd": "PAUSE", "reason": "advisor_auto_pause"},
    )

    msg = queue.get(timeout=1)
    assert msg["cmd"] == "PAUSE"
    assert msg["reason"] == "advisor_auto_pause"


@pytest.mark.asyncio
async def test_update_advisor_config_forwarded_to_child():
    bot_id = uuid4()
    supervisor = BotSupervisor(redis=AsyncMock(), db=AsyncMock())
    queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=20)
    supervisor._child_queues[bot_id] = queue

    await supervisor._dispatch_command(
        bot_id,
        {"cmd": "UPDATE_ADVISOR_CONFIG", "config": {"mode": "VETO"}},
    )

    msg = queue.get(timeout=1)
    assert msg["cmd"] == "UPDATE_ADVISOR_CONFIG"
    assert msg["config"] == {"mode": "VETO"}
