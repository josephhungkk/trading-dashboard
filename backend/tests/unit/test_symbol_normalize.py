import pytest

from app.brokers.symbol_normalize import canonical_crypto_symbol


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations():
    return None


def test_already_canonical():
    assert canonical_crypto_symbol("BTC/USD") == "BTC/USD"


def test_no_separator():
    assert canonical_crypto_symbol("BTCUSD") == "BTC/USD"


def test_dash_separator():
    assert canonical_crypto_symbol("BTC-USD") == "BTC/USD"


def test_underscore_separator():
    assert canonical_crypto_symbol("BTC_USD") == "BTC/USD"


def test_lowercase_normalized():
    assert canonical_crypto_symbol("btc/usd") == "BTC/USD"


def test_unknown_pair_rejected():
    with pytest.raises(ValueError):
        canonical_crypto_symbol("INVALIDCOIN")


def test_empty_string_rejected():
    with pytest.raises(ValueError):
        canonical_crypto_symbol("")


def test_eth_usdt():
    assert canonical_crypto_symbol("ETHUSDT") == "ETH/USDT"
