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


def _hash_chat_id(chat_id: int, salt: str) -> str:
    return hmac.new(salt.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()[:16]


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
                return []
        return []

    async def _save_history(self, key: str, history: list[dict[str, str]]) -> None:
        await self._redis.set(key, json.dumps(history[-_MAX_TURNS * 2 :]), ex=_CONV_TTL)

    async def handle(self, msg: Message) -> None:
        chat_id = msg.chat.id
        lock = self._get_lock(chat_id)

        if lock.locked():
            await msg.answer("Previous reply still in progress, please wait.")
            return

        async with lock:
            hash_key = _hash_chat_id(chat_id, self._salt)
            conv_key = f"telegram:chat:{hash_key}"
            history = await self._load_history(conv_key)
            history.append({"role": "user", "content": msg.text or ""})
            try:
                result = await self._ai.complete(
                    capability=_AI_CAPABILITY,
                    messages=history,
                )
                reply = result.content
                history.append({"role": "assistant", "content": reply})
                await self._save_history(conv_key, history)
                await msg.answer(reply)
            except Exception:
                log.exception("telegram.chat_ai_failed")
                await msg.answer("AI unavailable, try again later.")
