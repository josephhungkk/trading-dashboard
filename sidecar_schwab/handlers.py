"""gRPC Broker servicer for Schwab. Stubs filled out in chunk B."""
from __future__ import annotations

import grpc

from sidecar_schwab._generated.broker.v1 import (
    broker_pb2,
    broker_pb2_grpc,
)


class BrokerServicer(broker_pb2_grpc.BrokerServicer):
    """Schwab gRPC service. Empty stubs in A4; chunk B fills them out."""

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        # Minimal pre-Configure response. Real impl in B5 populates
        # started_at + gateway_version; gateway_connected stays False until
        # token + account_hash cache are both populated (H4 invariant).
        return broker_pb2.HealthResponse(
            label="schwab",
            broker_id="schwab",
            gateway_version="",
            gateway_connected=False,
            sidecar_version="0.7.0-stub",
        )
