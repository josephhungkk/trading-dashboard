"""Phase 8c T-C.7 - Real Alpaca paper-account crypto E2E place + cancel.

Runs against live Alpaca paper API. Auto-skipped when ALPACA_PAPER_* env vars
are not set. Invoked by .github/workflows/nightly-real-alpaca-crypto.yml.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest

pytestmark = pytest.mark.real_broker


@pytest.mark.asyncio
async def test_real_alpaca_crypto_notional_market_order() -> None:
    if not os.getenv("ALPACA_PAPER_API_KEY") or not os.getenv("ALPACA_PAPER_API_SECRET"):
        pytest.skip("Alpaca paper credentials are not configured")

    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    client = TradingClient(
        os.environ["ALPACA_PAPER_API_KEY"],
        os.environ["ALPACA_PAPER_API_SECRET"],
        paper=True,
    )
    # Wrap blocking SDK calls so the asyncio event loop stays responsive
    # (chunk-C python MED-3 / spec HIGH-1).
    order = await asyncio.to_thread(
        client.submit_order,
        MarketOrderRequest(
            symbol="BTC/USD",
            notional=Decimal("1.00"),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        ),
    )

    assert order.id
    await asyncio.to_thread(client.cancel_order_by_id, order.id)
