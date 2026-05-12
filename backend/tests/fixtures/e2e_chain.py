"""Shared fixture for the 3 e2e trade/modify/bracket chain tests.

Provides a `chain_client` fixture that:
  - Spins up an in-process gRPC FakeBrokerServicer with mTLS.
  - Wires a real BrokerRegistry({"isa-paper": BrokerSidecarClient}).
  - Wires a real AccountService against the test DB + registry.
  - Seeds a single `isa-paper` paper-mode broker_accounts row.
  - Overrides the autouse `_app_state` fixture's MagicMock stubs.
  - Yields an httpx ASGITransport AsyncClient driven through
    `app.router.lifespan_context(app)` so config_service, redis,
    capability_svc, vol_service, and balance_snapshot_writer are wired.

This is the "Bucket A" pattern referenced in
docs/superpowers/plans/2026-05-08-ci-debt-cleanup.md (Companion Issues).
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator

import grpc
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app._generated.broker.v1 import broker_pb2_grpc
from app.core.cf_access import AdminIdentity
from app.core.db import engine
from app.core.deps import (
    require_admin_jwt,
    set_account_service,
    set_broker_registry,
)
from app.main import app
from app.services.brokers import AccountService, BrokerRegistry
from tests.fixtures.sidecar_servicer import (
    FakeBrokerServicer,
    _build_ephemeral_pki,
    _client_for,
    _DispatchingBrokerServicer,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest_asyncio.fixture
async def chain_client() -> AsyncIterator[tuple[AsyncClient, FakeBrokerServicer]]:
    """Yield (AsyncClient, FakeBrokerServicer) for an E2E chain test.

    The client targets the FastAPI ASGI app with lifespan_context driven,
    so set_config_service() and friends run. broker_registry + account_
    service are overridden post-lifespan to point at the in-process fake
    gRPC server.
    """

    san = f"chain-test-{uuid.uuid4().hex[:8]}"
    pki = _build_ephemeral_pki(server_san=san)
    servicer = FakeBrokerServicer(label="isa-paper")
    servicer.server_san = san
    servicer.pki = pki
    servicer.live_stream = True

    grpc_server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(
        _DispatchingBrokerServicer(servicer),
        grpc_server,
    )
    server_credentials = grpc.ssl_server_credentials(
        [(pki["server_key_pem"], pki["server_cert_pem"])],
        root_certificates=pki["ca_pem"],
        require_client_auth=True,
    )
    port = _free_port()
    grpc_server.add_secure_port(f"127.0.0.1:{port}", server_credentials)
    await grpc_server.start()

    sidecar_client_obj = _client_for(
        target=f"127.0.0.1:{port}",
        san=san,
        pki=pki,
        deadline=5.0,
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = BrokerRegistry({"isa-paper": sidecar_client_obj})
    account_service = AccountService(registry, factory)

    set_broker_registry(registry)
    set_account_service(account_service)

    # Seed an isa-paper account that matches FakeBrokerServicer's
    # ListManagedAccounts response (account_number=DUA0000000,
    # mode=PAPER, gateway_label=isa-paper).
    account_number = "DUA0000000"
    async with factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO broker_accounts (
                  broker_id, account_number, alias, mode, gateway_label,
                  currency_base, last_seen_via
                )
                VALUES (
                  'ibkr'::broker_id_enum, :account_number, 'e2e-chain-isa-paper',
                  'paper'::trading_mode_enum, 'isa-paper', 'USD', 'isa-paper'
                )
                ON CONFLICT (broker_id, account_number) DO NOTHING
                """
            ),
            {"account_number": account_number},
        )
        await s.commit()

    async def _admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    app.dependency_overrides[require_admin_jwt] = _admin

    try:
        async with app.router.lifespan_context(app):
            # lifespan tries to build_broker_registry() and probably fails
            # because no broker secrets are seeded; re-wire our fake
            # registry/account_service AFTER lifespan yields so the
            # MissingBrokerSecrets path's set_*() (if any) is overridden.
            set_broker_registry(registry)
            set_account_service(account_service)
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as c:
                yield c, servicer
    finally:
        app.dependency_overrides.clear()
        await sidecar_client_obj.close()
        await grpc_server.stop(grace=1.0)
        async with factory() as s:
            await s.execute(
                text(
                    "DELETE FROM orders WHERE account_id IN "
                    "(SELECT id FROM broker_accounts WHERE account_number = :a)"
                ),
                {"a": account_number},
            )
            await s.execute(
                text("DELETE FROM broker_accounts WHERE account_number = :a"),
                {"a": account_number},
            )
            await s.commit()
