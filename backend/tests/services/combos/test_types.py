from decimal import Decimal

import pytest

from app.services.combos.types import (
    ComboContext,
    ComboEnvelope,
    LegSpec,
)

pytestmark = pytest.mark.no_db


def test_leg_spec_requires_side():
    leg = LegSpec(
        instrument_id=1,
        side="buy",
        qty=Decimal("1"),
        position_effect="OPEN",
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        expiry="2026-01-17",
        strike=Decimal("250"),
        put_call="C",
    )
    assert leg.side == "buy"


def test_combo_context_has_envelope():
    env = ComboEnvelope(
        net_debit_credit=Decimal("3.2"),
        kind="DEBIT",
        max_loss=Decimal("320"),
        max_profit=Decimal("680"),
        break_even=[Decimal("253.2")],
    )
    ctx = ComboContext(legs=[], envelope=env, account_id="x", mode="preview")
    assert ctx.envelope.kind == "DEBIT"


def test_combo_envelope_unbounded_has_none_max_loss():
    env = ComboEnvelope(
        net_debit_credit=Decimal("5"),
        kind="DEBIT",
        max_loss=None,
        max_profit=None,
        break_even=[],
    )
    assert env.max_loss is None
