from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeClient:
    def __init__(self) -> None:
        self.submitted: list[Any] = []

    def submit_order(self, request: Any) -> Any:
        self.submitted.append(request)
        return SimpleNamespace(id="alpaca-combo-1")


def _combo_request() -> broker_pb2.PlaceComboRequest:
    return broker_pb2.PlaceComboRequest(
        account_id="acct-1",
        strategy_type="VERTICAL",
        tif="DAY",
        limit_price="1.25",
        client_combo_id="combo-1",
        legs=[
            broker_pb2.ComboLegRequest(
                symbol=broker_pb2.SymbolRef(raw_symbol="AAPL", currency="USD"),
                option_hint=broker_pb2.OptionContractHint(
                    expiry_iso="2026-06-19",
                    strike="150",
                    put_call="C",
                ),
                side="buy",
                ratio=1,
            ),
            broker_pb2.ComboLegRequest(
                symbol=broker_pb2.SymbolRef(raw_symbol="AAPL", currency="USD"),
                option_hint=broker_pb2.OptionContractHint(
                    expiry_iso="2026-06-19",
                    strike="155",
                    put_call="C",
                ),
                side="sell",
                ratio=1,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_get_supported_combo_strategies_returns_all_five() -> None:
    response = await AlpacaServicer().GetSupportedComboStrategies(
        broker_pb2.GetSupportedComboStrategiesRequest(broker_id="alpaca"),
        context=object(),  # type: ignore[arg-type]
    )

    assert list(response.strategy_types) == [
        "VERTICAL",
        "CALENDAR",
        "DIAGONAL",
        "STRADDLE",
        "STRANGLE",
    ]


@pytest.mark.asyncio
async def test_place_combo_submits_mleg_order() -> None:
    client = FakeClient()
    service = AlpacaServicer()

    async def fake_configured_client(account_id: str, context: Any) -> FakeClient:
        assert account_id == "acct-1"
        del context
        return client

    service._configured_trading_client = fake_configured_client  # type: ignore[method-assign]

    response = await service.PlaceCombo(
        _combo_request(),
        context=object(),  # type: ignore[arg-type]
    )

    assert response.broker_combo_id == "alpaca-combo-1"
    assert [leg.broker_order_id for leg in response.legs] == ["", ""]
    assert len(client.submitted) == 1
    submitted = client.submitted[0]
    assert submitted.symbol == "AAPL"
    assert submitted.client_order_id == "combo-1"
    assert len(submitted.legs) == 2
