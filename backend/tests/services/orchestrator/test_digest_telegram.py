from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.orchestrator.digest_telegram import send_digest


@pytest.mark.asyncio
async def test_send_digest_sorted_by_sharpe() -> None:
    telegram = AsyncMock()
    bot_stats = [
        {
            "bot_id": "1",
            "bot_name": "Bot C",
            "sharpe_30d": 0.5,
            "sharpe_7d": None,
            "max_drawdown": None,
            "win_rate": None,
        },
        {
            "bot_id": "2",
            "bot_name": "Bot A",
            "sharpe_30d": 1.5,
            "sharpe_7d": None,
            "max_drawdown": None,
            "win_rate": None,
        },
        {
            "bot_id": "3",
            "bot_name": "Bot B",
            "sharpe_30d": 1.0,
            "sharpe_7d": None,
            "max_drawdown": None,
            "win_rate": None,
        },
    ]
    await send_digest(telegram, bot_stats)
    telegram.send.assert_called_once()
    message = telegram.send.call_args[0][0]
    lines = [ln for ln in message.split("\n") if any(n in ln for n in ("Bot A", "Bot B", "Bot C"))]
    assert len(lines) == 3
    assert "Bot A" in lines[0]
    assert "Bot B" in lines[1]
    assert "Bot C" in lines[2]


@pytest.mark.asyncio
async def test_trend_badge_improving() -> None:
    telegram = AsyncMock()
    bot_stats = [
        {
            "bot_id": "1",
            "bot_name": "Bot A",
            "sharpe_30d": 1.0,
            "sharpe_7d": 1.5,
            "max_drawdown": None,
            "win_rate": None,
        }
    ]
    await send_digest(telegram, bot_stats)
    message = telegram.send.call_args[0][0]
    assert "▲" in message


@pytest.mark.asyncio
async def test_trend_badge_degrading() -> None:
    telegram = AsyncMock()
    bot_stats = [
        {
            "bot_id": "1",
            "bot_name": "Bot A",
            "sharpe_30d": 1.0,
            "sharpe_7d": 0.7,
            "max_drawdown": None,
            "win_rate": None,
        }
    ]
    await send_digest(telegram, bot_stats)
    message = telegram.send.call_args[0][0]
    assert "▼" in message


@pytest.mark.asyncio
async def test_trend_badge_stable() -> None:
    telegram = AsyncMock()
    bot_stats = [
        {
            "bot_id": "1",
            "bot_name": "Bot A",
            "sharpe_30d": 1.0,
            "sharpe_7d": 1.02,
            "max_drawdown": None,
            "win_rate": None,
        }
    ]
    await send_digest(telegram, bot_stats)
    message = telegram.send.call_args[0][0]
    assert "—" in message


@pytest.mark.asyncio
async def test_underperformer_flag() -> None:
    telegram = AsyncMock()
    bot_stats = [
        {
            "bot_id": "1",
            "bot_name": "Bot A",
            "sharpe_30d": -0.2,
            "sharpe_7d": None,
            "max_drawdown": None,
            "win_rate": None,
        }
    ]
    await send_digest(telegram, bot_stats, underperform_threshold=0.0)
    message = telegram.send.call_args[0][0]
    assert "⚠" in message


@pytest.mark.asyncio
async def test_kill_switch_none_telegram() -> None:
    await send_digest(
        None,
        [
            {
                "bot_id": "1",
                "bot_name": "Bot A",
                "sharpe_30d": 1.0,
                "sharpe_7d": None,
                "max_drawdown": None,
                "win_rate": None,
            }
        ],
    )


@pytest.mark.asyncio
async def test_none_sharpes_treated_as_neg_inf() -> None:
    telegram = AsyncMock()
    bot_stats = [
        {
            "bot_id": "1",
            "bot_name": "Bot None",
            "sharpe_30d": None,
            "sharpe_7d": None,
            "max_drawdown": None,
            "win_rate": None,
        },
        {
            "bot_id": "2",
            "bot_name": "Bot Good",
            "sharpe_30d": 1.0,
            "sharpe_7d": None,
            "max_drawdown": None,
            "win_rate": None,
        },
    ]
    await send_digest(telegram, bot_stats)
    message = telegram.send.call_args[0][0]
    lines = [ln for ln in message.split("\n") if "Bot Good" in ln or "Bot None" in ln]
    assert "Bot Good" in lines[0]
    assert "Bot None" in lines[1]
