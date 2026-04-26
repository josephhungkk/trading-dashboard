"""Broker sidecar gRPC client."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, TypeVar, cast
from uuid import UUID

import grpc  # type: ignore[import-untyped]
import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from app.brokers import base
from app.core import metrics
from app.services.ibkr_maintenance import compute_broker_maintenance

log = structlog.get_logger(__name__)

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)
BrokerId = Literal["ibkr", "futu", "schwab"]
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


class BrokerSidecarUnavailable(Exception):  # noqa: N818
    def __init__(self, message: str, *, label: str = "") -> None:
        super().__init__(message)
        self.label = label


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
                f"broker sidecar {self.label} {method} unavailable: {exc.code().name}",
                label=self.label,
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
            SELECT id, broker_id, account_number, alias, mode, gateway_label,
                   currency_base, display_order, last_nlv, last_nlv_currency,
                   last_nlv_at
              FROM broker_accounts
             WHERE deleted_at IS NULL
             ORDER BY display_order, account_number;
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
                      currency_base, display_order;
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
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._stop_event = asyncio.Event()
        self._tick_lock = asyncio.Lock()

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

    async def _discover_once(self) -> None:
        healthy_clients = await self._registry.healthy_clients()
        healthy = [(client, client.label) for client in healthy_clients]
        healthy_labels = [label for _client, label in healthy]

        log.info("broker_discover_iteration_start", healthy_labels=healthy_labels)

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
                :last_seen_via,
                now(),
                NULL,
                now()
            )
            ON CONFLICT (broker_id, account_number) DO UPDATE
               SET mode = EXCLUDED.mode,
                   gateway_label = EXCLUDED.gateway_label,
                   currency_base = EXCLUDED.currency_base,
                   last_seen_via = EXCLUDED.last_seen_via,
                   last_seen_at = EXCLUDED.last_seen_at,
                   deleted_at = NULL,
                   updated_at = now()
            """
        )

        rows_seen_keys = [("ibkr", account.account_number) for _label, account in rows_seen]
        soft_delete_count = 0

        async with self._session_factory() as session, session.begin():
            for label, account in rows_seen:
                await session.execute(
                    upsert_stmt,
                    {
                        "broker_id": "ibkr",
                        "account_number": account.account_number,
                        "mode": self._mode_value(account.mode),
                        "gateway_label": label,
                        "currency_base": account.currency_base,
                        "last_seen_via": label,
                    },
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
                           AND last_seen_at < now() - INTERVAL '30 minutes';
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
                           AND last_seen_at < now() - INTERVAL '30 minutes';
                        """
                    )
                    soft_delete_params = {"healthy_labels": healthy_labels}

                soft_delete_result = await session.execute(soft_delete_stmt, soft_delete_params)
                # SQLAlchemy 2.0's typed Result protocol omits `rowcount`,
                # but the asyncpg-backed CursorResult exposes it at runtime.
                soft_delete_count = getattr(soft_delete_result, "rowcount", 0) or 0

        log.info(
            "broker_discover_iteration_ok",
            upsert_count=len(rows_seen),
            soft_delete_count=soft_delete_count,
        )

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
        ) -> tuple[str, str, object] | None:
            client = await self._registry.get_client(label)
            try:
                summary = await asyncio.wait_for(
                    client.get_account_summary(account_number),
                    timeout=10.0,
                )
                return (label, account_number, summary)
            except TimeoutError, BrokerSidecarUnavailable, BrokerSidecarTimeout:
                return None

        results: list[tuple[str, str, object] | None | BaseException] = list(
            await asyncio.gather(
                *(
                    _fetch_summary(label, account_number)
                    for (label, account_number) in summary_targets
                ),
                return_exceptions=True,
            )
        )
        log.debug("broker_discover_summary_fanout_done", result_count=len(results))

    async def stop(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _mode_value(mode: base.TradingMode) -> str:
        if isinstance(mode, str):
            return mode.lower()
        return mode.value.lower()


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
    )


def _check_avg_cost_unit_invariant(
    account_id: UUID,
    positions: list[base.Position],
    summary: base.Summary,
) -> None:
    total_cost = sum(
        Decimal(position.quantity) * Decimal(position.avg_cost.value) for position in positions
    )
    nlv = Decimal(summary.net_liquidation.value)
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
