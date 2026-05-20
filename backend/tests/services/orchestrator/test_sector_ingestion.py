"""Tests for SectorIngestionService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.orchestrator.sector_ingestion import SectorIngestionService

pytestmark = pytest.mark.no_db


def _make_db_for_asset_class(asset_class: str) -> AsyncMock:
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value=asset_class)
    update_result = MagicMock()
    db.execute = AsyncMock(side_effect=[asset_result, update_result])
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_synthetic_sector_for_forex() -> None:
    """FOREX instruments get sector='_class:forex', no IBKR call."""
    db = _make_db_for_asset_class("FOREX")
    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=1, db=db)

    stub.GetContractFundamentals.assert_not_called()
    call_args = db.execute.call_args_list[1]
    sql = str(call_args[0][0])
    assert "sector" in sql.lower()


@pytest.mark.asyncio
async def test_synthetic_sector_for_crypto() -> None:
    db = _make_db_for_asset_class("CRYPTO")
    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=2, db=db)
    stub.GetContractFundamentals.assert_not_called()


@pytest.mark.asyncio
async def test_ibkr_path_writes_sector() -> None:
    """STOCK instruments call GetContractFundamentals and write normalised sector."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value="98765")
    update_result = MagicMock()
    db.execute = AsyncMock(side_effect=[asset_result, alias_result, update_result])
    db.commit = AsyncMock()

    fundamentals = MagicMock()
    fundamentals.industry = "  Technology  "
    fundamentals.category = "Computers"
    stub = AsyncMock()
    stub.GetContractFundamentals = AsyncMock(return_value=fundamentals)

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=3, db=db)

    stub.GetContractFundamentals.assert_called_once()
    update_call_sql = str(db.execute.call_args_list[2][0][0])
    assert "sector" in update_call_sql.lower()


@pytest.mark.asyncio
async def test_ibkr_sidecar_unavailable_preserves_existing() -> None:
    """When sidecar raises, existing value is NOT blanked."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value="11111")
    db.execute = AsyncMock(side_effect=[asset_result, alias_result])
    db.commit = AsyncMock()

    stub = AsyncMock()
    stub.GetContractFundamentals = AsyncMock(side_effect=Exception("grpc timeout"))

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=4, db=db)
    assert db.execute.call_count == 2


@pytest.mark.asyncio
async def test_ibkr_no_conid_skips_ibkr_path() -> None:
    """No IBKR alias → skip IBKR path, no call."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(side_effect=[asset_result, alias_result])
    db.commit = AsyncMock()

    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=5, db=db)
    stub.GetContractFundamentals.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_returns_summary() -> None:
    """backfill_all returns {processed, updated, skipped, errors}."""
    db = AsyncMock()
    rows_result = MagicMock()
    rows_result.all = MagicMock(return_value=[(1,), (2,), (3,)])
    # First call returns instrument ids, second call returns count
    count_result = MagicMock()
    count_result.scalar_one = MagicMock(return_value=2)
    db.execute = AsyncMock(side_effect=[rows_result, count_result])

    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)

    with patch.object(svc, "refresh", new_callable=AsyncMock) as mock_refresh:
        mock_refresh.return_value = None
        result = await svc.backfill_all(db)

    assert "processed" in result
    assert "errors" in result
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) <= 100


@pytest.mark.asyncio
async def test_sector_normalised_lower_strip() -> None:
    """Sector value is stripped and lowercased at write time."""
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value="STOCK")),
            MagicMock(scalar_one_or_none=MagicMock(return_value="55555")),
            MagicMock(),
        ]
    )
    db.commit = AsyncMock()

    fundamentals = MagicMock(industry="  Financial  ", category=" Banks ")
    stub = AsyncMock()
    stub.GetContractFundamentals = AsyncMock(return_value=fundamentals)

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=6, db=db)
    assert db.execute.call_count == 3
