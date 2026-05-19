from __future__ import annotations

import asyncio
import json
import multiprocessing
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

logger = structlog.get_logger(__name__)

_HEARTBEAT_POLL = 8
_HEARTBEAT_TTL = 10
_RESPAWN_DELAYS = [10, 30, 60]
_COMMAND_DONE_TTL = 3600
_STREAM_GROUP = "supervisor"


async def _handle_pause_cmd(
    bot_id: UUID | str,
    reason: str,
    redis: Any,
    db: Any,
) -> None:
    import json

    payload = json.dumps({"bot_id": str(bot_id), "status": "paused", "reason": reason})
    await redis.publish(f"bot:status:{bot_id}", payload)


class BotSupervisor:
    def __init__(self, redis: Any, db: AsyncSession) -> None:
        self._redis = redis
        self._db = db
        self._running_bots: dict[str, multiprocessing.Process] = {}
        self._respawn_counts: dict[str, int] = {}
        self._child_queues: dict[str, multiprocessing.Queue[dict[str, Any]]] = {}
        self._respawn_tasks: set[asyncio.Task[None]] = set()

    async def _process_command(self, bot_id: str, message_id: str, payload: dict[str, Any]) -> None:
        done_key = f"bot:control:done:{bot_id}"
        already_done = await self._redis.sismember(done_key, message_id)
        if already_done:
            return
        await self._dispatch_command(bot_id, payload)
        await self._redis.sadd(done_key, message_id)
        await self._redis.expire(done_key, _COMMAND_DONE_TTL)
        stream_key = f"bot:control:{bot_id}"
        await self._redis.xack(stream_key, _STREAM_GROUP, message_id)

    async def _dispatch_command(self, bot_id: str, payload: dict[str, Any]) -> None:
        cmd = payload.get("cmd")
        if cmd == "START":
            await self._start_bot(bot_id)
        elif cmd == "STOP":
            self._send_to_child(bot_id, {"cmd": "STOP"})
        elif cmd == "PAUSE":
            reason = payload.get("reason", "manual")
            pause_bot_id = bot_id if isinstance(bot_id, UUID) else UUID(str(bot_id))
            await _handle_pause_cmd(pause_bot_id, reason, self._redis, self._db)
            self._send_to_child(bot_id, {"cmd": "PAUSE", "reason": reason})
        elif cmd == "RESUME":
            self._send_to_child(bot_id, {"cmd": "RESUME"})
        elif cmd == "UPDATE_ADVISOR_CONFIG":
            self._send_to_child(
                bot_id,
                {"cmd": "UPDATE_ADVISOR_CONFIG", "config": payload.get("config", {})},
            )
        elif cmd == "DEPLOY":
            self._send_to_child(bot_id, {"cmd": "STOP"})
            await asyncio.sleep(2)
            await self._start_bot(bot_id)

    def _send_to_child(self, bot_id: str, msg: dict[str, Any]) -> None:
        q = self._child_queues.get(bot_id)
        if q is not None:
            try:
                q.put_nowait(msg)
            except Exception:
                logger.warning("bot_child_queue_full", bot_id=bot_id)

    async def _start_bot(self, bot_id: str) -> None:
        stream_key = f"bot:control:{bot_id}"
        try:
            await self._redis.xgroup_create(stream_key, _STREAM_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.warning("bot_xgroup_create_error", bot_id=bot_id, exc=str(exc))
        already_running = bot_id in self._running_bots
        q: multiprocessing.Queue[dict[str, Any]] = multiprocessing.Queue(maxsize=20)
        self._child_queues[bot_id] = q
        p = multiprocessing.Process(target=_child_main, args=(bot_id, q), daemon=True)
        p.start()
        self._running_bots[bot_id] = p
        self._respawn_counts[bot_id] = 0
        metrics.bot_starts_total.labels(bot_id=bot_id, mode="unknown").inc()
        if not already_running:
            metrics.bot_active_count.labels(mode="unknown").inc()

    async def _check_heartbeat(self, bot_id: str) -> None:
        key = f"bot:heartbeat:{bot_id}"
        hb = await self._redis.get(key)
        if hb is not None:
            return
        metrics.bot_heartbeat_failures_total.labels(bot_id=bot_id).inc()
        count = self._respawn_counts.get(bot_id, 0)
        if count >= len(_RESPAWN_DELAYS):
            await self._db.execute(
                text(
                    "UPDATE bots SET status='error',"
                    " error_msg='max_respawn_exceeded' WHERE id = :id"
                ),
                {"id": bot_id},
            )
            await self._db.commit()
            metrics.bot_active_count.labels(mode="unknown").dec()
            self._running_bots.pop(bot_id, None)
            return
        delay = _RESPAWN_DELAYS[count]
        self._respawn_counts[bot_id] = count + 1
        task = asyncio.create_task(self._delayed_respawn(bot_id, delay))
        self._respawn_tasks.add(task)
        task.add_done_callback(self._respawn_tasks.discard)

    async def _delayed_respawn(self, bot_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._respawn_bot(bot_id)

    async def _respawn_bot(self, bot_id: str) -> None:
        metrics.bot_respawn_total.labels(bot_id=bot_id).inc()
        await self._start_bot(bot_id)

    async def run(self) -> None:
        await asyncio.gather(self._command_loop(), self._heartbeat_loop())

    async def _command_loop(self) -> None:
        while True:
            for bot_id in list(self._running_bots.keys()):
                stream_key = f"bot:control:{bot_id}"
                try:
                    messages = await self._redis.xreadgroup(
                        groupname=_STREAM_GROUP,
                        consumername="supervisor-0",
                        streams={stream_key: ">"},
                        count=10,
                        block=100,
                    )
                    for _, entries in messages or []:
                        for msg_id, fields in entries:
                            payload = {k.decode(): v.decode() for k, v in fields.items()}
                            await self._process_command(
                                bot_id=bot_id,
                                message_id=msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
                                payload=json.loads(payload.get("data", "{}")),
                            )
                except Exception:
                    logger.exception("bot_command_loop_error", bot_id=bot_id)
            await asyncio.sleep(0.1)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_POLL)
            for bot_id in list(self._running_bots.keys()):
                await self._check_heartbeat(bot_id)


def _child_main(bot_id: str, control_queue: multiprocessing.Queue[dict[str, Any]]) -> None:
    from app.bot.sandbox import install_denylist

    install_denylist(bot_id=bot_id)
    asyncio.run(_child_async_main(bot_id, control_queue))


async def _child_async_main(
    bot_id: str, control_queue: multiprocessing.Queue[dict[str, Any]]
) -> None:
    import os

    from redis.asyncio import Redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis = Redis.from_url(redis_url, decode_responses=False)
    heartbeat_task = asyncio.create_task(_heartbeat_writer(bot_id, redis))
    try:
        while True:
            await asyncio.sleep(5)
            try:
                msg = control_queue.get_nowait()
                if msg.get("cmd") == "STOP":
                    break
            except Exception:
                pass
    finally:
        heartbeat_task.cancel()
        await redis.aclose()


async def _heartbeat_writer(bot_id: str, redis: Any) -> None:
    while True:
        await redis.setex(f"bot:heartbeat:{bot_id}", 10, "1")
        await asyncio.sleep(5)
