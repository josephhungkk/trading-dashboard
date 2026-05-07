from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce

from scripts.empirical.alpaca_equity_oco_paper import run


class EquityClient:
    def __init__(self):
        self.order_data = None
        self.canceled = []

    def submit_order(self, order_data):
        self.order_data = order_data
        legs = [type('Leg', (), {'id': 'oco-1'})(), type('Leg', (), {'id': 'oco-2'})()]
        return type('Order', (), {'id': 'parent', 'legs': legs})()

    def cancel_order_by_id(self, order_id):
        self.canceled.append(order_id)


def test_equity_oco_script_passes_with_two_legs(capsys):
    client = EquityClient()

    assert run(client) == 0

    assert client.order_data.symbol == 'AAPL'
    assert client.order_data.side == OrderSide.SELL
    assert client.order_data.time_in_force == TimeInForce.GTC
    assert client.order_data.order_class == OrderClass.OCO
    assert client.canceled == ['oco-1', 'oco-2']
    assert 'PASS' in capsys.readouterr().out
