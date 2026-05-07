from scripts.empirical.alpaca_crypto_place_cancel_paper import normalize_input_symbol, run


class FakeClient:
    def __init__(self):
        self.cancelled = []

    def submit_order(self, order_data):
        assert order_data.symbol == 'BTC/USD'
        assert str(order_data.notional) == '1.00'
        return type('Order', (), {'id': 'crypto-paper-1'})()

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)


def test_normalize_input_symbol():
    assert normalize_input_symbol('BTCUSD') == 'BTC/USD'


def test_crypto_script_passes_with_fake_client(capsys):
    assert run(FakeClient(), cash_amount='1.00') == 0
    assert 'PASS' in capsys.readouterr().out
