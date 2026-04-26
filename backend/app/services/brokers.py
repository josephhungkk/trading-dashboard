"""Broker sidecar gRPC client."""

from __future__ import annotations

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
