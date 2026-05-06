"""gRPC Broker service handlers for the Futu sidecar."""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
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

type _TickCallback = Callable[[broker_pb2.QuoteMessage], None]

_STREAM_QUEUE_MAX = 2048
_CALL_SUBS_MAX = 500


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
        try:
            row = await self._client.get_account_summary(request.account_number)
            summary = summary_from_futu_row(
                row, account_number=request.account_number
            )
            return broker_pb2.SummaryResponse(summary=summary)
        except Exception as exc:
            log.error(
                "getaccountsummary_failed",
                account=request.account_number,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise

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
        if self._sim_mode:
            # Sim: echo back immediately; no real state change needed for tests.
            return broker_pb2.ModifyOrderResponse(
                broker_order_id=request.broker_order_id, status="SUBMITTED"
            )
        if not self._client.gateway_connected:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "gateway not connected")
            return broker_pb2.ModifyOrderResponse()  # defensive: abort raises in real gRPC

        ok, msg = await self._client.modify_order_live(
            account_number=request.account_number,
            broker_order_id=request.broker_order_id,
            qty=float(request.qty),
            price=float(request.limit_price.value) if request.limit_price.value else 0.0,
        )
        if not ok:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, msg)
            return broker_pb2.ModifyOrderResponse()  # defensive: abort raises in real gRPC
        return broker_pb2.ModifyOrderResponse(
            broker_order_id=request.broker_order_id, status="SUBMITTED"
        )

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: Any,
    ) -> broker_pb2.PlaceBracketResponse:
        if self._sim_mode:
            parent_id = sim.make_sim_id()
            sl_id = sim.make_sim_id()
            tp_id = sim.make_sim_id()
            return broker_pb2.PlaceBracketResponse(
                parent_broker_order_id=parent_id,
                stop_loss_broker_order_id=sl_id,
                take_profit_broker_order_id=tp_id,
                status="SUBMITTED",
            )
        if not self._client.gateway_connected:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "gateway not connected")
            return broker_pb2.PlaceBracketResponse()  # defensive: abort raises in real gRPC

        try:
            parent_id, sl_id, tp_id = await self._client.place_bracket(request)
        except Exception as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return broker_pb2.PlaceBracketResponse()  # defensive: abort raises in real gRPC
        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=parent_id,
            stop_loss_broker_order_id=sl_id,
            take_profit_broker_order_id=tp_id,
            status="SUBMITTED",
        )

    async def _get_or_init_futu_streamer(self) -> Any:
        lock = self.__dict__.setdefault("_streamer_lock", asyncio.Lock())
        async with lock:
            streamer = getattr(self, "_streamer", None)
            if streamer is not None:
                return streamer
            quote_ctx = await self._resolve_quote_context()
            if quote_ctx is None:
                raise RuntimeError("futu quote context not configured")

            from sidecar_futu.streamer import FutuStreamer

            streamer = FutuStreamer(quote_ctx)
            try:
                await streamer.start()
            except Exception:
                with contextlib.suppress(Exception):
                    await streamer.stop()
                raise
            self._streamer = streamer
            return streamer

    async def StreamQuotes(  # noqa: N802
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        context: Any,
    ) -> AsyncIterator[broker_pb2.QuoteMessage]:
        try:
            streamer = await self._get_or_init_futu_streamer()
        except RuntimeError as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return

        queue: asyncio.Queue[broker_pb2.QuoteMessage] = asyncio.Queue(
            maxsize=_STREAM_QUEUE_MAX
        )
        call_subs: set[str] = set()

        def tick_callback(message: broker_pb2.QuoteMessage) -> None:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest, then enqueue. If still full (race with
                # competing producer), record the drop + log so the
                # silent-failure path is visible in dashboards.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    metrics.futu_stream_quote_drops_total.inc()
                    log.warning("futu.stream_quotes.dropped")

        self._add_streamer_tick_callback(streamer, tick_callback)
        consumer_task = asyncio.create_task(
            self._consume_stream_quote_requests(
                request_iterator,
                streamer,
                call_subs,
            ),
            name="futu-stream-quotes-consumer",
        )
        try:
            while True:
                yield await queue.get()
        finally:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(consumer_task)
            if call_subs:
                await streamer.on_unsubscribe(_symbol_refs(call_subs))
            self._remove_streamer_tick_callback(streamer, tick_callback)

    async def _consume_stream_quote_requests(
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        streamer: Any,
        call_subs: set[str],
    ) -> None:
        async for request in request_iterator:
            try:
                op = request.WhichOneof("op")
                if op == "subscribe":
                    symbols = list(request.subscribe.symbols)
                    if len(call_subs) + len(symbols) > _CALL_SUBS_MAX:
                        log.warning(
                            "futu.stream_quotes.call_subs_cap_hit",
                            current=len(call_subs),
                            requested=len(symbols),
                            cap=_CALL_SUBS_MAX,
                        )
                        continue
                    await streamer.on_subscribe(symbols)
                    call_subs.update(_canonical_id(s) for s in symbols)
                elif op == "unsubscribe":
                    symbols = list(request.unsubscribe.symbols)
                    await streamer.on_unsubscribe(symbols)
                    call_subs.difference_update(_canonical_id(s) for s in symbols)
                elif op == "resync":
                    symbols = list(request.resync.expected)
                    if len(symbols) > _CALL_SUBS_MAX:
                        log.warning(
                            "futu.stream_quotes.resync_cap_hit",
                            requested=len(symbols),
                            cap=_CALL_SUBS_MAX,
                        )
                        continue
                    await streamer.on_resync(symbols)
                    call_subs.clear()
                    call_subs.update(_canonical_id(s) for s in symbols)
            except Exception as exc:
                log.warning(
                    "futu.stream_quotes.request_dispatch_error",
                    error=str(exc),
                )

    def _add_streamer_tick_callback(
        self,
        streamer: Any,
        callback: _TickCallback,
    ) -> None:
        callbacks = getattr(streamer, "_broker_servicer_tick_callbacks", None)
        if callbacks is None:
            callbacks = set()
            streamer._broker_servicer_tick_callbacks = callbacks
            previous = getattr(streamer, "tick_callback", None)
            streamer._broker_servicer_previous_tick_callback = previous

            def dispatch(message: broker_pb2.QuoteMessage) -> None:
                if previous is not None:
                    try:
                        previous(message)
                    except Exception as exc:
                        log.warning(
                            "futu.stream_quotes.previous_callback_error",
                            error=str(exc),
                        )
                for registered in tuple(callbacks):
                    try:
                        registered(message)
                    except Exception as exc:
                        log.warning(
                            "futu.stream_quotes.tick_callback_error",
                            error=str(exc),
                        )

            streamer.tick_callback = dispatch
        callbacks.add(callback)

    def _remove_streamer_tick_callback(
        self,
        streamer: Any,
        callback: _TickCallback,
    ) -> None:
        callbacks = getattr(streamer, "_broker_servicer_tick_callbacks", None)
        if callbacks is None:
            return
        callbacks.discard(callback)
        if callbacks:
            return
        previous = getattr(streamer, "_broker_servicer_previous_tick_callback", None)
        streamer.tick_callback = previous
        del streamer._broker_servicer_tick_callbacks
        del streamer._broker_servicer_previous_tick_callback

    async def _resolve_quote_context(self) -> Any | None:
        getter = getattr(self._client, "get_quote_context", None)
        if getter is not None:
            result = getter()
            if hasattr(result, "__await__"):
                return await result
            return result
        return getattr(self._client, "quote_ctx", None) or getattr(
            self._client, "_quote_ctx", None
        )

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


def _canonical_id(symbol: broker_pb2.SymbolRef) -> str:
    return symbol.canonical_id or symbol.raw_symbol


def _symbol_refs(canonical_ids: set[str]) -> list[broker_pb2.SymbolRef]:
    return [
        broker_pb2.SymbolRef(canonical_id=canonical_id)
        for canonical_id in sorted(canonical_ids)
    ]
