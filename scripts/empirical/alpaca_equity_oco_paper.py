#!/usr/bin/env python3
import os
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest


def build_client() -> TradingClient:
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
    order = client.submit_order(
        order_data=LimitOrderRequest(
            symbol='AAPL',
            qty=Decimal('1'),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OCO,
            limit_price=Decimal('200.00'),
            stop_price=Decimal('50.00'),
        )
    )
    legs = getattr(order, 'legs', [])
    if len(legs) != 2:
        print(f'FAIL: expected 2 OCO legs, got {len(legs)}')
        return 1
    ids = [str(leg.id) for leg in legs]
    for order_id in ids:
        client.cancel_order_by_id(order_id)
    print(f'PASS: OCO legs canceled: {ids}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (Exception,) as exc:
        print(f'FAIL: {exc}')
        raise SystemExit(1)
