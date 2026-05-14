from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

log = structlog.get_logger(__name__)

_PUBSUB_RETRY_DELAY = 5.0
_ALLOWLIST_CHANNEL = "app_config:invalidate:telegram_allowlist"


class _ConfigLike(Protocol):
    def get_json(self, ns: str, key: str, *, default: object = None) -> object: ...


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    chat_id: int
    from_user_id: int
    jwt_subject: str
    label: str


class AllowlistService:
    def __init__(self, config: _ConfigLike) -> None:
        self._config = config
        self._by_key: dict[tuple[int, int], AllowlistEntry] = {}

    async def load(self) -> list[AllowlistEntry]:
        raw = self._config.get_json("telegram_allowlist", "entries", default=[])
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, list):
            return []
        entries: list[AllowlistEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(
                    AllowlistEntry(
                        chat_id=int(item["chat_id"]),
                        from_user_id=int(item["from_user_id"]),
                        jwt_subject=str(item["jwt_subject"]),
                        label=str(item["label"]),
                    )
                )
            except KeyError, ValueError, TypeError:
                log.warning("telegram.allowlist_entry_malformed", item=item)
        return entries

    async def refresh(self) -> None:
        entries = await self.load()
        self._by_key = {(entry.chat_id, entry.from_user_id): entry for entry in entries}

    def lookup(self, chat_id: int, from_user_id: int) -> AllowlistEntry | None:
        return self._by_key.get((chat_id, from_user_id))

    def all_chat_ids(self) -> list[int]:
        return sorted({chat_id for chat_id, _from_user_id in self._by_key})

    async def run_pubsub_listener(self, redis: AsyncRedis) -> None:
        while True:
            pubsub = redis.pubsub()
            try:
                await pubsub.subscribe(_ALLOWLIST_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        await asyncio.sleep(0)
                        continue
                    try:
                        await self.refresh()
                    except Exception:
                        log.exception("telegram.allowlist_refresh_failed")
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                await pubsub.unsubscribe(_ALLOWLIST_CHANNEL)
                raise
            except Exception:
                log.exception(
                    "telegram.allowlist_pubsub_crashed",
                    retry_in=_PUBSUB_RETRY_DELAY,
                )
                await pubsub.unsubscribe(_ALLOWLIST_CHANNEL)
                await asyncio.sleep(_PUBSUB_RETRY_DELAY)
