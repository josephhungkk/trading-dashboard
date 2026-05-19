from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.backtest.commission import CommissionSchedule
from app.backtest.fill_simulator import FillSimulator
from app.bot.base import BarEvent

UTC = UTC

COMMISSION_CFG = {
    "captured_at": "2026-05-19T00:00:00Z",
    "active_broker_id": "schwab",
    "schedules": {"schwab": {"us_equity": 0.0}},
}


def make_bar(open_: float, close: float = 0.0, ts: str = "2024-01-02T09:30:00Z") -> BarEvent:
    return BarEvent(
        canonical_id="AAPL",
        timeframe="1d",
        open=Decimal(str(open_)),
        high=Decimal(str(open_ + 1)),
        low=Decimal(str(open_ - 1)),
        close=Decimal(str(close or open_)),
        volume=Decimal("1000000"),
        ts=datetime.fromisoformat(ts.replace("Z", "+00:00")),
    )


def make_sim(slippage_bps=0.0):
    cs = CommissionSchedule(COMMISSION_CFG)
    return FillSimulator(
        slippage_bps=Decimal(str(slippage_bps)),
        slippage_atr_pct=None,
        commission=cs,
        market_calendar_exchange="NYSE",
    )


def test_buy_fills_at_next_bar_open():
    sim = make_sim()
    fills = []
    order_id = uuid4()
    sim.queue_order(
        order_id=order_id,
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        order_type="MKT",
        limit_price=None,
        tif="GTC",
    )
    bar = make_bar(open_=182.50)
    sim.process_pending_orders(bar, on_fill=fills.append)
    assert len(fills) == 1
    assert fills[0].price == Decimal("182.50")
    assert fills[0].side == "BUY"


def test_buy_adverse_slippage():
    sim = make_sim(slippage_bps=10)
    fills = []
    order_id = uuid4()
    sim.queue_order(
        order_id=order_id,
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        order_type="MKT",
        limit_price=None,
        tif="GTC",
    )
    bar = make_bar(open_=100.0)
    sim.process_pending_orders(bar, on_fill=fills.append)
    # 100 * 10/10000 = 0.10 adverse → 100.10
    assert fills[0].price == Decimal("100.10")


def test_sell_adverse_slippage():
    sim = make_sim(slippage_bps=10)
    fills = []
    order_id = uuid4()
    sim.queue_order(
        order_id=order_id,
        canonical_id="AAPL",
        side="SELL",
        qty=Decimal("100"),
        order_type="MKT",
        limit_price=None,
        tif="GTC",
    )
    bar = make_bar(open_=100.0)
    sim.process_pending_orders(bar, on_fill=fills.append)
    # SELL adverse → lower → 99.90
    assert fills[0].price == Decimal("99.90")


def test_ioc_cancel_if_not_filled():
    sim = make_sim()
    fills = []
    order_id = uuid4()
    # LIMIT order above market — won't fill at bar.open
    sim.queue_order(
        order_id=order_id,
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        order_type="LMT",
        limit_price=Decimal("50.0"),
        tif="IOC",
    )
    bar = make_bar(open_=182.50)
    sim.process_pending_orders(bar, on_fill=fills.append)
    assert len(fills) == 0
    assert len(sim._pending) == 0  # cancelled


def test_gtc_expires_after_90_days():
    sim = make_sim()
    fills = []
    order_id = uuid4()
    from datetime import timedelta

    placed = datetime(2024, 1, 1, tzinfo=UTC)
    sim.queue_order(
        order_id=order_id,
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        order_type="LMT",
        limit_price=Decimal("1.0"),
        tif="GTC",
        placed_at_ts=placed,
    )
    # advance 91 bars (1d each → 91 calendar days past placement)
    for i in range(91):
        bar_ts_val = placed + timedelta(days=i + 1)
        bar_obj = BarEvent(
            canonical_id="AAPL",
            timeframe="1d",
            open=Decimal("182.50"),
            high=Decimal("183.50"),
            low=Decimal("181.50"),
            close=Decimal("182.50"),
            volume=Decimal("1000000"),
            ts=bar_ts_val,
        )
        sim.process_pending_orders(bar_obj, on_fill=fills.append)
    assert len(fills) == 0
    assert len(sim._pending) == 0  # expired


def test_force_close_uses_close_price():
    sim = make_sim(slippage_bps=0)
    fills = []
    order_id = uuid4()
    sim.queue_order(
        order_id=order_id,
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        order_type="MKT",
        limit_price=None,
        tif="GTC",
    )
    sim.process_pending_orders(make_bar(open_=100.0, close=105.0), on_fill=fills.append)
    assert sim.get_position("AAPL") == Decimal("100")
    # force close
    forced = []
    final_bar = make_bar(open_=110.0, close=108.0)
    sim.force_close_open_positions(final_bar, on_fill=forced.append)
    assert len(forced) == 1
    assert forced[0].price == Decimal("108.0")  # close price, not open
    assert forced[0].side == "SELL"
