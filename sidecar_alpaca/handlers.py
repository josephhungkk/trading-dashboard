"""gRPC Broker servicer skeleton for Alpaca."""
# ruff: noqa: E402,I001

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from sidecar_alpaca import config, normalize
from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.client import (
    AlpacaClient,
    AlpacaClientError,
    configure_trading_client,
    get_trading_client,
    load_api_error_class,
    load_order_request_classes,
    load_trading_stream_class,
)
from sidecar_alpaca.streamer import AlpacaStreamer

log = structlog.get_logger(module="sidecar_alpaca.handlers")

_ORDER_EVENT_QUEUES: dict[str, asyncio.Queue[broker_pb2.OrderEventMessage]] = {}
_ORDER_EVENT_SUBSCRIPTIONS: dict[str, Any] = {}
_TRADING_STREAM_COUNTS: dict[str, int] = {}
_DEDUPE: dict[tuple[str, str, str, int], float] = {}
_ORDER_EVENT_QUEUE_MAXSIZE = 1000
_TRADING_STREAM_CAP = 5


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
            account_id = self._account_id_from_metadata(metadata)
            configure_trading_client(
                account_id=account_id,
                mode=payload_mode,
                api_key=metadata.get("api_key", ""),
                api_secret=metadata.get("api_secret", ""),
                paper=payload_mode == "paper",
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
        try:
            client = await self._new_client()
            rows = await client.list_managed_accounts()
            accounts = [
                normalize.to_proto_account(
                    row,
                    gateway_label=f"alpaca-{config.MODE}",
                    mode=config.MODE,
                )
                for row in rows
            ]
            return broker_pb2.AccountsResponse(accounts=accounts)
        except (AlpacaClientError, RuntimeError) as exc:
            self._set_unavailable(context, exc)
            return broker_pb2.AccountsResponse()

    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SummaryResponse:
        try:
            client = await self._new_client()
            row = await client.get_account_summary()
            summary = normalize.to_proto_account_summary(row)
            return broker_pb2.SummaryResponse(summary=summary)
        except (AlpacaClientError, RuntimeError) as exc:
            self._set_unavailable(context, exc)
            return broker_pb2.SummaryResponse()

    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PositionsResponse:
        try:
            client = await self._new_client()
            rows = await client.get_positions()
            positions = [normalize.to_proto_position(row) for row in rows]
            return broker_pb2.PositionsResponse(positions=positions)
        except (AlpacaClientError, RuntimeError) as exc:
            self._set_unavailable(context, exc)
            return broker_pb2.PositionsResponse()

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.OrdersResponse:
        try:
            client = await self._new_client()
            rows = await client.get_orders()
            orders = [normalize.to_proto_order(row) for row in rows]
            return broker_pb2.OrdersResponse(orders=orders)
        except (AlpacaClientError, RuntimeError) as exc:
            self._set_unavailable(context, exc)
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
        cash_amount = getattr(request, "cash_amount", "")
        log.debug("place_order_cash_amount_received", cash_amount=cash_amount)

        account_id = self._account_id(request.account_number)
        if config.USE_IN_MEMORY_DEDUPE:
            if self._place_order_is_duplicate(request):
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    "client_order_id_duplicate",
                )
        client = await self._configured_trading_client(account_id, context)
        order_request = self._build_order_request(request)
        try:
            order = client.submit_order(order_request)
        except (Exception,) as exc:
            await self._abort_internal(context, exc)
        return broker_pb2.PlaceOrderResponse(
            broker_order_id=str(getattr(order, "id", "")),
            status=_enum_value(getattr(order, "status", "")),
        )

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.CancelOrderResponse:
        account_id = self._account_id(request.account_number)
        client = await self._configured_trading_client(account_id, context)
        api_error = load_api_error_class()
        try:
            client.cancel_order_by_id(request.broker_order_id)
        except (api_error,) as exc:
            if _api_error_status(exc) in {404, 422}:
                return broker_pb2.CancelOrderResponse(accepted=False)
            await self._abort_internal(context, exc)
        except (Exception,) as exc:
            await self._abort_internal(context, exc)
        return broker_pb2.CancelOrderResponse(accepted=True)

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ModifyOrderResponse:
        account_id = self._account_id(request.account_number)
        client = await self._configured_trading_client(account_id, context)
        replace_request = self._build_replace_order_request(request)
        api_error = load_api_error_class()
        try:
            order = client.replace_order_by_id(
                request.broker_order_id,
                replace_request,
            )
        except (api_error,) as exc:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details(str(exc))
            return broker_pb2.ModifyOrderResponse()
        except (Exception,) as exc:
            await self._abort_internal(context, exc)
        return broker_pb2.ModifyOrderResponse(
            broker_order_id=str(getattr(order, "id", "")),
            status=_enum_value(getattr(order, "status", "")),
        )

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
        account_id = self._account_id(request.account_number)
        await self._acquire_trading_stream(account_id, context)
        queue = self._order_event_queue(account_id)
        try:
            await self._ensure_order_event_subscription(account_id)
            while True:
                yield await queue.get()
        finally:
            self._release_trading_stream(account_id)

    async def StreamQuotes(  # noqa: N802
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.QuoteMessage]:
        streamer = AlpacaStreamer(self._auth)

        async def tick_callback(quote_message: broker_pb2.QuoteMessage) -> None:
            await context.write(quote_message)

        streamer.tick_callback = tick_callback
        await streamer.start()
        try:
            async for request in request_iterator:
                op = request.WhichOneof("op")
                if op == "subscribe":
                    iex_symbols, crypto_ids = _partition_crypto_symbols(
                        list(request.subscribe.symbols)
                    )
                    await streamer.on_subscribe(iex_symbols)
                    await streamer.on_subscribe_crypto(crypto_ids)
                    continue
                if op == "unsubscribe":
                    iex_symbols, crypto_ids = _partition_crypto_symbols(
                        list(request.unsubscribe.symbols)
                    )
                    await streamer.on_unsubscribe(iex_symbols)
                    await streamer.on_unsubscribe_crypto(crypto_ids)
                    continue
                if op == "resync":
                    iex_symbols, crypto_ids = _partition_crypto_symbols(
                        list(request.resync.expected)
                    )
                    await streamer.on_resync(iex_symbols)
                    await streamer.on_resync_crypto(crypto_ids)
        finally:
            await streamer.stop()
        if False:
            yield broker_pb2.QuoteMessage()

    @staticmethod
    def _payload_mode(request: broker_pb2.ConfigureRequest) -> str:
        payload_mode = getattr(request, "mode", "")
        if payload_mode:
            return str(payload_mode).lower()
        return request.metadata.get("mode", "")

    @staticmethod
    def _account_id_from_metadata(metadata: dict[str, str]) -> str:
        return (
            metadata.get("account_id")
            or metadata.get("account_number")
            or config.ALPACA_ACCOUNT_LABEL
        )

    @staticmethod
    def _account_id(account_number: str) -> str:
        return account_number or config.ALPACA_ACCOUNT_LABEL

    async def _configured_trading_client(
        self,
        account_id: str,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        client = get_trading_client(account_id=account_id, mode=config.MODE)
        if client is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "trading_client_not_configured",
            )
        return client

    def _build_order_request(self, request: broker_pb2.PlaceOrderRequest) -> Any:
        request_classes = load_order_request_classes()
        order_type = _normalize_order_type(request.order_type)
        request_class = request_classes[order_type]
        values: dict[str, Any] = {
            "symbol": _alpaca_symbol(request.conid),
            "side": request.side.lower(),
            "time_in_force": request.tif.lower(),
        }
        if request.client_order_id:
            values["client_order_id"] = request.client_order_id
        if request.qty:
            values["qty"] = Decimal(request.qty)
        if order_type in {"LIMIT", "STOP_LIMIT", "LOC", "LOO"}:
            values["limit_price"] = Decimal(request.limit_price)
        if order_type in {"STOP", "STOP_LIMIT"}:
            values["stop_price"] = Decimal(request.stop_price)
        if order_type in {"TRAIL", "TRAIL_LIMIT"}:
            values[_trail_field_name(request)] = Decimal(request.trail_offset)
        if order_type == "TRAIL_LIMIT":
            values["limit_price"] = Decimal(request.trail_limit_offset)
        return request_class(**values)

    def _build_replace_order_request(
        self,
        request: broker_pb2.ModifyOrderRequest,
    ) -> Any:
        request_classes = load_order_request_classes()
        values: dict[str, Any] = {}
        if request.qty:
            values["qty"] = Decimal(request.qty)
        if request.HasField("limit_price") and request.limit_price.value:
            values["limit_price"] = Decimal(request.limit_price.value)
        if request.HasField("stop_price") and request.stop_price.value:
            values["stop_price"] = Decimal(request.stop_price.value)
        if request.trail_offset:
            values["trail"] = Decimal(request.trail_offset)
        return request_classes["REPLACE"](**values)

    def _place_order_is_duplicate(
        self,
        request: broker_pb2.PlaceOrderRequest,
    ) -> bool:
        now = time.time()
        expired = [key for key, seen_at in _DEDUPE.items() if now - seen_at > 60]
        for key in expired:
            _DEDUPE.pop(key, None)
        key = (
            self._account_id(request.account_number),
            _alpaca_symbol(request.conid),
            request.qty,
            int(now // 10),
        )
        if key in _DEDUPE:
            return True
        _DEDUPE[key] = now
        return False

    async def _acquire_trading_stream(
        self,
        account_id: str,
        context: grpc.aio.ServicerContext,
    ) -> None:
        count = _TRADING_STREAM_COUNTS.get(account_id, 0)
        if count >= _TRADING_STREAM_CAP:
            await context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                "trading_stream_cap_5",
            )
        _TRADING_STREAM_COUNTS[account_id] = count + 1

    @staticmethod
    def _release_trading_stream(account_id: str) -> None:
        count = _TRADING_STREAM_COUNTS.get(account_id, 0)
        if count <= 1:
            _TRADING_STREAM_COUNTS.pop(account_id, None)
            return
        _TRADING_STREAM_COUNTS[account_id] = count - 1

    @staticmethod
    def _order_event_queue(
        account_id: str,
    ) -> asyncio.Queue[broker_pb2.OrderEventMessage]:
        queue = _ORDER_EVENT_QUEUES.get(account_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=_ORDER_EVENT_QUEUE_MAXSIZE)
            _ORDER_EVENT_QUEUES[account_id] = queue
        return queue

    async def _ensure_order_event_subscription(self, account_id: str) -> None:
        if account_id in _ORDER_EVENT_SUBSCRIPTIONS:
            return
        api_key, api_secret = await self._auth.get_credentials()
        trading_stream = load_trading_stream_class()(
            api_key,
            api_secret,
            paper=config.MODE == "paper",
        )

        async def handler(update: Any) -> None:
            try:
                self._enqueue_order_event(account_id, update)
            except (RuntimeError, ValueError, TypeError) as exc:
                log.warning("orderevent_callback_failed", exc_info=exc)

        trading_stream.subscribe_trade_updates(handler)
        # Alpaca crypto order updates are not exposed by a separate alpaca-py
        # stream; route all trade updates through TradingStream until a
        # crypto-specific API lands.
        run = getattr(trading_stream, "run", None)
        if run is not None:
            asyncio.create_task(asyncio.to_thread(run), name="alpaca-trading-stream")
        _ORDER_EVENT_SUBSCRIPTIONS[account_id] = trading_stream

    def _enqueue_order_event(self, account_id: str, update: Any) -> None:
        queue = self._order_event_queue(account_id)
        message = _trade_update_to_message(update)
        if queue.full():
            try:
                queue.get_nowait()
            except (asyncio.QueueEmpty,) as exc:
                log.warning("orderevent_queue_empty_on_overflow", exc_info=exc)
            log.warning("orderevent_queue_full", key="orderevent_queue_full")
        queue.put_nowait(message)

    async def _new_client(self) -> AlpacaClient:
        api_key, api_secret = await self._auth.get_credentials()
        return AlpacaClient(
            api_key,
            api_secret,
            paper=config.MODE == "paper",
        )

    @staticmethod
    def _set_unavailable(
        context: grpc.aio.ServicerContext,
        exc: AlpacaClientError | RuntimeError,
    ) -> None:
        context.set_code(grpc.StatusCode.UNAVAILABLE)
        detail = exc.message if isinstance(exc, AlpacaClientError) else str(exc)
        context.set_details(detail)

    @staticmethod
    async def _abort_internal(
        context: grpc.aio.ServicerContext,
        exc: Exception,
    ) -> None:
        log.warning("alpaca_trade_rpc_failed", exc_info=exc)
        await context.abort(grpc.StatusCode.INTERNAL, str(exc))

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


def _partition_crypto_symbols(
    symbols: list[broker_pb2.SymbolRef],
) -> tuple[list[broker_pb2.SymbolRef], list[str]]:
    iex_symbols: list[broker_pb2.SymbolRef] = []
    crypto_ids: list[str] = []
    for symbol in symbols:
        canonical_id = symbol.canonical_id or symbol.raw_symbol
        if canonical_id.startswith("crypto:"):
            crypto_ids.append(canonical_id)
            continue
        iex_symbols.append(symbol)
    return iex_symbols, crypto_ids


def _normalize_order_type(order_type: str) -> str:
    normalized = order_type.upper()
    if normalized.startswith("ORDER_TYPE_"):
        normalized = normalized.removeprefix("ORDER_TYPE_")
    if normalized == "TRAILING_STOP":
        return "TRAIL"
    if normalized in {
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    }:
        return normalized
    raise ValueError(f"unsupported alpaca order type: {order_type}")


def _alpaca_symbol(conid: str) -> str:
    if conid.startswith("crypto:"):
        return normalize.canonical_to_alpaca_crypto(conid)
    if ":" in conid:
        parts = conid.split(":")
        if len(parts) >= 2:
            return parts[1]
    return conid


def _trail_field_name(request: broker_pb2.PlaceOrderRequest) -> str:
    offset_type = request.trail_offset_type.lower()
    if offset_type in {"percent", "pct", "%"}:
        return "trail_percent"
    return "trail_price"


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _api_error_status(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError) as parse_exc:
            log.warning("alpaca_api_error_status_parse_failed", exc_info=parse_exc)
    text = str(exc)
    for status in (404, 422):
        if str(status) in text:
            return status
    return None


def _trade_update_to_message(update: Any) -> broker_pb2.OrderEventMessage:
    order = getattr(update, "order", update)
    event_at = Timestamp()
    timestamp = (
        getattr(update, "timestamp", None)
        or getattr(update, "event_at", None)
        or getattr(update, "updated_at", None)
    )
    if hasattr(timestamp, "timestamp"):
        event_at.FromSeconds(int(timestamp.timestamp()))
    else:
        event_at.GetCurrentTime()
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(getattr(order, "id", "")),
        client_order_id=str(getattr(order, "client_order_id", "")),
        status=_enum_value(getattr(order, "status", getattr(update, "event", ""))),
        filled_qty=str(getattr(order, "filled_qty", "")),
        avg_fill_price=str(getattr(order, "filled_avg_price", "")),
        event_at=event_at,
        raw_payload=_json_default(update),
        exec_id=str(getattr(update, "execution_id", "")),
        kind=str(getattr(update, "event", "")),
    )


def _json_default(value: Any) -> str:
    try:
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump(mode="json"))
        if hasattr(value, "dict"):
            return json.dumps(value.dict())
        return json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        log.warning("alpaca_order_event_json_failed", exc_info=exc)
        return str(value)
