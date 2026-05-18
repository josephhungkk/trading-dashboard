"""Phase 15a: _check_forex_exposure risk gate tests."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_ctx(**kwargs):
    from app.services.risk_service import EvaluationContext

    defaults = {
        "account_id": uuid.uuid4(),
        "broker_id": "ibkr",
        "instrument_id": 1,
        "side": "BUY",
        "qty": Decimal("10000"),
        "price": Decimal("1.0800"),
        "order_type": "MARKET",
        "time_in_force": "IOC",
        "request_id": "req-001",
        "currency_base": "USD",
        "asset_class": "FOREX",
        "notional": Decimal("10800"),
    }
    defaults.update(kwargs)
    return EvaluationContext(**defaults)


def _make_risk_service():
    from app.services.risk_service import RiskService

    db = AsyncMock()
    redis = AsyncMock()
    cfg = MagicMock()
    cfg.get_bool = AsyncMock(return_value=False)
    svc = RiskService(db=db, redis=redis, config=cfg, sidecar=MagicMock())
    return svc


async def test_check_forex_blocks_when_session_closed():
    svc = _make_risk_service()
    with patch("app.services.risk_service.is_forex_session_open", return_value=False):
        ctx = _make_ctx()
        result = await svc._check_forex_exposure(ctx)
    assert result is not None
    blocker, warning = result
    assert blocker is not None
    assert blocker.code == "session_closed"
    assert warning is None


async def test_check_forex_blocks_notional_cap():
    svc = _make_risk_service()
    with patch("app.services.risk_service.is_forex_session_open", return_value=True):
        svc._resolve_limit = AsyncMock(return_value=MagicMock(limit_value=Decimal("5000")))
        ctx = _make_ctx(notional=Decimal("10000"))
        result = await svc._check_forex_exposure(ctx)
    assert result is not None
    blocker, _warning = result
    assert blocker is not None
    assert blocker.code == "forex_notional_exceeded"


async def test_check_forex_passes_when_open_no_cap():
    svc = _make_risk_service()
    with patch("app.services.risk_service.is_forex_session_open", return_value=True):
        svc._resolve_limit = AsyncMock(return_value=None)
        ctx = _make_ctx()
        result = await svc._check_forex_exposure(ctx)
    assert result is not None
    blocker, _warning = result
    assert blocker is None
