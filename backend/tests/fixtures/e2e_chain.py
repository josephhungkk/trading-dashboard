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
from contextlib import ExitStack
from typing import Any, cast
from unittest.mock import patch

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
from app.services.ibkr_maintenance import BrokerMaintenance
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
        # Seed an instrument + symbol alias for the AAPL conid the chain
        # tests use. Pre-seeding the alias avoids the cold-path
        # _resolve_instrument_id eager-create branch, which expects a
        # proto Contract shape (canonical_id/primary_exchange) that
        # BrokerSidecarClient.get_contract doesn't actually emit — that
        # mismatch is a separate bug. The conftest seed already inserts
        # 'equity_us:AAPL:NASDAQ'; just bind the alias here.
        instrument_id_row = await s.execute(
            text("SELECT id FROM instruments WHERE canonical_id = 'equity_us:AAPL:NASDAQ'")
        )
        instrument_id = instrument_id_row.scalar_one_or_none()
        if instrument_id is not None:
            await s.execute(
                text(
                    """
                    INSERT INTO symbol_aliases (source, raw_symbol, instrument_id)
                    VALUES ('ibkr', '265598', :iid)
                    ON CONFLICT (source, raw_symbol) DO NOTHING
                    """
                ),
                {"iid": instrument_id},
            )
        await s.commit()

    # Per-fixture-instance actor email so consecutive `pytest` invocations
    # don't share the orders_service Redis rate-limit bucket
    # (`rl:orders-preview:{email}`, 10/60s, set in orders_service:1683).
    # Previously hardcoded `ci@example.com` caused test_full_modify_chain
    # to flake with 429 after a handful of back-to-back invocations of
    # the same test_postgres + test-redis pair.
    actor_email = f"ci-{uuid.uuid4().hex[:8]}@example.com"

    async def _admin() -> AdminIdentity:
        return AdminIdentity(email=actor_email, kind="user", claims={})

    app.dependency_overrides[require_admin_jwt] = _admin

    # IBKR daily maintenance window (~05:45 UTC) and the weekend window
    # both return 503 from preview/place_order — irrelevant to the chain
    # tests' behaviour, so neutralise the gate by patching the
    # compute_broker_maintenance entry points to always report inactive.
    # Patched at the import sites used by orders_service + brokers.
    _inactive = BrokerMaintenance(active=False)
    maintenance_patches = ExitStack()
    maintenance_patches.enter_context(
        patch(
            "app.services.orders_service.compute_broker_maintenance",
            return_value=_inactive,
        )
    )
    maintenance_patches.enter_context(
        patch(
            "app.services.brokers.compute_broker_maintenance",
            return_value=_inactive,
        )
    )

    order_consumer = None
    try:
        async with app.router.lifespan_context(app):
            # lifespan tries to build_broker_registry() and probably fails
            # because no broker secrets are seeded; re-wire our fake
            # registry/account_service AFTER lifespan yields so the
            # MissingBrokerSecrets path's set_*() (if any) is overridden.
            set_broker_registry(registry)
            set_account_service(account_service)

            # Start an OrderEventConsumer against the fake broker so
            # the FakeBrokerServicer's CancelOrder / ModifyOrder events
            # flow into the orders table — without this the trade
            # chain's "wait for status=cancelled" poll hangs.
            from app.services.order_event_consumer import OrderEventConsumer

            order_consumer = OrderEventConsumer(
                registry,
                factory,
                cast("Any", app.state.redis),
            )
            await order_consumer.start()

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as c:
                yield c, servicer
    finally:
        if order_consumer is not None:
            await order_consumer.stop()
        app.dependency_overrides.clear()
        maintenance_patches.close()
        await sidecar_client_obj.close()
        await grpc_server.stop(grace=1.0)
        async with factory() as s:
            # Drop FK dependents first: order_events references both
            # orders and broker_accounts; risk_decisions references
            # broker_accounts. Cleanup order: order_events ->
            # risk_decisions -> orders -> broker_accounts.
            account_subq = "(SELECT id FROM broker_accounts WHERE account_number = :a)"
            await s.execute(
                text(f"DELETE FROM order_events WHERE account_id IN {account_subq}"),
                {"a": account_number},
            )
            await s.execute(
                text(f"DELETE FROM risk_decisions WHERE account_id IN {account_subq}"),
                {"a": account_number},
            )
            await s.execute(
                text(f"DELETE FROM orders WHERE account_id IN {account_subq}"),
                {"a": account_number},
            )
            await s.execute(
                text("DELETE FROM broker_accounts WHERE account_number = :a"),
                {"a": account_number},
            )
            await s.commit()
