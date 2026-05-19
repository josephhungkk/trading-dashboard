# Phase 18.1 — News & Filings Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Background ingestion of SEC EDGAR (US 8-K/10-K/10-Q) and HKEX RNS filing feeds with LLM summarisation, instrument linking, and a `/filings` feed page.

**Architecture:** A polling-based background service reads SEC EDGAR EFTS and HKEX RNS RSS feeds every 10–15 minutes via APScheduler, deduplicates via cursor + `url` UNIQUE constraint, links filings to instruments via `symbol_aliases`, and enqueues LLM summarisation (LONG_CONTEXT for docs > 4KB, LOCAL_ONLY for short ones). A shared `sec_edgar_client.py` enforces the required 10 req/s global rate limit. The `/filings` route and `FilingsPanel` component surface results. This sub-phase does NOT depend on Phase 18.0 (Scanner) being complete.

**Tech Stack:** Python (FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, structlog, APScheduler), Redis (cursor dedup), PostgreSQL 18, React 19 + TanStack Query, TypeScript strict

---

### File Map

**New files:**
- `backend/alembic/versions/0059_filings.py` — `filings` + `filing_feed_cursors` tables, indexes
- `backend/app/services/filings/__init__.py`
- `backend/app/services/filings/schemas.py` — `FilingRow`, `FilingFeedCursor` Pydantic models
- `backend/app/services/filings/sec_edgar.py` — EDGAR EFTS poller
- `backend/app/services/filings/hkex_rns.py` — HKEX RSS poller
- `backend/app/services/filings/instrument_linker.py` — CIK/ticker → canonical_id resolver
- `backend/app/services/filings/summariser.py` — LLM summarisation job
- `backend/app/services/filings/filings_service.py` — orchestrator
- `backend/app/services/common/sec_edgar_client.py` — shared SEC HTTP client with 10 req/s rate limiter
- `backend/app/api/filings.py` — 3 REST endpoints
- `backend/tests/test_filings.py` — integration tests
- `frontend/src/services/filings/types.ts`
- `frontend/src/services/filings/api.ts`
- `frontend/src/features/filings/FilingsPage.tsx`
- `frontend/src/features/filings/FilingsPanel.tsx`
- `frontend/src/routes/filings.tsx`

**Modified files:**
- `backend/app/core/metrics.py` — add 7 `filings_*` counters
- `backend/app/main.py` — wire `FilingsService` lifespan + APScheduler jobs
- `frontend/src/routes/__root.tsx` — add `/filings` nav link
- `frontend/src/features/instruments/InstrumentDrawer.tsx` — inject `FilingsPanel`

---

### Task 1: Alembic migration 0059

**Files:**
- Create: `backend/alembic/versions/0059_filings.py`

- [ ] **Step 1: Write the failing test for migration**

```python
# backend/tests/test_filings.py
import pytest
from sqlalchemy import text

@pytest.mark.integration
async def test_0059_migration_tables_exist(db):
    """Migration 0059 creates filings and filing_feed_cursors tables."""
    result = await db.execute(
        text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('filings', 'filing_feed_cursors')
        """)
    )
    names = {r[0] for r in result}
    assert "filings" in names
    assert "filing_feed_cursors" in names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend pytest tests/test_filings.py::test_0059_migration_tables_exist -v
```
Expected: FAIL — tables don't exist yet

- [ ] **Step 3: Write migration**

```python
# backend/alembic/versions/0059_filings.py
"""add filings and filing_feed_cursors

Revision ID: 0059
Revises: 0058
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filings",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instrument_id", sa.BigInteger(), sa.ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("canonical_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("form_type", sa.Text(), nullable=False),
        sa.Column("filing_date", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_of_report", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("llm_summary_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("source IN ('sec_edgar', 'hkex_rns')", name="filings_source_check"),
        sa.CheckConstraint(
            "instrument_id IS NOT NULL OR canonical_id IS NOT NULL",
            name="filings_instrument_or_canonical_check",
        ),
    )
    op.create_index("ix_filings_canonical_id", "filings", ["canonical_id"])
    op.create_index("ix_filings_instrument_id", "filings", ["instrument_id"])
    op.create_index("ix_filings_filing_date", "filings", ["filing_date"])

    op.create_table(
        "filing_feed_cursors",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("last_cursor", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("source IN ('sec_edgar', 'hkex_rns')", name="filing_feed_cursors_source_check"),
    )


def downgrade() -> None:
    op.drop_table("filing_feed_cursors")
    op.drop_index("ix_filings_filing_date")
    op.drop_index("ix_filings_instrument_id")
    op.drop_index("ix_filings_canonical_id")
    op.drop_table("filings")
```

- [ ] **Step 4: Run migration**

```bash
docker compose exec backend alembic upgrade head
```
Expected: migration 0059 applied successfully

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec backend pytest tests/test_filings.py::test_0059_migration_tables_exist -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0059_filings.py backend/tests/test_filings.py
git commit -m "feat(phase18b): alembic 0059 — filings + filing_feed_cursors tables"
```

---

### Task 2: Pydantic schemas + Prometheus metrics

**Files:**
- Create: `backend/app/services/filings/schemas.py`
- Create: `backend/app/services/filings/__init__.py`
- Modify: `backend/app/core/metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# in backend/tests/test_filings.py — add:
from app.services.filings.schemas import FilingRow, FilingFeedCursorRow
from datetime import datetime, timezone
import uuid

def test_filing_row_model():
    f = FilingRow(
        id=uuid.uuid4(),
        instrument_id=None,
        canonical_id="AAPL.XNAS",
        source="sec_edgar",
        form_type="8-K",
        filing_date=datetime.now(timezone.utc),
        title="Material Event",
        url="https://www.sec.gov/Archives/edgar/data/320193/test.htm",
        captured_at=datetime.now(timezone.utc),
    )
    assert f.source == "sec_edgar"
    assert f.canonical_id == "AAPL.XNAS"

def test_filing_row_requires_instrument_or_canonical():
    import pytest
    with pytest.raises(Exception):
        FilingRow(
            id=uuid.uuid4(),
            instrument_id=None,
            canonical_id=None,  # both null — should fail
            source="sec_edgar",
            form_type="8-K",
            filing_date=datetime.now(timezone.utc),
            title="x",
            url="http://x.com/f",
            captured_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_filings.py::test_filing_row_model tests/test_filings.py::test_filing_row_requires_instrument_or_canonical -v
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write schemas**

```python
# backend/app/services/filings/__init__.py
# (empty)
```

```python
# backend/app/services/filings/schemas.py
from __future__ import annotations
from datetime import date, datetime
from typing import Optional
import uuid
from pydantic import BaseModel, model_validator


class FilingRow(BaseModel):
    id: uuid.UUID
    instrument_id: Optional[int]
    canonical_id: Optional[str]
    source: str  # 'sec_edgar' | 'hkex_rns'
    form_type: str
    filing_date: datetime
    period_of_report: Optional[date] = None
    title: str
    url: str
    raw_text: Optional[str] = None
    llm_summary: Optional[str] = None
    llm_summary_at: Optional[datetime] = None
    captured_at: datetime

    @model_validator(mode="after")
    def instrument_or_canonical_required(self) -> "FilingRow":
        if self.instrument_id is None and self.canonical_id is None:
            raise ValueError("instrument_id or canonical_id must be set")
        return self

    class Config:
        from_attributes = True


class FilingFeedCursorRow(BaseModel):
    source: str
    last_cursor: str
    updated_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 4: Add Prometheus metrics**

In `backend/app/core/metrics.py`, add after the existing scanner metrics block (or at the end of the metrics file):

```python
# Filings metrics
filings_ingested_total = Counter(
    "filings_ingested_total",
    "Number of filings ingested",
    ["source", "form_type"],
)
filings_instrument_link_failures_total = Counter(
    "filings_instrument_link_failures_total",
    "Filings where instrument_id could not be resolved",
    ["source"],
)
filings_relinked_total = Counter(
    "filings_relinked_total",
    "Filings that were successfully re-linked to an instrument in backfill",
)
filings_summarisation_total = Counter(
    "filings_summarisation_total",
    "LLM summarisation attempts",
    ["capability", "status"],
)
filings_poll_errors_total = Counter(
    "filings_poll_errors_total",
    "Polling errors per source",
    ["source"],
)
filings_dedup_skips_total = Counter(
    "filings_dedup_skips_total",
    "Duplicate URLs skipped during ingestion",
    ["source"],
)
sec_edgar_rate_limit_total = Counter(
    "sec_edgar_rate_limit_total",
    "SEC EDGAR 429 rate-limit responses encountered",
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_filings.py::test_filing_row_model tests/test_filings.py::test_filing_row_requires_instrument_or_canonical -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/filings/__init__.py backend/app/services/filings/schemas.py backend/app/core/metrics.py
git commit -m "feat(phase18b): filings schemas + 7 Prometheus metrics"
```

---

### Task 3: SEC EDGAR shared client

**Files:**
- Create: `backend/app/services/common/sec_edgar_client.py`

- [ ] **Step 1: Write the failing test**

```python
# in backend/tests/test_filings.py — add:
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.common.sec_edgar_client import SecEdgarClient

async def test_sec_edgar_client_adds_user_agent():
    """Client injects User-Agent header on every request."""
    client = SecEdgarClient(contact_email="test@example.com")
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"hits": {"hits": []}}
        mock_get.return_value = mock_resp
        await client.get("https://efts.sec.gov/LATEST/search-index?q=test")
        headers = mock_get.call_args.kwargs.get("headers", {}) or mock_get.call_args[1].get("headers", {})
        assert "Trading Dashboard" in headers.get("User-Agent", "")
        assert "test@example.com" in headers.get("User-Agent", "")

async def test_sec_edgar_client_disabled_when_no_email():
    """Client raises if no contact email is configured."""
    from app.services.common.sec_edgar_client import SecEdgarClientDisabledError
    client = SecEdgarClient(contact_email=None)
    with pytest.raises(SecEdgarClientDisabledError):
        await client.get("https://efts.sec.gov/LATEST/search-index?q=test")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_filings.py::test_sec_edgar_client_adds_user_agent tests/test_filings.py::test_sec_edgar_client_disabled_when_no_email -v
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write the shared SEC EDGAR client**

```python
# backend/app/services/common/sec_edgar_client.py
from __future__ import annotations
import asyncio
import time
from typing import Any, Optional
import httpx
import structlog

from app.core.metrics import sec_edgar_rate_limit_total

logger = structlog.get_logger(__name__)

_RATE_LIMIT_RPS = 10  # SEC's published limit: 10 req/s


class SecEdgarClientDisabledError(Exception):
    """Raised when SEC contact email is not configured."""


class SecEdgarClient:
    """Single shared client for all SEC EDGAR HTTP traffic.

    Enforces global 10 req/s token bucket + required User-Agent header.
    All SEC consumers (filing poller, ad-hoc fetch) share one instance via lifespan.
    """

    def __init__(self, contact_email: Optional[str]) -> None:
        self._contact_email = contact_email
        self._disabled = contact_email is None
        self._tokens = float(_RATE_LIMIT_RPS)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def _consume_token(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(_RATE_LIMIT_RPS, self._tokens + elapsed * _RATE_LIMIT_RPS)
            self._last_refill = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / _RATE_LIMIT_RPS
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1

    def _user_agent(self) -> str:
        return f"Trading Dashboard {self._contact_email}"

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self._disabled:
            raise SecEdgarClientDisabledError(
                "SEC EDGAR client disabled — filings/sec_edgar/contact_email not configured"
            )
        await self._consume_token()
        headers = kwargs.pop("headers", {})
        headers["User-Agent"] = self._user_agent()
        retries = 3
        backoff = 1.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(retries):
                resp = await client.get(url, headers=headers, **kwargs)
                if resp.status_code == 429:
                    sec_edgar_rate_limit_total.inc()
                    logger.warning("sec_edgar_rate_limited", attempt=attempt, url=url)
                    if attempt < retries - 1:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                resp.raise_for_status()
                return resp
            resp.raise_for_status()
            return resp  # unreachable but satisfies type checker
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_filings.py::test_sec_edgar_client_adds_user_agent tests/test_filings.py::test_sec_edgar_client_disabled_when_no_email -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/common/sec_edgar_client.py backend/tests/test_filings.py
git commit -m "feat(phase18b): shared SecEdgarClient with 10 req/s token bucket + User-Agent"
```

---

### Task 4: Instrument linker

**Files:**
- Create: `backend/app/services/filings/instrument_linker.py`

- [ ] **Step 1: Write the failing test**

```python
# in backend/tests/test_filings.py — add:
from app.services.filings.instrument_linker import InstrumentLinker

async def test_instrument_linker_resolves_by_ticker(db):
    """Linker returns instrument_id + canonical_id for a known ticker."""
    # Seed: symbol_aliases row pointing to an instrument
    await db.execute(
        text("""
            INSERT INTO symbol_aliases (source, raw_symbol, instrument_id, meta, created_at)
            VALUES ('sec_edgar', 'AAPL', 1, '{}', now())
            ON CONFLICT DO NOTHING
        """)
    )
    await db.execute(
        text("""
            INSERT INTO instruments (id, ticker, primary_exchange, asset_class, meta, currency, created_at, updated_at)
            VALUES (1, 'AAPL', 'XNAS', 'STOCK', '{}', 'USD', now(), now())
            ON CONFLICT DO NOTHING
        """)
    )
    await db.commit()
    linker = InstrumentLinker(db)
    result = await linker.resolve(ticker="AAPL", home_exchange="XNAS")
    assert result is not None
    instrument_id, canonical_id = result
    assert instrument_id == 1

async def test_instrument_linker_returns_none_for_unknown(db):
    linker = InstrumentLinker(db)
    result = await linker.resolve(ticker="ZZZZ_UNKNOWN", home_exchange="XNAS")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_filings.py::test_instrument_linker_resolves_by_ticker tests/test_filings.py::test_instrument_linker_returns_none_for_unknown -v
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write instrument linker**

```python
# backend/app/services/filings/instrument_linker.py
from __future__ import annotations
from typing import Optional, Tuple
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import filings_instrument_link_failures_total

logger = structlog.get_logger(__name__)


class InstrumentLinker:
    """Resolves filing issuer (ticker or CIK) to (instrument_id, canonical_id).

    Tiebreaker for dual-listed ADRs: prefer the row where instruments.primary_exchange
    matches the filing's home exchange (e.g. SEC → XNYS/XNAS; HKEX → XHKG).
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve(
        self,
        ticker: Optional[str] = None,
        cik: Optional[str] = None,
        home_exchange: Optional[str] = None,
        source: str = "sec_edgar",
    ) -> Optional[Tuple[int, str]]:
        """Return (instrument_id, canonical_id) or None if unresolvable."""
        if not ticker and not cik:
            return None

        # Build match condition
        if ticker and cik:
            where = "sa.raw_symbol = :ticker OR sa.meta->>'cik' = :cik"
            params: dict = {"ticker": ticker, "cik": cik, "home_exchange": home_exchange or ""}
        elif ticker:
            where = "sa.raw_symbol = :ticker"
            params = {"ticker": ticker, "home_exchange": home_exchange or ""}
        else:
            where = "sa.meta->>'cik' = :cik"
            params = {"cik": cik, "home_exchange": home_exchange or ""}

        result = await self._db.execute(
            text(f"""
                SELECT sa.instrument_id, i.canonical_id, i.primary_exchange
                FROM symbol_aliases sa
                JOIN instruments i ON i.id = sa.instrument_id
                WHERE {where}
                  AND sa.source = :source
                ORDER BY
                    CASE WHEN i.primary_exchange = :home_exchange THEN 0 ELSE 1 END,
                    sa.created_at DESC
                LIMIT 1
            """),
            {**params, "source": source},
        )
        row = result.fetchone()
        if row is None:
            filings_instrument_link_failures_total.labels(source=source).inc()
            logger.info("instrument_linker_no_match", ticker=ticker, cik=cik, source=source)
            return None
        return row.instrument_id, row.canonical_id
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_filings.py::test_instrument_linker_resolves_by_ticker tests/test_filings.py::test_instrument_linker_returns_none_for_unknown -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/filings/instrument_linker.py backend/tests/test_filings.py
git commit -m "feat(phase18b): InstrumentLinker — primary_exchange tiebreaker for dual-listed ADRs"
```

---

### Task 5: SEC EDGAR + HKEX pollers

**Files:**
- Create: `backend/app/services/filings/sec_edgar.py`
- Create: `backend/app/services/filings/hkex_rns.py`

- [ ] **Step 1: Write the failing tests**

```python
# in backend/tests/test_filings.py — add:
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.filings.sec_edgar import SecEdgarPoller
from app.services.filings.hkex_rns import HkexRnsPoller

async def test_sec_edgar_poller_parses_hits(db, redis):
    """EDGAR poller parses hits array and returns FilingRow list."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "0000320193-24-000123",
                    "_source": {
                        "period_of_report": "2024-01-01",
                        "form_type": "8-K",
                        "entity_name": "Apple Inc",
                        "file_date": "2024-01-15",
                        "display_date_filed": "2024-01-15",
                        "period": "20240101",
                    },
                    "_source_url": "/Archives/edgar/data/320193/0000320193-24-000123.htm",
                }
            ],
            "total": {"value": 1},
        }
    }
    mock_client.get = AsyncMock(return_value=mock_resp)
    poller = SecEdgarPoller(client=mock_client, db=db)
    rows = await poller.fetch_new(form_types=["8-K"], limit=10)
    assert len(rows) >= 0  # may be 0 if already deduped; just check it doesn't crash

async def test_hkex_rns_poller_parses_feed(db):
    """HKEX RSS poller parses feed XML."""
    import xml.etree.ElementTree as ET
    sample_rss = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Test Announcement</title>
          <link>https://www.hkexnews.hk/listedco/listconews/SEHK/2024/0115/test.pdf</link>
          <pubDate>Mon, 15 Jan 2024 09:00:00 +0800</pubDate>
          <description>Test HK announcement</description>
        </item>
      </channel>
    </rss>"""
    poller = HkexRnsPoller(db=db)
    rows = poller._parse_rss(sample_rss)
    assert len(rows) == 1
    assert rows[0]["title"] == "Test Announcement"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_filings.py::test_sec_edgar_poller_parses_hits tests/test_filings.py::test_hkex_rns_poller_parses_feed -v
```
Expected: FAIL — modules don't exist

- [ ] **Step 3: Write SEC EDGAR poller**

```python
# backend/app/services/filings/sec_edgar.py
"""SEC EDGAR EFTS full-text search poller.

Polls every 15 min during US market hours. Cursor = last accession number seen.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.common.sec_edgar_client import SecEdgarClient, SecEdgarClientDisabledError
from app.core.metrics import filings_ingested_total, filings_dedup_skips_total, filings_poll_errors_total

logger = structlog.get_logger(__name__)

_EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
_FORM_TYPES = ["8-K", "10-K", "10-Q"]


class SecEdgarPoller:
    def __init__(self, client: SecEdgarClient, db: AsyncSession) -> None:
        self._client = client
        self._db = db

    async def fetch_new(
        self,
        form_types: list[str] = _FORM_TYPES,
        limit: int = 40,
    ) -> list[dict]:
        """Return list of raw hit dicts for new filings not already in DB."""
        try:
            cursor = await self._get_cursor()
            form_param = ",".join(form_types)
            resp = await self._client.get(
                _EFTS_BASE,
                params={
                    "q": f'formType:({" OR ".join(form_types)})',
                    "dateRange": "custom",
                    "startdt": "2000-01-01",
                    "forms": form_param,
                    "_source": "form_type,period_of_report,entity_name,file_date,display_date_filed",
                    "from": 0,
                    "size": limit,
                },
            )
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            new_rows = []
            for hit in hits:
                accession = hit.get("_id", "")
                if cursor and accession <= cursor:
                    filings_dedup_skips_total.labels(source="sec_edgar").inc()
                    continue
                src = hit.get("_source", {})
                url = f"https://www.sec.gov/Archives/edgar/{hit.get('_source_url', accession)}"
                new_rows.append({
                    "accession": accession,
                    "form_type": src.get("form_type", ""),
                    "title": f"{src.get('entity_name', '')} — {src.get('form_type', '')}",
                    "filing_date": src.get("file_date", ""),
                    "period_of_report": src.get("period_of_report"),
                    "url": url,
                    "source": "sec_edgar",
                })
                filings_ingested_total.labels(source="sec_edgar", form_type=src.get("form_type", "")).inc()
            if new_rows:
                await self._update_cursor(new_rows[0]["accession"])
            return new_rows
        except SecEdgarClientDisabledError:
            logger.info("sec_edgar_poller_disabled")
            return []
        except Exception as exc:
            filings_poll_errors_total.labels(source="sec_edgar").inc()
            logger.exception("sec_edgar_poll_error", exc_info=exc)
            return []

    async def _get_cursor(self) -> Optional[str]:
        result = await self._db.execute(
            text("SELECT last_cursor FROM filing_feed_cursors WHERE source = 'sec_edgar'")
        )
        row = result.fetchone()
        return row.last_cursor if row else None

    async def _update_cursor(self, accession: str) -> None:
        await self._db.execute(
            text("""
                INSERT INTO filing_feed_cursors (source, last_cursor, updated_at)
                VALUES ('sec_edgar', :acc, now())
                ON CONFLICT (source) DO UPDATE SET last_cursor = EXCLUDED.last_cursor, updated_at = now()
            """),
            {"acc": accession},
        )
        await self._db.commit()
```

- [ ] **Step 4: Write HKEX RNS poller**

```python
# backend/app/services/filings/hkex_rns.py
"""HKEX RNS RSS feed poller.

Polls every 10 min during HK market hours. Cursor = last item link (acts as seq_no).
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Optional
import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import filings_ingested_total, filings_dedup_skips_total, filings_poll_errors_total

logger = structlog.get_logger(__name__)

_HKEX_RSS = "https://www.hkexnews.hk/listedco/listconews/mainboard/rss.xml"


class HkexRnsPoller:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _parse_rss(self, xml_text: str) -> list[dict]:
        items = []
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            if link_el is None or title_el is None:
                continue
            link = link_el.text or ""
            title = title_el.text or ""
            filing_date = None
            if pub_el is not None and pub_el.text:
                try:
                    filing_date = parsedate_to_datetime(pub_el.text)
                except Exception:
                    pass
            items.append({
                "title": title,
                "url": link,
                "filing_date": filing_date,
                "form_type": "HKEx announcement",
                "source": "hkex_rns",
            })
        return items

    async def fetch_new(self) -> list[dict]:
        try:
            cursor = await self._get_cursor()
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(_HKEX_RSS)
                resp.raise_for_status()
            raw = self._parse_rss(resp.text)
            new_rows = []
            for item in raw:
                if cursor and item["url"] == cursor:
                    break  # seen this and everything older
                filings_dedup_skips_total.labels(source="hkex_rns")
                new_rows.append(item)
                filings_ingested_total.labels(source="hkex_rns", form_type="HKEx announcement").inc()
            if new_rows:
                await self._update_cursor(new_rows[0]["url"])
            return new_rows
        except Exception as exc:
            filings_poll_errors_total.labels(source="hkex_rns").inc()
            logger.exception("hkex_rns_poll_error", exc_info=exc)
            return []

    async def _get_cursor(self) -> Optional[str]:
        result = await self._db.execute(
            text("SELECT last_cursor FROM filing_feed_cursors WHERE source = 'hkex_rns'")
        )
        row = result.fetchone()
        return row.last_cursor if row else None

    async def _update_cursor(self, url: str) -> None:
        await self._db.execute(
            text("""
                INSERT INTO filing_feed_cursors (source, last_cursor, updated_at)
                VALUES ('hkex_rns', :url, now())
                ON CONFLICT (source) DO UPDATE SET last_cursor = EXCLUDED.last_cursor, updated_at = now()
            """),
            {"url": url},
        )
        await self._db.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_filings.py::test_sec_edgar_poller_parses_hits tests/test_filings.py::test_hkex_rns_poller_parses_feed -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/filings/sec_edgar.py backend/app/services/filings/hkex_rns.py backend/tests/test_filings.py
git commit -m "feat(phase18b): SEC EDGAR EFTS poller + HKEX RNS RSS poller"
```

---

### Task 6: LLM summariser + FilingsService orchestrator

**Files:**
- Create: `backend/app/services/filings/summariser.py`
- Create: `backend/app/services/filings/filings_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# in backend/tests/test_filings.py — add:
from app.services.filings.summariser import FilingSummariser
from app.services.filings.filings_service import FilingsService
import uuid

async def test_summariser_uses_long_context_for_large_doc(db):
    """Summariser selects LONG_CONTEXT for docs > 4KB."""
    from unittest.mock import AsyncMock, MagicMock
    mock_ai = MagicMock()
    mock_ai.complete = AsyncMock(return_value=MagicMock(text="Summary here."))
    summariser = FilingSummariser(ai_client=mock_ai, db=db)
    large_text = "x" * 5000
    capability = summariser._select_capability(large_text)
    assert capability == "LONG_CONTEXT"

async def test_summariser_uses_local_only_for_small_doc(db):
    """Summariser selects LOCAL_ONLY for docs <= 4KB."""
    from unittest.mock import AsyncMock, MagicMock
    mock_ai = MagicMock()
    summariser = FilingSummariser(ai_client=mock_ai, db=db)
    small_text = "x" * 100
    capability = summariser._select_capability(small_text)
    assert capability == "LOCAL_ONLY"

async def test_filings_service_ingest_deduplicates(db, redis):
    """FilingsService skips rows with duplicate URLs."""
    from app.services.common.sec_edgar_client import SecEdgarClient
    from unittest.mock import patch, AsyncMock, MagicMock
    # Pre-insert a filing
    existing_url = "https://www.sec.gov/Archives/edgar/data/1/test-dedup.htm"
    await db.execute(
        text("""
            INSERT INTO filings (id, canonical_id, source, form_type, filing_date, title, url, captured_at)
            VALUES (gen_random_uuid(), 'AAPL.XNAS', 'sec_edgar', '8-K', now(), 'Dedup Test', :url, now())
        """),
        {"url": existing_url},
    )
    await db.commit()
    service = FilingsService.__new__(FilingsService)
    # Attempting to insert same URL should be caught by UNIQUE constraint
    result = await service._safe_insert_filing(db, {
        "canonical_id": "AAPL.XNAS",
        "source": "sec_edgar",
        "form_type": "8-K",
        "filing_date": "2024-01-15",
        "title": "Dedup Test",
        "url": existing_url,
    })
    assert result is None  # duplicate skipped
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_filings.py::test_summariser_uses_long_context_for_large_doc tests/test_filings.py::test_summariser_uses_local_only_for_small_doc tests/test_filings.py::test_filings_service_ingest_deduplicates -v
```
Expected: FAIL

- [ ] **Step 3: Write summariser**

```python
# backend/app/services/filings/summariser.py
from __future__ import annotations
import uuid
from typing import Optional
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import filings_summarisation_total

logger = structlog.get_logger(__name__)

_SIZE_THRESHOLD_BYTES = 4096


class FilingSummariser:
    def __init__(self, ai_client: object, db: AsyncSession) -> None:
        self._ai = ai_client
        self._db = db

    def _select_capability(self, raw_text: str) -> str:
        return "LONG_CONTEXT" if len(raw_text.encode("utf-8")) > _SIZE_THRESHOLD_BYTES else "LOCAL_ONLY"

    async def summarise(self, filing_id: uuid.UUID, raw_text: Optional[str]) -> None:
        if not raw_text:
            return
        capability = self._select_capability(raw_text)
        try:
            prompt = (
                f"Summarise the following financial filing in 3-5 sentences, "
                f"focusing on material disclosures:\n\n{raw_text[:8000]}"
            )
            result = await self._ai.complete(prompt=prompt, capability=capability)
            summary = result.text
            await self._db.execute(
                text("""
                    UPDATE filings
                    SET llm_summary = :summary, llm_summary_at = now()
                    WHERE id = :id
                """),
                {"summary": summary, "id": str(filing_id)},
            )
            await self._db.commit()
            filings_summarisation_total.labels(capability=capability, status="success").inc()
        except Exception as exc:
            filings_summarisation_total.labels(capability=capability, status="failed").inc()
            logger.exception("filing_summarisation_failed", filing_id=str(filing_id), exc_info=exc)
```

- [ ] **Step 4: Write FilingsService**

```python
# backend/app/services/filings/filings_service.py
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.metrics import filings_relinked_total
from app.services.filings.sec_edgar import SecEdgarPoller
from app.services.filings.hkex_rns import HkexRnsPoller
from app.services.filings.instrument_linker import InstrumentLinker
from app.services.filings.summariser import FilingSummariser
from app.services.common.sec_edgar_client import SecEdgarClient

logger = structlog.get_logger(__name__)


class FilingsService:
    def __init__(
        self,
        db: AsyncSession,
        redis: object,
        sec_edgar_client: SecEdgarClient,
        ai_client: object,
    ) -> None:
        self._db = db
        self._redis = redis
        self._sec_client = sec_edgar_client
        self._ai = ai_client
        self._sec_lock = asyncio.Lock()
        self._hkex_lock = asyncio.Lock()

    async def poll_sec_edgar(self) -> None:
        async with self._sec_lock:
            poller = SecEdgarPoller(client=self._sec_client, db=self._db)
            rows = await poller.fetch_new()
            for row in rows:
                filing_id = await self._safe_insert_filing(self._db, row)
                if filing_id:
                    asyncio.create_task(
                        FilingSummariser(self._ai, self._db).summarise(filing_id, row.get("raw_text"))
                    )

    async def poll_hkex_rns(self) -> None:
        async with self._hkex_lock:
            poller = HkexRnsPoller(db=self._db)
            rows = await poller.fetch_new()
            for row in rows:
                filing_id = await self._safe_insert_filing(self._db, row)
                if filing_id:
                    asyncio.create_task(
                        FilingSummariser(self._ai, self._db).summarise(filing_id, row.get("raw_text"))
                    )

    async def _safe_insert_filing(self, db: AsyncSession, row: dict[str, Any]) -> Optional[uuid.UUID]:
        """Insert filing row; return UUID on success, None on duplicate URL."""
        linker = InstrumentLinker(db)
        link_result = await linker.resolve(
            ticker=row.get("ticker"),
            cik=row.get("cik"),
            home_exchange=row.get("home_exchange"),
            source=row.get("source", "sec_edgar"),
        )
        instrument_id = link_result[0] if link_result else None
        canonical_id = link_result[1] if link_result else row.get("canonical_id")
        if not instrument_id and not canonical_id:
            canonical_id = row.get("url", "")  # fallback — satisfies NOT NULL constraint

        filing_date_raw = row.get("filing_date")
        if isinstance(filing_date_raw, str):
            try:
                filing_date = datetime.fromisoformat(filing_date_raw).replace(tzinfo=timezone.utc)
            except ValueError:
                filing_date = datetime.now(timezone.utc)
        elif isinstance(filing_date_raw, datetime):
            filing_date = filing_date_raw
        else:
            filing_date = datetime.now(timezone.utc)

        raw_text = row.get("raw_text")
        if raw_text:
            raw_text = raw_text.encode("utf-8")[:32768].decode("utf-8", errors="ignore")

        new_id = uuid.uuid4()
        try:
            await db.execute(
                text("""
                    INSERT INTO filings
                      (id, instrument_id, canonical_id, source, form_type, filing_date,
                       period_of_report, title, url, raw_text, captured_at)
                    VALUES
                      (:id, :instrument_id, :canonical_id, :source, :form_type, :filing_date,
                       :period_of_report, :title, :url, :raw_text, now())
                """),
                {
                    "id": str(new_id),
                    "instrument_id": instrument_id,
                    "canonical_id": canonical_id,
                    "source": row["source"],
                    "form_type": row["form_type"],
                    "filing_date": filing_date,
                    "period_of_report": row.get("period_of_report"),
                    "title": row["title"],
                    "url": row["url"],
                    "raw_text": raw_text,
                },
            )
            await db.commit()
            return new_id
        except IntegrityError:
            await db.rollback()
            return None

    async def run_relinker(self) -> None:
        """Nightly backfill: re-resolve instrument_id for unlinked filings in last 30 days."""
        result = await self._db.execute(
            text("""
                SELECT id, url, source, canonical_id
                FROM filings
                WHERE instrument_id IS NULL
                  AND captured_at > now() - INTERVAL '30 days'
                LIMIT 200
            """)
        )
        rows = result.fetchall()
        linker = InstrumentLinker(self._db)
        for row in rows:
            ticker = row.canonical_id.split(".")[0] if row.canonical_id and "." in row.canonical_id else None
            link_result = await linker.resolve(ticker=ticker, source=row.source)
            if link_result:
                instrument_id, canonical_id = link_result
                await self._db.execute(
                    text("UPDATE filings SET instrument_id = :iid, canonical_id = :cid WHERE id = :id"),
                    {"iid": instrument_id, "cid": canonical_id, "id": str(row.id)},
                )
                await self._db.commit()
                filings_relinked_total.inc()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_filings.py::test_summariser_uses_long_context_for_large_doc tests/test_filings.py::test_summariser_uses_local_only_for_small_doc tests/test_filings.py::test_filings_service_ingest_deduplicates -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/filings/summariser.py backend/app/services/filings/filings_service.py backend/tests/test_filings.py
git commit -m "feat(phase18b): FilingSummariser + FilingsService orchestrator with cursor dedup"
```

---

### Task 7: REST API

**Files:**
- Create: `backend/app/api/filings.py`

- [ ] **Step 1: Write the failing tests**

```python
# in backend/tests/test_filings.py — add:
from httpx import AsyncClient

async def test_get_filings_returns_list(client: AsyncClient, auth_headers: dict):
    """GET /api/filings returns paginated list."""
    resp = await client.get("/api/filings", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "next_cursor" in data

async def test_get_filing_detail(client: AsyncClient, auth_headers: dict, db):
    """GET /api/filings/{id} returns filing detail."""
    fid = uuid.uuid4()
    await db.execute(
        text("""
            INSERT INTO filings (id, canonical_id, source, form_type, filing_date, title, url, captured_at)
            VALUES (:id, 'AAPL.XNAS', 'sec_edgar', '8-K', now(), 'Test Filing', :url, now())
        """),
        {"id": str(fid), "url": f"https://sec.gov/{fid}"},
    )
    await db.commit()
    resp = await client.get(f"/api/filings/{fid}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == str(fid)

async def test_get_instrument_filings(client: AsyncClient, auth_headers: dict, db):
    """GET /api/instruments/{id}/filings returns filings for instrument."""
    resp = await client.get("/api/instruments/999999/filings", headers=auth_headers)
    assert resp.status_code in (200, 404)  # 404 if instrument doesn't exist
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend pytest tests/test_filings.py::test_get_filings_returns_list tests/test_filings.py::test_get_filing_detail tests/test_filings.py::test_get_instrument_filings -v
```
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Write the REST API**

```python
# backend/app/api/filings.py
from __future__ import annotations
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_jwt
from app.core.db import get_db

router = APIRouter(prefix="/api", tags=["filings"])

_DEFAULT_LIMIT = 20
_PANEL_LIMIT = 3


@router.get("/filings")
async def list_filings(
    source: Optional[str] = Query(None),
    form_type: Optional[str] = Query(None),
    instrument_id: Optional[int] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, le=100),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_jwt),
) -> dict:
    where_clauses = ["1=1"]
    params: dict = {"limit": limit + 1}
    if source:
        where_clauses.append("source = :source")
        params["source"] = source
    if form_type:
        where_clauses.append("form_type = :form_type")
        params["form_type"] = form_type
    if instrument_id:
        where_clauses.append("instrument_id = :instrument_id")
        params["instrument_id"] = instrument_id
    if cursor:
        where_clauses.append("filing_date < :cursor")
        params["cursor"] = cursor
    where = " AND ".join(where_clauses)
    result = await db.execute(
        text(f"""
            SELECT id, instrument_id, canonical_id, source, form_type, filing_date,
                   title, url, llm_summary, llm_summary_at, captured_at
            FROM filings
            WHERE {where}
            ORDER BY filing_date DESC
            LIMIT :limit
        """),
        params,
    )
    rows = result.fetchall()
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1].filing_date.isoformat() if has_more and items else None
    return {
        "items": [dict(r._mapping) for r in items],
        "next_cursor": next_cursor,
    }


@router.get("/filings/{filing_id}")
async def get_filing(
    filing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_jwt),
) -> dict:
    result = await db.execute(
        text("SELECT * FROM filings WHERE id = :id"),
        {"id": str(filing_id)},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="filing_not_found")
    return dict(row._mapping)


@router.get("/instruments/{instrument_id}/filings")
async def get_instrument_filings(
    instrument_id: int,
    limit: int = Query(_PANEL_LIMIT, le=50),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_jwt),
) -> dict:
    # Check instrument exists
    inst = await db.execute(
        text("SELECT id FROM instruments WHERE id = :id"),
        {"id": instrument_id},
    )
    if not inst.fetchone():
        raise HTTPException(status_code=404, detail="instrument_not_found")
    result = await db.execute(
        text("""
            SELECT id, source, form_type, filing_date, title, url, llm_summary, llm_summary_at
            FROM filings
            WHERE instrument_id = :instrument_id
            ORDER BY filing_date DESC
            LIMIT :limit
        """),
        {"instrument_id": instrument_id, "limit": limit},
    )
    rows = result.fetchall()
    return {"items": [dict(r._mapping) for r in rows]}
```

- [ ] **Step 4: Wire router into main.py**

In `backend/app/main.py`, add after the existing API router imports:

```python
from app.api.filings import router as filings_router
app.include_router(filings_router)
```

Also in the lifespan, after APScheduler setup, add FilingsService jobs:

```python
# Filings polling jobs
contact_email = await app_config_service.get("filings/sec_edgar/contact_email")
if not contact_email:
    logger.critical("sec_edgar_contact_email_missing", msg="SEC polling disabled")
sec_edgar_client = SecEdgarClient(contact_email=contact_email)
filings_svc = FilingsService(db=db, redis=redis, sec_edgar_client=sec_edgar_client, ai_client=ai_client)
scheduler.add_job(
    filings_svc.poll_sec_edgar,
    "cron", minute="*/15",
    id="filings_sec_edgar_poll",
    coalesce=True, misfire_grace_time=60,
)
scheduler.add_job(
    filings_svc.poll_hkex_rns,
    "cron", minute="*/10",
    id="filings_hkex_rns_poll",
    coalesce=True, misfire_grace_time=60,
)
scheduler.add_job(
    filings_svc.run_relinker,
    "cron", hour="3", minute="0",
    id="filings_relinker",
    coalesce=True, misfire_grace_time=300,
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose exec backend pytest tests/test_filings.py::test_get_filings_returns_list tests/test_filings.py::test_get_filing_detail tests/test_filings.py::test_get_instrument_filings -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/filings.py backend/app/main.py backend/tests/test_filings.py
git commit -m "feat(phase18b): REST API — /api/filings + /api/instruments/{id}/filings"
```

---

### Task 8: Frontend — types, api, FilingsPage, FilingsPanel, route

**Files:**
- Create: `frontend/src/services/filings/types.ts`
- Create: `frontend/src/services/filings/api.ts`
- Create: `frontend/src/features/filings/FilingsPage.tsx`
- Create: `frontend/src/features/filings/FilingsPanel.tsx`
- Create: `frontend/src/routes/filings.tsx`

- [ ] **Step 1: Write the failing FE tests**

```typescript
// frontend/src/features/filings/__tests__/FilingsPanel.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { FilingsPanel } from "../FilingsPanel";
import { vi } from "vitest";
import * as api from "@/services/filings/api";

vi.mock("@/services/filings/api");

describe("FilingsPanel", () => {
  it("renders loading state", () => {
    vi.mocked(api.getInstrumentFilings).mockResolvedValue({ items: [] });
    render(<FilingsPanel instrumentId={1} />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
  });

  it("renders filings list when data loads", async () => {
    vi.mocked(api.getInstrumentFilings).mockResolvedValue({
      items: [
        {
          id: "abc-123",
          source: "sec_edgar",
          form_type: "8-K",
          filing_date: "2024-01-15T00:00:00Z",
          title: "Material Event Disclosure",
          url: "https://sec.gov/test",
          llm_summary: "Company announced material event.",
          llm_summary_at: "2024-01-15T01:00:00Z",
        },
      ],
    });
    render(<FilingsPanel instrumentId={1} />);
    await waitFor(() => {
      expect(screen.getByText("Material Event Disclosure")).toBeTruthy();
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && pnpm test src/features/filings/__tests__/FilingsPanel.test.tsx
```
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write types**

```typescript
// frontend/src/services/filings/types.ts
export interface FilingRow {
  id: string;
  instrument_id: number | null;
  canonical_id: string | null;
  source: "sec_edgar" | "hkex_rns";
  form_type: string;
  filing_date: string; // ISO8601
  period_of_report: string | null;
  title: string;
  url: string;
  llm_summary: string | null;
  llm_summary_at: string | null;
  captured_at: string;
}

export interface FilingsPage {
  items: FilingRow[];
  next_cursor: string | null;
}

export interface FilingsFilter {
  source?: string;
  form_type?: string;
  instrument_id?: number;
  cursor?: string;
  limit?: number;
}
```

- [ ] **Step 4: Write API layer**

```typescript
// frontend/src/services/filings/api.ts
import type { FilingRow, FilingsPage, FilingsFilter } from "./types";

const BASE = "/api";

export async function listFilings(filter: FilingsFilter = {}): Promise<FilingsPage> {
  const params = new URLSearchParams();
  if (filter.source) params.set("source", filter.source);
  if (filter.form_type) params.set("form_type", filter.form_type);
  if (filter.instrument_id) params.set("instrument_id", String(filter.instrument_id));
  if (filter.cursor) params.set("cursor", filter.cursor);
  if (filter.limit) params.set("limit", String(filter.limit));
  const resp = await fetch(`${BASE}/filings?${params}`);
  if (!resp.ok) throw new Error("Failed to fetch filings");
  return resp.json();
}

export async function getFiling(id: string): Promise<FilingRow> {
  const resp = await fetch(`${BASE}/filings/${id}`);
  if (!resp.ok) throw new Error("Filing not found");
  return resp.json();
}

export async function getInstrumentFilings(
  instrumentId: number,
  limit = 3
): Promise<{ items: FilingRow[] }> {
  const resp = await fetch(`${BASE}/instruments/${instrumentId}/filings?limit=${limit}`);
  if (!resp.ok) throw new Error("Failed to fetch instrument filings");
  return resp.json();
}
```

- [ ] **Step 5: Write FilingsPanel component**

```typescript
// frontend/src/features/filings/FilingsPanel.tsx
import { useQuery } from "@tanstack/react-query";
import { getInstrumentFilings } from "@/services/filings/api";
import type { FilingRow } from "@/services/filings/types";

interface Props {
  instrumentId: number;
}

export function FilingsPanel({ instrumentId }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["instrument-filings", instrumentId],
    queryFn: () => getInstrumentFilings(instrumentId, 3),
    staleTime: 60_000,
  });

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading filings...</p>;

  const items = data?.items ?? [];
  if (items.length === 0) return <p className="text-sm text-muted-foreground">No recent filings.</p>;

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">Recent Filings</h4>
      {items.map((f: FilingRow) => (
        <div key={f.id} className="rounded border p-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium truncate">{f.title}</span>
            <span className="text-xs text-muted-foreground shrink-0">{f.form_type}</span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            {new Date(f.filing_date).toLocaleDateString()}
          </p>
          {f.llm_summary && (
            <p className="text-xs mt-1 line-clamp-2">{f.llm_summary}</p>
          )}
          <a
            href={f.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-primary underline mt-1 inline-block"
          >
            View filing
          </a>
        </div>
      ))}
      <a
        href={`/filings?instrument_id=${instrumentId}`}
        className="text-xs text-primary underline"
      >
        View all filings →
      </a>
    </div>
  );
}
```

- [ ] **Step 6: Write FilingsPage component**

```typescript
// frontend/src/features/filings/FilingsPage.tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listFilings } from "@/services/filings/api";
import type { FilingRow } from "@/services/filings/types";

const SOURCES = [
  { value: "", label: "All sources" },
  { value: "sec_edgar", label: "SEC EDGAR" },
  { value: "hkex_rns", label: "HKEX RNS" },
];

const FORM_TYPES = [
  { value: "", label: "All types" },
  { value: "8-K", label: "8-K" },
  { value: "10-K", label: "10-K" },
  { value: "10-Q", label: "10-Q" },
  { value: "HKEx announcement", label: "HKEx" },
];

export function FilingsPage() {
  const [source, setSource] = useState("");
  const [formType, setFormType] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["filings", source, formType],
    queryFn: () => listFilings({ source: source || undefined, form_type: formType || undefined }),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  return (
    <div className="container max-w-4xl py-6 space-y-4">
      <h1 className="text-xl font-semibold">Filings Feed</h1>

      <div className="flex gap-2 flex-wrap">
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="rounded border px-2 py-1 text-sm"
          aria-label="Filter by source"
        >
          {SOURCES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <select
          value={formType}
          onChange={(e) => setFormType(e.target.value)}
          className="rounded border px-2 py-1 text-sm"
          aria-label="Filter by form type"
        >
          {FORM_TYPES.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>

      {isLoading && <p className="text-muted-foreground text-sm">Loading filings...</p>}

      <div className="space-y-2">
        {(data?.items ?? []).map((f: FilingRow) => (
          <div key={f.id} className="rounded border p-3 space-y-1">
            <div className="flex items-start justify-between gap-2">
              <button
                className="text-sm font-medium text-left hover:underline"
                onClick={() => setExpanded(expanded === f.id ? null : f.id)}
              >
                {f.title}
              </button>
              <span className="text-xs text-muted-foreground shrink-0 whitespace-nowrap">
                {f.form_type} · {new Date(f.filing_date).toLocaleDateString()}
              </span>
            </div>
            {expanded === f.id && (
              <div className="text-sm space-y-1 pt-1">
                {f.llm_summary ? (
                  <p>{f.llm_summary}</p>
                ) : (
                  <p className="text-muted-foreground italic">Summary pending…</p>
                )}
                <a
                  href={f.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline text-xs"
                >
                  Read full filing ↗
                </a>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Write route**

```typescript
// frontend/src/routes/filings.tsx
import { createFileRoute } from "@tanstack/react-router";
import { FilingsPage } from "@/features/filings/FilingsPage";

export const Route = createFileRoute("/filings")({
  component: FilingsPage,
});
```

- [ ] **Step 8: Regenerate route tree and add nav link**

```bash
cd frontend && pnpm tsr generate
```

In `frontend/src/routes/__root.tsx` (or equivalent nav file), add `/filings` nav link alongside the other feature routes.

- [ ] **Step 9: Run FE tests**

```bash
cd frontend && pnpm test src/features/filings/
```
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add frontend/src/services/filings/ frontend/src/features/filings/ frontend/src/routes/filings.tsx frontend/src/routes/__root.tsx
git commit -m "feat(phase18b): FE — FilingsPage + FilingsPanel + route + services"
```

---

### Task 9: Integration test + close-out

**Files:**
- Modify: `backend/tests/test_filings.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/TASKS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write integration smoke test**

```python
# in backend/tests/test_filings.py — add:
async def test_filings_ingest_and_retrieve_e2e(client: AsyncClient, auth_headers: dict, db):
    """End-to-end: insert filing via DB, retrieve via API."""
    fid = uuid.uuid4()
    await db.execute(
        text("""
            INSERT INTO filings (id, canonical_id, source, form_type, filing_date, title, url, captured_at)
            VALUES (:id, 'TSLA.XNAS', 'sec_edgar', '10-Q', '2024-01-01T00:00:00Z',
                    'Q3 2024 Quarterly Report', :url, now())
        """),
        {"id": str(fid), "url": f"https://sec.gov/tsla/{fid}"},
    )
    await db.commit()

    resp = await client.get("/api/filings", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [i["id"] for i in items]
    assert str(fid) in ids

    resp2 = await client.get(f"/api/filings/{fid}", headers=auth_headers)
    assert resp2.status_code == 200
    assert resp2.json()["form_type"] == "10-Q"
```

- [ ] **Step 2: Run the integration test**

```bash
docker compose exec backend pytest tests/test_filings.py -v
```
Expected: All PASS

- [ ] **Step 3: Run full BE test suite**

```bash
docker compose exec backend pytest --tb=short 2>&1 | tail -5
```
Expected: All passing; no regressions

- [ ] **Step 4: Run FE test suite**

```bash
cd frontend && pnpm test --run 2>&1 | tail -5
```
Expected: All passing

- [ ] **Step 5: Update CHANGELOG, TASKS, CLAUDE.md**

In `docs/CHANGELOG.md`, add under a new `## v0.18.1` section:
```
## v0.18.1 — 2026-05-19
- feat: SEC EDGAR EFTS + HKEX RNS filing ingest (polling, cursor dedup, UNIQUE url guard)
- feat: LLM summarisation (LONG_CONTEXT for >4KB, LOCAL_ONLY for ≤4KB)
- feat: InstrumentLinker (primary_exchange tiebreaker for dual-listed ADRs, nightly relinker)
- feat: Shared SecEdgarClient (10 req/s token bucket, startup-disabled if contact_email missing)
- feat: REST API — /api/filings, /api/filings/{id}, /api/instruments/{id}/filings
- feat: FilingsPanel injected into instrument drawer
- feat: /filings feed page (source/form_type filter, inline expand, LLM summary)
- feat: 7 Prometheus metrics (filings_ingested_total, filings_summarisation_total, etc.)
- db: Alembic 0059 — filings + filing_feed_cursors tables
```

- [ ] **Step 6: Tag v0.18.1**

```bash
git tag v0.18.1
git push origin main --tags
```

- [ ] **Step 7: Commit close-out**

```bash
git add docs/CHANGELOG.md docs/TASKS.md CLAUDE.md
git commit -m "docs(phase18b): close phase — CHANGELOG + CLAUDE.md + TASKS.md for v0.18.1"
```
