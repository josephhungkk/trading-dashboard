"""End-to-end discover loop with 4 in-process gRPC fake sidecars.

Each fake binds an ephemeral 127.0.0.1 port, has its own mTLS PKI, and
returns a fixed AccountsResponse on ListManagedAccounts. The test wires
a real BrokerRegistry + BrokerSidecarClient pointed at the in-process
servers and runs `_discover_once`, then checks the broker_accounts table.

Covers Step 44.1 of the Phase 4 plan.
"""

from __future__ import annotations

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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from app.core.config import settings
from app.services.brokers import (
    BrokerDiscoverer,
    BrokerRegistry,
    BrokerSidecarClient,
)

# --- ephemeral mTLS material (shared CA + per-server SAN cert) ---------------


def _build_ephemeral_pki(server_sans: list[str]) -> dict[str, Any]:
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

    server_certs: dict[str, tuple[bytes, bytes]] = {}
    for san in server_sans:
        server_certs[san] = _issue(san, eku=x509.ExtendedKeyUsageOID.SERVER_AUTH, san_dns=san)
    client_cert, client_key = _issue("test-client", eku=x509.ExtendedKeyUsageOID.CLIENT_AUTH)

    return {
        "ca_pem": ca_cert.public_bytes(serialization.Encoding.PEM),
        "server_certs": server_certs,
        "client_cert_pem": client_cert,
        "client_key_pem": client_key,
    }


# --- fake servicer (configurable per-fixture account list) -------------------


class _ConfigurableServicer(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    def __init__(self, label: str, account_number: str, mode: int) -> None:
        self.label = label
        self.account_number = account_number
        self.mode = mode

    async def Health(  # noqa: N802
        self, request: broker_pb2.HealthRequest, context: grpc.aio.ServicerContext
    ) -> broker_pb2.HealthResponse:
        del request, context
        return broker_pb2.HealthResponse(
            label=self.label,
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
                    account_number=self.account_number,
                    mode=self.mode,
                    gateway_label=self.label,
                    currency_base="USD",
                )
            ]
        )


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return cast(int, s.getsockname()[1])
    finally:
        s.close()


def _build_client(*, label: str, port: int, pki: dict[str, Any]) -> BrokerSidecarClient:
    client = BrokerSidecarClient(
        label=label,
        target=f"127.0.0.1:{port}",
        ca_bundle_pem=pki["ca_pem"],
        client_key_pem=pki["client_key_pem"],
        client_cert_pem=pki["client_cert_pem"],
        deadline_seconds=5.0,
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
            options=(("grpc.default_authority", label),),
        ),
    )
    object.__setattr__(client, "stub", cast(Any, broker_pb2_grpc.BrokerStub)(client.channel))
    return client


# --- fixtures ----------------------------------------------------------------


SIDECARS: tuple[tuple[str, str, int], ...] = (
    ("isa-live", "DUA0000001", broker_pb2.LIVE),
    ("isa-paper", "DUA0000002", broker_pb2.PAPER),
    ("normal-live", "DUA0000003", broker_pb2.LIVE),
    ("normal-paper", "DUA0000004", broker_pb2.PAPER),
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_broker_accounts(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    # Drop dependent rows first; FKs from orders/positions/etc. reference
    # broker_accounts and would otherwise raise ForeignKeyViolationError.
    async with session_factory() as s:
        await s.execute(text("DELETE FROM orders"))
        await s.execute(text("DELETE FROM broker_accounts"))
        await s.commit()
    yield
    async with session_factory() as s:
        await s.execute(text("DELETE FROM orders"))
        await s.execute(text("DELETE FROM broker_accounts"))
        await s.commit()


@pytest.fixture
async def fleet() -> AsyncIterator[tuple[BrokerRegistry, list[grpc.aio.Server], list[int]]]:
    """Spin up 4 in-process gRPC servers + matching BrokerSidecarClients in
    one BrokerRegistry. Yields the registry + server handles for shutdown
    + the ports so individual servers can be killed mid-test."""
    sans = [label for label, _acct, _mode in SIDECARS]
    pki = _build_ephemeral_pki(sans)
    ports = [_free_port() for _ in SIDECARS]
    servers: list[grpc.aio.Server] = []
    clients: dict[str, BrokerSidecarClient] = {}

    for (label, account, mode), port in zip(SIDECARS, ports, strict=True):
        servicer = _ConfigurableServicer(label, account, mode)
        server = grpc.aio.server()
        broker_pb2_grpc.add_BrokerServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
        server_cert, server_key = pki["server_certs"][label]
        server_credentials = grpc.ssl_server_credentials(
            [(server_key, server_cert)],
            root_certificates=pki["ca_pem"],
            require_client_auth=True,
        )
        server.add_secure_port(f"127.0.0.1:{port}", server_credentials)
        await server.start()
        servers.append(server)

        clients[label] = _build_client(label=label, port=port, pki=pki)

    registry = BrokerRegistry(clients, freshness_seconds=300.0)
    try:
        yield registry, servers, ports
    finally:
        await registry.close()
        for server in servers:
            await server.stop(grace=0.5)


# --- tests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_upserts_all_4_accounts(
    fleet: tuple[BrokerRegistry, list[grpc.aio.Server], list[int]],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry, _servers, _ports = fleet
    await registry.probe_once()

    discoverer = BrokerDiscoverer(registry, session_factory)
    await discoverer._discover_once()

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT broker_id, account_number, gateway_label, currency_base, mode, "
                        "       last_seen_via, deleted_at "
                        "FROM broker_accounts ORDER BY account_number"
                    )
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 4
    expected = {
        ("DUA0000001", "isa-live", "live"),
        ("DUA0000002", "isa-paper", "paper"),
        ("DUA0000003", "normal-live", "live"),
        ("DUA0000004", "normal-paper", "paper"),
    }
    actual = {(r["account_number"], r["gateway_label"], r["mode"]) for r in rows}
    assert actual == expected
    for r in rows:
        assert r["broker_id"] == "ibkr"
        assert r["currency_base"] == "USD"
        assert r["last_seen_via"] == r["gateway_label"]
        assert r["deleted_at"] is None


@pytest.mark.asyncio
async def test_killed_sidecar_marks_label_degraded(
    fleet: tuple[BrokerRegistry, list[grpc.aio.Server], list[int]],
) -> None:
    """One sidecar killed mid-tick → degraded_sidecars reflects it."""
    registry, servers, _ports = fleet
    await registry.probe_once()
    assert await registry.degraded_labels() == []

    await servers[1].stop(grace=0)
    await registry.probe_once()

    degraded = await registry.degraded_labels()
    assert "isa-paper" in degraded
    healthy_count = 4 - len(degraded)
    assert healthy_count == 3


@pytest.mark.asyncio
async def test_zero_soft_deletes_when_all_unhealthy(
    fleet: tuple[BrokerRegistry, list[grpc.aio.Server], list[int]],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """When every sidecar is unhealthy, _discover_once must NOT issue any
    soft-delete UPDATE — the C1 race-free guarantee depends on this."""
    registry, servers, _ports = fleet

    await registry.probe_once()
    discoverer = BrokerDiscoverer(registry, session_factory)
    await discoverer._discover_once()

    for server in servers:
        await server.stop(grace=0)
    await registry.probe_once()
    assert len(await registry.degraded_labels()) == 4
    assert await registry.healthy_clients() == []

    await discoverer._discover_once()

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT account_number, deleted_at "
                        "FROM broker_accounts ORDER BY account_number"
                    )
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 4
    for r in rows:
        assert r["deleted_at"] is None, f"{r['account_number']} unexpectedly soft-deleted"
