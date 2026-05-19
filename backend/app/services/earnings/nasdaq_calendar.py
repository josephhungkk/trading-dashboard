from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import structlog

from app.core import metrics

log = structlog.get_logger()

_BASE = "https://api.nasdaq.com/api/calendar/earnings"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingDashboard/1.0)",
    "Accept": "application/json",
}
_TIME_MAP = {"BMO": "before_open", "AMC": "after_close", "DMT": "during_market"}


class NasdaqCalendarPoller:
    def _parse_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for item in (data.get("data") or {}).get("rows") or []:
            ticker = item.get("symbol", "")
            if not ticker:
                continue
            time_raw = (item.get("time") or "").upper()
            rows.append(
                {
                    "ticker": ticker,
                    "announced_date": item.get("earningsDate"),
                    "time_of_day": _TIME_MAP.get(time_raw, "unknown"),
                    "eps_estimate": item.get("epsForecast"),
                    "source": "nasdaq_api",
                    "source_priority": 2,
                }
            )
        return rows

    async def fetch(self, days_ahead: int = 7) -> list[dict[str, Any]]:
        today = date.today()
        try:
            async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
                resp = await client.get(_BASE, params={"date": today.isoformat(), "limit": 500})
                resp.raise_for_status()
            rows = self._parse_response(resp.json())
            metrics.earnings_events_ingested_total.labels(source="nasdaq_api").inc(len(rows))
            return rows
        except Exception:
            metrics.earnings_poll_errors_total.labels(source="nasdaq_api").inc()
            log.exception("nasdaq_calendar_poll_error")
            return []
