import json
from unittest.mock import AsyncMock

import pytest

from app.services.alerts.channels.in_app import InAppChannel
from app.services.alerts.delivery import AlertFire, DeliveryDispatcher, DeliveryOutcome


@pytest.mark.asyncio
async def test_in_app_publishes_to_redis() -> None:
    redis = AsyncMock()
    channel = InAppChannel(redis=redis)
    fire = AlertFire(
        fire_id=1,
        alert_id=42,
        jwt_subject="user-1",
        verdict="true",
        evaluated_values={"close": 201.5},
        user_label="AAPL above 200",
    )
    result = await channel.deliver(fire, config={})
    assert result is DeliveryOutcome.sent
    redis.publish.assert_called_once()
    channel_name, payload = redis.publish.call_args.args
    assert channel_name == "alerts:fire:user-1"
    body = json.loads(payload)
    assert body["alert_id"] == 42
    assert body["user_label"] == "AAPL above 200"


@pytest.mark.asyncio
async def test_dispatcher_fans_out_per_channel_isolated() -> None:
    success_channel = AsyncMock()
    success_channel.name = "in_app"
    success_channel.deliver.return_value = DeliveryOutcome.sent

    failing_channel = AsyncMock()
    failing_channel.name = "webhook"
    failing_channel.deliver.side_effect = RuntimeError("network")

    dispatcher = DeliveryDispatcher(
        channels={"in_app": success_channel, "webhook": failing_channel}
    )
    fire = AlertFire(
        fire_id=1,
        alert_id=42,
        jwt_subject="u",
        verdict="true",
        evaluated_values={},
        user_label="x",
    )
    outcomes = await dispatcher.fan_out(fire, channel_keys=["in_app", "webhook"])
    assert outcomes["in_app"] is DeliveryOutcome.sent
    assert outcomes["webhook"] is DeliveryOutcome.failed
