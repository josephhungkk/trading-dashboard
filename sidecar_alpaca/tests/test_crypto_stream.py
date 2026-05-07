import pytest

from sidecar_alpaca.streaming import crypto_order_event_source


class FakeCryptoStream:
    def __init__(self):
        self.handler = None
        self.subscribed = False

    def subscribe_trade_updates(self, handler):
        self.handler = handler
        self.subscribed = True

    async def run(self):
        await self.handler(
            {"order": {"id": "cr-1", "symbol": "BTCUSD"}, "event": "fill"}
        )


@pytest.mark.asyncio
async def test_crypto_stream_subscribes_trade_updates():
    stream = FakeCryptoStream()
    events = []
    async for event in crypto_order_event_source(lambda: stream):
        events.append(event)
        break
    assert stream.subscribed is True
    assert events[0]["external_order_id"] == "cr-1"
    assert events[0]["symbol"] == "BTC/USD"
