from __future__ import annotations

import uuid
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cgt.importers import ibkr_flex

SAMPLE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse queryName="CGT" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U12345" fromDate="2025-01-01" toDate="2025-01-31">
      <Trades>
        <Trade accountId="U12345" currency="USD" assetCategory="STK"
               symbol="AAPL" isin="US0378331005"
               tradeDate="2025-01-15" dateTime="2025-01-15;12:00:00"
               quantity="100" tradePrice="150.00" tradeMoney="15000"
               ibCommission="-9.99" ibCommissionCurrency="USD"
               buySell="BUY" tradeID="T1001" ibExecID="E1001"
               exchange="NASDAQ" />
      </Trades>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


@pytest.fixture
def mock_account():
    return uuid.uuid4()


@pytest.fixture
def mock_session():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.fetchone.return_value = None
    result.fetchall.return_value = []
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=ctx)
    return session


@pytest.mark.asyncio
async def test_dedup_sha256_skips_on_conflict(mock_session, mock_account):
    """Second import of same SHA256 returns skipped=True."""
    insert_result = MagicMock()
    insert_result.fetchone.return_value = None  # ON CONFLICT DO NOTHING → no row

    with patch(
        "app.services.cgt.importers.ibkr_flex._fetch_flex_xml", AsyncMock(return_value=SAMPLE_XML)
    ):
        mock_session.execute = AsyncMock(return_value=insert_result)
        result = await ibkr_flex.run_import(mock_account, "tok", "qid", mock_session)

    assert result.get("skipped") is True
    assert result["trades_imported"] == 0


@pytest.mark.asyncio
async def test_run_import_returns_stmt_row(mock_session, mock_account):
    """When broker_statements INSERT returns a row, processing proceeds."""
    stmt_row = MagicMock()
    stmt_row.id = uuid.uuid4()

    call_count = 0

    async def execute_side_effect(q, params=None):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        if call_count == 1:
            # broker_statements INSERT → returns stmt_row
            r.fetchone.return_value = stmt_row
        else:
            r.fetchone.return_value = None
            r.scalar_one_or_none.return_value = None
        r.fetchall.return_value = []
        return r

    mock_session.execute = execute_side_effect

    from datetime import datetime
    from types import SimpleNamespace

    fake_trade = SimpleNamespace(
        symbol="AAPL",
        isin="US0378331005",
        currency="USD",
        ibCommissionCurrency="USD",
        buySell=SimpleNamespace(name="BUY"),
        quantity=100,
        tradePrice=150.0,
        ibCommission=-9.99,
        tradeID="T1001",
        ibExecID="E1001",
        assetCategory="STK",
        dateTime=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
    )
    fake_flex = SimpleNamespace(FlexStatements=[SimpleNamespace(Trades=[fake_trade])])

    import sys
    import types as _types

    fake_ibflex_mod = _types.ModuleType("ibflex")
    fake_ibflex_mod.parse = lambda _b: fake_flex  # type: ignore[attr-defined]
    sys.modules.setdefault("ibflex", fake_ibflex_mod)

    with patch(
        "app.services.cgt.importers.ibkr_flex._fetch_flex_xml", AsyncMock(return_value=SAMPLE_XML)
    ):
        with patch(
            "app.services.cgt.importers.ibkr_flex._resolve_instrument",
            AsyncMock(return_value=(99, "US0378331005")),
        ):
            with patch(
                "app.services.cgt.importers.ibkr_flex.to_gbp",
                AsyncMock(return_value=(100.0, 1.27, "hmrc_monthly")),
            ):
                with patch("app.services.cgt.engine.process", AsyncMock()):
                    result = await ibkr_flex.run_import(mock_account, "tok", "qid", mock_session)

    assert "trades_imported" in result
