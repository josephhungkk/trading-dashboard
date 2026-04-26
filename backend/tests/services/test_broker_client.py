"""Tests for BrokerSidecarClient (Phase 4 Task 31).

Stands up an in-process grpc.aio server with a fake BrokerServicer + ephemeral
mTLS material, then exercises each of the six RPC wrappers end-to-end against
it. Verifies proto -> Pydantic conversion, mTLS handshake, and that
DEADLINE_EXCEEDED on the wire raises BrokerSidecarTimeout.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from app.services.brokers import (
    BrokerSidecarClient,
    BrokerSidecarTimeout,
    BrokerSidecarUnavailable,
)

# --- ephemeral mTLS material -------------------------------------------------


def _build_ephemeral_pki(server_san: str) -> dict[str, bytes]:
    """Returns dict with ca_pem, server_cert_pem, server_key_pem,
    client_cert_pem, client_key_pem - all PEM-encoded bytes. Uses ECDSA
    SECP256R1 for speed (same pattern as sidecar/tests/test_tls.py)."""

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = datetime.now(UTC)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    def _issue(
        common_name: str, *, eku: x509.ObjectIdentifier, san_dns: str | None = None
    ) -> tuple[bytes, bytes]:
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
            .issuer_name(ca_name)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        )
        if san_dns is not None:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(san_dns)]), critical=False
            )
        leaf_cert = builder.sign(ca_key, hashes.SHA256())
        cert_pem = leaf_cert.public_bytes(serialization.Encoding.PEM)
        key_pem = leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cert_pem, key_pem

    server_cert, server_key = _issue(
        server_san,
        eku=x509.ExtendedKeyUsageOID.SERVER_AUTH,
        san_dns=server_san,
    )
    client_cert, client_key = _issue(
        "test-client",
        eku=x509.ExtendedKeyUsageOID.CLIENT_AUTH,
    )

    return {
        "ca_pem": ca_cert.public_bytes(serialization.Encoding.PEM),
        "server_cert_pem": server_cert,
        "server_key_pem": server_key,
        "client_cert_pem": client_cert,
        "client_key_pem": client_key,
    }


# --- fake servicer -----------------------------------------------------------


class _FakeServicer(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    def __init__(self, *, slow_health: bool = False) -> None:
        self.slow_health = slow_health

    async def Health(  # noqa: N802
        self, request: broker_pb2.HealthRequest, context: grpc.aio.ServicerContext
    ) -> broker_pb2.HealthResponse:
        del request, context
        if self.slow_health:
            await asyncio.sleep(2.0)
        return broker_pb2.HealthResponse(
            label="test-label",
            gateway_connected=True,
            gateway_version="999",
            sidecar_version="0.4.0-test",
        )

    async def ListManagedAccounts(  # noqa: N802
        self, request: broker_pb2.Empty, context: grpc.aio.ServicerContext
    ) -> broker_pb2.AccountsResponse:
        del request, context
        return broker_pb2.AccountsResponse(
            accounts=[
                broker_pb2.Account(
                    account_number="DUA0000000",
                    mode=broker_pb2.PAPER,
                    gateway_label="isa-paper",
                    currency_base="USD",
                )
            ]
        )

    async def GetAccountSummary(  # noqa: N802
        self, request: broker_pb2.AccountRef, context: grpc.aio.ServicerContext
    ) -> broker_pb2.SummaryResponse:
        del context, request
        money = broker_pb2.Money(value="100.50", currency="USD")
        return broker_pb2.SummaryResponse(
            summary=broker_pb2.Summary(
                net_liquidation=money,
                total_cash=money,
                realized_pnl=money,
                unrealized_pnl=money,
                buying_power=money,
            )
        )

    async def GetPositions(  # noqa: N802
        self, request: broker_pb2.AccountRef, context: grpc.aio.ServicerContext
    ) -> broker_pb2.PositionsResponse:
        del context, request
        return broker_pb2.PositionsResponse(positions=[])

    async def GetOrders(  # noqa: N802
        self, request: broker_pb2.AccountRef, context: grpc.aio.ServicerContext
    ) -> broker_pb2.OrdersResponse:
        del context, request
        return broker_pb2.OrdersResponse(orders=[])

    async def GetContract(  # noqa: N802
        self, request: broker_pb2.ContractRef, context: grpc.aio.ServicerContext
    ) -> broker_pb2.ContractResponse:
        del context, request
        return broker_pb2.ContractResponse(
            contract=broker_pb2.Contract(
                symbol="AAPL",
                exchange="SMART",
                currency="USD",
                asset_class=broker_pb2.STOCK,
                conid="265598",
                local_symbol="AAPL",
            )
        )


# --- in-process server fixture -----------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return cast(int, s.getsockname()[1])
    finally:
        s.close()


def _client_for(
    *, port: int, san: str, pki: dict[str, bytes], deadline: float
) -> BrokerSidecarClient:
    """Build a BrokerSidecarClient and override its channel/stub so the
    grpc.default_authority matches our ephemeral cert SAN."""
    client = BrokerSidecarClient(
        label=san,
        target=f"127.0.0.1:{port}",
        ca_bundle_pem=pki["ca_pem"],
        client_key_pem=pki["client_key_pem"],
        client_cert_pem=pki["client_cert_pem"],
        deadline_seconds=deadline,
    )
    object.__setattr__(
        client,
        "channel",
        grpc.aio.secure_channel(
            f"127.0.0.1:{port}",
            grpc.ssl_channel_credentials(
                root_certificates=pki["ca_pem"],
                private_key=pki["client_key_pem"],
                certificate_chain=pki["client_cert_pem"],
            ),
            options=(("grpc.default_authority", san),),
        ),
    )
    object.__setattr__(client, "stub", cast(Any, broker_pb2_grpc.BrokerStub)(client.channel))
    return client


@pytest.fixture
async def mtls_server() -> AsyncIterator[BrokerSidecarClient]:
    port = _free_port()
    san = f"sidecar-test-{port}"
    pki = _build_ephemeral_pki(server_san=san)
    servicer = _FakeServicer()

    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    server_credentials = grpc.ssl_server_credentials(
        [(pki["server_key_pem"], pki["server_cert_pem"])],
        root_certificates=pki["ca_pem"],
        require_client_auth=True,
    )
    server.add_secure_port(f"127.0.0.1:{port}", server_credentials)
    await server.start()

    client = _client_for(port=port, san=san, pki=pki, deadline=5.0)
    try:
        yield client
    finally:
        await client.close()
        await server.stop(grace=1.0)


# --- tests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_roundtrips(mtls_server: BrokerSidecarClient) -> None:
    resp = await mtls_server.health()
    assert resp.label == "test-label"
    assert resp.gateway_connected is True
    assert resp.sidecar_version == "0.4.0-test"


@pytest.mark.asyncio
async def test_list_managed_accounts_roundtrips(mtls_server: BrokerSidecarClient) -> None:
    accounts = await mtls_server.list_managed_accounts()
    assert len(accounts) == 1
    assert accounts[0].account_number == "DUA0000000"
    assert accounts[0].mode == "PAPER"
    assert accounts[0].currency_base == "USD"


@pytest.mark.asyncio
async def test_get_account_summary_roundtrips(mtls_server: BrokerSidecarClient) -> None:
    summary = await mtls_server.get_account_summary("DUA0000000")
    assert summary.net_liquidation.value == "100.50"
    assert summary.net_liquidation.currency == "USD"


@pytest.mark.asyncio
async def test_get_positions_roundtrips(mtls_server: BrokerSidecarClient) -> None:
    assert await mtls_server.get_positions("DUA0000000") == []


@pytest.mark.asyncio
async def test_get_orders_roundtrips(mtls_server: BrokerSidecarClient) -> None:
    assert await mtls_server.get_orders("DUA0000000") == []


@pytest.mark.asyncio
async def test_get_contract_roundtrips(mtls_server: BrokerSidecarClient) -> None:
    contract = await mtls_server.get_contract("265598")
    assert contract.symbol == "AAPL"
    assert contract.conid == "265598"
    assert contract.asset_class == "STOCK"


@pytest.mark.asyncio
async def test_timeout_raises_broker_sidecar_timeout() -> None:
    """Servicer sleeps 2s; client deadline 0.5s -> DEADLINE_EXCEEDED ->
    BrokerSidecarTimeout."""
    port = _free_port()
    san = f"sidecar-test-{port}"
    pki = _build_ephemeral_pki(server_san=san)
    servicer = _FakeServicer(slow_health=True)

    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    server_credentials = grpc.ssl_server_credentials(
        [(pki["server_key_pem"], pki["server_cert_pem"])],
        root_certificates=pki["ca_pem"],
        require_client_auth=True,
    )
    server.add_secure_port(f"127.0.0.1:{port}", server_credentials)
    await server.start()

    client = _client_for(port=port, san=san, pki=pki, deadline=0.5)
    try:
        with pytest.raises(BrokerSidecarTimeout):
            await client.health()
    finally:
        await client.close()
        await server.stop(grace=1.0)


@pytest.mark.asyncio
async def test_unreachable_server_raises_unavailable() -> None:
    """No server on the target port -> BrokerSidecarUnavailable (or Timeout
    on slow CI runners — both are acceptable failure modes)."""
    pki = _build_ephemeral_pki(server_san="sidecar-test-unreachable")
    port = _free_port()
    client = _client_for(port=port, san="sidecar-test-unreachable", pki=pki, deadline=0.5)
    try:
        with pytest.raises((BrokerSidecarUnavailable, BrokerSidecarTimeout)):
            await client.health()
    finally:
        await client.close()
