"""Free-form message → AI router (REASONING capability), per-chat asyncio lock."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import structlog
from aiogram.types import Message

log = structlog.get_logger(__name__)

_MAX_TURNS = 20
_CONV_TTL = 86400  # 24h
_AI_CAPABILITY = "REASONING"
_MAX_MESSAGE_LEN = 2000
_MAX_REPLY_LEN = 4096  # Telegram hard limit per message


def _hash_chat_id(chat_id: int, salt: str) -> str:
    return hmac.new(salt.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()


class TelegramChat:
    def __init__(self, *, ai_client: Any, redis: Any, chat_id_hash_salt: str) -> None:
        self._ai = ai_client
        self._redis = redis
        self._salt = chat_id_hash_salt
        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def _load_history(self, key: str) -> list[dict[str, str]]:
        raw = await self._redis.get(key)
        if raw:
            try:
                return json.loads(raw)  # type: ignore[no-any-return]
            except Exception:
                log.warning("telegram.chat_history_corrupted", key=key)
                return []
        return []

    async def _save_history(self, key: str, history: list[dict[str, str]]) -> None:
        await self._redis.set(key, json.dumps(history[-_MAX_TURNS * 2 :]), ex=_CONV_TTL)

    async def handle(self, msg: Message) -> None:
        chat_id = msg.chat.id
        lock = self._get_lock(chat_id)

        # Non-blocking acquire: if already held by another in-flight task, reply and bail.
        # asyncio.Lock has no blocking=False, so use wait_for with a very short timeout.
        # In the single-threaded event loop, if the lock is free, acquire() returns
        # synchronously without yielding, so timeout=0.0 never fires on a free lock.
        try:
            await asyncio.wait_for(asyncio.shield(lock.acquire()), timeout=0.001)
        except TimeoutError:
            await msg.answer("Previous reply still in progress, please wait.")
            return

        try:
            hash_key = _hash_chat_id(chat_id, self._salt)
            conv_key = f"telegram:chat:{hash_key}"
            history = await self._load_history(conv_key)
            content = (msg.text or "")[:_MAX_MESSAGE_LEN]
            history.append({"role": "user", "content": content})
            try:
                result = await self._ai.complete(
                    capability=_AI_CAPABILITY,
                    messages=history,
                )
                reply = str(result.content)[:_MAX_REPLY_LEN]
                history.append({"role": "assistant", "content": reply})
                await self._save_history(conv_key, history)
                await msg.answer(reply)
            except Exception:
                log.exception("telegram.chat_ai_failed")
                await msg.answer("AI unavailable, try again later.")
        finally:
            lock.release()
            # Evict the lock once no waiter holds it (safe in single-threaded asyncio).
            if not lock.locked():
                self._locks.pop(chat_id, None)
