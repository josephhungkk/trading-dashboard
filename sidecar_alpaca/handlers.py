"""gRPC Broker servicer skeleton for Alpaca."""
# ruff: noqa: E402,I001

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import structlog
from google.protobuf.timestamp_pb2 import Timestamp

_GENERATED_ROOT = Path(__file__).resolve().parent / "_generated"
if str(_GENERATED_ROOT) not in sys.path:
    sys.path.insert(0, str(_GENERATED_ROOT))

from sidecar_alpaca._generated.broker.v1 import (
    broker_pb2,
    broker_pb2_grpc,
)
from sidecar_alpaca import config
from sidecar_alpaca.auth import AuthCache

log = structlog.get_logger(module="sidecar_alpaca.handlers")


class AlpacaServicer(broker_pb2_grpc.BrokerServicer):
    """Alpaca Broker service implementation stub."""

    def __init__(self, auth_cache: AuthCache | None = None) -> None:
        self._auth = auth_cache or AuthCache()

    async def Configure(  # noqa: N802
        self,
        request: broker_pb2.ConfigureRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ConfigureResponse:
        try:
            payload_mode = self._payload_mode(request)
            if payload_mode != config.MODE:
                await context.abort(grpc.StatusCode.UNIMPLEMENTED, "mode mismatch")
                return broker_pb2.ConfigureResponse(ok=False, detail="mode mismatch")

            metadata = dict(request.metadata)
            await self._auth.set_credentials(
                api_key=metadata.get("api_key", ""),
                api_secret=metadata.get("api_secret", ""),
            )
            return broker_pb2.ConfigureResponse(ok=True)
        except (grpc.RpcError, ValueError, RuntimeError) as exc:
            await self._auth.clear()
            log.warning("alpaca_configure_failed", exc_info=exc)
            raise

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        started_at = Timestamp()
        started_at.GetCurrentTime()
        return broker_pb2.HealthResponse(
            label=f"alpaca-{config.MODE}",
            broker_id="alpaca",
            gateway_connected=False,
            gateway_version="alpaca-py",
            sidecar_version="0.7.3",
            started_at=started_at,
        )

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.AccountsResponse:
        await self._abort_unimplemented(
            context,
            "Alpaca ListManagedAccounts lands in C1",
        )
        return broker_pb2.AccountsResponse()

    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SummaryResponse:
        await self._abort_unimplemented(context, "Alpaca GetAccountSummary lands in C1")
        return broker_pb2.SummaryResponse()

    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PositionsResponse:
        await self._abort_unimplemented(context, "Alpaca GetPositions lands in C1")
        return broker_pb2.PositionsResponse()

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.OrdersResponse:
        await self._abort_unimplemented(context, "Alpaca GetOrders lands in C1")
        return broker_pb2.OrdersResponse()

    async def GetContract(  # noqa: N802
        self,
        request: broker_pb2.ContractRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ContractResponse:
        await self._abort_unimplemented(context, "Alpaca GetContract lands in C1")
        return broker_pb2.ContractResponse()

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceOrderResponse:
        await self._abort_unimplemented(context, "Alpaca PlaceOrder lands in Phase 8")
        return broker_pb2.PlaceOrderResponse()

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.CancelOrderResponse:
        await self._abort_unimplemented(context, "Alpaca CancelOrder lands in Phase 8")
        return broker_pb2.CancelOrderResponse()

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ModifyOrderResponse:
        await self._abort_unimplemented(context, "Alpaca ModifyOrder lands in Phase 8")
        return broker_pb2.ModifyOrderResponse()

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceBracketResponse:
        await self._abort_unimplemented(context, "Alpaca PlaceBracket lands in Phase 8")
        return broker_pb2.PlaceBracketResponse()

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SearchContractsResponse:
        await self._abort_unimplemented(context, "Alpaca SearchContracts lands in C1")
        return broker_pb2.SearchContractsResponse()

    async def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.OrderEventMessage]:
        await self._abort_unimplemented(context, "Alpaca OrderEvent lands in Phase 8")
        if False:
            yield broker_pb2.OrderEventMessage()

    async def StreamQuotes(  # noqa: N802
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.QuoteMessage]:
        await self._abort_unimplemented(context, "Alpaca StreamQuotes lands in C1")
        if False:
            yield broker_pb2.QuoteMessage()

    @staticmethod
    def _payload_mode(request: broker_pb2.ConfigureRequest) -> str:
        payload_mode = getattr(request, "mode", "")
        if payload_mode:
            return str(payload_mode).lower()
        return request.metadata.get("mode", "")

    @staticmethod
    async def _abort_unimplemented(
        context: grpc.aio.ServicerContext,
        detail: str,
    ) -> None:
        try:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, detail)
        except (grpc.RpcError, ValueError, RuntimeError) as exc:
            log.warning(
                "alpaca_unimplemented_abort_failed",
                detail=detail,
                exc_info=exc,
            )
            raise
