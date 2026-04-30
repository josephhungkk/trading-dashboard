"""E5 — Contract tests for the Futu sidecar against a real (in-process) grpc server.

Spins grpc.aio.server() with the BrokerHandlers servicer and exercises Health +
Configure over the wire. No futu OpenD, no mTLS — just the gRPC plumbing
contract.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import grpc
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from sidecar_futu._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar_futu.handlers import BrokerHandlers


def _make_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.mark.asyncio
async def test_full_handler_chain_against_real_grpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub the futu SDK so Configure's init task doesn't try a real OpenD connect."""
    from unittest.mock import MagicMock

    monkeypatch.setattr("sidecar_futu.futu_client.SysConfig", MagicMock())

    async def stub_init_loop() -> None:
        await asyncio.Event().wait()  # block until cancelled

    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    monkeypatch.setattr(handlers._client, "_init_loop", stub_init_loop)

    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(handlers, server)
    port = server.add_insecure_port("[::]:0")
    await server.start()

    try:
        async with grpc.aio.insecure_channel(f"localhost:{port}") as ch:
            stub = broker_pb2_grpc.BrokerStub(ch)

            h = await stub.Health(broker_pb2.HealthRequest())
            assert h.broker_id == "futu"
            assert h.label == "futu"
            assert h.gateway_connected is False  # Configure not called yet
            assert h.started_at.seconds > 0

            cr = await stub.Configure(
                broker_pb2.ConfigureRequest(
                    unlock_pwd_md5="0" * 32,
                    rsa_priv_pem=_make_pem(),
                    opend_host="x",
                    opend_port=11111,
                    connection_id="x",
                )
            )
            assert cr.ok is True
            assert cr.detail == ""
    finally:
        if handlers._client._init_task is not None:
            handlers._client._init_task.cancel()
            try:
                await handlers._client._init_task
            except asyncio.CancelledError:
                pass
        handlers._client._cleanup_rsa_tempfile()
        await server.stop(grace=1)


@pytest.mark.asyncio
async def test_configure_rejects_invalid_pem_over_wire() -> None:
    """Validation rejection round-trips as ok=False, detail='invalid_rsa_pem'."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(handlers, server)
    port = server.add_insecure_port("[::]:0")
    await server.start()

    try:
        async with grpc.aio.insecure_channel(f"localhost:{port}") as ch:
            stub = broker_pb2_grpc.BrokerStub(ch)

            cr = await stub.Configure(
                broker_pb2.ConfigureRequest(
                    unlock_pwd_md5="0" * 32,
                    rsa_priv_pem="not-a-pem",
                    opend_host="x",
                    opend_port=11111,
                    connection_id="x",
                )
            )
            assert cr.ok is False
            assert cr.detail == "invalid_rsa_pem"
    finally:
        await server.stop(grace=1)
