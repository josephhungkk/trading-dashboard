from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from sidecar_ibkr._generated.broker.v1 import broker_pb2
from sidecar_ibkr.handlers import BrokerHandlers


class FakeIB:
    def __init__(self) -> None:
        self.contracts: list[Any] = []
        self.placed: list[tuple[Any, Any]] = []

    async def reqContractDetailsAsync(self, contract: Any) -> list[Any]:  # noqa: N802
        self.contracts.append(contract)
        return [SimpleNamespace(contract=SimpleNamespace(conId=1000 + len(self.contracts)))]

    def placeOrder(self, contract: Any, order: Any) -> Any:  # noqa: N802
        self.placed.append((contract, order))
        return SimpleNamespace(order=SimpleNamespace(orderId=99999))


def _handler(ib: FakeIB) -> BrokerHandlers:
    return BrokerHandlers(
        ib=ib,
        pnl_cache=None,  # type: ignore[arg-type]
        label="ibkr-test",
        version="test",
        last_tick_ref={},
        simulator_only=False,
        started_at=datetime.now(UTC),
    )


def _combo_request() -> broker_pb2.PlaceComboRequest:
    return broker_pb2.PlaceComboRequest(
        account_id="DU123",
        strategy_type="VERTICAL",
        tif="DAY",
        limit_price="1.25",
        client_combo_id="combo-1",
        legs=[
            broker_pb2.ComboLegRequest(
                symbol=broker_pb2.SymbolRef(
                    raw_symbol="AAPL",
                    exchange="SMART",
                    currency="USD",
                ),
                option_hint=broker_pb2.OptionContractHint(
                    expiry_iso="2026-06-19",
                    strike="150",
                    put_call="C",
                ),
                side="buy",
                ratio=1,
            ),
            broker_pb2.ComboLegRequest(
                symbol=broker_pb2.SymbolRef(
                    raw_symbol="AAPL",
                    exchange="SMART",
                    currency="USD",
                ),
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
    response = await _handler(FakeIB()).GetSupportedComboStrategies(
        broker_pb2.GetSupportedComboStrategiesRequest(broker_id="ibkr"),
        context=object(),
    )

    assert list(response.strategy_types) == [
        "VERTICAL",
        "CALENDAR",
        "DIAGONAL",
        "STRADDLE",
        "STRANGLE",
    ]


@pytest.mark.asyncio
async def test_place_combo_returns_combo_id_and_empty_leg_order_ids() -> None:
    ib = FakeIB()
    response = await _handler(ib).PlaceCombo(_combo_request(), context=object())

    assert response.broker_combo_id == "99999"
    assert [leg.broker_order_id for leg in response.legs] == ["", ""]
    assert [leg.status for leg in response.legs] == ["working", "working"]
    assert len(ib.contracts) == 2
    assert len(ib.placed) == 1
