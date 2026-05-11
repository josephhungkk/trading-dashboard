"""Broker sidecar gRPC client."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, TypeVar, cast
from uuid import UUID

import grpc  # type: ignore[import-untyped]
import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from app.brokers import base
from app.core import metrics
from app.services.ibkr_maintenance import compute_broker_maintenance
from app.services.pnl_intraday_writer import PnlIntradayWriter
from app.services.quotes.base import canonical_id_components, country_for_exchange

log = structlog.get_logger(__name__)

# ISO-4217 currency code: 3 uppercase ASCII letters. Mirrors the DB CHECK
# constraint on broker_accounts.last_nlv_currency to prevent IntegrityError
# from values that pass isascii()/isupper() (e.g. "A1B") but fail [A-Z]{3}.
_ISO3_RE = re.compile(r"^[A-Z]{3}$")

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)
BrokerId = Literal["ibkr", "futu", "schwab", "alpaca"]
TradingMode = Literal["live", "paper"]


@dataclass(frozen=True)
class _AccountRow:
    id: UUID
    broker_id: BrokerId
    account_number: str
    alias: str | None
    mode: TradingMode
    gateway_label: str
    currency_base: str
    display_order: int
    last_nlv: Decimal | None = None
    last_nlv_currency: str | None = None
    last_nlv_at: datetime | None = None
    position_count: int = 0


class BrokerSidecarUnavailable(Exception):  # noqa: N818
    def __init__(
        self,
        message: str,
        *,
        label: str = "",
        grpc_code: str = "",
        grpc_details: str = "",
    ) -> None:
        super().__init__(message)
        self.label = label
        self.grpc_code = grpc_code
        self.grpc_details = grpc_details


class BrokerSidecarTimeout(Exception):  # noqa: N818
    pass


class AccountNotFound(Exception):  # noqa: N818
    """Raised by AccountService when the uuid doesn't resolve."""


class _UnaryUnary(Protocol[RequestT, ResponseT]):
    async def __call__(self, request: RequestT, **kwargs: float | None) -> ResponseT: ...


class _Timestamp(Protocol):
    seconds: int
    nanos: int

    def ToDatetime(self, tzinfo: tzinfo | None = None) -> datetime: ...  # noqa: N802


class BrokerSidecarClient:
    """gRPC client for a single broker sidecar.

    Supports both mTLS (IBKR — over WG to NUC) and insecure (in-cluster
    docker on td-net for Schwab Phase 7a + Alpaca Phase 7c, where peer
    trust is the docker network boundary). Set ``use_mtls=False`` for
    in-cluster sidecars.
    """

    def __init__(
        self,
        *,
        label: str,
        target: str,
        ca_bundle_pem: bytes,
        client_key_pem: bytes,
        client_cert_pem: bytes,
        deadline_seconds: float = 5.0,
        use_mtls: bool = True,
    ) -> None:
        self.label = label
        self.target = target
        self.deadline_seconds = deadline_seconds
        if use_mtls:
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
        else:
            # In-cluster docker on td-net (Schwab Phase 7a, Alpaca Phase 7c).
            # No TLS; peer trust is the docker network boundary.
            self.channel = grpc.aio.insecure_channel(target)
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

    async def configure(
        self,
        *,
        unlock_pwd_md5: str = "",
        rsa_priv_pem: str = "",
        opend_host: str = "127.0.0.1",
        opend_port: int = 11111,
        connection_id: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> broker_pb2.ConfigureResponse:
        request = broker_pb2.ConfigureRequest(
            unlock_pwd_md5=unlock_pwd_md5,
            rsa_priv_pem=rsa_priv_pem,
            opend_host=opend_host,
            opend_port=opend_port,
            connection_id=connection_id,
            metadata=metadata or {},
        )
        return await self._call(
            method="Configure",
            rpc=cast(
                "_UnaryUnary[broker_pb2.ConfigureRequest, broker_pb2.ConfigureResponse]",
                self.stub.Configure,
            ),
            request=request,
        )

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

    async def place_order(
        self,
        account_number: str,
        client_order_id: str,
        conid: str,
        side: str,
        order_type: str,
        tif: str,
        qty: str,
        limit_price: str = "",
        stop_price: str = "",
        trail_offset: str = "",
        trail_offset_type: str = "",
        trail_limit_offset: str = "",
        expiry_date: str = "",
    ) -> base.PlaceOrderResult:
        request = broker_pb2.PlaceOrderRequest(
            account_number=account_number,
            client_order_id=client_order_id,
            conid=conid,
            side=side,
            order_type=order_type,
            tif=tif,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            trail_offset=trail_offset,
            trail_offset_type=trail_offset_type,
            trail_limit_offset=trail_limit_offset,
            expiry_date=expiry_date,
        )
        response = await self._call(
            method="PlaceOrder",
            rpc=cast(
                "_UnaryUnary[broker_pb2.PlaceOrderRequest, broker_pb2.PlaceOrderResponse]",
                self.stub.PlaceOrder,
            ),
            request=request,
        )
        return base.PlaceOrderResult(
            broker_order_id=response.broker_order_id,
            status=response.status,
        )

    async def preview_order(
        self,
        *,
        account_id: str,
        side: str,
        symbol: str,
        asset_class: str,
        order_type: str,
        time_in_force: str,
        qty: str,
        limit_price: str | None = None,
        stop_price: str | None = None,
    ) -> broker_pb2.PreviewOrderResponse:
        """Phase 10a C5 (M6): pre-trade margin/risk preview.

        ``idempotency_key`` is a content-hash (blake2b 16-byte digest) of the
        canonical request payload (sorted keys + compact JSON, excluding the
        key itself). Identical requests collapse to one whatIf round-trip in
        the sidecar's LRU cache. Spec §5 [M6].
        """
        import hashlib
        import json as _json

        canonical = _json.dumps(
            {
                "account_hash": account_id,
                "side": side,
                "symbol": symbol,
                "asset_class": asset_class,
                "order_type": order_type,
                "time_in_force": time_in_force,
                "qty": qty,
                "limit_price": limit_price,
                "stop_price": stop_price,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        idem = "preview:" + hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()
        request = broker_pb2.PreviewOrderRequest(
            account_hash=account_id,
            side=side,
            symbol=symbol,
            asset_class=asset_class,
            order_type=order_type,
            time_in_force=time_in_force,
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            idempotency_key=idem,
        )
        return await self._call(
            method="PreviewOrder",
            rpc=cast(
                "_UnaryUnary[broker_pb2.PreviewOrderRequest, broker_pb2.PreviewOrderResponse]",
                self.stub.PreviewOrder,
            ),
            request=request,
        )

    async def modify_order(
        self,
        *,
        broker_order_id: str,
        account_number: str,
        contract: base.Contract,
        side: str,
        order_type: str,
        tif: str,
        qty: str,
        limit_price: str,
        stop_price: str,
        client_order_id: str,
        trail_offset: str = "",
        trail_offset_type: str = "",
        trail_limit_offset: str = "",
        expiry_date: str = "",
    ) -> base.ModifyOrderResult:
        # ModifyOrderRequest expects Money protos for prices (vs PlaceOrderRequest
        # which takes plain strings). Wrap with the contract's currency so the
        # sidecar can echo it back on fills.
        request = broker_pb2.ModifyOrderRequest(
            broker_order_id=broker_order_id,
            account_number=account_number,
            side=side,
            order_type=order_type,
            tif=tif,
            qty=qty,
            limit_price=broker_pb2.Money(value=limit_price or "0", currency=contract.currency),
            stop_price=broker_pb2.Money(value=stop_price or "0", currency=contract.currency),
            client_order_id=client_order_id,
            trail_offset=trail_offset,
            trail_offset_type=trail_offset_type,
            trail_limit_offset=trail_limit_offset,
            expiry_date=expiry_date,
        )
        response = await self._call(
            method="ModifyOrder",
            rpc=cast(
                "_UnaryUnary[broker_pb2.ModifyOrderRequest, broker_pb2.ModifyOrderResponse]",
                self.stub.ModifyOrder,
            ),
            request=request,
        )
        return base.ModifyOrderResult(
            broker_order_id=response.broker_order_id,
            status=response.status,
        )

    async def place_bracket(
        self,
        *,
        parent_request_proto: Any,
        stop_loss_proto: Any,
        take_profit_proto: Any,
        oca_group: str,
    ) -> base.BracketResult:
        request = broker_pb2.PlaceBracketRequest(
            parent=parent_request_proto,
            stop_loss=stop_loss_proto
            if stop_loss_proto is not None
            else broker_pb2.PlaceOrderRequest(),
            take_profit=take_profit_proto
            if take_profit_proto is not None
            else broker_pb2.PlaceOrderRequest(),
            oca_group=oca_group,
            has_stop_loss=stop_loss_proto is not None,
            has_take_profit=take_profit_proto is not None,
        )
        response = await self._call(
            method="PlaceBracket",
            rpc=cast(
                "_UnaryUnary[broker_pb2.PlaceBracketRequest, broker_pb2.PlaceBracketResponse]",
                self.stub.PlaceBracket,
            ),
            request=request,
        )
        return base.BracketResult(
            parent_broker_order_id=response.parent_broker_order_id,
            stop_loss_broker_order_id=response.stop_loss_broker_order_id,
            take_profit_broker_order_id=response.take_profit_broker_order_id,
            status=response.status,
        )

    async def cancel_order(self, account_number: str, broker_order_id: str) -> bool:
        request = broker_pb2.CancelOrderRequest(
            account_number=account_number,
            broker_order_id=broker_order_id,
        )
        response = await self._call(
            method="CancelOrder",
            rpc=cast(
                "_UnaryUnary[broker_pb2.CancelOrderRequest, broker_pb2.CancelOrderResponse]",
                self.stub.CancelOrder,
            ),
            request=request,
        )
        return response.accepted

    async def get_historical_bars(
        self,
        canonical_id: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        limit: int = 1000,
    ) -> base.HistoricalBarsResult:
        """Fetch historical OHLCV bars from the sidecar (Phase 9 BarService)."""
        from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

        request = broker_pb2.GetHistoricalBarsRequest(
            canonical_id=canonical_id,
            timeframe=timeframe,
            range_start=Timestamp(seconds=int(range_start.timestamp())),
            range_end=Timestamp(seconds=int(range_end.timestamp())),
            limit=limit,
        )
        response = await self._call(
            method="GetHistoricalBars",
            rpc=cast(
                "_UnaryUnary[broker_pb2.GetHistoricalBarsRequest, broker_pb2.GetHistoricalBarsResponse]",  # noqa: E501
                self.stub.GetHistoricalBars,
            ),
            request=request,
        )
        return base.HistoricalBarsResult(
            bars=[
                base.HistoricalBar(
                    bucket_start=datetime.fromtimestamp(b.bucket_start.seconds, tz=UTC),
                    open=Decimal(b.open) if b.open else Decimal(0),
                    high=Decimal(b.high) if b.high else Decimal(0),
                    low=Decimal(b.low) if b.low else Decimal(0),
                    close=Decimal(b.close) if b.close else Decimal(0),
                    volume=Decimal(b.volume) if b.volume else None,
                    trade_count=b.trade_count,
                )
                for b in response.bars
            ],
            truncated=response.truncated,
        )

    async def search_contracts(self, query: str, asset_class: str = "") -> list[base.Contract]:
        request = broker_pb2.SearchContractsRequest(query=query, asset_class=asset_class)
        response = await self._call(
            method="SearchContracts",
            rpc=cast(
                "_UnaryUnary[broker_pb2.SearchContractsRequest, broker_pb2.SearchContractsResponse]",  # noqa: E501
                self.stub.SearchContracts,
            ),
            request=request,
        )
        return [_contract_from_proto(contract) for contract in response.contracts]

    async def order_event_stream(
        self,
        account_number: str,
    ) -> AsyncIterator[base.OrderEventMessage]:
        request = broker_pb2.AccountRef(account_number=account_number)
        async for msg in self._stream_call("OrderEvent", self.stub.OrderEvent, request):
            yield base.OrderEventMessage(
                broker_order_id=msg.broker_order_id,
                client_order_id=msg.client_order_id,
                status=msg.status,
                filled_qty=msg.filled_qty,
                avg_fill_price=msg.avg_fill_price,
                broker_event_at=_timestamp_from_proto(msg.event_at),
                raw_payload=msg.raw_payload,
                exec_id=msg.exec_id,
                kind=msg.kind,
            )

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
                f"broker sidecar {self.label} {method} unavailable: {exc.code().name}",
                label=self.label,
                grpc_code=exc.code().name,
                grpc_details=str(exc.details() or ""),
            ) from exc

        log.info(
            "broker_sidecar_rpc_ok",
            label=self.label,
            method=method,
            latency_ms=_latency_ms(started),
        )
        return response

    async def _stream_call(
        self,
        method: str,
        rpc: Callable[..., Any],
        request: Any,
    ) -> AsyncIterator[Any]:
        # Streaming RPCs (OrderEvent) must NOT carry a per-call deadline —
        # the deadline applies to the full stream lifespan, so a 5s default
        # tears the subscription down every 5 seconds with DEADLINE_EXCEEDED.
        # The consumer's reconnect-and-resync still bounds total downtime;
        # individual connection liveness is governed by gRPC keepalives.
        started = time.perf_counter()
        try:
            async for msg in rpc(request):
                yield msg
        except asyncio.CancelledError:
            log.debug(
                "broker_sidecar_stream_cancelled",
                label=self.label,
                method=method,
                latency_ms=_latency_ms(started),
            )
            raise
        except grpc.aio.AioRpcError as exc:
            latency_ms = _latency_ms(started)
            log.info(
                "broker_sidecar_stream_failed",
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
                f"broker sidecar {self.label} {method} unavailable: {exc.code().name}",
                label=self.label,
                grpc_code=exc.code().name,
                grpc_details=str(exc.details() or ""),
            ) from exc

        log.info(
            "broker_sidecar_stream_closed",
            label=self.label,
            method=method,
            latency_ms=_latency_ms(started),
        )


def _latency_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _timestamp_from_proto(timestamp: _Timestamp) -> datetime | None:
    if timestamp.seconds == 0 and timestamp.nanos == 0:
        return None
    return timestamp.ToDatetime(tzinfo=UTC)


def _health_from_proto(health: broker_pb2.HealthResponse) -> base.HealthResponse:
    response = base.HealthResponse(
        label=health.label,
        gateway_connected=health.gateway_connected,
        gateway_version=health.gateway_version,
        last_tick_at=_timestamp_from_proto(health.last_tick_at),
        sidecar_version=health.sidecar_version,
    )
    object.__setattr__(response, "started_at", health.started_at)
    object.__setattr__(response, "broker_id", health.broker_id)
    return response


def _account_from_proto(account: broker_pb2.Account) -> base.Account:
    return base.Account(
        account_number=account.account_number,
        mode=cast("base.TradingMode", broker_pb2.TradingMode.Name(account.mode)),
        gateway_label=account.gateway_label,
        currency_base=account.currency_base,
        account_hash=account.account_hash,  # HIGH-db-1: preserve Schwab hash through discoverer
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
        multiplier=contract.multiplier,
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
        self._configured: dict[str, datetime] = {}
        self._configured_started_at: dict[str, int] = {}
        self._reconfig_locks: dict[str, asyncio.Lock] = {}
        self._config_service: Any | None = None
        self._configurer: Any | None = None

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

    def _reconfig_lock(self, label: str) -> asyncio.Lock:
        lock = self._reconfig_locks.get(label)
        if lock is None:
            lock = asyncio.Lock()
            self._reconfig_locks[label] = lock
        return lock

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

        from app.services.broker_registry_factory import SIDECAR_BROKERS

        expected_broker = SIDECAR_BROKERS.get(label)
        actual_broker = getattr(health, "broker_id", "")
        if expected_broker and actual_broker and actual_broker != expected_broker:
            log.critical(
                "broker_registry_label_mismatch",
                label=label,
                expected=expected_broker,
                actual=actual_broker,
            )
            metrics.broker_registry_label_mismatch_total.labels(label=label).inc()
            await self._mark_health(label, ok=False, health=health)
            return

        # H2: if started_at differs from cached, sidecar restarted -> re-Configure.
        started_at_dt: datetime | None = None
        try:
            started_at = cast("_Timestamp | None", getattr(health, "started_at", None))
            if started_at is not None and started_at.seconds:
                started_at_dt = started_at.ToDatetime(tzinfo=UTC)
        except Exception:
            started_at_dt = None

        configurer = self._configurer
        if (
            configurer is not None
            and started_at_dt is not None
            and label in getattr(configurer, "targets", set())
        ):
            async with self._reconfig_lock(label):
                cached = self._configured.get(label)
                if cached is None or cached != started_at_dt:
                    try:
                        ok = await configurer.configure(label)
                        if ok:
                            self._configured[label] = started_at_dt
                        else:
                            log.warning("broker_reconfigure_returned_not_ok", label=label)
                            await self._mark_health(label, ok=False, health=health)
                            return
                    except Exception as exc:
                        log.warning("broker_reconfigure_failed", label=label, error=str(exc))
                        await self._mark_health(label, ok=False, health=health)
                        return

        schwab_started_at = cast("_Timestamp | None", getattr(health, "started_at", None))
        if label == "schwab" and schwab_started_at is not None:
            current = schwab_started_at.seconds
            if current > 0 and self._configured_started_at.get(label) != current:
                config_service = self._config_service
                if config_service is None and configurer is not None:
                    config_service = getattr(configurer, "config_service", None)
                    self._config_service = config_service
                if config_service is None:
                    structlog.get_logger().warning(
                        "schwab_restart_detected_no_reconfigure",
                        started_at=current,
                        label=label,
                    )
                    self._configured_started_at[label] = current
                else:
                    from app.services.broker_registry_factory import reconfigure_schwab

                    try:
                        await reconfigure_schwab(config_service)
                        self._configured_started_at[label] = current
                    except Exception as exc:
                        # Phase 4 retro H1: a reconfigure failure means the
                        # in-memory ConfigService no longer matches the
                        # restarted sidecar. Marking healthy=True here would
                        # let the discoverer fan out queries that the sidecar
                        # rejects with `503 broker layer not yet configured`.
                        # Mark unhealthy so the next probe retries cleanly.
                        structlog.get_logger().error(
                            "schwab_reconfigure_on_started_at_delta_failed",
                            error_class=type(exc).__name__,
                            label=label,
                        )
                        await self._mark_health(label, ok=False, health=None)
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


class AccountService:
    """Orchestrates uuid->tuple resolution + sidecar fan-out for the
    /api/accounts/* REST routes. The single backend chokepoint that
    translates the frontend's account_id (UUID) into the (broker_id,
    gateway_label, account_number) the sidecar needs.
    All methods read broker_accounts WHERE deleted_at IS NULL."""

    def __init__(
        self,
        registry: BrokerRegistry,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory

    async def list_accounts(self) -> base.AccountListResponse:
        """Returns AccountListResponse with accounts populated from
        broker_accounts WHERE deleted_at IS NULL ORDER BY display_order,
        account_number. degraded_sidecars from registry.degraded_labels()."""
        stmt = text(
            """
            SELECT a.id, a.broker_id, a.account_number, a.alias, a.mode,
                   a.gateway_label, a.currency_base, a.display_order,
                   a.last_nlv, a.last_nlv_currency, a.last_nlv_at,
                   COALESCE(p.cnt, 0) AS position_count
              FROM broker_accounts a
              LEFT JOIN (
                   SELECT account_id, count(*) AS cnt
                     FROM positions
                    GROUP BY account_id
                   ) p ON p.account_id = a.id
             WHERE a.deleted_at IS NULL
             ORDER BY a.display_order, a.account_number;
            """
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = [_account_row_from_mapping(row) for row in result.mappings().all()]

        degraded_sidecars = await self._registry.degraded_labels()
        return base.AccountListResponse(
            accounts=[_account_response_from_row(row) for row in rows],
            degraded_sidecars=degraded_sidecars,
            broker_maintenance=compute_broker_maintenance(datetime.now(UTC)),
        )

    async def list_broker_status(self) -> list[base.BrokerSidecarStatus]:
        """Per-sidecar (broker, label, mode, connected) tuples for the
        Windows tray probe at GET /api/brokers/accounts. One row per
        (gateway_label, mode, broker_id) triple in broker_accounts; the
        `connected` flag mirrors the registry's health probe."""
        stmt = text(
            """
            SELECT DISTINCT broker_id, gateway_label, mode
              FROM broker_accounts
             WHERE deleted_at IS NULL
             ORDER BY broker_id, mode, gateway_label;
            """
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = result.mappings().all()

        degraded = set(await self._registry.degraded_labels())
        return [
            base.BrokerSidecarStatus(
                broker=row["broker_id"],
                label=row["gateway_label"],
                mode=row["mode"],
                connected=row["gateway_label"] not in degraded,
            )
            for row in rows
        ]

    async def get_summary(self, account_id: UUID) -> base.Summary:
        """Resolve uuid -> (gateway_label, account_number), fetch via
        registry.get_client(gateway_label).get_account_summary()."""
        row = await self._resolve_account(account_id)
        client = await self._registry.get_client(row.gateway_label)
        return await client.get_account_summary(row.account_number)

    async def get_positions(self, account_id: UUID) -> list[base.Position]:
        """Resolve uuid -> (gateway_label, account_number), fetch positions
        AND summary in parallel (asyncio.gather), run H11 invariant check,
        return positions."""
        row = await self._resolve_account(account_id)
        client = await self._registry.get_client(row.gateway_label)
        positions, summary = await asyncio.gather(
            client.get_positions(row.account_number),
            client.get_account_summary(row.account_number),
        )
        _check_avg_cost_unit_invariant(account_id, positions, summary)
        return positions

    async def get_orders(self, account_id: UUID) -> list[base.Order]:
        """Resolve uuid -> (gateway_label, account_number), fetch orders
        via registry.get_client(gateway_label).get_orders()."""
        row = await self._resolve_account(account_id)
        client = await self._registry.get_client(row.gateway_label)
        return await client.get_orders(row.account_number)

    async def update_alias(
        self,
        account_id: UUID,
        update: base.AccountAliasUpdate,
    ) -> base.AccountResponse:
        """UPDATE broker_accounts SET alias=:alias, updated_at=now()
        WHERE id=:id AND deleted_at IS NULL.
        Raises AccountNotFound if row doesn't exist or is soft-deleted."""
        stmt = text(
            """
            UPDATE broker_accounts
               SET alias = :alias,
                   updated_at = now()
             WHERE id = :id AND deleted_at IS NULL
            RETURNING id, broker_id, account_number, alias, mode, gateway_label,
                      currency_base, display_order,
                      last_nlv, last_nlv_currency, last_nlv_at;
            """
        )
        async with self._session_factory() as session, session.begin():
            result = await session.execute(stmt, {"id": account_id, "alias": update.alias})
            row = result.mappings().one_or_none()

        if row is None:
            raise AccountNotFound(f"account {account_id} not found")
        return _account_response_from_row(_account_row_from_mapping(row))

    async def _resolve_account(self, account_id: UUID) -> _AccountRow:
        stmt = text(
            """
            SELECT id, broker_id, account_number, alias, mode, gateway_label,
                   currency_base, display_order
              FROM broker_accounts
             WHERE id = :id AND deleted_at IS NULL;
            """
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt, {"id": account_id})
            row = result.mappings().one_or_none()
        if row is None:
            raise AccountNotFound(f"account {account_id} not found")
        return _account_row_from_mapping(row)


class BrokerDiscoverer:
    """Polls each healthy sidecar's ListManagedAccounts every N seconds and
    upserts broker_accounts rows. Soft-deletes rows that healthy sidecars
    failed to report — ONLY when the sidecar that owns the row is actually
    healthy this tick (C1 race-free guarantee)."""

    def __init__(
        self,
        registry: BrokerRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_seconds: float = 30.0,
        redis: Any = None,
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._stop_event = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        # Phase 9.7: track (broker_id, account_number) pairs seen since process
        # start so we can trigger a one-shot BASE-tag refresh when a mid-run
        # account appears for the first time.
        self._known_accounts: set[tuple[str, str]] = set()
        # Phase 9.7 G1: per-(label, account_number) monotonic timestamp of the
        # last successful position fetch. Feeds broker_poller_drift_seconds
        # gauge (BrokerPollerDriftHigh alert fires at >60s sustained 2m).
        self._last_position_tick_at: dict[tuple[str, str], float] = {}
        # Phase 10a.5 A4.3: redis injected for orphan-token UNLINK sweep +
        # BP counter reconcile. Optional (None) so existing tests that
        # build a BrokerDiscoverer without redis still construct.
        self._redis = redis

    async def discover_loop(self) -> None:
        # Single-consumer invariant: only this method calls _discover_once,
        # so the locked()-then-acquire pattern is race-free here. If a
        # second caller is ever added, replace with wait_for(acquire(), 0).
        while not self._stop_event.is_set():
            if self._tick_lock.locked():
                log.warning(
                    "broker_discover_iteration_skipped_overlap",
                    interval_seconds=self._interval,
                )
            else:
                async with self._tick_lock:
                    try:
                        await self._discover_once()
                    except Exception as exc:
                        log.exception(
                            "broker_discover_loop_exception",
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def _unlink_risk_counter_orphans(self) -> int:
        """Phase 10a.5 A4.3: reap orphan risk-counter tokens before reconcile.

        A token whose ``decrement_pdt`` / ``commit_bp`` fired but whose dispatch
        never completed (process crash, network drop between Redis write and
        broker SubmitOrder) would otherwise leak past its 86400s TTL. Sweeping
        every tick bounds the leak window to one discoverer cycle (~30s).

        Returns the total number of orphan token keys unlinked.
        """
        if self._redis is None:
            return 0
        total = 0
        for pattern in ("risk:pdt:tok:*", "risk:bp:tok:*"):
            cursor: int | bytes = 0
            try:
                while True:
                    cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=100)
                    if keys:
                        await self._redis.unlink(*keys)
                        total += len(keys)
                    if cursor in (0, b"0"):
                        break
            except (Exception,) as exc:  # noqa: B013
                log.warning(
                    "risk_counter_orphan_sweep_failed",
                    pattern=pattern,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                metrics.risk_counter_cleanup_failures_total.inc()
                break
        metrics.risk_counter_orphan_tokens_total.set(total)
        return total

    async def _discover_once(self) -> None:
        healthy_clients = await self._registry.healthy_clients()
        healthy = [(client, client.label) for client in healthy_clients]
        healthy_labels = [label for _client, label in healthy]

        log.info("broker_discover_iteration_start", healthy_labels=healthy_labels)

        # Phase 10a.5 A4.3: reap orphan tokens BEFORE this tick's reconciles
        # so a stale token cannot survive the counter overwrite below.
        try:
            await self._unlink_risk_counter_orphans()
        except (Exception,) as _exc:  # noqa: B013
            log.warning("risk_counter_orphan_sweep_outer_failed", error=str(_exc))

        account_results = await asyncio.gather(
            *(client.list_managed_accounts() for client, _label in healthy),
            return_exceptions=True,
        )

        rows_seen: list[tuple[str, base.Account]] = []
        for (_client, label), result in zip(healthy, account_results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "broker_discover_iteration_failed",
                    label=label,
                    error=str(result),
                    error_type=type(result).__name__,
                )
                continue
            rows_seen.extend((label, account) for account in result)

        upsert_stmt = text(
            """
            INSERT INTO broker_accounts (
                broker_id,
                account_number,
                mode,
                gateway_label,
                currency_base,
                account_hash,
                last_seen_via,
                last_seen_at,
                deleted_at,
                updated_at
            )
            VALUES (
                CAST(:broker_id AS broker_id_enum),
                :account_number,
                CAST(:mode AS trading_mode_enum),
                :gateway_label,
                :currency_base,
                NULLIF(:account_hash, ''),
                :last_seen_via,
                now(),
                NULL,
                now()
            )
            ON CONFLICT (broker_id, account_number) DO UPDATE
               SET mode = EXCLUDED.mode,
                   gateway_label = EXCLUDED.gateway_label,
                   currency_base = EXCLUDED.currency_base,
                   account_hash = COALESCE(EXCLUDED.account_hash, broker_accounts.account_hash),
                   last_seen_via = EXCLUDED.last_seen_via,
                   last_seen_at = EXCLUDED.last_seen_at,
                   deleted_at = NULL,
                   last_nlv = CASE
                       WHEN broker_accounts.deleted_at IS NOT NULL THEN NULL
                       ELSE broker_accounts.last_nlv
                   END,
                   last_nlv_currency = CASE
                       WHEN broker_accounts.deleted_at IS NOT NULL THEN NULL
                       ELSE broker_accounts.last_nlv_currency
                   END,
                   last_nlv_at = CASE
                       WHEN broker_accounts.deleted_at IS NOT NULL THEN NULL
                       ELSE broker_accounts.last_nlv_at
                   END,
                   updated_at = now()
            """
        )

        from app.services.broker_registry_factory import SIDECAR_BROKERS

        rows_seen_keys = [
            (SIDECAR_BROKERS.get(label, "ibkr"), account.account_number)
            for label, account in rows_seen
        ]
        soft_delete_count = 0

        async with self._session_factory() as session, session.begin():
            # Phase 5b.1 A3 (R1): capture account_ids about to be resurrected
            # BEFORE the upsert flips deleted_at -> NULL, so we can clear
            # their positions cache to avoid showing week-old stale data.
            # Mirrors the existing last_nlv* CASE null-out for resurrected rows.
            resurrected_ids: list[UUID] = []
            if rows_seen_keys:
                resurrect_check_stmt = text(
                    """
                    SELECT id FROM broker_accounts
                     WHERE (broker_id, account_number) IN :keys
                       AND deleted_at IS NOT NULL
                    """
                ).bindparams(bindparam("keys", expanding=True))
                resurrect_rows = await session.execute(
                    resurrect_check_stmt,
                    {"keys": rows_seen_keys},
                )
                resurrected_ids = [cast(UUID, r[0]) for r in resurrect_rows]

            for label, account in rows_seen:
                await session.execute(
                    upsert_stmt,
                    {
                        "broker_id": SIDECAR_BROKERS.get(label, "ibkr"),
                        "account_number": account.account_number,
                        "mode": self._mode_value(account.mode),
                        "gateway_label": label,
                        "currency_base": account.currency_base,
                        "account_hash": account.account_hash,  # HIGH-db-1
                        "last_seen_via": label,
                    },
                )

            if resurrected_ids:
                await session.execute(
                    text("DELETE FROM positions WHERE account_id = ANY(:ids)"),
                    {"ids": resurrected_ids},
                )

            if healthy_labels:
                # healthy_labels is passed as a single Postgres TEXT[] (not an
                # expanding placeholder) so asyncpg can hand it to ANY(...) as
                # one array parameter. rows_seen_keys remains expanding=True
                # because (broker_id, account_number) NOT IN (...) needs the
                # row-tuple list unrolled into N tuples.
                if rows_seen_keys:
                    soft_delete_stmt = text(
                        """
                        UPDATE broker_accounts
                           SET deleted_at = now(),
                               updated_at = now()
                         WHERE deleted_at IS NULL
                           AND last_seen_via = ANY(:healthy_labels)
                           AND (broker_id, account_number) NOT IN :rows_seen_keys
                           AND last_seen_at < now() - INTERVAL '30 minutes'
                        RETURNING id;
                        """
                    ).bindparams(
                        bindparam("rows_seen_keys", expanding=True),
                    )
                    soft_delete_params = {
                        "healthy_labels": healthy_labels,
                        "rows_seen_keys": rows_seen_keys,
                    }
                else:
                    soft_delete_stmt = text(
                        """
                        UPDATE broker_accounts
                           SET deleted_at = now(),
                               updated_at = now()
                         WHERE deleted_at IS NULL
                           AND last_seen_via = ANY(:healthy_labels)
                           AND last_seen_at < now() - INTERVAL '30 minutes'
                        RETURNING id;
                        """
                    )
                    soft_delete_params = {"healthy_labels": healthy_labels}

                # Phase 4 retro M7: SQLAlchemy 2.0 async CursorResult.rowcount
                # is unreliable across drivers (asyncpg vs aiomysql) and is
                # explicitly out-of-spec for the typed Result protocol.
                # RETURNING id + len(rows) is portable and exact.
                soft_delete_result = await session.execute(soft_delete_stmt, soft_delete_params)
                soft_delete_count = len(soft_delete_result.all())

        # Phase 9.7: one-shot BASE-tag refresh for mid-run new accounts.
        # When a (broker_id, account_number) pair appears for the first time
        # this process lifetime, call list_managed_accounts() + get_account_summary()
        # with a 15 s timeout so the sidecar's BASE-tag round has time to
        # complete before the NLV fan-out below reads from it.
        #
        # Rationale: at startup the sidecar runs reqAccountUpdates(True/False, acct)
        # for every known account (ibkr_sidecar.py C2 round, ~2.3 s/account).
        # A mid-run account added AFTER the sidecar started never gets that
        # round, so currency_base stays empty until sidecar restart.  Triggering
        # a summary fetch here forces the sidecar's ib_async subscription to
        # refresh its account-value cache.  We call list_managed_accounts() first
        # as a lightweight ping; the summary call is the one that matters.
        from app.services.broker_registry_factory import SIDECAR_BROKERS as _SB97

        _newly_seen: list[tuple[str, str, str]] = []  # (label, broker_id, account_number)
        for label, account in rows_seen:
            broker_id_97 = _SB97.get(label, "ibkr")
            pair = (broker_id_97, account.account_number)
            if pair not in self._known_accounts:
                _newly_seen.append((label, broker_id_97, account.account_number))

        if _newly_seen:

            async def _base_refresh(label97: str, acct_num97: str) -> None:
                try:
                    client97 = await self._registry.get_client(label97)
                    # list_managed_accounts() is a lightweight ping that helps
                    # the sidecar confirm the account is live before the heavier
                    # summary call.
                    await asyncio.wait_for(client97.list_managed_accounts(), timeout=5.0)
                    await asyncio.wait_for(
                        client97.get_account_summary(acct_num97),
                        timeout=15.0,
                    )
                except TimeoutError, BrokerSidecarUnavailable, BrokerSidecarTimeout, KeyError:
                    pass

            await asyncio.gather(
                *(_base_refresh(lbl, acct) for lbl, _bid, acct in _newly_seen),
                return_exceptions=True,
            )

            for lbl97, bid97, acct97 in _newly_seen:
                log.info(
                    "broker_account_first_seen",
                    label=lbl97,
                    broker_id=bid97,
                    account_number_len=len(acct97),
                )
                self._known_accounts.add((bid97, acct97))

        # Phase 5a (spec section 5): GetAccountSummary fan-out for per-account NLV cache.
        # Each call is bounded by wait_for(timeout=10.0); gather collects results
        # with return_exceptions=True so one slow/dead sidecar cannot taint the
        # others. C3 + C4 will consume results to skip-write + UPDATE.
        summary_targets: list[tuple[str, str]] = [
            (label, account.account_number) for label, account in rows_seen
        ]

        async def _fetch_summary(
            label: str,
            account_number: str,
        ) -> tuple[str, str, base.Summary] | None:
            try:
                client = await self._registry.get_client(label)
                summary = await asyncio.wait_for(
                    client.get_account_summary(account_number),
                    timeout=10.0,
                )
                return (label, account_number, summary)
            except TimeoutError, BrokerSidecarUnavailable, BrokerSidecarTimeout, KeyError:
                return None

        results: list[tuple[str, str, base.Summary] | None | BaseException] = await asyncio.gather(
            *(_fetch_summary(label, account_number) for (label, account_number) in summary_targets),
            return_exceptions=True,
        )

        def _is_populated(summary: base.Summary) -> bool:
            nlv_currency = summary.net_liquidation.currency
            nlv_value = summary.net_liquidation.value
            return _ISO3_RE.match(nlv_currency) is not None and bool(nlv_value)

        def _format_decimal(s: str) -> str | None:
            try:
                d = Decimal(s).quantize(Decimal("1e-8"))
            except InvalidOperation:
                return None
            if d == Decimal(0):
                # Skip zero NLV — unfunded accounts should show no cached value
                # (FE renders the placeholder rather than starting the staleness clock).
                return None
            return format(d, "f")

        nlv_update_stmt = text(
            """
            UPDATE broker_accounts
               SET last_nlv = CAST(:nlv AS NUMERIC(20, 8)),
                   last_nlv_currency = :currency,
                   last_nlv_at = now(),
                   updated_at = now()
             WHERE broker_id = CAST(:broker_id AS broker_id_enum)
               AND account_number = :account_number
               AND deleted_at IS NULL
            """
        )

        nlv_update_count = 0
        nlv_overflow_count = 0
        t_start = time.monotonic()
        from app.services.broker_registry_factory import SIDECAR_BROKERS

        async with self._session_factory() as session:
            async with session.begin():
                for r in results:
                    if r is None or isinstance(r, BaseException):
                        continue
                    label, account_number, summary = r
                    if not _is_populated(summary):
                        continue
                    nlv_str = _format_decimal(summary.net_liquidation.value)
                    if nlv_str is None:
                        log.warning(
                            "broker_discover_nlv_unparsable",
                            label=label,
                            account_number=account_number,
                            raw_value_len=len(summary.net_liquidation.value),
                        )
                        continue
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                nlv_update_stmt,
                                {
                                    "broker_id": SIDECAR_BROKERS.get(label, "ibkr"),
                                    "account_number": account_number,
                                    "nlv": nlv_str,
                                    "currency": summary.net_liquidation.currency,
                                },
                            )
                        nlv_update_count += 1
                    except DBAPIError as exc:
                        # 22003 = numeric_value_out_of_range (asyncpg sqlstate);
                        # locale-stable vs string-matching the message text.
                        if getattr(exc.orig, "sqlstate", None) != "22003":
                            raise
                        nlv_overflow_count += 1
                        metrics.broker_discover_nlv_overflow_total.inc()
                        # Don't log raw_value or str(exc): asyncpg's DBAPIError
                        # messages embed the offending NUMERIC value, leaking
                        # account NLV into structured logs.
                        log.warning(
                            "broker_discover_nlv_overflow",
                            label=label,
                            account_number=account_number,
                            raw_value_len=len(summary.net_liquidation.value),
                            error_class=type(exc).__name__,
                        )
        metrics.broker_discover_nlv_update_duration_ms.observe((time.monotonic() - t_start) * 1000)

        # Phase 5b.1 A3: positions fan-out (mirrors NLV pattern above).
        positions_targets: list[tuple[str, str]] = [
            (label, account.account_number) for label, account in rows_seen
        ]
        await self._discover_positions(positions_targets)

        log.info(
            "broker_discover_iteration_ok",
            upsert_count=len(rows_seen),
            soft_delete_count=soft_delete_count,
            nlv_update_count=nlv_update_count,
            nlv_overflow_count=nlv_overflow_count,
        )

    async def _discover_positions(self, targets: list[tuple[str, str]]) -> None:
        """Fan out GetPositions per account, upsert positions, delete vanished rows.

        Phase 5b.1 A3 — mirrors _discover_nlv. Per-account savepoint isolates
        NUMERIC(20,8) overflow; RPC failures leave the row untouched; gather
        return_exceptions=True ensures one failure doesn't break the batch.
        """
        if not targets:
            return

        t_start = time.monotonic()

        async def _fetch(
            label: str, account_number: str
        ) -> tuple[str, str, list[base.Position]] | None:
            try:
                client = await self._registry.get_client(label)
                positions = await asyncio.wait_for(
                    client.get_positions(account_number),
                    timeout=10.0,
                )
                return (label, account_number, positions)
            except TimeoutError, BrokerSidecarUnavailable, BrokerSidecarTimeout, KeyError:
                return None

        results = await asyncio.gather(
            *(_fetch(label, account_number) for (label, account_number) in targets),
            return_exceptions=True,
        )

        from app.services.broker_registry_factory import SIDECAR_BROKERS

        async with self._session_factory() as session, session.begin():
            for r in results:
                if r is None or isinstance(r, BaseException):
                    continue
                label, account_number, positions = r
                # Resolve account_id for this (broker_id, account_number) tuple.
                acct_row = (
                    await session.execute(
                        text(
                            "SELECT id FROM broker_accounts "
                            " WHERE broker_id = CAST(:broker_id AS broker_id_enum) "
                            "   AND account_number = :account_number "
                            "   AND deleted_at IS NULL"
                        ),
                        {
                            "broker_id": SIDECAR_BROKERS.get(label, "ibkr"),
                            "account_number": account_number,
                        },
                    )
                ).one_or_none()
                if acct_row is None:
                    continue
                account_id = cast(UUID, acct_row[0])
                resolved_broker_id = SIDECAR_BROKERS.get(label, "ibkr")
                try:
                    async with session.begin_nested():
                        await self._upsert_positions(
                            session, account_id, positions, resolved_broker_id
                        )
                    # Phase 9.7 G1: record the successful tick timestamp.
                    # MUST be inside the try block so an overflow / DB error
                    # does NOT update the timestamp (silent-failure-hunter
                    # HIGH-2 — otherwise drift gauge would mask stale data
                    # for accounts whose upsert kept failing).
                    self._last_position_tick_at[(label, account_number)] = time.monotonic()
                except DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) != "22003":
                        raise
                    metrics.broker_discover_positions_overflow_total.labels(label=label).inc()
                    log.warning(
                        "broker_discover_positions_overflow",
                        label=label,
                        account_number=account_number,
                    )

                # Phase 10a.5 A2.3: pnl_intraday fan-in.
                # Source-field invariant (CRIT-1): realized_today MUST come from
                # SUM(Position.realized_pnl_today), NOT Summary.realized_pnl
                # (the latter is cumulative-since-open for IBKR).
                # Multi-currency policy (HIGH-1): only positions whose
                # realized_pnl_today.currency matches account.currency_base are
                # summed; mismatched positions are dropped + counted.
                #
                # summary_updated_at is set to observation-time (now()) rather
                # than threaded from the Summary fan-out — positions and summary
                # are fetched in disjoint fan-outs in the current discoverer;
                # the spec's "Summary.updated_at" path requires a refactor that
                # is out of scope for A2.3. now() is conservative for staleness:
                # the gate's WARN threshold (90s) still fires on real outages.
                try:
                    async with session.begin_nested():
                        currency_row = (
                            await session.execute(
                                text("SELECT currency_base FROM broker_accounts WHERE id = :aid"),
                                {"aid": account_id},
                            )
                        ).one_or_none()
                        if currency_row is not None:
                            account_currency = str(currency_row[0])
                            matching = [
                                p
                                for p in positions
                                if p.realized_pnl_today.currency == account_currency
                                and p.unrealized_pnl.currency == account_currency
                            ]
                            skipped = len(positions) - len(matching)
                            if skipped > 0:
                                metrics.pnl_intraday_currency_skip_total.labels(
                                    broker_id=resolved_broker_id,
                                ).inc(skipped)
                            realized_today_total = sum(
                                (Decimal(p.realized_pnl_today.value) for p in matching),
                                Decimal("0"),
                            )
                            unrealized_total = sum(
                                (Decimal(p.unrealized_pnl.value) for p in matching),
                                Decimal("0"),
                            )
                            writer = PnlIntradayWriter(session)
                            await writer.upsert(
                                account_id=account_id,
                                realized_today=realized_today_total,
                                unrealized=unrealized_total,
                                currency=account_currency,
                                summary_updated_at=datetime.now(UTC),
                                source_label=label,
                            )
                            metrics.pnl_intraday_last_update_seconds.labels(
                                account_id=str(account_id),
                            ).set(0.0)
                except DBAPIError as exc:
                    metrics.pnl_intraday_upsert_failures_total.inc()
                    log.warning(
                        "pnl_intraday_upsert_failed",
                        account_id=str(account_id),
                        err=str(exc),
                    )

        # Phase 9.7 G1: refresh broker_poller_drift_seconds gauge for every
        # account we've ever successfully polled. Even if THIS tick failed
        # for some accounts, the gauge for those accounts grows as
        # (now - last_successful_at) and triggers BrokerPollerDriftHigh.
        # Prune entries for accounts that have left the targets list
        # (soft-deleted or label retired) so the gauge doesn't keep growing
        # for non-existent accounts (code-reviewer MED-2).
        live_keys = set(targets)
        for stale_key in list(self._last_position_tick_at.keys()):
            if stale_key not in live_keys:
                del self._last_position_tick_at[stale_key]
                metrics.broker_poller_drift_seconds.remove(stale_key[0], stale_key[1])
        now = time.monotonic()
        for (label, account_number), last_at in self._last_position_tick_at.items():
            metrics.broker_poller_drift_seconds.labels(
                gateway_label=label, account_id=account_number
            ).set(now - last_at)

        metrics.broker_discover_positions_update_duration_ms.observe(
            (time.monotonic() - t_start) * 1000
        )

    async def _upsert_positions(
        self,
        session: AsyncSession,
        account_id: UUID,
        positions: list[base.Position],
        broker_id: str = "ibkr",
    ) -> None:
        """Atomic upsert + delta-delete for one account's positions.

        Uses NOT EXISTS (architect-review HIGH-4) — NULL-safe and slightly
        faster than NOT IN.

        HIGH-db-1: broker_id is now threaded from the call site (already known
        at the discoverer loop) instead of issuing a SELECT per account tick.

        Empty-broker-response handling: an empty positions list (account fully
        liquidated, instrument expired, etc.) falls through to the same
        upsert+delete CTE — `jsonb_to_recordset` over `[]` produces zero
        upsert rows, so the `NOT EXISTS (SELECT 1 FROM upserted ...)` clause
        deletes every existing row for the account. Previously this method
        returned early on `[]`, leaving stale rows orphaned.
        """
        rows: list[dict[str, str | None]] = []
        for p in positions:
            symbol = p.contract.symbol
            primary_exchange = p.contract.exchange or ""
            country = country_for_exchange(primary_exchange)
            asset_class = _proto_asset_class_to_str(p.contract.asset_class)
            canonical_id: str | None = None
            if not symbol:
                metrics.QUOTE_POSITION_CANONICAL_UNRESOLVED_TOTAL.labels(
                    broker_id=broker_id, reason="no_symbol"
                ).inc()
            elif not primary_exchange:
                metrics.QUOTE_POSITION_CANONICAL_UNRESOLVED_TOTAL.labels(
                    broker_id=broker_id, reason="no_exchange"
                ).inc()
            elif country is None:
                metrics.QUOTE_POSITION_CANONICAL_UNRESOLVED_TOTAL.labels(
                    broker_id=broker_id, reason="no_country"
                ).inc()
            else:
                canonical_id = f"{asset_class.lower()}:{symbol.upper()}:{country}"
                canonical_id_components(canonical_id)
                metrics.QUOTE_POSITION_CANONICAL_RESOLVED_TOTAL.labels(broker_id=broker_id).inc()
            rows.append(
                {
                    "conid": p.contract.conid,
                    "qty": p.quantity,
                    "avg_cost": p.avg_cost.value,
                    "currency": p.avg_cost.currency,
                    "multiplier": (getattr(p.contract, "multiplier", "") or "1"),
                    "asset_class": asset_class,
                    "symbol": symbol,
                    "primary_exchange": primary_exchange,
                    "canonical_id": canonical_id,
                }
            )
        rows_json = json.dumps(rows)
        await session.execute(
            text(
                """
                WITH upserted AS (
                  INSERT INTO positions (account_id, conid, qty, avg_cost, currency,
                                         multiplier, asset_class, symbol,
                                         primary_exchange, canonical_id, updated_at)
                  SELECT :account_id, conid, qty::numeric, avg_cost::numeric, currency,
                         multiplier::numeric, asset_class, symbol, primary_exchange,
                         canonical_id, now()
                    FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                      AS x(conid varchar, qty varchar, avg_cost varchar, currency varchar,
                           multiplier varchar, asset_class varchar, symbol varchar,
                           primary_exchange varchar, canonical_id varchar)
                  ON CONFLICT (account_id, conid) DO UPDATE
                    SET qty = EXCLUDED.qty,
                        avg_cost = EXCLUDED.avg_cost,
                        currency = EXCLUDED.currency,
                        multiplier = EXCLUDED.multiplier,
                        asset_class = EXCLUDED.asset_class,
                        symbol = EXCLUDED.symbol,
                        primary_exchange = EXCLUDED.primary_exchange,
                        canonical_id = EXCLUDED.canonical_id,
                        updated_at = now()
                  RETURNING conid
                )
                DELETE FROM positions p
                 WHERE p.account_id = :account_id
                   AND NOT EXISTS (SELECT 1 FROM upserted u WHERE u.conid = p.conid);
                """
            ),
            {"account_id": account_id, "rows": rows_json},
        )

    async def stop(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _mode_value(mode: base.TradingMode) -> str:
        if isinstance(mode, str):
            return mode.lower()
        return mode.value.lower()


_POSITIONS_ASSET_CLASSES = frozenset(
    {
        "STOCK",
        "ETF",
        "OPTION",
        "FUTURE",
        "FOREX",
        "CRYPTO",
        "BOND",
        "MUTUAL_FUND",
        "WARRANT",
    }
)


def _proto_asset_class_to_str(ac: object) -> str:
    """Normalize asset_class to positions.asset_class VARCHAR.

    Pydantic decoders surface the proto AssetClass enum as a string Literal
    via broker_pb2.AssetClass.Name() at line ~385. Default "STOCK" for
    unknown — never raise: positions upsert must not 500 on an exotic
    asset class.
    """
    if isinstance(ac, str) and ac.upper() in _POSITIONS_ASSET_CLASSES:
        return ac.upper()
    return "STOCK"


def _account_row_from_mapping(row: RowMapping) -> _AccountRow:
    return _AccountRow(
        id=cast(UUID, row["id"]),
        broker_id=cast("BrokerId", row["broker_id"]),
        account_number=cast(str, row["account_number"]),
        alias=cast(str | None, row["alias"]),
        mode=cast("TradingMode", row["mode"]),
        gateway_label=cast(str, row["gateway_label"]),
        currency_base=cast(str, row["currency_base"]),
        display_order=cast(int, row["display_order"]),
        last_nlv=cast(Decimal | None, row.get("last_nlv")),
        last_nlv_currency=cast(str | None, row.get("last_nlv_currency")),
        last_nlv_at=cast(datetime | None, row.get("last_nlv_at")),
        position_count=int(row.get("position_count") or 0),
    )


def _format_nlv(d: Decimal | None) -> str | None:
    if d is None:
        return None
    if not d.is_finite():
        return None
    try:
        quantized = d.quantize(Decimal("1e-8"))
    except InvalidOperation:
        return None
    return format(quantized, "f")


_ACCOUNT_BOUNDARY_STRIP_FIELDS: frozenset[str] = frozenset(
    {
        "gateway_label",
        "account_number",
        "account_hash",
    }
)


def _account_response_from_row(row: _AccountRow) -> base.AccountResponse:
    return base.AccountResponse(
        id=row.id,
        broker_id=row.broker_id,
        alias=row.alias,
        mode=row.mode,
        currency_base=row.currency_base,
        display_order=row.display_order,
        nlv=_format_nlv(row.last_nlv),
        nlv_currency=row.last_nlv_currency,
        nlv_at=row.last_nlv_at,
        position_count=row.position_count,
    )


def _check_avg_cost_unit_invariant(
    account_id: UUID,
    positions: list[base.Position],
    summary: base.Summary,
) -> None:
    # Phase 4 retro M1: blank/empty avg_cost and non-positive NLV (unfunded
    # paper accounts, brand-new sub-accounts) used to crash this guard with
    # InvalidOperation before reaching the comparison. Skip rather than raise.
    try:
        total_cost = sum(
            (Decimal(p.quantity) * Decimal(p.avg_cost.value) for p in positions),
            start=Decimal("0"),
        )
        nlv = Decimal(summary.net_liquidation.value)
    except InvalidOperation, ValueError:
        return
    if nlv <= Decimal("0"):
        return
    if total_cost > Decimal("1.5") * nlv:
        ratio = Decimal("Infinity") if nlv == Decimal("0") else total_cost / nlv
        log.warning(
            "avg_cost_unit_suspected_wrong",
            account_id=str(account_id),
            total_cost=str(total_cost),
            nlv=str(nlv),
            ratio=str(ratio),
        )
        metrics.avg_cost_unit_suspected_wrong_total.labels(account_id=str(account_id)).inc()
