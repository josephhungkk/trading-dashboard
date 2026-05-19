from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
import structlog

from app.core import metrics
from app.services.filings.instrument_linker import InstrumentLinker

log = structlog.get_logger()

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_FORMS = ["8-K", "10-K", "10-Q"]
_PAGE_SIZE = 20


class SecEdgarPoller:
    """Polls SEC EDGAR EFTS for recent US filings."""

    def __init__(
        self,
        *,
        db_factory: Any,
        edgar_client: Any,
        ai_client: Any = None,
    ) -> None:
        self._db_factory = db_factory
        self._edgar_client = edgar_client
        self._ai_client = ai_client

    async def poll(self) -> None:
        for form_type in _FORMS:
            try:
                await self._poll_form(form_type)
            except Exception:
                metrics.filings_poll_errors_total.labels(source="sec_edgar").inc()
                log.exception("sec_edgar.poll_error", form_type=form_type)

    async def _poll_form(self, form_type: str) -> None:
        async with self._db_factory() as db:
            cursor_row = await db.execute(
                sa.text("SELECT last_cursor FROM filing_feed_cursors WHERE source = 'sec_edgar'")
            )
            cr = cursor_row.fetchone()
            last_cursor = cr.last_cursor if cr else None

            params: dict[str, Any] = {
                "q": f'"{form_type}"',
                "dateRange": "custom",
                "startdt": "2020-01-01",
                "forms": form_type,
                "from": "0",
                "size": str(_PAGE_SIZE),
                "_source": (
                    "file_date,form_type,entity_name,file_num,period_of_report,biz_location,tickers"
                ),
                "hits.hits.total.value": "true",
                "hits.hits._source.period_of_report": "true",
            }
            if last_cursor:
                params["search_after"] = last_cursor

            resp = await self._edgar_client.get(_EFTS_URL, params=params)
            if resp.status_code != 200:
                log.warning("sec_edgar.bad_response", status=resp.status_code, form=form_type)
                return

            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                return

            linker = InstrumentLinker(db)
            new_cursor = None

            for hit in hits:
                src = hit.get("_source", {})
                sort_val = hit.get("sort")
                if sort_val:
                    new_cursor = json.dumps(sort_val)

                ticker = src.get("tickers", [None])[0] if src.get("tickers") else None
                cik = hit.get("_id", "").split(":", 1)[0] if ":" in hit.get("_id", "") else None
                instrument_id, canonical_id = await linker.link(
                    source="sec_edgar",
                    ticker=ticker,
                    cik=cik,
                )

                filing_url = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&CIK={cik}&type={form_type}&dateb=&owner=include&count=1"
                )
                title = f"{src.get('entity_name', 'Unknown')} - {form_type}"
                filing_date_str = src.get("file_date")
                if not filing_date_str:
                    continue
                filing_date = datetime.fromisoformat(filing_date_str).replace(tzinfo=UTC)

                try:
                    await db.execute(
                        sa.text(
                            """
                            INSERT INTO filings
                                (instrument_id, canonical_id, source, form_type,
                                 filing_date, title, url)
                            VALUES (:iid, :cid, 'sec_edgar', :ft, :fd, :title, :url)
                            ON CONFLICT (url) DO NOTHING
                            """
                        ),
                        {
                            "iid": instrument_id,
                            "cid": canonical_id,
                            "ft": form_type,
                            "fd": filing_date,
                            "title": title,
                            "url": filing_url,
                        },
                    )
                    metrics.filings_ingested_total.labels(
                        source="sec_edgar", form_type=form_type
                    ).inc()
                except Exception:
                    metrics.filings_dedup_skips_total.labels(source="sec_edgar").inc()

            if new_cursor:
                await db.execute(
                    sa.text(
                        """
                        INSERT INTO filing_feed_cursors (source, last_cursor)
                        VALUES ('sec_edgar', :cur)
                        ON CONFLICT (source) DO UPDATE SET last_cursor = :cur, updated_at = now()
                        """
                    ),
                    {"cur": new_cursor},
                )
            await db.commit()
