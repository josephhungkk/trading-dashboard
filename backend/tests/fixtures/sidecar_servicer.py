"""Reusable in-process BrokerSidecarClient gRPC fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from uuid_utils import uuid7

from app._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from app.services.brokers import BrokerSidecarClient

PkiMaterial = dict[str, bytes]


def _build_ephemeral_pki(server_san: str) -> PkiMaterial:
    """Return PEM-encoded CA, server, and client cert material for mTLS."""

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
        common_name: str,
        *,
        eku: x509.ObjectIdentifier,
        san_dns: str | None = None,
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
                x509.SubjectAlternativeName([x509.DNSName(san_dns)]),
                critical=False,
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


class FakeBrokerServicer(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    """Canned Broker service for BrokerSidecarClient integration tests."""

    def __init__(self) -> None:
        self.place_order_response: broker_pb2.PlaceOrderResponse | None = None
        self.cancel_order_response: broker_pb2.CancelOrderResponse | None = None
        self.search_contracts_response: broker_pb2.SearchContractsResponse | None = None
        self.order_event_messages: list[broker_pb2.OrderEventMessage] = []
        self.place_order_calls: list[broker_pb2.PlaceOrderRequest] = []
        self.cancel_order_calls: list[broker_pb2.CancelOrderRequest] = []
        self._sim_orders: dict[str, dict[str, str]] = {}
        self._bracket_children: dict[str, list[str]] = {}
        self._event_subscribers: list[asyncio.Queue[broker_pb2.OrderEventMessage]] = []
        self.delay_seconds = 0.0
        self.unavailable_methods: set[str] = set()
        self.server_san = ""
        self.pki: PkiMaterial = {}

    async def _before_rpc(
        self,
        method: str,
        context: grpc.aio.ServicerContext,
    ) -> None:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if method in self.unavailable_methods:
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"{method} unavailable")

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        del request
        await self._before_rpc("Health", context)
        return broker_pb2.HealthResponse(
            label="test-label",
            gateway_connected=True,
            gateway_version="999",
            sidecar_version="0.4.0-test",
        )

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.AccountsResponse:
        del request
        await self._before_rpc("ListManagedAccounts", context)
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
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SummaryResponse:
        del request
        await self._before_rpc("GetAccountSummary", context)
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
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PositionsResponse:
        del request
        await self._before_rpc("GetPositions", context)
        return broker_pb2.PositionsResponse(positions=[])

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.OrdersResponse:
        del request
        await self._before_rpc("GetOrders", context)
        return broker_pb2.OrdersResponse(orders=[])

    async def GetContract(  # noqa: N802
        self,
        request: broker_pb2.ContractRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ContractResponse:
        await self._before_rpc("GetContract", context)
        return broker_pb2.ContractResponse(
            contract=broker_pb2.Contract(
                conid=request.conid,
                symbol="AAPL",
                exchange="NASDAQ",
                currency="USD",
                asset_class=broker_pb2.STOCK,
                multiplier="1",
                local_symbol="AAPL",
            )
        )

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceOrderResponse:
        await self._before_rpc("PlaceOrder", context)
        self.place_order_calls.append(request)
        if self.place_order_response is not None:
            return self.place_order_response
        sim_id = f"SIM-{uuid7()}"
        self._sim_orders[sim_id] = {
            "client_order_id": request.client_order_id,
            "account_number": request.account_number,
        }
        for queue in self._event_subscribers:
            await queue.put(
                broker_pb2.OrderEventMessage(
                    broker_order_id=sim_id,
                    client_order_id=request.client_order_id,
                    status="submitted",
                    filled_qty="0",
                    avg_fill_price="0",
                    raw_payload="{}",
                )
            )
        return broker_pb2.PlaceOrderResponse(
            broker_order_id=sim_id,
            status="Submitted",
        )

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ModifyOrderResponse:
        await self._before_rpc("ModifyOrder", context)
        for queue in self._event_subscribers:
            await queue.put(
                broker_pb2.OrderEventMessage(
                    broker_order_id=request.broker_order_id,
                    client_order_id=request.client_order_id,
                    status="modified",
                    filled_qty="0",
                    avg_fill_price="0",
                    raw_payload="{}",
                    exec_id="",
                    kind="status",
                )
            )
        return broker_pb2.ModifyOrderResponse(
            broker_order_id=request.broker_order_id,
            status="Modified",
        )

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceBracketResponse:
        await self._before_rpc("PlaceBracket", context)
        parent_id = f"SIM-{uuid7()}"
        sl_id = f"SIM-{uuid7()}" if request.has_stop_loss else ""
        tp_id = f"SIM-{uuid7()}" if request.has_take_profit else ""
        children = [c for c in (sl_id, tp_id) if c]
        self._bracket_children[parent_id] = children
        self._sim_orders[parent_id] = {
            "client_order_id": request.parent.client_order_id,
            "account_number": request.parent.account_number,
        }
        if sl_id:
            self._sim_orders[sl_id] = {
                "client_order_id": request.stop_loss.client_order_id,
                "account_number": request.stop_loss.account_number,
            }
        if tp_id:
            self._sim_orders[tp_id] = {
                "client_order_id": request.take_profit.client_order_id,
                "account_number": request.take_profit.account_number,
            }
        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=parent_id,
            stop_loss_broker_order_id=sl_id,
            take_profit_broker_order_id=tp_id,
            status="Submitted",
        )

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.CancelOrderResponse:
        await self._before_rpc("CancelOrder", context)
        self.cancel_order_calls.append(request)
        if self.cancel_order_response is not None:
            return self.cancel_order_response
        sim_meta = self._sim_orders.pop(request.broker_order_id, None)
        if sim_meta is None:
            return broker_pb2.CancelOrderResponse(accepted=False)
        for queue in self._event_subscribers:
            await queue.put(
                broker_pb2.OrderEventMessage(
                    broker_order_id=request.broker_order_id,
                    client_order_id=sim_meta["client_order_id"],
                    status="cancelled",
                    filled_qty="0",
                    avg_fill_price="0",
                    raw_payload='{"sim_cancel_echo": true}',
                )
            )
        children = self._bracket_children.pop(request.broker_order_id, [])
        for child_id in children:
            child_meta = self._sim_orders.pop(child_id, None)
            if child_meta is None:
                continue
            for queue in self._event_subscribers:
                await queue.put(
                    broker_pb2.OrderEventMessage(
                        broker_order_id=child_id,
                        client_order_id=child_meta["client_order_id"],
                        status="cancelled",
                        filled_qty="0",
                        avg_fill_price="0",
                        raw_payload='{"oca_cascade": true}',
                    )
                )
        return broker_pb2.CancelOrderResponse(accepted=True)

    async def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.OrderEventMessage]:
        del request
        await self._before_rpc("OrderEvent", context)
        queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue(maxsize=1000)
        self._event_subscribers.append(queue)
        try:
            for message in self.order_event_messages:
                yield message
            while not context.cancelled():
                yield await queue.get()
        finally:
            self._event_subscribers.remove(queue)

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SearchContractsResponse:
        del request
        await self._before_rpc("SearchContracts", context)
        if self.search_contracts_response is not None:
            return self.search_contracts_response
        return broker_pb2.SearchContractsResponse(
            contracts=[
                broker_pb2.Contract(
                    conid="265598",
                    symbol="AAPL",
                    exchange="NASDAQ",
                    currency="USD",
                    asset_class=broker_pb2.STOCK,
                    multiplier="1",
                    local_symbol="AAPL",
                )
            ]
        )


class _DispatchingBrokerServicer(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    def __init__(self, servicer: FakeBrokerServicer) -> None:
        self._servicer = servicer

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        return await self._servicer.Health(request, context)

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.AccountsResponse:
        return await self._servicer.ListManagedAccounts(request, context)

    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SummaryResponse:
        return await self._servicer.GetAccountSummary(request, context)

    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PositionsResponse:
        return await self._servicer.GetPositions(request, context)

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.OrdersResponse:
        return await self._servicer.GetOrders(request, context)

    async def GetContract(  # noqa: N802
        self,
        request: broker_pb2.ContractRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ContractResponse:
        return await self._servicer.GetContract(request, context)

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceOrderResponse:
        return await self._servicer.PlaceOrder(request, context)

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.CancelOrderResponse:
        return await self._servicer.CancelOrder(request, context)

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ModifyOrderResponse:
        return await self._servicer.ModifyOrder(request, context)

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceBracketResponse:
        return await self._servicer.PlaceBracket(request, context)

    async def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.OrderEventMessage]:
        async for message in self._servicer.OrderEvent(request, context):
            yield message

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SearchContractsResponse:
        return await self._servicer.SearchContracts(request, context)


def _default_contract() -> broker_pb2.Contract:
    return broker_pb2.Contract(
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        asset_class=broker_pb2.STOCK,
        conid="265598",
        local_symbol="AAPL",
    )


def _client_for(
    *,
    target: str,
    san: str,
    pki: PkiMaterial,
    deadline: float,
) -> BrokerSidecarClient:
    client = BrokerSidecarClient(
        label=san,
        target=target,
        ca_bundle_pem=pki["ca_pem"],
        client_key_pem=pki["client_key_pem"],
        client_cert_pem=pki["client_cert_pem"],
        deadline_seconds=deadline,
    )
    object.__setattr__(
        client,
        "channel",
        grpc.aio.secure_channel(
            target,
            grpc.ssl_channel_credentials(
                root_certificates=pki["ca_pem"],
                private_key=pki["client_key_pem"],
                certificate_chain=pki["client_cert_pem"],
            ),
            options=(("grpc.default_authority", san),),
        ),
    )
    object.__setattr__(
        client,
        "stub",
        cast(Any, broker_pb2_grpc.BrokerStub)(client.channel),
    )
    return client


@pytest.fixture
async def sidecar_server() -> AsyncIterator[tuple[FakeBrokerServicer, str]]:
    """Yield a fake servicer and secure server address for in-process tests."""

    target = "127.0.0.1:0"
    san = f"sidecar-test-{uuid4()}"
    pki = _build_ephemeral_pki(server_san=san)
    servicer = FakeBrokerServicer()
    servicer.server_san = san
    servicer.pki = pki

    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(
        _DispatchingBrokerServicer(servicer),
        server,
    )  # type: ignore[no-untyped-call]
    server_credentials = grpc.ssl_server_credentials(
        [(pki["server_key_pem"], pki["server_cert_pem"])],
        root_certificates=pki["ca_pem"],
        require_client_auth=True,
    )
    port = server.add_secure_port(target, server_credentials)
    target = f"127.0.0.1:{port}"
    await server.start()

    try:
        yield servicer, target
    finally:
        await server.stop(grace=1.0)


@pytest.fixture
async def sidecar_client(
    sidecar_server: tuple[FakeBrokerServicer, str],
) -> AsyncIterator[BrokerSidecarClient]:
    """Yield a BrokerSidecarClient wired to sidecar_server with mTLS."""

    servicer, target = sidecar_server
    client = _client_for(
        target=target,
        san=servicer.server_san,
        pki=servicer.pki,
        deadline=5.0,
    )
    try:
        yield client
    finally:
        await client.close()
