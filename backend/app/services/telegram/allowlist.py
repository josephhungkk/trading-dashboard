from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    chat_id: int
    from_user_id: int
    jwt_subject: str
    label: str


class AllowlistService:
    def __init__(self, config: Any) -> None:
        self._config = config
        self._by_key: dict[tuple[int, int], AllowlistEntry] = {}

    async def load(self) -> list[AllowlistEntry]:
        raw = self._config.get_json("telegram_allowlist", "entries", default=[])
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, list):
            return []
        return [
            AllowlistEntry(
                chat_id=int(item["chat_id"]),
                from_user_id=int(item["from_user_id"]),
                jwt_subject=str(item["jwt_subject"]),
                label=str(item["label"]),
            )
            for item in raw
            if isinstance(item, dict)
        ]

    async def refresh(self) -> None:
        entries = await self.load()
        self._by_key = {(entry.chat_id, entry.from_user_id): entry for entry in entries}

    def lookup(self, chat_id: int, from_user_id: int) -> AllowlistEntry | None:
        return self._by_key.get((chat_id, from_user_id))

    def all_chat_ids(self) -> list[int]:
        return sorted({chat_id for chat_id, _from_user_id in self._by_key})

    async def run_pubsub_listener(self, redis: Any) -> None:
        pubsub = redis.pubsub()
        channel = "app_config:invalidate:telegram_allowlist"
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    await asyncio.sleep(0)
                    continue
                await self.refresh()
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe(channel)
