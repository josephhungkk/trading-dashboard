"""Tests for marginal-variance gate and correlation vol cache."""

import json
import math
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.orchestrator.exposure_gate import ExposureOutcome, PortfolioExposureGate
from app.services.orchestrator.exposure_gate_lua import _SCRIPT_VERSION

pytestmark = pytest.mark.no_db


class FakeRedis:
    def __init__(self, store: dict | None = None) -> None:
        self._store: dict = store or {}

    async def get(self, key: str) -> bytes | None:
        v = self._store.get(key)
        if isinstance(v, str):
            return v.encode()
        if isinstance(v, bytes):
            return v
        return v

    async def hgetall(self, key: str) -> dict:
        v = self._store.get(key, {})
        return v if isinstance(v, dict) else {}

    async def hset(self, key: str, mapping: dict) -> None:
        self._store[key] = mapping

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def evalsha(self, *args) -> None:
        pass

    async def eval(self, script: str, numkeys: int, *args) -> None:
        pass

    async def script_load(self, script: str) -> str:
        return "fake_sha"

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self._store)


class _FakePipeline:
    def __init__(self, store: dict) -> None:
        self._store = store
        self._cmds: list = []

    def set(self, key: str, val: str, ex: int = 0) -> _FakePipeline:
        self._cmds.append(("set", key, val))
        return self

    async def execute(self) -> list:
        for cmd, key, val in self._cmds:
            if cmd == "set":
                self._store[key] = val
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.execute()


def test_script_version_constant_exists() -> None:
    """_SCRIPT_VERSION must be defined so tests can detect drift."""
    assert isinstance(_SCRIPT_VERSION, int)
    assert _SCRIPT_VERSION > 0


@pytest.mark.asyncio
async def test_mv_opposite_side_hedge_effective_near_zero() -> None:
    """Opposite-side hedge (rho=+1, w_i=-w_new) -> effective ~= 0."""
    account_id = uuid.uuid4()
    instrument_id = 99
    existing_instr = 88

    correlation_matrix = {
        str(existing_instr): {str(instrument_id): 1.0, str(existing_instr): 1.0},
        str(instrument_id): {str(existing_instr): 1.0, str(instrument_id): 1.0},
    }
    redis = FakeRedis(
        {
            f"portfolio:exposure:{account_id}": {
                "total": b"50000",
                f"instr:{existing_instr}": b"-50000",
            },
            f"portfolio:correlation:{account_id}": json.dumps(correlation_matrix).encode(),
            "fx:mid:USD:USD": b"1.0",
        }
    )

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=instrument_id,
        order_notional=Decimal("50000"),
        exposure={
            "total": Decimal("50000"),
            f"instr:{existing_instr}": Decimal("-50000"),
        },
    )
    assert mv is not None
    assert mv < Decimal("1.0")


@pytest.mark.asyncio
async def test_mv_uncorrelated_effective_equals_raw() -> None:
    """Uncorrelated trade (rho=0) -> effective ~= raw_notional."""
    account_id = uuid.uuid4()
    instrument_id = 99
    existing_instr = 88

    correlation_matrix = {
        str(existing_instr): {str(instrument_id): 0.0, str(existing_instr): 1.0},
        str(instrument_id): {str(existing_instr): 0.0, str(instrument_id): 1.0},
    }
    redis = FakeRedis(
        {
            f"portfolio:exposure:{account_id}": {
                "total": b"50000",
                f"instr:{existing_instr}": b"50000",
            },
            f"portfolio:correlation:{account_id}": json.dumps(correlation_matrix).encode(),
        }
    )

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=instrument_id,
        order_notional=Decimal("10000"),
        exposure={
            "total": Decimal("50000"),
            f"instr:{existing_instr}": Decimal("50000"),
        },
    )
    assert mv is not None
    assert abs(mv - Decimal("10000")) < Decimal("1")


@pytest.mark.asyncio
async def test_mv_concentrated_more_restrictive() -> None:
    """Perfectly correlated same-side (rho=+1, w_i=w_new) -> effective = sqrt(3)*raw."""
    account_id = uuid.uuid4()
    instrument_id = 99
    existing_instr = 88

    correlation_matrix = {
        str(existing_instr): {str(instrument_id): 1.0, str(existing_instr): 1.0},
        str(instrument_id): {str(existing_instr): 1.0, str(instrument_id): 1.0},
    }
    redis = FakeRedis(
        {
            f"portfolio:exposure:{account_id}": {
                "total": b"10000",
                f"instr:{existing_instr}": b"10000",
            },
            f"portfolio:correlation:{account_id}": json.dumps(correlation_matrix).encode(),
        }
    )

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=instrument_id,
        order_notional=Decimal("10000"),
        exposure={
            "total": Decimal("10000"),
            f"instr:{existing_instr}": Decimal("10000"),
        },
    )
    assert mv is not None
    assert abs(float(mv) - 10000 * math.sqrt(3)) < 100


@pytest.mark.asyncio
async def test_mv_stale_matrix_falls_back_to_raw() -> None:
    """Matrix absent → _compute_mv_notional returns None → raw notional used."""
    account_id = uuid.uuid4()
    redis = FakeRedis()

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=1,
        order_notional=Decimal("5000"),
        exposure={"total": Decimal("0")},
    )
    assert mv is None


@pytest.mark.asyncio
async def test_mv_disabled_uses_raw_notional() -> None:
    """When marginal_variance_enabled=false, raw notional path, no matrix read."""
    account_id = uuid.uuid4()
    redis = FakeRedis(
        {
            f"portfolio:exposure:{account_id}": {"total": b"0"},
            f"portfolio:correlation:{account_id}": b"{}",
        }
    )

    gate = PortfolioExposureGate(redis)
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),  # gate_enabled
            MagicMock(scalar_one_or_none=MagicMock(return_value='"false"')),  # mv_enabled
            MagicMock(all=MagicMock(return_value=[])),  # _fetch_limits returns empty
        ]
    )

    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
        instrument_sector=None,
    )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_per_sector_limit_blocks() -> None:
    """per_sector limit blocks when sector notional projected > limit."""
    account_id = uuid.uuid4()
    redis = FakeRedis(
        {
            f"portfolio:exposure:{account_id}": {
                "total": b"0",
                "sector:technology": b"90000",
            },
        }
    )

    gate = PortfolioExposureGate(redis)
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),  # gate_enabled
            MagicMock(scalar_one_or_none=MagicMock(return_value='"false"')),  # mv_enabled
            MagicMock(
                all=MagicMock(
                    return_value=[
                        (1, "per_sector", None, Decimal("100000"), "USD", True, "technology"),
                    ]
                )
            ),
        ]
    )

    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("200"),
        currency="USD",
        db=db,
        instrument_sector="technology",
    )
    assert outcome == ExposureOutcome.BLOCK


@pytest.mark.asyncio
async def test_lua_sector_key_written() -> None:
    """update_on_fill passes sector key as third Lua ARGV."""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="sha1")
    redis.evalsha = AsyncMock()
    gate = PortfolioExposureGate(redis)

    await gate.update_on_fill(
        account_id=uuid.uuid4(),
        instrument_id=7,
        signed_delta_usd=Decimal("5000"),
        sector="technology",
    )
    call_args = redis.evalsha.call_args[0]
    assert "sector:technology" in call_args


@pytest.mark.asyncio
async def test_lua_empty_sector_key_when_none() -> None:
    """update_on_fill passes empty string for sector when sector=None."""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="sha1")
    redis.evalsha = AsyncMock()
    gate = PortfolioExposureGate(redis)

    await gate.update_on_fill(
        account_id=uuid.uuid4(),
        instrument_id=7,
        signed_delta_usd=Decimal("5000"),
        sector=None,
    )
    call_args = redis.evalsha.call_args[0]
    assert "" in call_args
