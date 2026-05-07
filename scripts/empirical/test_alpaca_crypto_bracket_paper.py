from scripts.empirical.alpaca_crypto_bracket_paper import run


class FailingClient:
    def submit_order(self, order_data):
        raise RuntimeError('bracket orders are not supported for crypto')


class PassingClient:
    def submit_order(self, order_data):
        return type('Order', (), {'id': 'unexpected'})()


def test_crypto_bracket_expected_fail(capsys):
    assert run(FailingClient()) == 0
    assert 'EXPECTED_FAIL' in capsys.readouterr().out


def test_crypto_bracket_unexpected_pass(capsys):
    assert run(PassingClient()) == 1
    assert 'UNEXPECTED_PASS' in capsys.readouterr().out
