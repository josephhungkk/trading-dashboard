from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
import structlog

from app.core import metrics

log = structlog.get_logger()

_BASE = "https://finnhub.io/api/v1/calendar/earnings"
_HOUR_MAP = {"bmo": "before_open", "amc": "after_close", "dmh": "during_market"}


class FinnhubCalendarPoller:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _parse_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for item in data.get("earningsCalendar") or []:
            ticker = item.get("symbol", "")
            if not ticker:
                continue
            hour = (item.get("hour") or "").lower()
            rows.append(
                {
                    "ticker": ticker,
                    "announced_date": item.get("date"),
                    "time_of_day": _HOUR_MAP.get(hour, "unknown"),
                    "eps_estimate": item.get("epsEstimate"),
                    "eps_actual": item.get("epsActual"),
                    "revenue_estimate": item.get("revenueEstimate"),
                    "revenue_actual": item.get("revenueActual"),
                    "source": "finnhub_api",
                    "source_priority": 1,
                }
            )
        return rows

    async def fetch(self, days_ahead: int = 7) -> list[dict[str, Any]]:
        if not self._api_key:
            log.info("finnhub_poller_disabled", reason="no api_key")
            return []
        today = date.today()
        end = today + timedelta(days=days_ahead)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    _BASE,
                    params={
                        "from": today.isoformat(),
                        "to": end.isoformat(),
                        "token": self._api_key,
                    },
                )
                resp.raise_for_status()
            rows = self._parse_response(resp.json())
            metrics.earnings_events_ingested_total.labels(source="finnhub_api").inc(len(rows))
            return rows
        except Exception:
            metrics.earnings_poll_errors_total.labels(source="finnhub_api").inc()
            log.exception("finnhub_calendar_poll_error")
            return []
