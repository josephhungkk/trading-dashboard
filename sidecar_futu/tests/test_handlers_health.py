from datetime import UTC, datetime, timedelta

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_health_returns_broker_id_and_started_at():
    started = datetime.now(UTC)
    handlers = BrokerHandlers(started_at=started)
    request = broker_pb2.HealthRequest()
    response = await handlers.Health(request, context=None)

    assert response.broker_id == "futu"
    assert response.gateway_connected is False
    response_dt = response.started_at.ToDatetime(tzinfo=UTC)
    assert abs(response_dt - started) < timedelta(seconds=1)
