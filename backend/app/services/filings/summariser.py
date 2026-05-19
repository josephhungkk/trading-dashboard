from __future__ import annotations

import time
from typing import Any

import structlog

from app.core import metrics

log = structlog.get_logger()

_LONG_CONTEXT_THRESHOLD = 4096


async def summarise_filing(
    *,
    title: str,
    raw_text: str,
    source: str,
    ai_client: Any,
) -> str | None:
    """Generate a concise 3-5 sentence summary of the filing. Returns None on failure."""
    depth_start = time.monotonic()
    capability = "LONG_CONTEXT" if len(raw_text) > _LONG_CONTEXT_THRESHOLD else "LOCAL_ONLY"
    prompt = (
        f"Filing title: {title}\n\n"
        f"Content:\n{raw_text[:16000]}\n\n"
        "Summarise this filing in 3-5 sentences. Focus on material facts, "
        "numbers, and what this means for investors. Be concise and factual."
    )
    try:
        result = await ai_client.complete(
            capability=capability,
            messages=[{"role": "user", "content": prompt}],
        )
        metrics.filings_summarisation_total.labels(source=source, status="ok").inc()
        metrics.filings_llm_latency_seconds.labels(source=source).observe(
            time.monotonic() - depth_start
        )
        content = getattr(result, "content", None)
        return str(content) if content is not None else None
    except Exception:
        log.exception("filings.summariser.error", title=title, source=source)
        metrics.filings_summarisation_total.labels(source=source, status="error").inc()
        return None
