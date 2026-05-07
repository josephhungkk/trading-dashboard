#!/usr/bin/env python3
import os
from decimal import ROUND_HALF_UP, Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest


def expected_prices(reference_price: int | float | Decimal):
    base = Decimal(str(reference_price))
    take_profit = (base * Decimal('1.02')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    stop_loss = (base * Decimal('0.99')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f'{take_profit:.2f}', f'{stop_loss:.2f}'


def build_client() -> TradingClient:
    return TradingClient(os.environ['ALPACA_PAPER_API_KEY'], os.environ['ALPACA_PAPER_API_SECRET'], paper=True)


def run(client=None, reference_price=100) -> int:
    client = client or build_client()
    tp, sl = expected_prices(reference_price)
    order = client.submit_order(
        order_data=MarketOrderRequest(
            symbol='AAPL',
            qty=Decimal('1'),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=Decimal(tp)),
            stop_loss=StopLossRequest(stop_price=Decimal(sl)),
        )
    )
    ids = [str(order.id)] + [str(leg.id) for leg in getattr(order, 'legs', [])]
    if len(ids) != 3:
        print(f'FAIL: expected 3 orders, got {len(ids)}')
        return 1
    for order_id in ids:
        client.cancel_order_by_id(order_id)
    print(f'PASS: bracket parent and children canceled: {ids}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (Exception,) as exc:
        print(f'FAIL: {exc}')
        raise SystemExit(1)
