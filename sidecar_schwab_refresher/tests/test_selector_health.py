"""Phase 7a E3 — H2 selector health probe."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab_refresher.selectors import probe_selectors, SelectorHealthError


@pytest.mark.asyncio
async def test_all_selectors_present_returns_true():
    page = MagicMock()
    locator = MagicMock()
    locator.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=locator)
    result = await probe_selectors(page)
    assert result is True
    assert page.locator.call_count == 3


@pytest.mark.asyncio
async def test_missing_selector_raises():
    page = MagicMock()
    locator = MagicMock()
    locator.wait_for = AsyncMock(side_effect=Exception("Timeout"))
    page.locator = MagicMock(return_value=locator)
    with pytest.raises(SelectorHealthError, match="missing"):
        await probe_selectors(page)
