from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.advisor.types import ContextSummary, OrderIntent

logger = structlog.get_logger(__name__)

_MAX_BARS = 50
_MAX_FILLS = 10
_MAX_RISK_DECISIONS = 5
_MAX_TEXT_CHARS = 200
_ROLE_TAG_RE = re.compile(r"</?(?:system|user|assistant|tool)>", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```|~~~")


def _sanitise_text(s: str) -> str:
    """Collapse double newlines, strip code fences, cap at 200 chars, redact role tokens."""
    s = re.sub(r"\n{2,}", "\n", s)
    s = _CODE_FENCE_RE.sub("", s)
    s = s[:_MAX_TEXT_CHARS]
    s = _ROLE_TAG_RE.sub("[redacted_role_tag]", s)
    return s


def _sanitise_dict(d: dict, text_keys: tuple[str, ...]) -> dict:
    result = dict(d)
    for k in text_keys:
        if k in result and isinstance(result[k], str):
            result[k] = _sanitise_text(result[k])
    return result


class ContextBuilder:
    @staticmethod
    async def build(
        intent: OrderIntent,
        strategy_params: dict,
        db: AsyncSession,
    ) -> tuple[str, ContextSummary]:
        """Build JSON context payload + compact ContextSummary. Pure DB reads."""
        account_id = str(intent.account_id)
        canonical_id = intent.canonical_id

        bars_result = await db.execute(
            text(
                "SELECT ts, open, high, low, close, volume "
                "FROM bars_1m WHERE canonical_id = :cid "
                "ORDER BY ts DESC LIMIT :lim"
            ),
            {"cid": canonical_id, "lim": _MAX_BARS},
        )
        bars = [dict(r._mapping) for r in bars_result]

        pos_result = await db.execute(
            text(
                "SELECT canonical_id, position, avg_cost, market_value_base "
                "FROM positions WHERE account_id = :aid"
            ),
            {"aid": account_id},
        )
        positions = [dict(r._mapping) for r in pos_result]

        fills_result = await db.execute(
            text(
                "SELECT o.canonical_id, o.side, o.qty, f.fill_price, f.filled_at "
                "FROM order_fills f JOIN orders o ON o.id = f.order_id "
                "WHERE o.account_id = :aid ORDER BY f.filled_at DESC LIMIT :lim"
            ),
            {"aid": account_id, "lim": _MAX_FILLS},
        )
        fills = [dict(r._mapping) for r in fills_result]

        rd_result = await db.execute(
            text(
                "SELECT check_name, verdict, reasoning, created_at "
                "FROM risk_decisions WHERE account_id = :aid "
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            {"aid": account_id, "lim": _MAX_RISK_DECISIONS},
        )
        risk_decisions = [
            _sanitise_dict(dict(r._mapping), ("reasoning", "check_name")) for r in rd_result
        ]

        rl_result = await db.execute(
            text(
                "SELECT kind, numeric_value, string_value "
                "FROM risk_limits WHERE (account_id = :aid OR account_id IS NULL) "
                "AND deleted_at IS NULL"
            ),
            {"aid": account_id},
        )
        risk_limits = [dict(r._mapping) for r in rl_result]

        pnl_result = await db.execute(
            text(
                "SELECT pnl_realised_usd, pnl_unrealised_usd "
                "FROM pnl_intraday WHERE account_id = :aid"
            ),
            {"aid": account_id},
        )
        pnl_row = pnl_result.fetchone()
        pnl_intraday: dict[str, Any] = dict(pnl_row._mapping) if pnl_row else {}

        ks_result = await db.execute(
            text("SELECT account_id, active FROM kill_switches WHERE account_id = :aid"),
            {"aid": account_id},
        )
        kill_switches = [dict(r._mapping) for r in ks_result]

        payload = {
            "intent": intent.model_dump(mode="json"),
            "bars": bars[:_MAX_BARS],
            "open_positions": positions,
            "recent_fills": fills[:_MAX_FILLS],
            "strategy_params": strategy_params,
            "risk_decisions_recent": risk_decisions,
            "risk_limits": risk_limits,
            "pnl_intraday": pnl_intraday,
            "kill_switches": kill_switches,
        }
        payload_str = json.dumps(payload, default=str)

        params_hash = hashlib.sha256(
            json.dumps(strategy_params, sort_keys=True).encode()
        ).hexdigest()[:16]
        token_estimate = len(payload_str) // 4

        summary = ContextSummary(
            bar_count=len(bars[:_MAX_BARS]),
            position_count=len(positions),
            recent_fill_count=len(fills[:_MAX_FILLS]),
            risk_decision_count=len(risk_decisions),
            params_hash=params_hash,
            payload_token_estimate=token_estimate,
        )
        return payload_str, summary
