from types import SimpleNamespace

import pytest

# Backend does not ship alpaca-py as a runtime dep (sidecar_alpaca owns it).
# Skip the suite when not installed; CI sidecar image has it available.
pytest.importorskip("alpaca")

from alpaca.trading.enums import OrderClass

from app.services.oco_orchestrator import dispatch_oco_alpaca_equity


class FakeAlpacaClient:
    def __init__(self) -> None:
        self.order_data = None

    def submit_order(self, order_data):
        self.order_data = order_data
        return SimpleNamespace(
            id="oco-parent",
            legs=[SimpleNamespace(id="limit-leg"), SimpleNamespace(id="stop-leg")],
        )


@pytest.mark.asyncio
async def test_dispatch_oco_alpaca_equity_uses_native_oco() -> None:
    client = FakeAlpacaClient()
    req = SimpleNamespace(
        symbol="AAPL",
        side="SELL",
        qty="1",
        limit_price="200.00",
        stop_price="150.00",
        stop_limit_price="149.50",
        tif="GTC",
    )

    resp = await dispatch_oco_alpaca_equity(req, client)

    assert resp.external_order_id == "oco-parent"
    assert resp.leg_order_ids == ["limit-leg", "stop-leg"]
    assert client.order_data.order_class is OrderClass.OCO
