from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.telegram.allowlist import AllowlistEntry, AllowlistService

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_allowlist_load_returns_entries() -> None:
    config = AsyncMock()
    config.get_json = AsyncMock(
        return_value=[
            {
                "chat_id": 123,
                "from_user_id": 456,
                "jwt_subject": "user@example.test",
                "label": "primary",
            }
        ]
    )
    service = AllowlistService(config=config)

    assert await service.load() == [
        AllowlistEntry(
            chat_id=123,
            from_user_id=456,
            jwt_subject="user@example.test",
            label="primary",
        )
    ]


@pytest.mark.asyncio
async def test_allowlist_lookup_known_chat() -> None:
    config = AsyncMock()
    config.get_json = AsyncMock(
        return_value=[
            {
                "chat_id": 123,
                "from_user_id": 456,
                "jwt_subject": "user@example.test",
                "label": "primary",
            }
        ]
    )
    service = AllowlistService(config=config)

    await service.refresh()

    assert service.lookup(123, 456) == AllowlistEntry(
        chat_id=123,
        from_user_id=456,
        jwt_subject="user@example.test",
        label="primary",
    )


@pytest.mark.asyncio
async def test_allowlist_lookup_unknown_returns_none() -> None:
    config = AsyncMock()
    config.get_json = AsyncMock(return_value=[])
    service = AllowlistService(config=config)

    await service.refresh()

    assert service.lookup(123, 456) is None
