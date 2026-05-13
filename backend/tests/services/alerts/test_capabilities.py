"""Phase 11b chunk A5: capability registry seed-if-missing tests.

Single-source-of-truth lives in ``app_config[alert_capabilities/capability_map]``
(HIGH-7 — no parallel SQL table). ``ensure_seeded`` is idempotent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.alerts.capabilities import (
    KEY,
    NAMESPACE,
    ensure_seeded,
    get_capability_map,
)

pytestmark = pytest.mark.asyncio


async def _clear_namespace(session: AsyncSession) -> None:
    await session.execute(
        text("DELETE FROM app_config WHERE namespace = :ns"),
        {"ns": NAMESPACE},
    )
    await session.commit()


async def test_ensure_seeded_creates_default_namespace(session: AsyncSession) -> None:
    await _clear_namespace(session)
    await ensure_seeded(session)
    caps = await get_capability_map(session)
    assert caps["news_feed"]["available"] is False
    assert caps["filings_feed"]["available"] is False
    assert caps["earnings_calendar"]["available"] is False


async def test_ensure_seeded_is_idempotent(session: AsyncSession) -> None:
    await _clear_namespace(session)
    await ensure_seeded(session)
    await ensure_seeded(session)
    caps = await get_capability_map(session)
    assert "news_feed" in caps
    row_count = (
        await session.execute(
            text("SELECT count(*) FROM app_config WHERE namespace = :ns AND key = :k"),
            {"ns": NAMESPACE, "k": KEY},
        )
    ).scalar_one()
    assert row_count == 1


async def test_get_capability_map_returns_empty_when_unseeded(
    session: AsyncSession,
) -> None:
    await _clear_namespace(session)
    caps = await get_capability_map(session)
    assert caps == {}
