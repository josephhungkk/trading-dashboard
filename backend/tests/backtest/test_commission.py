from decimal import Decimal

import pytest

from app.backtest.commission import CommissionSchedule

IBKR_CFG = {
    "captured_at": "2026-05-19T00:00:00Z",
    "active_broker_id": "ibkr",
    "schedules": {
        "ibkr": {"per_share": 0.005, "min_per_order": 1.00, "tier": "fixed"},
        "futu": {"per_trade_hkd": 30.0},
        "schwab": {"us_equity": 0.0},
        "alpaca": {"us_equity": 0.0},
    },
}


def test_ibkr_fixed_100_shares():
    cs = CommissionSchedule(IBKR_CFG)
    assert cs.compute("ibkr", qty=Decimal("100")) == Decimal("1.00")


def test_ibkr_fixed_300_shares():
    cs = CommissionSchedule(IBKR_CFG)
    assert cs.compute("ibkr", qty=Decimal("300")) == Decimal("1.50")


def test_futu_flat():
    cfg = {**IBKR_CFG, "active_broker_id": "futu"}
    cs = CommissionSchedule(cfg)
    assert cs.compute("futu", qty=Decimal("500")) == Decimal("30.0")


def test_schwab_zero():
    cfg = {**IBKR_CFG, "active_broker_id": "schwab"}
    cs = CommissionSchedule(cfg)
    assert cs.compute("schwab", qty=Decimal("100")) == Decimal("0")


def test_unknown_broker_raises():
    cs = CommissionSchedule(IBKR_CFG)
    with pytest.raises(ValueError, match="unknown_broker"):
        cs.compute("unknown", qty=Decimal("100"))
