"""Alpaca handler skeleton tests."""

from __future__ import annotations

import os

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2, broker_pb2_grpc


@pytest.mark.asyncio
async def test_health_reports_mode_from_env() -> None:
    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(AlpacaServicer(), server)
    try:
        port = server.add_insecure_port("127.0.0.1:0")
    except RuntimeError as exc:
        pytest.skip(f"grpc bind failed: {exc}")
    if port == 0:
        pytest.skip("grpc bind failed")

    await server.start()
    try:
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = broker_pb2_grpc.BrokerStub(channel)
        response = await stub.Health(broker_pb2.HealthRequest())
        assert response.label == f"alpaca-{os.environ['MODE']}"
    finally:
        await channel.close()
        await server.stop(grace=0)
