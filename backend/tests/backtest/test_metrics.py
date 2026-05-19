from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.backtest.metrics import ClosedTrade, MetricsComputer

UTC = UTC


def make_trade(pnl: float, forced: bool = False) -> ClosedTrade:
    return ClosedTrade(
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        entry_price=Decimal("100"),
        exit_price=Decimal(str(100 + pnl)),
        entry_slippage=Decimal("0"),
        exit_slippage=Decimal("0"),
        commission=Decimal("0"),
        pnl=Decimal(str(pnl)),
        forced_close=forced,
        opened_at=datetime(2024, 1, 2, tzinfo=UTC),
        closed_at=datetime(2024, 1, 3, tzinfo=UTC),
    )


def make_bar_ts(days: int) -> datetime:
    from datetime import timedelta

    return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=days)


def test_sharpe_none_when_no_trades():
    mc = MetricsComputer(exchange="NYSE")
    bar_ts = [make_bar_ts(i) for i in range(5)]
    result = mc.compute([], bar_ts)
    assert result["sharpe"] is None


def test_total_return_and_win_rate():
    mc = MetricsComputer(exchange="NYSE")
    trades = [make_trade(100), make_trade(-50), make_trade(200)]
    bar_ts = [make_bar_ts(i) for i in range(10)]
    result = mc.compute(trades, bar_ts)
    assert result["total_trades"] == 3
    assert result["win_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_forced_close_pnl_aggregate():
    mc = MetricsComputer(exchange="NYSE")
    trades = [make_trade(100), make_trade(-20, forced=True)]
    bar_ts = [make_bar_ts(i) for i in range(5)]
    result = mc.compute(trades, bar_ts)
    assert result["forced_close_pnl"] == Decimal("-20")


def test_max_drawdown_non_negative():
    mc = MetricsComputer(exchange="NYSE")
    trades = [make_trade(-500)]
    bar_ts = [make_bar_ts(i) for i in range(5)]
    result = mc.compute(trades, bar_ts)
    assert result["max_drawdown_pct"] >= 0


def test_drawdown_curve_non_negative():
    mc = MetricsComputer(exchange="NYSE")
    trades = [make_trade(-100), make_trade(50)]
    bar_ts = [make_bar_ts(i) for i in range(5)]
    result = mc.compute(trades, bar_ts)
    for _, dd in result["drawdown_curve"]:
        assert dd >= 0


def test_empty_bars_returns_empty_report():
    mc = MetricsComputer(exchange="NYSE")
    result = mc.compute([], [])
    assert result["total_trades"] == 0
    assert result["pnl_curve"] == []
