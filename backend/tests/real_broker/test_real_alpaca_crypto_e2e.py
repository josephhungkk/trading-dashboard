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
    # Alpaca crypto constraints:
    #   - time_in_force must be GTC or IOC (DAY → api error 42210000)
    #   - notional must be >= $10 minimum cost basis (api error 40310000)
    # Equity uses DAY + smaller notional; these are crypto-pair specifics.
    order = await asyncio.to_thread(
        client.submit_order,
        MarketOrderRequest(
            symbol="BTC/USD",
            notional=Decimal("10.00"),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
        ),
    )

    assert order.id
    # Crypto markets are 24/7 so this can fill before our cancel arrives;
    # treat "already in filled state" as functional success (matches the
    # equity-side _cancel() tolerance in test_real_alpaca_equity_e2e.py).
    try:
        await asyncio.to_thread(client.cancel_order_by_id, order.id)
    except Exception as exc:  # alpaca.common.exceptions.APIError lacks public type
        msg = str(exc).lower()
        if "filled" in msg or "canceled" in msg or "expired" in msg:
            return
        raise
