from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cgt.hmrc_rates import fetch_and_store_rates

FIXTURE_XML = (
    Path(__file__).parent.parent.parent / "fixtures/hmrc_rates/monthly_xml_2025-01.xml"
).read_bytes()


@pytest.mark.asyncio
async def test_parse_xml_and_upsert():
    """fetch_and_store_rates() parses XML and upserts hmrc_fx_rates rows."""
    session = MagicMock()
    session.execute = AsyncMock()

    with patch("app.services.cgt.hmrc_rates._fetch_xml", AsyncMock(return_value=FIXTURE_XML)):
        await fetch_and_store_rates(date(2025, 1, 1), session)

    # Should have called execute for USD, HKD, EUR (all in _TARGET_CURRENCIES)
    assert session.execute.call_count >= 3


@pytest.mark.asyncio
async def test_fetch_http_error_logged():
    """HTTP error should log and not raise (job continues on next month)."""
    session = MagicMock()
    session.execute = AsyncMock()
    with patch("app.services.cgt.hmrc_rates._fetch_xml", AsyncMock(side_effect=Exception("404"))):
        # Should not raise
        await fetch_and_store_rates(date(2025, 3, 1), session)
