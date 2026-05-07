#!/usr/bin/env python3
import os
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest


def build_client():
    missing = [
        k for k in ('ALPACA_PAPER_API_KEY', 'ALPACA_PAPER_API_SECRET')
        if not os.environ.get(k)
    ]
    if missing:
        raise ValueError(f'Missing env vars: {", ".join(missing)}')
    return TradingClient(
        os.environ['ALPACA_PAPER_API_KEY'],
        os.environ['ALPACA_PAPER_API_SECRET'],
        paper=True,
    )


def run(client=None) -> int:
    client = client or build_client()
    try:
        order = client.submit_order(
            order_data=MarketOrderRequest(
                symbol='BTC/USD',
                notional=Decimal('1.00'),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=Decimal('200000.00')),
                stop_loss=StopLossRequest(stop_price=Decimal('1.00')),
            )
        )
    except (Exception,) as exc:
        print(f'EXPECTED_FAIL: {exc}')
        return 0
    print(f'UNEXPECTED_PASS: {order.id}')
    return 1


if __name__ == '__main__':
    raise SystemExit(run())
