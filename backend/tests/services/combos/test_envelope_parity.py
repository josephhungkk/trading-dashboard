import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.combos.pnl_envelope import compute_envelope
from app.services.combos.types import ComboSpec, LegSpec

pytestmark = pytest.mark.no_db

FIXTURES = json.loads((Path(__file__).parent / "fixtures/golden_envelopes.json").read_text())


def _build_spec(f):
    legs = [
        LegSpec(
            instrument_id=i,
            side=fixture_leg["side"],
            qty=Decimal("1"),
            position_effect="OPEN",
            symbol="AAPL",
            exchange="SMART",
            currency="USD",
            expiry=fixture_leg["expiry"],
            strike=Decimal(fixture_leg["strike"]),
            put_call=fixture_leg["put_call"],
        )
        for i, fixture_leg in enumerate(f["legs"])
    ]
    return ComboSpec(
        strategy_type=f["strategy"],
        underlying_symbol="AAPL",
        underlying_canonical_id="AAPL",
        legs=legs,
        tif="DAY",
        account_id="a",
    )


def test_golden_fixtures():
    for f in FIXTURES:
        spec = _build_spec(f)
        mids = {i: Decimal(fixture_leg["mid"]) for i, fixture_leg in enumerate(f["legs"])}
        env = compute_envelope(spec, mids)
        exp = f["expected"]
        assert str(env.net_debit_credit) == exp["net_debit_credit"]
        assert env.kind == exp["kind"]
        if exp["max_loss"] is None:
            assert env.max_loss is None
        else:
            assert str(env.max_loss) == exp["max_loss"]
        if exp["max_profit"] is None:
            assert env.max_profit is None
        else:
            assert str(env.max_profit) == exp["max_profit"]
        assert [str(b) for b in env.break_even] == exp["break_even"]
