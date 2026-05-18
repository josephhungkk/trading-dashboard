"""Tests for algo risk gate checks."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.risk_service import EvaluationContext, RiskService

pytestmark = pytest.mark.no_db


_CTX_BASE = {
    "account_id": UUID("00000000-0000-0000-0000-000000000001"),
    "broker_id": "ibkr",
    "instrument_id": 1,
    "side": "BUY",
    "qty": Decimal("500"),
    "price": Decimal("100"),
    "order_type": "LIMIT",
    "time_in_force": "DAY",
    "request_id": "test-req",
    "currency_base": "USD",
    "asset_class": "STOCK",
}


def _make_svc() -> RiskService:
    svc = RiskService.__new__(RiskService)
    svc._db = MagicMock()
    svc._redis = MagicMock()
    svc._config = MagicMock()
    svc._sidecar = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_check_iceberg_display_size_none() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(**{**_CTX_BASE, "algo_strategy": "ICEBERG", "algo_params": {}})
    result = await svc._check_iceberg_display_size(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert blocker.code == "display_size_required"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_malformed() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(
        **{
            **_CTX_BASE,
            "algo_strategy": "ICEBERG",
            "algo_params": {"display_size": "not_a_number"},
        }
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert blocker.code == "display_size_malformed"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_nonpositive() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(
        **{**_CTX_BASE, "algo_strategy": "ICEBERG", "algo_params": {"display_size": "0"}}
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert blocker.code == "display_size_nonpositive"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_gte_qty() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(
        **{
            **_CTX_BASE,
            "qty": Decimal("100"),
            "algo_strategy": "ICEBERG",
            "algo_params": {"display_size": "100"},
        }
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert blocker.code == "display_size_gte_qty"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_sub_lot_warns() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(
        **{
            **_CTX_BASE,
            "qty": Decimal("500"),
            "algo_strategy": "ICEBERG",
            "algo_params": {"display_size": "0.5"},
        }
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is not None
    blocker, warning = result
    assert blocker is None
    assert warning is not None
    assert warning.code == "display_size_sub_lot"


@pytest.mark.asyncio
async def test_check_iceberg_display_size_valid_passes() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(
        **{
            **_CTX_BASE,
            "qty": Decimal("500"),
            "algo_strategy": "ICEBERG",
            "algo_params": {"display_size": "50"},
        }
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_check_iceberg_display_size_skipped_for_non_display_algo() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(
        **{
            **_CTX_BASE,
            "algo_strategy": "TWAP",
            "algo_params": {"start_time": "10:00", "end_time": "14:00"},
        }
    )
    result = await svc._check_iceberg_display_size(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_check_algo_capability_skipped_when_no_strategy() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(**_CTX_BASE)
    result = await svc._check_algo_capability(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_check_algo_capability_blocks_unsupported() -> None:
    svc = _make_svc()
    ctx = EvaluationContext(**{**_CTX_BASE, "algo_strategy": "TWAP"})
    with patch(
        "app.services.algo.capability_service.AlgoCapabilityService.get_strategies",
        new=AsyncMock(return_value=[]),
    ):
        with patch("app.services.risk_service.AlgoCapabilityService") as mock_svc_cls:
            mock_svc_instance = MagicMock()
            mock_svc_instance.get_strategies = AsyncMock(return_value=[])
            mock_svc_cls.return_value = mock_svc_instance
            result = await svc._check_algo_capability(ctx)
    assert result is not None
    blocker, _ = result
    assert blocker is not None
    assert blocker.code == "unsupported_algo_strategy"
