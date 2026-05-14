from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_db


def _make_message(text: str, chat_id: int = 111) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_chat_calls_ai_with_reasoning_capability() -> None:
    from app.services.telegram.chat import TelegramChat

    mock_ai = AsyncMock()
    mock_ai.complete = AsyncMock(return_value=MagicMock(content="Hello!"))
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    msg = _make_message("Hi there")
    await chat.handle(msg)

    call_kwargs = mock_ai.complete.call_args.kwargs
    assert call_kwargs.get("capability") == "REASONING" or "REASONING" in str(call_kwargs)


@pytest.mark.asyncio
async def test_chat_appends_to_redis_history() -> None:
    from app.services.telegram.chat import TelegramChat

    mock_ai = AsyncMock()
    mock_ai.complete = AsyncMock(return_value=MagicMock(content="Hi!"))
    stored: dict[str, str] = {}
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    async def mock_set(key: str, val: str, ex: int = 0) -> None:
        stored[key] = val

    mock_redis.set = mock_set
    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    msg = _make_message("What is 2+2?", chat_id=111)
    await chat.handle(msg)

    assert len(stored) == 1
    history = json.loads(next(iter(stored.values())))
    assert any(m["role"] == "user" for m in history)
    assert any(m["role"] == "assistant" for m in history)


@pytest.mark.asyncio
async def test_chat_second_message_while_in_flight_returns_busy() -> None:
    from app.services.telegram.chat import TelegramChat

    lock = asyncio.Lock()
    await lock.acquire()

    mock_ai = AsyncMock()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    chat._locks[111] = lock

    msg = _make_message("second message", chat_id=111)
    await chat.handle(msg)
    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args.args[0].lower()
    assert "previous reply" in reply or "in progress" in reply
    lock.release()


@pytest.mark.asyncio
async def test_chat_ai_unavailable_graceful_reply() -> None:
    from app.services.telegram.chat import TelegramChat

    mock_ai = AsyncMock()
    mock_ai.complete = AsyncMock(side_effect=Exception("AI down"))
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    chat = TelegramChat(ai_client=mock_ai, redis=mock_redis, chat_id_hash_salt="salt")
    msg = _make_message("hello", chat_id=111)
    await chat.handle(msg)
    msg.answer.assert_awaited()
    assert "unavailable" in msg.answer.call_args.args[0].lower()
