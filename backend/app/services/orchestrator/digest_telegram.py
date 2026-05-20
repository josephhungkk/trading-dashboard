from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()


async def send_digest(
    telegram: Any,
    bot_stats: list[dict],
    underperform_threshold: float = 0.0,
) -> None:
    if telegram is None:
        return

    def _sharpe_key(bot: dict) -> float:
        v = bot.get("sharpe_30d")
        return float(v) if v is not None else float("-inf")

    sorted_bots = sorted(bot_stats, key=_sharpe_key, reverse=True)

    lines = [
        "Bot Health Digest",
        f"{'Rank':<6}{'Bot':<14}{'Sharpe':>7}{'Drawdown':>10}{'WinRate':>9}{'Trend':>7}",
    ]

    for rank, bot in enumerate(sorted_bots, start=1):
        bot_name = str(bot.get("bot_name", "Unknown"))
        sharpe_30d = bot.get("sharpe_30d")
        sharpe_7d = bot.get("sharpe_7d")
        max_drawdown = bot.get("max_drawdown")
        win_rate = bot.get("win_rate")

        sharpe_str = f"{sharpe_30d:.2f}" if sharpe_30d is not None else "N/A"
        drawdown_str = f"{max_drawdown:.2f}" if max_drawdown is not None else "N/A"
        win_rate_str = f"{win_rate:.2f}" if win_rate is not None else "N/A"

        badge = "—"
        if sharpe_7d is not None and sharpe_30d is not None:
            if sharpe_7d > sharpe_30d * 1.05:
                badge = "▲"
            elif sharpe_7d < sharpe_30d * 0.95:
                badge = "▼"

        underperforming = sharpe_30d is not None and sharpe_30d < underperform_threshold
        rank_str = f"{'⚠ ' if underperforming else ''}{rank}"

        lines.append(
            f"{rank_str:<6}{bot_name:<14}{sharpe_str:>7}{drawdown_str:>10}{win_rate_str:>9}{badge:>7}"
        )

    message = "\n".join(lines)
    await telegram.send(message)
    log.info("digest_telegram_sent", n_bots=len(bot_stats))
