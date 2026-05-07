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
    try:
        order = client.submit_order(
            order_data=LimitOrderRequest(
                symbol='BTC/USD',
                qty=Decimal('0.001'),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OCO,
                limit_price=Decimal('200000.00'),
                stop_price=Decimal('50000.00'),
            )
        )
    except (Exception,) as exc:
        print(f'EXPECTED_FAIL: {exc}')
        return 0
    print(f'UNEXPECTED_PASS: {order.id}')
    return 1


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (Exception,) as exc:
        # Mirrors equity script: any exception outside run()'s try/except
        # (e.g. missing env vars in build_client) prints a friendly FAIL
        # instead of a raw traceback (chunk-OCO spec MED-3).
        print(f'FAIL: {exc}')
        raise SystemExit(1)
