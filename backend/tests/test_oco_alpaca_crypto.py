from types import SimpleNamespace

import pytest

# Backend does not ship alpaca-py as a runtime dep (sidecar_alpaca owns it).
# Skip the suite when not installed; CI sidecar image has it available.
pytest.importorskip("alpaca")

from alpaca.trading.enums import OrderClass

from app.services.oco_orchestrator import dispatch_oco_alpaca_crypto

pytestmark = pytest.mark.no_db


class FakeAlpacaClient:
    """Sync interface matching alpaca-py TradingClient; the dispatcher wraps
    submit_order in asyncio.to_thread so the fake stays synchronous."""

    def __init__(self) -> None:
        self.order_data = None

    def submit_order(self, order_data):
        self.order_data = order_data
        return SimpleNamespace(
            id="crypto-oco-parent",
            legs=[SimpleNamespace(id="a"), SimpleNamespace(id="b")],
        )


@pytest.mark.asyncio
async def test_dispatch_oco_alpaca_crypto_pass_branch() -> None:
    client = FakeAlpacaClient()
    req = SimpleNamespace(
        symbol="BTC/USD",
        side="SELL",
        qty="0.001",
        limit_price="200000.00",
        stop_price="50000.00",
        stop_limit_price="",
        tif="GTC",
    )

    resp = await dispatch_oco_alpaca_crypto(req, client, crypto_oco_supported=True)

    assert resp.external_order_id == "crypto-oco-parent"
    assert resp.leg_order_ids == ["a", "b"]
    assert client.order_data.order_class is OrderClass.OCO


@pytest.mark.asyncio
async def test_dispatch_oco_alpaca_crypto_fail_branch() -> None:
    req = SimpleNamespace(symbol="BTC/USD")
    with pytest.raises(NotImplementedError, match="alpaca_crypto_oco_not_supported"):
        await dispatch_oco_alpaca_crypto(
            req,
            FakeAlpacaClient(),
            crypto_oco_supported=False,
        )
