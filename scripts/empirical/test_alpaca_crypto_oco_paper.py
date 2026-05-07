from alpaca.trading.enums import OrderClass, TimeInForce

from scripts.empirical.alpaca_crypto_oco_paper import run


class CryptoFailClient:
    def __init__(self):
        self.order_data = None

    def submit_order(self, order_data):
        self.order_data = order_data
        raise RuntimeError('OCO orders are not supported for crypto')


class CryptoPassClient:
    def submit_order(self, order_data):
        return type('Order', (), {'id': 'unexpected'})()


def test_crypto_oco_expected_fail(capsys):
    client = CryptoFailClient()

    assert run(client) == 0

    assert client.order_data.symbol == 'BTC/USD'
    assert client.order_data.time_in_force == TimeInForce.GTC
    assert client.order_data.order_class == OrderClass.OCO
    assert 'EXPECTED_FAIL' in capsys.readouterr().out


def test_crypto_oco_unexpected_pass(capsys):
    assert run(CryptoPassClient()) == 1
    assert 'UNEXPECTED_PASS' in capsys.readouterr().out
