import importlib
import os

os.environ.setdefault("MODE", "paper")


def test_default_crypto_location_us(monkeypatch):
    monkeypatch.delenv("ALPACA_CRYPTO_LOCATION", raising=False)

    from sidecar_alpaca import config

    importlib.reload(config)

    assert config.CRYPTO_LOCATION == "us"


def test_build_crypto_stream_uses_default_location(monkeypatch):
    monkeypatch.delenv("ALPACA_CRYPTO_LOCATION", raising=False)

    from sidecar_alpaca import config, streaming

    importlib.reload(config)
    importlib.reload(streaming)

    recorded = {}

    class FakeCryptoDataStream:
        def __init__(self, api_key, api_secret, **kwargs):
            recorded["api_key"] = api_key
            recorded["api_secret"] = api_secret
            recorded.update(kwargs)

    monkeypatch.setattr(streaming, "CryptoDataStream", FakeCryptoDataStream)

    streaming.build_crypto_stream("k", "s")

    assert recorded["feed"] == "us"
