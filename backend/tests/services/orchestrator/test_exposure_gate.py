from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.services.fx import get_fx_rate
from app.services.orchestrator.exposure_gate import ExposureOutcome, PortfolioExposureGate

pytestmark = pytest.mark.no_db


class FakeRedis:
    def __init__(self, store: dict[str, object] | None = None) -> None:
        self._store: dict[str, object] = store or {}

    async def get(self, key: str) -> bytes | None:
        v = self._store.get(key)
        if isinstance(v, str):
            return v.encode()
        if isinstance(v, bytes):
            return v
        return None

    async def hgetall(self, key: str) -> dict[object, object]:
        v = self._store.get(key, {})
        return v if isinstance(v, dict) else {}

    async def evalsha(self, *args: object, **kwargs: object) -> None:
        pass

    async def eval(self, script: str, numkeys: int, *args: object) -> None:
        pass

    async def script_load(self, script: str) -> str:
        return "fake_sha"


@pytest.mark.asyncio
async def test_get_fx_rate_usd_identity() -> None:
    redis = FakeRedis()
    rate = await get_fx_rate("USD", redis)
    assert rate == Decimal("1.0")


@pytest.mark.asyncio
async def test_get_fx_rate_cache_hit() -> None:
    redis = FakeRedis({"fx:mid:GBP:USD": "1.27"})
    rate = await get_fx_rate("GBP", redis)
    assert rate == Decimal("1.27")


@pytest.mark.asyncio
async def test_get_fx_rate_cache_miss_returns_one() -> None:
    redis = FakeRedis({})
    rate = await get_fx_rate("EUR", redis)
    assert rate == Decimal("1.0")


@pytest.mark.asyncio
async def test_exposure_gate_allow_under_limit() -> None:
    import uuid

    account_id = uuid.uuid4()
    redis = FakeRedis({f"portfolio:exposure:{account_id}": {"total": b"50000.0"}})
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[(1, "total_notional", None, Decimal("100000"), "USD", True)]
            )
        )
    )
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_exposure_gate_block_over_limit() -> None:
    import uuid

    account_id = uuid.uuid4()
    redis = FakeRedis({f"portfolio:exposure:{account_id}": {"total": b"95000.0"}})
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[(1, "total_notional", None, Decimal("100000"), "USD", True)]
            )
        )
    )
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("200"),
        price=Decimal("60"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.BLOCK


@pytest.mark.asyncio
async def test_exposure_gate_redis_miss_pg_fallback() -> None:
    import uuid

    account_id = uuid.uuid4()
    redis = FakeRedis({})

    gate_enabled_result = MagicMock()
    gate_enabled_result.scalar_one_or_none = MagicMock(return_value=None)

    pg_fallback_result = MagicMock()
    pg_fallback_result.scalar_one_or_none = MagicMock(return_value=Decimal("30000"))

    limits_result = MagicMock()
    limits_result.all = MagicMock(
        return_value=[(1, "total_notional", None, Decimal("100000"), "USD", True)]
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[gate_enabled_result, pg_fallback_result, limits_result])
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_exposure_gate_redis_miss_pg_miss_fail_closed() -> None:
    import uuid

    account_id = uuid.uuid4()
    redis = FakeRedis({})
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=OperationalError("conn", {}, Exception("db down")))
    gate = PortfolioExposureGate(redis=redis)
    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
    )
    assert outcome == ExposureOutcome.BLOCK


@pytest.mark.asyncio
async def test_exposure_gate_kill_switch_disabled() -> None:
    import uuid

    account_id = uuid.uuid4()
    redis = FakeRedis({f"portfolio:exposure:{account_id}": {"total": b"999999.0"}})
    db = AsyncMock()
    gate = PortfolioExposureGate(redis=redis)
    with patch.object(gate, "_gate_enabled", return_value=False):
        outcome = await gate.check(
            account_id=account_id,
            instrument_id=1,
            qty=Decimal("1000"),
            price=Decimal("200"),
            currency="USD",
            db=db,
        )
    assert outcome == ExposureOutcome.ALLOW
