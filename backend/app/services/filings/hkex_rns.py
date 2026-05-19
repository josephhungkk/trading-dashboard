from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

import httpx
import sqlalchemy as sa
import structlog

from app.core import metrics
from app.services.filings.instrument_linker import InstrumentLinker

log = structlog.get_logger()

_RNS_RSS_URL = "https://www.hkexnews.hk/listedco/listconews/SEHK/rss.xml"
_HKEX_TIMEOUT = 30.0


class HkexRnsPoller:
    """Polls HKEX RNS RSS feed for recent HK exchange filings."""

    def __init__(self, *, db_factory: Any, ai_client: Any = None) -> None:
        self._db_factory = db_factory
        self._ai_client = ai_client

    async def poll(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=_HKEX_TIMEOUT) as client:
                resp = await client.get(_RNS_RSS_URL)
            if resp.status_code != 200:
                log.warning("hkex_rns.bad_response", status=resp.status_code)
                metrics.filings_poll_errors_total.labels(source="hkex_rns").inc()
                return
            await self._process_feed(resp.text)
        except Exception:
            metrics.filings_poll_errors_total.labels(source="hkex_rns").inc()
            log.exception("hkex_rns.poll_error")

    async def _process_feed(self, xml_text: str) -> None:
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            log.warning("hkex_rns.parse_error")
            return

        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        items = root.findall(".//item")

        async with self._db_factory() as db:
            cursor_row = await db.execute(
                sa.text("SELECT last_cursor FROM filing_feed_cursors WHERE source = 'hkex_rns'")
            )
            cr = cursor_row.fetchone()
            last_cursor = cr.last_cursor if cr else None

            linker = InstrumentLinker(db)
            newest_url: str | None = None

            for item in items:
                link_el = item.find("link")
                title_el = item.find("title")
                pub_date_el = item.find("pubDate")
                subject_el = item.find("dc:subject", ns)

                if link_el is None or title_el is None or pub_date_el is None:
                    continue

                url = (link_el.text or "").strip()
                if not url:
                    continue

                if url == last_cursor:
                    break

                if newest_url is None:
                    newest_url = url

                title = (title_el.text or "").strip()
                pub_date_str = (pub_date_el.text or "").strip()
                try:
                    filing_date = (
                        datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
                        .astimezone(UTC)
                        .replace(tzinfo=UTC)
                    )
                except ValueError:
                    filing_date = datetime.now(UTC)

                stock_code = (subject_el.text or "").strip() if subject_el is not None else None
                instrument_id, canonical_id = await linker.link(
                    source="hkex_rns",
                    ticker=stock_code,
                )

                url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
                dedup_url = f"hkex:{url_hash}"

                try:
                    await db.execute(
                        sa.text(
                            """
                            INSERT INTO filings
                                (instrument_id, canonical_id, source, form_type,
                                 filing_date, title, url)
                            VALUES (:iid, :cid, 'hkex_rns', 'RNS', :fd, :title, :url)
                            ON CONFLICT (url) DO NOTHING
                            """
                        ),
                        {
                            "iid": instrument_id,
                            "cid": canonical_id or dedup_url,
                            "fd": filing_date,
                            "title": title[:500],
                            "url": url,
                        },
                    )
                    metrics.filings_ingested_total.labels(source="hkex_rns", form_type="RNS").inc()
                except Exception:
                    metrics.filings_dedup_skips_total.labels(source="hkex_rns").inc()

            if newest_url:
                await db.execute(
                    sa.text(
                        """
                        INSERT INTO filing_feed_cursors (source, last_cursor)
                        VALUES ('hkex_rns', :cur)
                        ON CONFLICT (source) DO UPDATE SET last_cursor = :cur, updated_at = now()
                        """
                    ),
                    {"cur": newest_url},
                )
            await db.commit()
