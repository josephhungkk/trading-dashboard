"""Symbol normalization helper coverage."""

from __future__ import annotations

import pytest

from sidecar_alpaca.symbol_util import canonical_crypto_symbol


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTCUSD", "BTC/USD"),
        ("BTC/USD", "BTC/USD"),
        ("BTCUSDT", "BTC/USDT"),
        ("ETHUSD", "ETH/USD"),
        ("SHIBUSD", "SHIB/USD"),
    ],
)
def test_canonical_crypto_symbol(raw: str, expected: str) -> None:
    assert canonical_crypto_symbol(raw) == expected


def test_canonical_crypto_symbol_rejects_empty() -> None:
    with pytest.raises(ValueError):
        canonical_crypto_symbol("")
