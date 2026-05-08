"""HIGH-code-4: to_proto_order must use asset_class from dict, not hardcode STOCK.

Prior to the fix, to_proto_order always emitted broker_pb2.AssetClass.STOCK
regardless of the order's actual asset class. Crypto orders were misclassified.

Also covers MED-code-2: canonical_to_alpaca_crypto with non-USD quote currency.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca._generated.broker.v1 import broker_pb2
from sidecar_alpaca.normalize import (
    canonical_to_alpaca_crypto,
    to_proto_order,
)

pytestmark = [pytest.mark.unit]


def _order_dict(
    *,
    symbol: str = "BTC/USD",
    asset_class: str = "CRYPTO",
    qty: str = "0.5",
    filled_qty: str = "0",
    side: str = "BUY",
    order_type: str = "MARKET",
    tif: str = "DAY",
    status: str = "NEW",
    currency: str = "USD",
) -> dict:
    return {
        "id": "order-abc",
        "client_order_id": "",
        "symbol": symbol,
        "asset_class": asset_class,
        "qty": qty,
        "filled_qty": filled_qty,
        "side": side,
        "type": order_type,
        "tif": tif,
        "status": status,
        "limit_price": "",
        "stop_price": "",
        "currency": currency,
    }


# ---------------------------------------------------------------------------
# HIGH-code-4: asset_class propagation in to_proto_order
# ---------------------------------------------------------------------------


def test_to_proto_order_crypto_gets_crypto_asset_class() -> None:
    """Crypto orders must map to AssetClass.CRYPTO, not STOCK."""
    order = to_proto_order(_order_dict(asset_class="CRYPTO", symbol="BTC/USD"))
    assert order.contract.asset_class == broker_pb2.AssetClass.CRYPTO, (
        f"expected CRYPTO ({broker_pb2.AssetClass.CRYPTO}), "
        f"got {order.contract.asset_class}"
    )


def test_to_proto_order_stock_gets_stock_asset_class() -> None:
    """US equity orders with asset_class='STOCK' must map to AssetClass.STOCK."""
    order = to_proto_order(
        _order_dict(asset_class="STOCK", symbol="AAPL", qty="10")
    )
    assert order.contract.asset_class == broker_pb2.AssetClass.STOCK


def test_to_proto_order_us_equity_normalised_to_stock() -> None:
    """US_EQUITY (Alpaca's native enum string) must map to AssetClass.STOCK."""
    order = to_proto_order(
        _order_dict(asset_class="US_EQUITY", symbol="TSLA", qty="5")
    )
    assert order.contract.asset_class == broker_pb2.AssetClass.STOCK


def test_to_proto_order_missing_asset_class_is_unspecified() -> None:
    """Empty asset_class must not crash — maps to ASSET_UNSPECIFIED."""
    data = _order_dict(symbol="UNKNOWN")
    data["asset_class"] = ""
    order = to_proto_order(data)
    assert order.contract.asset_class == broker_pb2.AssetClass.ASSET_UNSPECIFIED


def test_to_proto_order_none_asset_class_is_unspecified() -> None:
    """None asset_class must not crash — maps to ASSET_UNSPECIFIED."""
    data = _order_dict(symbol="UNKNOWN")
    data["asset_class"] = None
    order = to_proto_order(data)
    assert order.contract.asset_class == broker_pb2.AssetClass.ASSET_UNSPECIFIED


# ---------------------------------------------------------------------------
# MED-code-2: canonical_to_alpaca_crypto quote currency variants
# ---------------------------------------------------------------------------


def test_canonical_to_alpaca_crypto_standard_usd() -> None:
    """Standard 3-part form uses USD as the implicit quote currency."""
    assert canonical_to_alpaca_crypto("crypto:BTC:US") == "BTC/USD"


def test_canonical_to_alpaca_crypto_explicit_usdt() -> None:
    """4-part form with USDT as explicit quote currency."""
    assert canonical_to_alpaca_crypto("crypto:BTC:US:USDT") == "BTC/USDT"


def test_canonical_to_alpaca_crypto_explicit_eur() -> None:
    """4-part form with EUR as explicit quote currency."""
    assert canonical_to_alpaca_crypto("crypto:ETH:EU:EUR") == "ETH/EUR"


def test_canonical_to_alpaca_crypto_uppercase_enforced() -> None:
    """Base and quote must be uppercased regardless of input case."""
    assert canonical_to_alpaca_crypto("crypto:btc:us:usdt") == "BTC/USDT"


def test_canonical_to_alpaca_crypto_malformed_raises() -> None:
    """Non-crypto canonical id must raise ValueError."""
    with pytest.raises(ValueError, match="not a crypto canonical_id"):
        canonical_to_alpaca_crypto("stock:AAPL:US")


def test_canonical_to_alpaca_crypto_too_short_raises() -> None:
    """Fewer than 3 parts must raise ValueError."""
    with pytest.raises(ValueError, match="not a crypto canonical_id"):
        canonical_to_alpaca_crypto("crypto:BTC")
