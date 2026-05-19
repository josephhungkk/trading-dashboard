from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.common.sec_edgar_client import SecEdgarClient, SecEdgarClientDisabledError

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_sec_edgar_client_disabled_when_no_email():
    client = SecEdgarClient(contact_email=None)
    with pytest.raises(SecEdgarClientDisabledError):
        await client.get("https://efts.sec.gov/LATEST/search-index?q=test")


@pytest.mark.asyncio
async def test_sec_edgar_client_adds_user_agent():
    client = SecEdgarClient(contact_email="test@example.com")
    captured_headers: dict = {}

    class MockResp:
        status_code = 200

        def json(self):
            return {"hits": {"hits": []}}

    async def fake_get(url, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return MockResp()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.get = fake_get
        mock_cls.return_value = mock_instance

        await client.get("https://efts.sec.gov/LATEST/search-index?q=test")

    assert "Trading Dashboard" in captured_headers.get("User-Agent", "")
    assert "test@example.com" in captured_headers.get("User-Agent", "")


@pytest.mark.asyncio
async def test_instrument_linker_returns_none_on_miss():
    from app.services.filings.instrument_linker import InstrumentLinker

    db = AsyncMock()
    rows_result = MagicMock()
    rows_result.fetchone.return_value = None
    db.execute = AsyncMock(return_value=rows_result)
    linker = InstrumentLinker(db)
    iid, cid = await linker.link(source="sec_edgar", ticker="UNKNOWN_XYZ")
    assert iid is None
    assert cid == "UNKNOWN_XYZ"


@pytest.mark.asyncio
async def test_instrument_linker_returns_match():
    from app.services.filings.instrument_linker import InstrumentLinker

    db = AsyncMock()
    r = MagicMock()
    r.id = 42
    r.canonical_id = "AAPL.XNAS"
    rows_result = MagicMock()
    rows_result.fetchone.return_value = r
    db.execute = AsyncMock(return_value=rows_result)
    linker = InstrumentLinker(db)
    iid, cid = await linker.link(source="sec_edgar", ticker="AAPL")
    assert iid == 42
    assert cid == "AAPL.XNAS"


def test_filing_row_validates():
    import uuid
    from datetime import datetime

    from app.services.filings.schemas import FilingRow

    f = FilingRow(
        id=uuid.uuid4(),
        canonical_id="AAPL.XNAS",
        source="sec_edgar",
        form_type="8-K",
        filing_date=datetime.now(UTC),
        title="Test",
        url="https://sec.gov/test",
        captured_at=datetime.now(UTC),
    )
    assert f.source == "sec_edgar"


def test_filing_row_requires_instrument_or_canonical():
    import uuid
    from datetime import datetime

    from app.services.filings.schemas import FilingRow

    with pytest.raises(ValueError):
        FilingRow(
            id=uuid.uuid4(),
            instrument_id=None,
            canonical_id=None,
            source="sec_edgar",
            form_type="8-K",
            filing_date=datetime.now(UTC),
            title="x",
            url="https://sec.gov/x",
            captured_at=datetime.now(UTC),
        )
