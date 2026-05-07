#!/usr/bin/env python3
import os
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


def normalize_input_symbol(symbol: str) -> str:
    if symbol == 'BTCUSD':
        return 'BTC/USD'
    return symbol


def build_client() -> TradingClient:
    return TradingClient(os.environ['ALPACA_PAPER_API_KEY'], os.environ['ALPACA_PAPER_API_SECRET'], paper=True)


def run(client=None, cash_amount: str = '1.00') -> int:
    client = client or build_client()
    request = MarketOrderRequest(
        symbol=normalize_input_symbol('BTCUSD'),
        notional=Decimal(cash_amount),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    object.__setattr__(request, 'notional', Decimal(cash_amount))
    order = client.submit_order(order_data=request)
    order_id = str(order.id)
    if not order_id:
        print('FAIL: missing order id')
        return 1
    client.cancel_order_by_id(order_id)
    print(f'PASS: placed and canceled BTC/USD paper notional order {order_id}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (Exception,) as exc:
        print(f'FAIL: {exc}')
        raise SystemExit(1)
