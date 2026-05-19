from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_MAX_TOKENS = 4000
_ROLE_TAG_RE = re.compile(r"<\|?(system|user|assistant)\|?>", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MAX_FREE_TEXT = 200


def _sanitise(s: str) -> str:
    s = _ROLE_TAG_RE.sub("", s)
    s = _CODE_FENCE_RE.sub("[code]", s)
    return s[:_MAX_FREE_TEXT]


class TunerContextBuilder:
    async def build(
        self,
        bot_id: UUID,
        bot_row: dict[str, Any],
        db: AsyncSession,
    ) -> tuple[str, int]:
        """Return (fenced_payload, token_estimate). All reads in one transaction."""
        runs_result = await db.execute(
            text("""
                SELECT kpi_sharpe, kpi_mar, kpi_max_dd, kpi_win_rate,
                       kpi_avg_trade_pnl, total_orders, started_at, stopped_at
                FROM bot_runs
                WHERE bot_id=:bid AND status='stopped'
                ORDER BY started_at DESC LIMIT 10
            """),
            {"bid": str(bot_id)},
        )
        runs = [dict(r._mapping) for r in runs_result]

        orders_result = await db.execute(
            text("""
                SELECT side, qty, fill_price
                FROM bot_orders
                WHERE bot_id=:bid
                ORDER BY placed_at DESC LIMIT 100
            """),
            {"bid": str(bot_id)},
        )
        orders = [dict(r._mapping) for r in orders_result]

        advisor_result = await db.execute(
            text("""
                SELECT verdict, advice_tags
                FROM bot_advisor_decisions
                WHERE bot_id=:bid
                ORDER BY created_at DESC LIMIT 50
            """),
            {"bid": str(bot_id)},
        )
        advisor_rows = [dict(r._mapping) for r in advisor_result]
        verdict_counts: dict[str, int] = {}
        tag_freq: dict[str, int] = {}
        for row in advisor_rows:
            verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
            for tag in row["advice_tags"] or []:
                tag_freq[tag] = tag_freq.get(tag, 0) + 1
        top_tags = sorted(tag_freq, key=lambda t: -tag_freq[t])[:5]

        payload_data = {
            "strategy_params": bot_row.get("strategy_params", {}),
            "strategy_schema": bot_row.get("strategy_schema", {}),
            "recent_runs": runs,
            "order_summary": {
                "total_sampled": len(orders),
                "sides": {
                    "buy": sum(1 for o in orders if o["side"] == "buy"),
                    "sell": sum(1 for o in orders if o["side"] == "sell"),
                },
            },
            "advisor_summary": {
                "verdict_counts": verdict_counts,
                "top_advice_tags": top_tags,
            },
        }

        payload_json = json.dumps(payload_data, default=str)
        token_estimate = len(payload_json) // 4

        fenced = f"<<BEGIN_TUNER_CONTEXT>>\n{payload_json}\n<<END_TUNER_CONTEXT>>"
        return fenced, min(token_estimate, _MAX_TOKENS)
