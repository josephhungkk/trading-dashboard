from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.orchestrator.correlation import CorrelationService


def _make_db_with_bars(bars_by_instrument: dict[int, list[float]]) -> AsyncMock:
    """Return a mock DB that yields bars_1d rows per instrument.

    Input prices are newest-first (DESC order), matching what the DB returns.
    The service calls reversed() on this to get oldest-first for log returns.
    """

    async def execute_side_effect(stmt, params=None, **kwargs):
        iid = (params or {}).get("iid")
        rows = bars_by_instrument.get(iid, [])
        mock_result = MagicMock()
        mock_result.all.return_value = [(r,) for r in rows]
        return mock_result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute_side_effect)
    db.commit = AsyncMock()
    return db


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict = {}
        self._set_calls: list = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value
        self._set_calls.append((key, value, ex))

    async def get(self, key: str) -> bytes | None:
        v = self._store.get(key)
        return v.encode() if isinstance(v, str) else v


@pytest.mark.asyncio
async def test_correlation_two_instruments_identical_returns() -> None:
    """Two instruments with identical returns => rho = 1.0."""
    import uuid

    # newest-first (DESC), reversed() in service gives oldest-first
    prices = [111.0, 109.0, 107.0, 108.0, 106.0, 104.0, 105.0, 103.0, 101.0, 102.0, 100.0]
    db = _make_db_with_bars({1: prices, 2: prices})
    redis = FakeRedis()

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    matrix = await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1, 2],
        db=db,
        window_days=30,
    )
    assert abs(matrix["1"]["2"] - 1.0) < 1e-6
    assert abs(matrix["2"]["1"] - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_correlation_negative_rho() -> None:
    """Instruments with inverse returns => rho ~= -1.0."""
    import uuid

    # Alternating up/down pattern — newest-first (DESC)
    # up_asc (after reversed): 100,110,90,115,85,120,80,125,75,130,70
    # down_asc: mirror image — goes opposite on each step
    up = [70.0, 75.0, 80.0, 85.0, 90.0, 100.0, 115.0, 90.0, 130.0, 85.0, 100.0]
    # down perfectly mirrors up: when up rises, down falls
    down = [130.0, 125.0, 120.0, 115.0, 110.0, 100.0, 85.0, 110.0, 70.0, 115.0, 100.0]
    db = _make_db_with_bars({1: up, 2: down})
    redis = FakeRedis()

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    matrix = await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1, 2],
        db=db,
        window_days=30,
    )
    assert matrix["1"]["2"] < -0.95


@pytest.mark.asyncio
async def test_correlation_insufficient_bars_excluded() -> None:
    """Instrument with < 11 bars is excluded from matrix."""
    import uuid

    # newest-first (DESC)
    enough = [111.0, 110.0, 109.0, 108.0, 107.0, 106.0, 105.0, 103.0, 104.0, 102.0, 100.0]
    db = _make_db_with_bars({1: enough, 2: [100.0]})
    redis = FakeRedis()

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    matrix = await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1, 2],
        db=db,
        window_days=30,
    )
    # instrument 2 excluded — not in matrix at all or not cross-referenced
    assert "2" not in matrix or "2" not in matrix.get("1", {})


@pytest.mark.asyncio
async def test_correlation_redis_ttl_set() -> None:
    """Redis key is set with TTL=86400."""
    import uuid

    # newest-first (DESC)
    prices = [100.0 + i for i in range(11, -1, -1)]
    db = _make_db_with_bars({1: prices})
    redis = FakeRedis()

    account_id = uuid.uuid4()
    svc = CorrelationService(redis=redis)
    await svc.compute_and_store(
        account_id=account_id,
        instrument_ids=[1],
        db=db,
        window_days=30,
    )
    assert len(redis._set_calls) == 1
    _key, _val, ex = redis._set_calls[0]
    assert ex == 86400
