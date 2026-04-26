"""Broker sidecar gRPC client."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, tzinfo
from typing import Any, Protocol, TypeVar, cast

import grpc  # type: ignore[import-untyped]
import structlog

from app._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from app.brokers import base

log = structlog.get_logger(__name__)

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)


class BrokerSidecarUnavailable(Exception):  # noqa: N818
    pass


class BrokerSidecarTimeout(Exception):  # noqa: N818
    pass


class _UnaryUnary(Protocol[RequestT, ResponseT]):
    async def __call__(self, request: RequestT, **kwargs: float | None) -> ResponseT: ...


class _Timestamp(Protocol):
    seconds: int
    nanos: int

    def ToDatetime(self, tzinfo: tzinfo | None = None) -> datetime: ...  # noqa: N802


class BrokerSidecarClient:
    """One mTLS gRPC client for a single broker sidecar."""

    def __init__(
        self,
        *,
        label: str,
        target: str,
        ca_bundle_pem: bytes,
        client_key_pem: bytes,
        client_cert_pem: bytes,
        deadline_seconds: float = 5.0,
    ) -> None:
        self.label = label
        self.target = target
        self.deadline_seconds = deadline_seconds
        credentials = grpc.ssl_channel_credentials(
            root_certificates=ca_bundle_pem,
            private_key=client_key_pem,
            certificate_chain=client_cert_pem,
        )
        self.channel = grpc.aio.secure_channel(
            target,
            credentials,
            options=(("grpc.default_authority", f"sidecar-{label}"),),
        )
        self.stub = cast(Any, broker_pb2_grpc.BrokerStub)(self.channel)

    async def health(self) -> base.HealthResponse:
        response = await self._call(
            method="Health",
            rpc=cast(
                "_UnaryUnary[broker_pb2.HealthRequest, broker_pb2.HealthResponse]",
                self.stub.Health,
            ),
            request=broker_pb2.HealthRequest(),
        )
        return _health_from_proto(response)

    async def list_managed_accounts(self) -> list[base.Account]:
        response = await self._call(
            method="ListManagedAccounts",
            rpc=cast(
                "_UnaryUnary[broker_pb2.Empty, broker_pb2.AccountsResponse]",
                self.stub.ListManagedAccounts,
            ),
            request=broker_pb2.Empty(),
        )
        return [_account_from_proto(account) for account in response.accounts]

    async def get_account_summary(self, account_number: str) -> base.Summary:
        response = await self._call(
            method="GetAccountSummary",
            rpc=cast(
                "_UnaryUnary[broker_pb2.AccountRef, broker_pb2.SummaryResponse]",
                self.stub.GetAccountSummary,
            ),
            request=broker_pb2.AccountRef(account_number=account_number),
        )
        return _summary_from_proto(response.summary)

    async def get_positions(self, account_number: str) -> list[base.Position]:
        response = await self._call(
            method="GetPositions",
            rpc=cast(
                "_UnaryUnary[broker_pb2.AccountRef, broker_pb2.PositionsResponse]",
                self.stub.GetPositions,
            ),
            request=broker_pb2.AccountRef(account_number=account_number),
        )
        return [_position_from_proto(position) for position in response.positions]

    async def get_orders(self, account_number: str) -> list[base.Order]:
        response = await self._call(
            method="GetOrders",
            rpc=cast(
                "_UnaryUnary[broker_pb2.AccountRef, broker_pb2.OrdersResponse]",
                self.stub.GetOrders,
            ),
            request=broker_pb2.AccountRef(account_number=account_number),
        )
        return [_order_from_proto(order) for order in response.orders]

    async def get_contract(self, conid: str) -> base.Contract:
        response = await self._call(
            method="GetContract",
            rpc=cast(
                "_UnaryUnary[broker_pb2.ContractRef, broker_pb2.ContractResponse]",
                self.stub.GetContract,
            ),
            request=broker_pb2.ContractRef(conid=conid),
        )
        return _contract_from_proto(response.contract)

    async def close(self) -> None:
        await self.channel.close(grace=2.0)

    async def _call(
        self,
        *,
        method: str,
        rpc: _UnaryUnary[RequestT, ResponseT],
        request: RequestT,
    ) -> ResponseT:
        started = time.perf_counter()
        try:
            response = await rpc(request, timeout=self.deadline_seconds)
        except grpc.aio.AioRpcError as exc:
            latency_ms = _latency_ms(started)
            log.info(
                "broker_sidecar_rpc_failed",
                label=self.label,
                method=method,
                latency_ms=latency_ms,
                grpc_code=exc.code().name,
            )
            if exc.code() is grpc.StatusCode.DEADLINE_EXCEEDED:
                raise BrokerSidecarTimeout(
                    f"broker sidecar {self.label} {method} timed out"
                ) from exc
            raise BrokerSidecarUnavailable(
                f"broker sidecar {self.label} {method} unavailable: {exc.code().name}"
            ) from exc

        log.info(
            "broker_sidecar_rpc_ok",
            label=self.label,
            method=method,
            latency_ms=_latency_ms(started),
        )
        return response


def _latency_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _timestamp_from_proto(timestamp: _Timestamp) -> datetime | None:
    if timestamp.seconds == 0 and timestamp.nanos == 0:
        return None
    return timestamp.ToDatetime(tzinfo=UTC)


def _health_from_proto(health: broker_pb2.HealthResponse) -> base.HealthResponse:
    return base.HealthResponse(
        label=health.label,
        gateway_connected=health.gateway_connected,
        gateway_version=health.gateway_version,
        last_tick_at=_timestamp_from_proto(health.last_tick_at),
        sidecar_version=health.sidecar_version,
    )


def _account_from_proto(account: broker_pb2.Account) -> base.Account:
    return base.Account(
        account_number=account.account_number,
        mode=cast("base.TradingMode", broker_pb2.TradingMode.Name(account.mode)),
        gateway_label=account.gateway_label,
        currency_base=account.currency_base,
    )


def _money_from_proto(money: broker_pb2.Money) -> base.Money:
    return base.Money(value=str(money.value), currency=money.currency)


def _summary_from_proto(summary: broker_pb2.Summary) -> base.Summary:
    return base.Summary(
        net_liquidation=_money_from_proto(summary.net_liquidation),
        total_cash=_money_from_proto(summary.total_cash),
        realized_pnl=_money_from_proto(summary.realized_pnl),
        unrealized_pnl=_money_from_proto(summary.unrealized_pnl),
        buying_power=_money_from_proto(summary.buying_power),
        updated_at=_timestamp_from_proto(summary.updated_at),
    )


def _contract_from_proto(contract: broker_pb2.Contract) -> base.Contract:
    return base.Contract(
        symbol=contract.symbol,
        exchange=contract.exchange,
        currency=contract.currency,
        asset_class=cast("base.AssetClass", broker_pb2.AssetClass.Name(contract.asset_class)),
        conid=contract.conid,
        local_symbol=contract.local_symbol,
    )


def _position_from_proto(position: broker_pb2.Position) -> base.Position:
    return base.Position(
        contract=_contract_from_proto(position.contract),
        quantity=position.quantity,
        avg_cost=_money_from_proto(position.avg_cost),
        market_price=_money_from_proto(position.market_price),
        market_value=_money_from_proto(position.market_value),
        unrealized_pnl=_money_from_proto(position.unrealized_pnl),
        realized_pnl_today=_money_from_proto(position.realized_pnl_today),
        daily_pnl=_money_from_proto(position.daily_pnl),
    )


def _order_from_proto(order: broker_pb2.Order) -> base.Order:
    return base.Order(
        order_id=order.order_id,
        contract=_contract_from_proto(order.contract),
        side=cast("base.OrderSide", broker_pb2.OrderSide.Name(order.side)),
        order_type=cast("base.OrderType", broker_pb2.OrderType.Name(order.order_type)),
        quantity=order.quantity,
        limit_price=_money_from_proto(order.limit_price),
        stop_price=_money_from_proto(order.stop_price),
        time_in_force=cast("base.TimeInForce", broker_pb2.TimeInForce.Name(order.time_in_force)),
        status=cast("base.OrderStatus", broker_pb2.OrderStatus.Name(order.status)),
        quantity_filled=order.quantity_filled,
        avg_fill_price=_money_from_proto(order.avg_fill_price),
        submitted_at=_timestamp_from_proto(order.submitted_at),
        updated_at=_timestamp_from_proto(order.updated_at),
    )


class BrokerRegistry:
    """Owns BrokerSidecarClients plus a per-label health cache.

    The FastAPI service layer asks the registry for get_client(label)
    or healthy_clients().
    """

    def __init__(
        self,
        clients: dict[str, BrokerSidecarClient],
        *,
        freshness_seconds: float = 90.0,
        probe_interval_healthy: float = 60.0,
        probe_interval_unhealthy: float = 5.0,
    ) -> None:
        self._clients = clients
        self._freshness_seconds = freshness_seconds
        self._probe_interval_healthy = probe_interval_healthy
        self._probe_interval_unhealthy = probe_interval_unhealthy
        self._health_cache: dict[str, tuple[bool, float, base.HealthResponse | None]] = {}
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def get_client(self, label: str) -> BrokerSidecarClient:
        return self._clients[label]

    async def healthy_clients(self) -> list[BrokerSidecarClient]:
        healthy_labels = await self._healthy_labels()
        return [client for label, client in self._clients.items() if label in healthy_labels]

    async def degraded_labels(self) -> list[str]:
        healthy_labels = await self._healthy_labels()
        return [label for label in self._clients if label not in healthy_labels]

    async def probe_once(self) -> None:
        await asyncio.gather(
            *(self._probe_client(label, client) for label, client in self._clients.items())
        )

    async def health_probe_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.probe_once()
            degraded_labels = await self.degraded_labels()
            interval = (
                self._probe_interval_unhealthy if degraded_labels else self._probe_interval_healthy
            )
            log.debug(
                "broker_registry_loop_tick",
                degraded_labels=degraded_labels,
                next_probe_seconds=interval,
            )
            await self._sleep_until_stopped(interval)

    async def stop(self) -> None:
        self._stop_event.set()

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self._clients.values()))

    async def _probe_client(self, label: str, client: BrokerSidecarClient) -> None:
        try:
            health = await client.health()
        except (BrokerSidecarUnavailable, BrokerSidecarTimeout, Exception) as exc:
            await self._mark_health(label, ok=False, health=None)
            log.debug(
                "broker_registry_probe_failed",
                label=label,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return

        await self._mark_health(label, ok=True, health=health)
        log.debug("broker_registry_probe_ok", label=label)

    async def _mark_health(
        self,
        label: str,
        *,
        ok: bool,
        health: base.HealthResponse | None,
    ) -> None:
        async with self._lock:
            self._health_cache[label] = (ok, time.monotonic(), health)

    async def _healthy_labels(self) -> set[str]:
        now = time.monotonic()
        async with self._lock:
            return {
                label
                for label, (ok, probed_at, _health) in self._health_cache.items()
                if ok and now - probed_at <= self._freshness_seconds
            }

    async def _sleep_until_stopped(self, interval: float) -> None:
        sleep_until = time.monotonic() + interval
        while not self._stop_event.is_set():
            remaining = sleep_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.5))
