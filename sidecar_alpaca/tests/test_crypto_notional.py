"""Crypto notional order request coverage."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeRequest:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def test_market_cash_amount_maps_to_notional_without_qty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_order_request_classes",
        lambda: {"MARKET": FakeRequest},
    )
    svc = AlpacaServicer()

    result = svc._build_order_request(
        broker_pb2.PlaceOrderRequest(
            account_number="acct-1",
            conid="BTC/USD",
            side="BUY",
            order_type="MARKET",
            tif="DAY",
            cash_amount="10.00",
            client_order_id="cid",
        )
    )

    assert result.kwargs["notional"] == Decimal("10.00")
    assert result.kwargs.get("qty") is None
