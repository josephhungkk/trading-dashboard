from __future__ import annotations

import json
from typing import Any

import structlog

from app.core import metrics

log = structlog.get_logger()

_QUICK_PROMPT = (
    "{symbol} scanner match. Indicators: {indicators}.\n"
    "Summarise in one sentence why this is a notable setup."
)
_DEEP_PROMPT_NO_FILINGS = (
    "{symbol} scanner match. Indicators: {indicators}.\n"
    "Provide a 3-5 sentence analysis of the technical setup. "
    "Be specific about the indicator readings."
)
_DEEP_PROMPT_WITH_FILINGS = (
    "{symbol} scanner match. Indicators: {indicators}.\n"
    "Recent filings context: {filings}.\n"
    "Provide a 3-5 sentence analysis combining the technical setup "
    "and fundamental context."
)


async def generate_commentary(
    *,
    symbol: str,
    indicator_snapshot: dict[str, Any],
    depth: str,
    ai_client: Any,
    recent_filings: list[str] | None = None,
) -> str | None:
    try:
        indicators_json = json.dumps(indicator_snapshot, default=str)
        if depth == "quick":
            prompt = _QUICK_PROMPT.format(symbol=symbol, indicators=indicators_json)
            capability = "LOCAL_ONLY"
        elif recent_filings:
            filings_text = "; ".join(recent_filings[:3])
            prompt = _DEEP_PROMPT_WITH_FILINGS.format(
                symbol=symbol, indicators=indicators_json, filings=filings_text
            )
            capability = "REASONING"
        else:
            prompt = _DEEP_PROMPT_NO_FILINGS.format(symbol=symbol, indicators=indicators_json)
            capability = "REASONING"

        result = await ai_client.complete(
            capability=capability,
            messages=[{"role": "user", "content": prompt}],
        )
        metrics.scanner_llm_commentary_total.labels(depth=depth, status="ok").inc()
        return result.content
    except Exception:
        log.warning("scanner.commentary.error", symbol=symbol, depth=depth)
        metrics.scanner_llm_commentary_total.labels(depth=depth, status="error").inc()
        return None
