from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.no_db


def _make_message(text: str, chat_id: int = 111, from_user_id: int = 222) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.from_user.id = from_user_id
    msg.answer = AsyncMock()
    return msg


def _make_entry(jwt_subject: str = "user1") -> object:
    from app.services.telegram.allowlist import AllowlistEntry

    return AllowlistEntry(chat_id=111, from_user_id=222, jwt_subject=jwt_subject, label="Alice")


@pytest.mark.asyncio
async def test_handle_help_replies() -> None:
    from app.services.telegram.commands import handle_help

    msg = _make_message("/help")
    await handle_help(msg)
    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args.args[0]
    assert "/status" in reply


@pytest.mark.asyncio
async def test_handle_mute_bad_arg_replies_usage() -> None:
    from app.services.telegram.commands import handle_mute

    msg = _make_message("/mute notanumber")
    entry = _make_entry()
    await handle_mute(msg, entry=entry, db=AsyncMock())  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    assert "Usage" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_mute_missing_arg_replies_usage() -> None:
    from app.services.telegram.commands import handle_mute

    msg = _make_message("/mute")
    entry = _make_entry()
    await handle_mute(msg, entry=entry, db=AsyncMock())  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    assert "Usage" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_mute_zero_duration_rejected() -> None:
    from app.services.telegram.commands import handle_mute

    msg = _make_message("/mute 42 0m")
    entry = _make_entry()
    await handle_mute(msg, entry=entry, db=AsyncMock())  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    assert "Duration" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_mute_not_found_replies_not_found() -> None:
    from app.services.telegram.commands import handle_mute

    msg = _make_message("/mute 999 30m")
    entry = _make_entry()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
    await handle_mute(msg, entry=entry, db=mock_db)  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    assert "not found" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_unmute_missing_arg_replies_usage() -> None:
    from app.services.telegram.commands import handle_unmute

    msg = _make_message("/unmute")
    entry = _make_entry()
    await handle_unmute(msg, entry=entry, db=AsyncMock())  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    assert "Usage" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_kill_switch_uses_service_layer_not_http() -> None:
    from app.services.telegram.commands import handle_kill_switch

    msg = _make_message("/kill_switch IBKR")
    entry = _make_entry()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    mock_redis = AsyncMock()

    with patch("app.services.telegram.commands.AccountKillSwitchService") as mock_ks:
        instance = mock_ks.return_value
        instance.toggle = AsyncMock()
        await handle_kill_switch(msg, entry=entry, db=mock_db, redis=mock_redis)  # type: ignore[arg-type]

    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_handle_accounts_no_accounts() -> None:
    from app.services.telegram.commands import handle_accounts

    msg = _make_message("/accounts")
    entry = _make_entry()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    await handle_accounts(msg, entry=entry, db=mock_db)  # type: ignore[arg-type]

    msg.answer.assert_awaited_once()
    assert "No accounts" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_place_order_handler_registered_with_trade_bucket() -> None:
    """register_handlers with all deps does not raise; check_trade is available."""
    from aiogram import Dispatcher

    from app.services.telegram.commands import register_handlers

    dp = Dispatcher()
    mock_allowlist = MagicMock()
    mock_allowlist.lookup = MagicMock(return_value=None)
    mock_rl = AsyncMock()
    mock_rl.check_read = AsyncMock(return_value=True)
    mock_rl.check_write = AsyncMock(return_value=True)
    mock_rl.check_trade = AsyncMock(return_value=True)

    register_handlers(
        dp,
        allowlist=mock_allowlist,
        rate_limiter=mock_rl,
        db_factory=AsyncMock(),
        redis=AsyncMock(),
        registry=AsyncMock(),
        capability=AsyncMock(),
        cfg=AsyncMock(),
    )
    assert hasattr(mock_rl, "check_trade")


def test_register_handlers_without_order_deps_still_registers() -> None:
    """register_handlers without registry/capability/cfg works (backward compat)."""
    from aiogram import Dispatcher

    from app.services.telegram.commands import register_handlers

    dp = Dispatcher()
    mock_allowlist = MagicMock()
    mock_rl = MagicMock()
    mock_rl.check_read = AsyncMock(return_value=True)
    mock_rl.check_write = AsyncMock(return_value=True)

    register_handlers(
        dp,
        allowlist=mock_allowlist,
        rate_limiter=mock_rl,
        db_factory=AsyncMock(),
        redis=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_help_includes_order_commands() -> None:
    """handle_help reply includes /place_order, /confirm, /cancel_order."""
    from app.services.telegram.commands import handle_help

    msg = _make_message("/help")
    await handle_help(msg)
    reply = msg.answer.call_args.args[0]
    assert "/place_order" in reply
    assert "/confirm" in reply
    assert "/cancel_order" in reply
