"""gRPC Broker servicer skeleton for Alpaca."""
# ruff: noqa: E402,I001

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
from sidecar_alpaca.symbol_util import canonical_crypto_symbol

log = structlog.get_logger(module="sidecar_alpaca.handlers")


class _ConfiguredClientUnavailableError(RuntimeError):
    """Raised by _configured_trading_client when no client is configured.

    Callers catch this BEFORE issuing any gRPC writes and return an empty
    response — context.abort() has already been called inside the helper, so
    no additional gRPC status needs to be set.
    """


_ORDER_EVENT_QUEUES: dict[str, asyncio.Queue[broker_pb2.OrderEventMessage]] = {}
_ORDER_EVENT_SUBSCRIPTIONS: dict[str, Any] = {}
_ORDER_EVENT_TASKS: dict[str, asyncio.Task[None]] = {}
_SUBSCRIPTION_LOCKS: dict[str, asyncio.Lock] = {}
_TRADING_STREAM_COUNTS: dict[str, int] = {}
_DEDUPE: dict[tuple[Any, ...], float] = {}
_ORDER_EVENT_QUEUE_MAXSIZE = 1000
_TRADING_STREAM_CAP = 5
_CRYPTO_ORDER_QUOTE_SUFFIXES = ("USDT", "USDC", "USD")
StockHistoricalDataClient: Any | None = None
CryptoHistoricalDataClient: Any | None = None
StockBarsRequest: Any | None = None
CryptoBarsRequest: Any | None = None
TimeFrame: Any | None = None


def _subscription_lock(account_id: str) -> asyncio.Lock:
    if account_id not in _SUBSCRIPTION_LOCKS:
        _SUBSCRIPTION_LOCKS[account_id] = asyncio.Lock()
    return _SUBSCRIPTION_LOCKS[account_id]


class AlpacaServicer(broker_pb2_grpc.BrokerServicer):
    """Alpaca Broker service implementation stub."""

    def __init__(self, auth_cache: AuthCache | None = None) -> None:
        self._auth = auth_cache or AuthCache()
        self._stock_data_client: Any | None = None
        self._crypto_data_client: Any | None = None

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
        # Bound length: cash_amount is gRPC-untrusted (chunk-C sec MED-2).
        log.debug("place_order_cash_amount_received", cash_amount=str(cash_amount)[:32])

        account_id = self._account_id(request.account_number)
        if config.USE_IN_MEMORY_DEDUPE:
            if self._place_order_is_duplicate(request):
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    "client_order_id_duplicate",
                )
                return broker_pb2.PlaceOrderResponse()
        try:
            client = await self._configured_trading_client(account_id, context)
        except _ConfiguredClientUnavailableError:
            return broker_pb2.PlaceOrderResponse()
        try:
            order_request = self._build_order_request(request)
            order = await asyncio.to_thread(client.submit_order, order_request)
        except Exception as exc:
            # _build_order_request may raise InvalidOperation/ValueError on bad
            # input; keep inside try so _abort_internal sends the sentinel
            # instead of leaking raw input via gRPC INTERNAL detail.
            await self._abort_internal(context, exc)
            return broker_pb2.PlaceOrderResponse()
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
        try:
            client = await self._configured_trading_client(account_id, context)
        except _ConfiguredClientUnavailableError:
            return broker_pb2.CancelOrderResponse(accepted=False)
        api_error = load_api_error_class()
        try:
            await asyncio.to_thread(client.cancel_order_by_id, request.broker_order_id)
        except api_error as exc:
            if _api_error_status(exc) in {404, 422}:
                return broker_pb2.CancelOrderResponse(accepted=False)
            await self._abort_internal(context, exc)
            return broker_pb2.CancelOrderResponse(accepted=False)
        except Exception as exc:
            await self._abort_internal(context, exc)
            return broker_pb2.CancelOrderResponse(accepted=False)
        return broker_pb2.CancelOrderResponse(accepted=True)

    async def ModifyOrder(  # noqa: N802
        self,
        request: broker_pb2.ModifyOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ModifyOrderResponse:
        account_id = self._account_id(request.account_number)
        try:
            client = await self._configured_trading_client(account_id, context)
        except _ConfiguredClientUnavailableError:
            return broker_pb2.ModifyOrderResponse()
        api_error = load_api_error_class()
        try:
            replace_request = self._build_replace_order_request(request)
            order = await asyncio.to_thread(
                client.replace_order_by_id,
                request.broker_order_id,
                replace_request,
            )
        except api_error as exc:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            safe_detail = getattr(exc, "message", None) or "alpaca_replace_order_failed"
            context.set_details(str(safe_detail))
            return broker_pb2.ModifyOrderResponse()
        except Exception as exc:
            await self._abort_internal(context, exc)
            return broker_pb2.ModifyOrderResponse()
        return broker_pb2.ModifyOrderResponse(
            broker_order_id=str(getattr(order, "id", "")),
            status=_enum_value(getattr(order, "status", "")),
        )

    async def PlaceBracket(  # noqa: N802
        self,
        request: broker_pb2.PlaceBracketRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceBracketResponse:
        account_id = self._account_id(request.parent.account_number)
        try:
            client = await self._configured_trading_client(account_id, context)
        except _ConfiguredClientUnavailableError:
            return broker_pb2.PlaceBracketResponse()
        try:
            order_request = self._build_bracket_order_request(request)
            order = await asyncio.to_thread(client.submit_order, order_request)
        except Exception as exc:
            await self._abort_internal(context, exc)
            return broker_pb2.PlaceBracketResponse()

        legs = list(getattr(order, "legs", None) or [])
        # Classify legs by Alpaca order_type (chunk-B C-1): SDK does NOT
        # contract leg ordering. Match limit→take_profit, stop|stop_limit→
        # stop_loss. Fall back to index for fakes/edge cases.
        take_profit_leg, stop_loss_leg = _classify_bracket_legs(legs)
        if take_profit_leg is None or stop_loss_leg is None:
            log.warning(
                "alpaca_bracket_missing_legs",
                order_id=str(getattr(order, "id", "")),
                leg_count=len(legs),
            )
            await self._abort_internal(
                context,
                RuntimeError(f"bracket_missing_legs leg_count={len(legs)}"),
            )
            return broker_pb2.PlaceBracketResponse()
        return broker_pb2.PlaceBracketResponse(
            parent_broker_order_id=str(getattr(order, "id", "")),
            stop_loss_broker_order_id=str(getattr(stop_loss_leg, "id", "")),
            take_profit_broker_order_id=str(getattr(take_profit_leg, "id", "")),
            status=_enum_value(getattr(order, "status", "")),
        )

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SearchContractsResponse:
        await self._abort_unimplemented(context, "Alpaca SearchContracts lands in C1")
        return broker_pb2.SearchContractsResponse()

    async def PreviewOrder(  # noqa: N802
        self,
        request: broker_pb2.PreviewOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PreviewOrderResponse:
        """Phase 10a C4: alpaca-py has no pre-trade margin preview API.

        Spec §5: gate's _check_margin catches UNIMPLEMENTED and falls back
        to cached BP per the asymmetric WARN policy (spec §4 H4 row 4).
        """
        del request
        await self._abort_unimplemented(
            context,
            "alpaca-py does not provide pre-trade margin preview; "
            "gate falls back to cached BP per Phase 10a",
        )
        return broker_pb2.PreviewOrderResponse()

    async def GetHistoricalBars(  # noqa: N802
        self,
        request: broker_pb2.GetHistoricalBarsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.GetHistoricalBarsResponse:
        if request.timeframe != "1m":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "timeframe_1m_only")
            return broker_pb2.GetHistoricalBarsResponse()
        canonical_id = request.canonical_id.strip()
        if not canonical_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "canonical_id_required",
            )
            return broker_pb2.GetHistoricalBarsResponse()
        if request.range_start.seconds <= 0 or request.range_end.seconds <= 0:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "range_start_end_required",
            )
            return broker_pb2.GetHistoricalBarsResponse()
        if request.range_end.seconds <= request.range_start.seconds:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "range_end must be greater than range_start",
            )
            return broker_pb2.GetHistoricalBarsResponse()

        asset_class = _historical_asset_class(canonical_id)
        if asset_class is None:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "unsupported_asset_class",
            )
            return broker_pb2.GetHistoricalBarsResponse()

        try:
            stock_client, crypto_client = await self._configured_market_data_clients(
                context,
            )
            request_classes = _load_historical_data_classes()
            bars_request = _build_historical_bars_request(
                request_classes=request_classes,
                symbol=_historical_alpaca_symbol(canonical_id, asset_class),
                request=request,
            )
            if asset_class == "equity":
                response = await asyncio.to_thread(
                    stock_client.get_stock_bars, bars_request
                )
            else:
                response = await asyncio.to_thread(
                    crypto_client.get_crypto_bars, bars_request
                )
        except (grpc.RpcError, RuntimeError, ValueError) as exc:
            log.warning("alpaca_get_historical_bars_failed", exc_info=exc)
            raise
        except (Exception,) as exc:  # noqa: B013 - codex_defaults A
            await self._abort_internal(context, exc)
            return broker_pb2.GetHistoricalBarsResponse()

        symbol = _historical_alpaca_symbol(canonical_id, asset_class)
        bars = [
            _historical_bar_to_proto(bar)
            for bar in _bars_from_response(response, symbol)
        ]
        return broker_pb2.GetHistoricalBarsResponse(
            bars=bars,
            truncated=bool(getattr(response, "next_page_token", False)),
        )

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
            raise _ConfiguredClientUnavailableError("trading_client_not_configured")
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
        cash_amount = getattr(request, "cash_amount", "")
        if cash_amount:
            if order_type != "MARKET":
                # Backend XOR validator (chunk-0 T-0.7) enforces this upstream;
                # explicit guard here surfaces an INVALID_ARGUMENT instead of a
                # silent contract violation if the validator is bypassed.
                raise ValueError("cash_amount_market_only")
            values["notional"] = Decimal(cash_amount)
        elif request.qty:
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

    def _build_bracket_order_request(
        self,
        request: broker_pb2.PlaceBracketRequest,
    ) -> Any:
        request_classes = load_order_request_classes()
        parent = request.parent
        order_type = _normalize_order_type(parent.order_type)
        if order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("alpaca_bracket_parent_must_be_market_or_limit")
        if parent.tif and parent.tif.upper() != "DAY":
            raise ValueError("alpaca_bracket_tif_must_be_day")

        values: dict[str, Any] = {
            "symbol": _alpaca_symbol(parent.conid),
            "qty": Decimal(parent.qty),
            "side": parent.side.lower(),
            "time_in_force": "day",
            "order_class": request_classes["ORDER_CLASS"].BRACKET,
        }
        if parent.client_order_id:
            values["client_order_id"] = parent.client_order_id
        if order_type == "LIMIT":
            if not parent.limit_price:
                raise ValueError("alpaca_bracket_limit_requires_limit_price")
            values["limit_price"] = Decimal(parent.limit_price)
        if request.has_take_profit:
            values["take_profit"] = request_classes["BRACKET_TP"](
                limit_price=Decimal(request.take_profit.limit_price),
            )
        if request.has_stop_loss:
            stop_loss_values: dict[str, Any] = {
                "stop_price": Decimal(request.stop_loss.stop_price),
            }
            if request.stop_loss.limit_price:
                stop_loss_values["limit_price"] = Decimal(request.stop_loss.limit_price)
            values["stop_loss"] = request_classes["BRACKET_SL"](**stop_loss_values)
        return request_classes[order_type](**values)

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
        coid = (request.client_order_id or "").strip()
        account_id = self._account_id(request.account_number)
        if coid:
            key: tuple[Any, ...] = (account_id, "coid", coid)
        else:
            key = (
                account_id,
                "tuple",
                _alpaca_symbol(request.conid),
                str(request.qty),
                request.side,
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
            # CRIT-code-4: rejected acquires must not inflate the counter.
            return
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
        async with _subscription_lock(account_id):
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
                except Exception as exc:
                    log.warning("orderevent_callback_failed", exc_info=exc)

            trading_stream.subscribe_trade_updates(handler)
            # Alpaca crypto order updates are not exposed by a separate
            # alpaca-py stream; route all trade updates through TradingStream
            # until a crypto-specific API lands.
            run = getattr(trading_stream, "run", None)
            if run is not None:
                task = asyncio.create_task(
                    asyncio.to_thread(run), name="alpaca-trading-stream"
                )

                def _cleanup(t: asyncio.Task[None]) -> None:
                    if not t.cancelled():
                        task_exc = t.exception()
                        if task_exc is not None:
                            log.warning(
                                "alpaca_trading_stream_task_failed",
                                account_id=account_id,
                                exc_info=task_exc,
                            )
                    _ORDER_EVENT_SUBSCRIPTIONS.pop(account_id, None)
                    _ORDER_EVENT_TASKS.pop(account_id, None)

                task.add_done_callback(_cleanup)
                _ORDER_EVENT_TASKS[account_id] = task
            _ORDER_EVENT_SUBSCRIPTIONS[account_id] = trading_stream

    def _enqueue_order_event(self, account_id: str, update: Any) -> None:
        queue = self._order_event_queue(account_id)
        message = _trade_update_to_message(update)
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty as exc:
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

    async def _configured_market_data_clients(
        self,
        context: grpc.aio.ServicerContext,
    ) -> tuple[Any, Any]:
        try:
            api_key, api_secret = await self._auth.get_credentials()
        except (RuntimeError,) as exc:  # noqa: B013 - codex_defaults A
            log.warning("alpaca_market_data_credentials_missing", exc_info=exc)
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "alpaca_credentials_not_configured",
            )
            raise
        classes = _load_historical_data_classes()
        if self._stock_data_client is None:
            self._stock_data_client = classes["STOCK_CLIENT"](api_key, api_secret)
        if self._crypto_data_client is None:
            self._crypto_data_client = classes["CRYPTO_CLIENT"](api_key, api_secret)
        return self._stock_data_client, self._crypto_data_client

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
        # Don't leak raw SDK/exception text across the wire (security H-2)
        await context.abort(grpc.StatusCode.INTERNAL, "internal_error")

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


def _load_historical_data_classes() -> dict[str, Any]:
    global StockHistoricalDataClient
    global CryptoHistoricalDataClient
    global StockBarsRequest
    global CryptoBarsRequest
    global TimeFrame

    if StockHistoricalDataClient is None or CryptoHistoricalDataClient is None:
        from alpaca.data.historical import (
            CryptoHistoricalDataClient as _CryptoHistoricalDataClient,
            StockHistoricalDataClient as _StockHistoricalDataClient,
        )

        StockHistoricalDataClient = _StockHistoricalDataClient
        CryptoHistoricalDataClient = _CryptoHistoricalDataClient
    if StockBarsRequest is None or TimeFrame is None:
        from alpaca.data.requests import (
            CryptoBarsRequest as _CryptoBarsRequest,
            StockBarsRequest as _StockBarsRequest,
        )
        from alpaca.data.timeframe import TimeFrame as _TimeFrame

        StockBarsRequest = _StockBarsRequest
        CryptoBarsRequest = _CryptoBarsRequest
        TimeFrame = _TimeFrame
    return {
        "STOCK_CLIENT": StockHistoricalDataClient,
        "CRYPTO_CLIENT": CryptoHistoricalDataClient,
        "STOCK_BARS_REQUEST": StockBarsRequest,
        "CRYPTO_BARS_REQUEST": CryptoBarsRequest,
        "TIMEFRAME": TimeFrame,
    }


def _build_historical_bars_request(
    *,
    request_classes: dict[str, Any],
    symbol: str,
    request: broker_pb2.GetHistoricalBarsRequest,
) -> Any:
    values: dict[str, Any] = {
        "symbol_or_symbols": [symbol],
        "timeframe": request_classes["TIMEFRAME"].Minute,
        "start": datetime.fromtimestamp(request.range_start.seconds, UTC),
        "end": datetime.fromtimestamp(request.range_end.seconds, UTC),
    }
    if request.limit > 0:
        values["limit"] = request.limit
    request_class = (
        request_classes["CRYPTO_BARS_REQUEST"]
        if "/" in symbol
        else request_classes["STOCK_BARS_REQUEST"]
    )
    return request_class(**values)


def _historical_asset_class(canonical_id: str) -> str | None:
    if canonical_id.startswith("stock:"):
        return "equity"
    if canonical_id.startswith("crypto:"):
        return "crypto"
    if canonical_id.endswith(".US"):
        return "equity"
    if "/" in canonical_id:
        return "crypto"
    return None


def _historical_alpaca_symbol(canonical_id: str, asset_class: str) -> str:
    if asset_class == "crypto":
        if canonical_id.startswith("crypto:"):
            return normalize.canonical_to_alpaca_crypto(canonical_id)
        return canonical_id
    if canonical_id.startswith("stock:"):
        parts = canonical_id.split(":")
        if len(parts) >= 2:
            return parts[1]
    return canonical_id.removesuffix(".US")


def _bars_from_response(response: Any, symbol: str) -> list[Any]:
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        bars = data.get(symbol)
        if bars is not None:
            return list(bars)
        return [bar for symbol_bars in data.values() for bar in symbol_bars]
    if isinstance(response, list | tuple):
        return list(response)
    return list(getattr(response, "bars", []))


def _historical_bar_to_proto(bar: Any) -> broker_pb2.HistoricalBar:
    bucket_start = Timestamp()
    bucket_start.FromSeconds(int(bar.timestamp.timestamp()))
    return broker_pb2.HistoricalBar(
        bucket_start=bucket_start,
        open=str(Decimal(str(bar.open))),
        high=str(Decimal(str(bar.high))),
        low=str(Decimal(str(bar.low))),
        close=str(Decimal(str(bar.close))),
        volume=str(Decimal(str(bar.volume))),
        trade_count=int(bar.trade_count or 0),
    )


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
    if _looks_like_crypto_pair(conid):
        return canonical_crypto_symbol(conid)
    return conid


def _looks_like_crypto_pair(conid: str) -> bool:
    raw = conid.strip().upper()
    if "/" in raw:
        return True
    for suffix in _CRYPTO_ORDER_QUOTE_SUFFIXES:
        if raw.endswith(suffix):
            base = raw[: -len(suffix)]
            return bool(base) and base.isalpha()
    return False


def _classify_bracket_legs(
    legs: list[Any],
) -> tuple[Any | None, Any | None]:
    """Match bracket legs by Alpaca order_type, fall back to index for fakes.

    Alpaca's SDK does not contract leg ordering. Match limit→take_profit and
    stop|stop_limit→stop_loss; if order_type is missing on the leg objects
    (e.g. test fakes), fall back to the [TP, SL] index assumption.
    """
    take_profit_leg = None
    stop_loss_leg = None
    for leg in legs:
        kind = str(getattr(leg, "order_type", "")).lower()
        if kind == "limit" and take_profit_leg is None:
            take_profit_leg = leg
        elif kind in {"stop", "stop_limit"} and stop_loss_leg is None:
            stop_loss_leg = leg
    if take_profit_leg is None and len(legs) > 0 and stop_loss_leg is not legs[0]:
        take_profit_leg = legs[0]
    if stop_loss_leg is None and len(legs) > 1 and take_profit_leg is not legs[1]:
        stop_loss_leg = legs[1]
    return take_profit_leg, stop_loss_leg


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
