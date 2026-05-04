"""Schwab streamer token-rotation timing tests."""

from __future__ import annotations

import asyncio

import pytest

from sidecar_schwab.streamer import SchwabStreamer


@pytest.mark.asyncio
async def test_token_rotation_triggers_reconnect_within_2s(monkeypatch) -> None:
    tokens_refreshed = asyncio.Event()
    streamer = SchwabStreamer.for_tests(tokens_refreshed)
    reconnect_called = asyncio.Event()
    close_called = False
    replay_called = False

    async def recv_until_reconnect() -> str:
        never = asyncio.Future()
        rotation_task = asyncio.create_task(tokens_refreshed.wait())
        done, pending = await asyncio.wait(
            {never, rotation_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if rotation_task in done:
            return "token_rotation"
        await never
        return "ws_close"

    async def close_ws() -> None:
        nonlocal close_called
        close_called = True

    async def replay_subscriptions() -> None:
        nonlocal replay_called
        replay_called = True

    async def reconnect_with_new_creds() -> bool:
        await streamer._replay_subscriptions()
        reconnect_called.set()
        streamer._shutting_down = True
        return True

    monkeypatch.setattr(streamer, "_recv_until_reconnect", recv_until_reconnect)
    monkeypatch.setattr(streamer, "_close_ws", close_ws)
    monkeypatch.setattr(streamer, "_replay_subscriptions", replay_subscriptions)
    monkeypatch.setattr(
        streamer, "_reconnect_with_new_creds", reconnect_with_new_creds
    )

    reader_task = asyncio.create_task(streamer._reader_loop())
    await asyncio.sleep(0)
    tokens_refreshed.set()
    await asyncio.wait_for(reconnect_called.wait(), timeout=2)
    await reader_task

    assert reconnect_called.is_set()
    assert close_called is True
    assert replay_called is True
    assert not tokens_refreshed.is_set()
