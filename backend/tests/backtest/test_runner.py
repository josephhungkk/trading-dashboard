import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.backtest.runner import BacktestRunner


@pytest.mark.asyncio
async def test_params_schema_drift_fails_fast(db_session):
    redis = AsyncMock()
    redis.exists.return_value = False
    sem = asyncio.Semaphore(2)
    runner = BacktestRunner(db=db_session, redis=redis, semaphore=sem)

    # Insert a backtest row with a mismatched hash
    import uuid

    bt_id = str(uuid.uuid4())
    # (assuming bots row exists — use a fixture or skip if no DB)
    # This test verifies the hash-mismatch path raises ValueError
    with (
        patch.object(runner, "_load_and_start") as mock_load,
        patch("app.backtest.runner.extract_params_schema", return_value={"k": "v"}),
    ):
        mock_load.return_value = {
            "strategy_file": "nonexistent.py",
            "canonical_id": "AAPL",
            "timeframe": "1d",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "bars_source": "db",
            "params_snapshot": {},
            "params_schema_hash": "WRONG_HASH",
            "commission_cfg": {"active_broker_id": "ibkr", "schedules": {}},
            "slippage_bps": "5.0",
            "slippage_atr_pct": None,
        }
        with patch.object(runner, "_set_failed") as mock_fail:
            mock_fail.return_value = None
            await runner._replay(bt_id)
            mock_fail.assert_called_once()
            assert "params_schema_drift" in mock_fail.call_args[0][1]
