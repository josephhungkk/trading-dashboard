from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)


class AdvisorTelegramNotifier:
    def __init__(self, redis: Any, telegram_client: Any) -> None:
        self._redis = redis
        self._telegram_client = telegram_client
        self._db_factory: Any = None

    async def _get_chat_ids(self) -> list[str]:
        if self._db_factory is None:
            return []
        try:
            async with self._db_factory() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT value FROM app_config"
                            " WHERE key='telegram/advisor_veto_chat_ids'"
                        )
                    )
                ).one_or_none()
                if row is None:
                    return []
                raw = row[0]
                if isinstance(raw, list):
                    return [str(c) for c in raw]
                if isinstance(raw, str):
                    return [raw] if raw else []
        except Exception:
            logger.warning("advisor_notify.chat_ids_fetch_failed")
        return []

    async def run(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("bot:advisor:*")

        while True:
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    try:
                        frame = json.loads(message["data"])
                    except json.JSONDecodeError, TypeError:
                        continue
                    if frame.get("verdict") != "VETO":
                        continue

                    channel = message.get("channel", b"")
                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8", errors="replace")
                    bot_id = channel.split(":")[-1]

                    reasoning = str(frame.get("reasoning", ""))[:200]
                    msg_text = (
                        f"Advisor VETO for bot {bot_id}: "
                        f"{frame.get('canonical_id', '')} "
                        f"{frame.get('side', '')} {frame.get('qty', '')} "
                        f"— {reasoning}"
                    )

                    chat_ids = await self._get_chat_ids()
                    for chat_id in chat_ids:
                        try:
                            await self._telegram_client.send_message(chat_id, msg_text)
                        except Exception:
                            logger.error("advisor_notify.send_failed", chat_id=chat_id)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("advisor_notify.loop_error")
                await asyncio.sleep(1)

    async def _run_with_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory
        await self.run()

    async def start(self, db_factory: Any) -> asyncio.Task:
        return asyncio.create_task(self._run_with_db(db_factory))
