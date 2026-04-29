"""gRPC Broker service handlers for the Futu sidecar."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import grpc  # type: ignore[import-untyped]
import structlog
from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu import metrics, sim
from sidecar_futu._generated.broker.v1 import broker_pb2, broker_pb2_grpc
from sidecar_futu.futu_client import FutuClient
from sidecar_futu.normalize import (
    AccountMapped,
    AccountSkipped,
    account_from_futu_row,
    contract_from_futu_row,
    order_from_futu_row,
    position_from_futu_row,
    summary_from_futu_row,
)

log = structlog.get_logger(__name__)


class BrokerHandlers(broker_pb2_grpc.BrokerServicer):  # type: ignore[misc]
    # Generated BrokerServicer is typed Any; the ignore documents the
    # intentional subclass-of-Any rather than letting it leak.
    """Implements the proto Broker service for Futu."""

    def __init__(self, *, started_at: datetime, simulator: bool = True) -> None:
        self._started_at = started_at
        self._sim_mode = simulator
        self._client = FutuClient()
        self._sim_orders: dict[str, dict[str, str]] = {}

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: Any,
    ) -> broker_pb2.HealthResponse:
        ts = Timestamp()
        ts.FromDatetime(self._started_at)
        return broker_pb2.HealthResponse(
            label="futu",
            gateway_connected=self._client.gateway_connected,
            gateway_version="",
            sidecar_version="0.6.0",
            started_at=ts,
            broker_id="futu",
        )

    async def Configure(  # noqa: N802
        self,
        request: broker_pb2.ConfigureRequest,
        context: Any,
    ) -> broker_pb2.ConfigureResponse:
        detail = self._client.validate(request)
        if detail is not None:
            log.warning("configure_rejected", detail=detail)
            return broker_pb2.ConfigureResponse(ok=False, detail=detail)
        await self._client.configure(request)
        log.info("configure_accepted")
        return broker_pb2.ConfigureResponse(ok=True, detail="")

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: Any,
    ) -> broker_pb2.AccountsResponse:
        accounts: list[broker_pb2.Account] = []
        for row in await self._client.list_accounts():
            result = account_from_futu_row(row)
            if isinstance(result, AccountSkipped):
                metrics.broker_normalize_unknown_total.labels(
                    label="futu", field="trd_env"
                ).inc()
                log.warning("futu_normalize_unknown_trd_env", row=row)
                continue
            assert isinstance(result, AccountMapped)
            accounts.append(result.account)
        return broker_pb2.AccountsResponse(accounts=accounts)

    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: Any,
    ) -> broker_pb2.SummaryResponse:
        row = await self._client.get_account_summary(request.account_number)
        summary = summary_from_futu_row(row, account_number=request.account_number)
        return broker_pb2.SummaryResponse(summary=summary)

    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: Any,
    ) -> broker_pb2.PositionsResponse:
        rows = await self._client.get_positions(request.account_number)
        positions = [position_from_futu_row(row) for row in rows]
        return broker_pb2.PositionsResponse(positions=positions)

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: Any,
    ) -> broker_pb2.OrdersResponse:
        rows = await self._client.get_orders(request.account_number)
        orders = [order_from_futu_row(row) for row in rows]
        return broker_pb2.OrdersResponse(orders=orders)

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: Any,
    ) -> broker_pb2.SearchContractsResponse:
        rows = await self._client.search_contracts(request.query)
        contracts = [contract_from_futu_row(row) for row in rows]
        for contract in contracts:
            if contract.symbol.startswith("HK."):
                contract.exchange = "SEHK"
        return broker_pb2.SearchContractsResponse(contracts=contracts)

    async def GetContract(  # noqa: N802
        self,
        request: broker_pb2.ContractRef,
        context: Any,
    ) -> broker_pb2.ContractResponse:
        rows = await self._client.search_contracts(request.conid)
        if not rows:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"contract not found: {request.conid}",
            )
        contract = contract_from_futu_row(rows[0])
        if contract.symbol.startswith("HK."):
            contract.exchange = "SEHK"
        return broker_pb2.ContractResponse(contract=contract)

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        context: Any,
    ) -> broker_pb2.PlaceOrderResponse:
        if self._sim_mode:
            return await self._sim_place(request)
        if not self._client.gateway_connected:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "gateway not connected")

        try:
            broker_order_id, status = await self._client.place_order(request)
        except Exception as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        return broker_pb2.PlaceOrderResponse(broker_order_id=broker_order_id, status=status)

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        context: Any,
    ) -> broker_pb2.CancelOrderResponse:
        if self._sim_mode:
            return await self._sim_cancel(request)

        accepted = await self._client.cancel_order(
            request.account_number,
            request.broker_order_id,
        )
        return broker_pb2.CancelOrderResponse(accepted=accepted)

    async def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: Any,
    ) -> AsyncIterator[broker_pb2.OrderEventMessage]:
        queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue(
            maxsize=1000
        )
        self._client._order_event_queues.setdefault(
            request.account_number, []
        ).append(queue)
        log.info("orderevent_subscribed", account=request.account_number)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            try:
                self._client._order_event_queues[request.account_number].remove(
                    queue
                )
            except (KeyError, ValueError):
                pass
            log.info("orderevent_unsubscribed", account=request.account_number)

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: Any,
    ) -> broker_pb2.ModifyOrderResponse:
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED, "Modify deferred to Phase 7"
        )
        raise AssertionError("unreachable: abort raises")  # mypy guard

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: Any,
    ) -> broker_pb2.PlaceBracketResponse:
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED, "Bracket deferred to Phase 7"
        )
        raise AssertionError("unreachable: abort raises")

    async def _sim_place(
        self,
        request: broker_pb2.PlaceOrderRequest,
    ) -> broker_pb2.PlaceOrderResponse:
        sim_id = sim.make_sim_id()
        self._sim_orders[sim_id] = {
            "client_order_id": request.client_order_id,
            "account_number": request.account_number,
        }
        queues = self._client._order_event_queues.get(request.account_number, [])
        sim.dispatch(
            queues,
            sim.synthetic_place_event(
                broker_order_id=sim_id,
                client_order_id=request.client_order_id,
            ),
        )
        return broker_pb2.PlaceOrderResponse(broker_order_id=sim_id, status="submitted")

    async def _sim_cancel(
        self,
        request: broker_pb2.CancelOrderRequest,
    ) -> broker_pb2.CancelOrderResponse:
        if not request.broker_order_id.startswith("SIM-"):
            return broker_pb2.CancelOrderResponse(accepted=False)
        entry = self._sim_orders.pop(request.broker_order_id, None)
        if entry is None:
            return broker_pb2.CancelOrderResponse(accepted=False)
        queues = self._client._order_event_queues.get(entry["account_number"], [])
        sim.dispatch(
            queues,
            sim.synthetic_cancel_event(
                broker_order_id=request.broker_order_id,
                client_order_id=entry["client_order_id"],
            ),
        )
        return broker_pb2.CancelOrderResponse(accepted=True)
