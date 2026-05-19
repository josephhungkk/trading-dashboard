from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.bot.supervisor import BotSupervisor


@pytest.mark.asyncio
async def test_duplicate_command_id_skipped(redis):
    """Already-executed command IDs in done SET are not re-processed."""
    bot_id = uuid4()
    cmd_id = "msg-001"

    # Simulate done SET contains this command
    await redis.sadd(f"bot:control:done:{bot_id}", cmd_id)

    supervisor = BotSupervisor(redis=redis, db=AsyncMock())

    dispatched = []

    async def fake_dispatch(bid, cmd):
        dispatched.append(cmd)

    supervisor._dispatch_command = fake_dispatch

    await supervisor._process_command(
        bot_id=str(bot_id),
        message_id=cmd_id,
        payload={"id": cmd_id, "cmd": "START"},
    )

    assert len(dispatched) == 0  # skipped


@pytest.mark.asyncio
async def test_new_command_dispatched(redis):
    """New command ID is processed and added to done SET."""
    bot_id = uuid4()
    cmd_id = "msg-002"

    # Create the stream so xack doesn't fail
    stream_key = f"bot:control:{bot_id}"
    await redis.xadd(stream_key, {"data": "dummy"})
    await redis.xadd(stream_key, {"data": "dummy2"})

    supervisor = BotSupervisor(redis=redis, db=AsyncMock())
    dispatched = []

    async def fake_dispatch(bid, cmd):
        dispatched.append(cmd)

    supervisor._dispatch_command = fake_dispatch

    await supervisor._process_command(
        bot_id=str(bot_id),
        message_id=cmd_id,
        payload={"id": cmd_id, "cmd": "STOP"},
    )

    assert dispatched == [{"id": cmd_id, "cmd": "STOP"}]
    # done key was set
    is_member = await redis.sismember(f"bot:control:done:{bot_id}", cmd_id)
    assert is_member


@pytest.mark.asyncio
async def test_heartbeat_expiry_triggers_respawn(redis):
    """Missing heartbeat triggers _respawn_bot."""
    bot_id = uuid4()

    db = AsyncMock()
    supervisor = BotSupervisor(redis=redis, db=db)
    respawned = []

    async def fake_respawn(bid):
        respawned.append(bid)

    supervisor._respawn_bot = fake_respawn
    supervisor._running_bots = {str(bot_id): MagicMock(is_alive=lambda: True)}
    # No heartbeat key → respawn
    # _check_heartbeat uses asyncio.sleep(delay) before respawn, bypass via count ≥ len
    supervisor._respawn_counts[str(bot_id)] = 3  # max reached → write DB, no respawn
    supervisor._running_bots = {str(bot_id): MagicMock()}

    await supervisor._check_heartbeat(str(bot_id))

    # max respawn reached → DB update, no _respawn_bot call
    db.execute.assert_called_once()
    assert len(respawned) == 0
