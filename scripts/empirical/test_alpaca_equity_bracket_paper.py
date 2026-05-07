from scripts.empirical.alpaca_equity_bracket_paper import expected_prices, run


class FakeClient:
    def submit_order(self, order_data):
        legs = [type('Leg', (), {'id': 'child-1'})(), type('Leg', (), {'id': 'child-2'})()]
        return type('Order', (), {'id': 'parent', 'legs': legs})()

    def cancel_order_by_id(self, order_id):
        return None


def test_expected_prices():
    take_profit, stop_loss = expected_prices(100)
    assert take_profit == '102.00'
    assert stop_loss == '99.00'


def test_bracket_script_passes_with_three_orders(capsys):
    assert run(FakeClient(), reference_price=100) == 0
    assert 'PASS' in capsys.readouterr().out
