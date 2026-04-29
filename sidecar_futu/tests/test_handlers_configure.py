import asyncio
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _make_rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.mark.asyncio
async def test_configure_valid_creds():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    response = await handlers.Configure(
        broker_pb2.ConfigureRequest(
            unlock_pwd_md5="0123456789abcdef0123456789abcdef",
            rsa_priv_pem=_make_rsa_pem(),
            opend_host="127.0.0.1",
            opend_port=11111,
            connection_id="x",
        ),
        context=None,
    )
    assert response.ok is True
    assert response.detail == ""


@pytest.mark.asyncio
async def test_configure_invalid_md5():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    response = await handlers.Configure(
        broker_pb2.ConfigureRequest(
            unlock_pwd_md5="not-md5",
            rsa_priv_pem=_make_rsa_pem(),
            opend_host="x",
            opend_port=11111,
            connection_id="x",
        ),
        context=None,
    )
    assert response.ok is False
    assert "md5" in response.detail


@pytest.mark.asyncio
async def test_configure_invalid_pem():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    response = await handlers.Configure(
        broker_pb2.ConfigureRequest(
            unlock_pwd_md5="0123456789abcdef0123456789abcdef",
            rsa_priv_pem="not-a-pem",
            opend_host="x",
            opend_port=11111,
            connection_id="x",
        ),
        context=None,
    )
    assert response.ok is False
    assert "rsa" in response.detail


@pytest.mark.asyncio
async def test_configure_cancels_inflight():
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    valid_pem = _make_rsa_pem()
    request = broker_pb2.ConfigureRequest(
        unlock_pwd_md5="0123456789abcdef0123456789abcdef",
        rsa_priv_pem=valid_pem,
        opend_host="x",
        opend_port=11111,
        connection_id="x",
    )

    await handlers.Configure(request, context=None)
    first_task = handlers._client._init_task
    assert first_task is not None

    await handlers.Configure(request, context=None)
    second_task = handlers._client._init_task

    assert second_task is not first_task
    assert first_task.cancelled()

    assert second_task is not None
    second_task.cancel()
    try:
        await second_task
    except asyncio.CancelledError:
        pass
