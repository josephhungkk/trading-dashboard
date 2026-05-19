from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.scanner.scanner_service import CANDIDATE_COUNT_CAP, ScannerService
from app.services.scanner.schemas import ScanConfig, UniverseConfig


def make_svc():
    run_row = MagicMock()
    run_row.id = uuid4()
    rows_result = MagicMock()
    rows_result.fetchone.return_value = run_row
    rows_result.fetchall.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=rows_result)
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)

    db_factory = MagicMock(return_value=db)

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.publish = AsyncMock()

    svc = ScannerService(db_factory=db_factory, redis=redis, cfg=MagicMock())
    return svc


@pytest.mark.asyncio
async def test_run_scan_no_matches():
    svc = make_svc()

    with (
        patch("app.services.scanner.scanner_service.UniverseResolver") as mock_ur,
        patch("app.services.scanner.scanner_service.IndicatorComputer") as mock_ic,
    ):
        mock_ur.return_value.resolve = AsyncMock(return_value=["AAPL"])
        mock_ic.return_value.compute = AsyncMock(return_value=28.0)

        config = ScanConfig(
            name="test",
            universe_config=UniverseConfig(type="tickers", params={"tickers": ["AAPL"]}),
            rule_expr="rsi(14) < 10",
            llm_depth="quick",
        )
        run_id = await svc.run_scan(config=config, scan_id=None)
        assert run_id is not None


def test_candidate_count_cap():
    assert CANDIDATE_COUNT_CAP == 500
