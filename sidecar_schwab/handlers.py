"""gRPC Broker servicer for Schwab (v3 — metadata-map ConfigureRequest).

Configure mutates server state — it owns the SchwabClient instance and the
TokenCache. All other RPCs read from state populated by Configure.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import sys
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from requests import HTTPError

_GENERATED_ROOT = Path(__file__).resolve().parent / "_generated"
if str(_GENERATED_ROOT) not in sys.path:
    sys.path.insert(0, str(_GENERATED_ROOT))

from sidecar_schwab._generated.broker.v1 import (  # noqa: E402
    broker_pb2,
    broker_pb2_grpc,
)
from sidecar_schwab.auth import TokenCache  # noqa: E402
from sidecar_schwab.client import SchwabClient  # noqa: E402
from sidecar_schwab.normalize import map_asset_type as _asset_type_to_enum  # noqa: E402

if TYPE_CHECKING:
    from sidecar_schwab.client import SchwabHTTPError

log = logging.getLogger(__name__)

type _TickCallback = Callable[[broker_pb2.QuoteMessage], None]

_FRESH_WINDOW = timedelta(minutes=25)  # H4 — 25min headroom inside 30min TTL

# Required metadata keys for Schwab Configure.
_REQUIRED_META_KEYS = ("app_key", "app_secret", "refresh_token")

# Per-call StreamQuotes guards — prevent a misbehaving / slow consumer
# from exhausting sidecar memory.
_STREAM_QUEUE_MAX = 2048  # in-flight ticks before drop-oldest kicks in
_CALL_SUBS_MAX = 500  # canonical_ids tracked per gRPC call
_SEARCH_CACHE_TTL_S = 300  # 5 min
_SEARCH_CACHE_MAX_ENTRIES = 1000
_REPLAY_CACHE_MAX_SIZE = 10_000


class _ReplayCache:
    """HIGH-5: LRU-capped idempotency cache for PlaceOrder.

    Replaces the unbounded ``dict`` that could grow without bound in long-running
    processes with high order volume.
    """

    def __init__(self, maxsize: int = _REPLAY_CACHE_MAX_SIZE) -> None:
        self._cache: OrderedDict[
            tuple[str, str], broker_pb2.PlaceOrderResponse
        ] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: tuple[str, str]) -> broker_pb2.PlaceOrderResponse | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        self._cache.move_to_end(key)
        return entry

    def put(self, key: tuple[str, str], value: broker_pb2.PlaceOrderResponse) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._cache


class BrokerServicer(broker_pb2_grpc.BrokerServicer):
    """Schwab gRPC service implementation."""

    def __init__(self) -> None:
        self._configure_lock = asyncio.Lock()
        self._token_refresh_lock = asyncio.Lock()
        self._configure_count = 0
        self._client: SchwabClient | None = None
        self._token_cache: TokenCache | None = None
        self._backend_channel: grpc.aio.Channel | None = None  # HIGH-code-3: track for cleanup
        self._last_meta_fingerprint: str | None = None
        self._configured_at: datetime | None = None
        self._hashes_loaded_once = False
        # 8a C3 -- trade write-path state; HIGH-5: capped LRU replaces bare dict.
        self._replay_cache: _ReplayCache = _ReplayCache()
        self._search_cache: dict[
            tuple[str, int], tuple[list[broker_pb2.Contract], float]
        ] = {}
        self._simulator: Any = None  # set by D4 lifespan; tests inject directly
        self._poller: Any = None  # set by D4 lifespan; tests inject directly

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        """H4 invariant: gateway_connected = token_fresh AND hashes are present."""
        token_fresh = (
            self._token_cache is not None
            and self._token_cache._access_issued_at is not None
            and (datetime.now(UTC) - self._token_cache._access_issued_at)
            < _FRESH_WINDOW
        )
        hashes_present = self._client is not None and bool(self._client._account_hashes)
        connected = token_fresh and hashes_present

        started_ts = Timestamp()
        if self._configured_at is not None:
            started_ts.FromDatetime(self._configured_at)

        return broker_pb2.HealthResponse(
            label="schwab",
            broker_id="schwab",
            gateway_version="schwabdev-3.0.3",
            gateway_connected=connected,
            sidecar_version=os.environ.get("SIDECAR_BUILD_SHA", "0.7.0"),
            started_at=started_ts,
        )

    # Configure

    async def Configure(  # noqa: N802
        self,
        request: broker_pb2.ConfigureRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.ConfigureResponse:
        meta = dict(request.metadata)
        missing = [k for k in _REQUIRED_META_KEYS if not meta.get(k)]
        if missing:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"schwab Configure: missing metadata keys {missing}",
            )
            return broker_pb2.ConfigureResponse(
                ok=False,
                detail=f"missing:{','.join(missing)}",
            )

        async with self._configure_lock:
            fingerprint = self._fingerprint(meta)
            if fingerprint == self._last_meta_fingerprint:
                return broker_pb2.ConfigureResponse(ok=True)

            issued_at_str = meta.get("access_issued_at", "")
            access_issued_at = self._parse_iso(issued_at_str)

            # H4 — discard the supplied access_token if it's stale; sidecar
            # will trigger a backend RequestTokenRefresh on first outbound call.
            now = datetime.now(UTC)
            if access_issued_at and (now - access_issued_at) < _FRESH_WINDOW:
                effective_access = meta.get("access_token", "")
            else:
                effective_access = ""
                access_issued_at = now - _FRESH_WINDOW * 2  # mark "definitely stale"

            backend_addr = os.environ.get("BACKEND_ADMIN_GRPC", "backend:8001")
            # HIGH-code-3: close the previous channel before creating a new one
            # to prevent connection-descriptor leaks on repeated Configure calls.
            if self._backend_channel is not None:
                with contextlib.suppress(Exception):
                    await self._backend_channel.close()
            self._backend_channel = grpc.aio.insecure_channel(backend_addr)
            refresh_client = broker_pb2_grpc.BackendCallbackStub(self._backend_channel)

            self._token_cache = TokenCache(refresh_client=refresh_client)
            self._token_cache.set_tokens(
                access_token=effective_access,
                refresh_token=meta["refresh_token"],
                access_issued_at=access_issued_at,
            )
            try:
                self._client = SchwabClient.from_credentials(
                    app_key=meta["app_key"],
                    app_secret=meta["app_secret"],
                    token_cache=self._token_cache,
                )
            except (ValueError,) as _exc:
                log.warning("schwab_client_init_failed; using deferred client shell")
                self._client = SchwabClient(
                    schwabdev_client=None,
                    token_cache=self._token_cache,
                )
            self._hashes_loaded_once = False
            self._last_meta_fingerprint = fingerprint
            self._configured_at = now
            self._configure_count += 1
            log.info(
                "schwab_configured count=%d access_was_fresh=%s",
                self._configure_count,
                bool(effective_access),
            )
            return broker_pb2.ConfigureResponse(ok=True)

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.AccountsResponse:
        """v3 -- real proto signature: Empty to AccountsResponse."""
        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured (call Configure first)",
            )
            return broker_pb2.AccountsResponse()

        from sidecar_schwab.normalize import normalize_account

        # H3 -- emit 'initial' first, then 'rotation_detected'.
        reason = "initial" if not self._hashes_loaded_once else "rotation_detected"
        hashes = await self._client.refresh_hashes(reason=reason)
        self._hashes_loaded_once = True

        accounts: list[broker_pb2.Account] = []
        for account_number, hash_value in hashes.items():
            details = await self._fetch_account_with_404_retry(account_number)
            account_details = details.get("securitiesAccount", details)
            acct = normalize_account(account_details)
            acct.account_hash = hash_value
            acct.gateway_label = "schwab"
            accounts.append(acct)
        return broker_pb2.AccountsResponse(accounts=accounts)

    async def GetAccountSummary(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SummaryResponse:
        if self._client is None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "not configured")
            return broker_pb2.SummaryResponse()

        from sidecar_schwab.normalize import normalize_summary

        details = await self._fetch_account_with_404_retry(request.account_number)
        summary = normalize_summary(details)
        return broker_pb2.SummaryResponse(summary=summary)

    async def GetPositions(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PositionsResponse:
        if self._client is None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "not configured")
            return broker_pb2.PositionsResponse()

        from sidecar_schwab.normalize import normalize_position

        details = await self._fetch_account_with_404_retry(request.account_number)
        sa = details.get("securitiesAccount") or {}
        positions = [normalize_position(p) for p in (sa.get("positions") or [])]
        return broker_pb2.PositionsResponse(positions=positions)

    async def GetOrders(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.OrdersResponse:
        if self._client is None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "not configured")
            return broker_pb2.OrdersResponse()

        from sidecar_schwab.normalize import normalize_order

        end = datetime.now(UTC)
        start = end - timedelta(days=7)
        h = self._client.hash_for(request.account_number)
        rows = await self._client.get_orders(
            account_hash=h,
            from_dt=start.isoformat(),
            to_dt=end.isoformat(),
            max_results=200,
        )
        orders = [normalize_order(r) for r in (rows or [])]
        return broker_pb2.OrdersResponse(orders=orders)

    async def GetContract(self, request, context):  # noqa: N802
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED, "Schwab GetContract lands in Phase 7b"
        )
        return broker_pb2.ContractResponse()

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.PlaceOrderResponse:
        import time as _time

        from sidecar_schwab.client import SchwabHTTPError
        from sidecar_schwab.metrics import SCHWAB_PLACE_ORDER_DURATION_MS
        from sidecar_schwab.normalize import (
            to_schwab_order_payload,
            to_schwab_oco_payload,  # noqa: F401 — used by orchestrator for OCO REST
        )
        # OCO orders: no PlaceOco RPC exists in broker.proto.  The backend
        # orchestrator calls to_schwab_oco_payload(leg_a, leg_b) directly and
        # POSTs the result via schwabdev REST.  This handler handles SINGLE only.

        coid = request.client_order_id
        cash_amount = request.cash_amount
        log.debug("place_order_cash_amount_received", cash_amount=cash_amount)

        # 1. SIM route: client_order_id starting with "SIM-" never hits live REST.
        if coid.startswith("SIM-") and self._simulator is not None:
            broker_order_id = self._simulator.register(
                account_number=request.account_number,
                client_order_id=coid,
                request=request,
            )
            return broker_pb2.PlaceOrderResponse(
                broker_order_id=broker_order_id,
                status="submitted",
            )

        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured",
            )
            return broker_pb2.PlaceOrderResponse()

        account_hash = self._client.hash_for(request.account_number)
        if not account_hash:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"unknown account_number: {request.account_number}",
            )
            return broker_pb2.PlaceOrderResponse()

        # 2. Replay cache (idempotency for client retries). HIGH-5: LRU-capped.
        cache_key = (account_hash, coid)
        cached = self._replay_cache.get(cache_key)
        if cached is not None:
            return cached

        # 3. Token pre-warm (HIGH-4).
        await self._client.ensure_fresh_token()

        # 4. Live REST.
        payload = to_schwab_order_payload(
            side=request.side,
            order_type=request.order_type,
            tif=request.tif,
            qty=request.qty,
            symbol=request.conid,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            trail_offset=request.trail_offset,
            trail_offset_type=request.trail_offset_type,
            trail_limit_offset=request.trail_limit_offset,
            expiry_date=request.expiry_date or None,
        )
        t0 = _time.monotonic()
        try:
            result = await self._client.place_order(
                account_hash=account_hash, payload=payload
            )
        except (SchwabHTTPError,) as exc:
            SCHWAB_PLACE_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
            await self._abort_for_http(context, exc)
        except (OSError, TimeoutError) as exc:
            SCHWAB_PLACE_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")

        SCHWAB_PLACE_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)

        rsp = broker_pb2.PlaceOrderResponse(
            broker_order_id=result["broker_order_id"],
            status="submitted",
        )
        self._replay_cache.put(cache_key, rsp)
        if self._poller is not None:
            self._poller.activate_fast(account_number=request.account_number)
        return rsp

    async def _abort_for_http(  # noqa: N802
        self,
        context: grpc.aio.ServicerContext,
        exc: "SchwabHTTPError",
    ) -> None:
        body = (getattr(exc, "args", [str(exc)])[0] or "")[:200]
        if exc.status_code == 401:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, body)
        elif exc.status_code == 403:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, body)
        elif exc.status_code == 429:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, body)
        elif 400 <= exc.status_code < 500:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, body)
        else:
            await context.abort(grpc.StatusCode.UNAVAILABLE, body)

    async def CancelOrder(self, request, context):  # noqa: N802
        import time as _time

        from sidecar_schwab.client import SchwabHTTPError
        from sidecar_schwab.metrics import SCHWAB_CANCEL_ORDER_DURATION_MS

        if request.broker_order_id.startswith("SIM-") and self._simulator is not None:
            self._simulator.cancel(request.broker_order_id)
            return broker_pb2.CancelOrderResponse(accepted=True)

        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured",
            )
            return broker_pb2.CancelOrderResponse()

        account_hash = self._client.hash_for(request.account_number)
        if not account_hash:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"unknown account_number: {request.account_number}",
            )
            return broker_pb2.CancelOrderResponse()

        await self._client.ensure_fresh_token()

        t0 = _time.monotonic()
        try:
            await self._client.cancel_order(
                account_hash=account_hash,
                order_id=request.broker_order_id,
            )
        except (SchwabHTTPError,) as exc:
            SCHWAB_CANCEL_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
            await self._abort_for_http(context, exc)
        except (OSError, TimeoutError) as exc:
            SCHWAB_CANCEL_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")

        SCHWAB_CANCEL_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
        if self._poller is not None:
            self._poller.activate_fast(account_number=request.account_number)
        return broker_pb2.CancelOrderResponse(accepted=True)

    async def ModifyOrder(self, request, context):  # noqa: N802
        import time as _time

        from sidecar_schwab.client import SchwabHTTPError
        from sidecar_schwab.metrics import SCHWAB_MODIFY_ORDER_DURATION_MS
        from sidecar_schwab.normalize import to_schwab_order_payload

        if request.broker_order_id.startswith("SIM-") and self._simulator is not None:
            new_id = self._simulator.modify(request.broker_order_id, request)
            return broker_pb2.ModifyOrderResponse(
                broker_order_id=new_id,
                status="submitted",
                parent_broker_order_id=request.broker_order_id,
            )

        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured",
            )
            return broker_pb2.ModifyOrderResponse()

        account_hash = self._client.hash_for(request.account_number)
        if not account_hash:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"unknown account_number: {request.account_number}",
            )
            return broker_pb2.ModifyOrderResponse()

        await self._client.ensure_fresh_token()

        side = broker_pb2.OrderSide.Name(request.side)
        order_type = broker_pb2.OrderType.Name(request.order_type).removeprefix(
            "ORDER_TYPE_"
        )
        tif = broker_pb2.TimeInForce.Name(request.tif).removeprefix("TIF_")
        payload = to_schwab_order_payload(
            side=side,
            order_type=order_type,
            tif=tif,
            qty=request.qty,
            symbol=request.contract.symbol or request.contract.conid,
            limit_price=request.limit_price.value,
            stop_price=request.stop_price.value,
            trail_offset=request.trail_offset,
            trail_offset_type=request.trail_offset_type,
            trail_limit_offset=request.trail_limit_offset,
            expiry_date=request.expiry_date or None,
        )

        t0 = _time.monotonic()
        try:
            result = await self._client.replace_order(
                account_hash=account_hash,
                order_id=request.broker_order_id,
                payload=payload,
            )
        except (SchwabHTTPError,) as exc:
            SCHWAB_MODIFY_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
            await self._abort_for_http(context, exc)
        except (OSError, TimeoutError) as exc:
            SCHWAB_MODIFY_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")

        SCHWAB_MODIFY_ORDER_DURATION_MS.observe((_time.monotonic() - t0) * 1000)

        if self._poller is not None:
            self._poller.activate_fast(account_number=request.account_number)
        return broker_pb2.ModifyOrderResponse(
            broker_order_id=result["broker_order_id"],
            status="submitted",
            parent_broker_order_id=request.broker_order_id,
        )

    async def PlaceBracket(self, request, context):  # noqa: N802
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED, "Schwab PlaceBracket lands in Phase 8"
        )
        return broker_pb2.PlaceBracketResponse()

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.SearchContractsResponse:
        import time as _time

        from sidecar_schwab.client import SchwabHTTPError

        if not request.query:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "query must not be empty"
            )
            return broker_pb2.SearchContractsResponse()
        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured",
            )
            return broker_pb2.SearchContractsResponse()

        cache_key = (request.query.upper(), request.asset_class)
        now = _time.monotonic()
        cached = self._search_cache.get(cache_key)
        if cached is not None and now - cached[1] < _SEARCH_CACHE_TTL_S:
            return broker_pb2.SearchContractsResponse(contracts=cached[0])

        await self._client.ensure_fresh_token()
        try:
            results = await self._client.search_instruments(query=request.query)
        except (SchwabHTTPError,) as exc:
            await self._abort_for_http(context, exc)
            # CRIT-2: return after abort to prevent UnboundLocalError on `results`.
            return broker_pb2.SearchContractsResponse()
        except (OSError, TimeoutError) as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"schwab transport: {exc}")
            return broker_pb2.SearchContractsResponse()

        contracts = [
            broker_pb2.Contract(
                symbol=r.get("symbol", ""),
                exchange=r.get("exchange", ""),
                asset_class=_asset_type_to_enum(r.get("assetType", "")),
            )
            for r in results
        ]
        if len(self._search_cache) >= _SEARCH_CACHE_MAX_ENTRIES:
            oldest = min(self._search_cache, key=lambda k: self._search_cache[k][1])
            del self._search_cache[oldest]
        self._search_cache[cache_key] = (contracts, now)
        return broker_pb2.SearchContractsResponse(contracts=contracts)

    async def GetHistoricalBars(  # noqa: N802
        self,
        request: broker_pb2.GetHistoricalBarsRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.GetHistoricalBarsResponse:
        from sidecar_schwab.client import SchwabHTTPError

        if request.timeframe != "1m":
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "timeframe must be 1m",
            )
        if not request.canonical_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "canonical_id must not be empty",
            )
        if request.range_start.seconds <= 0 or request.range_end.seconds <= 0:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "range_start and range_end must be set",
            )
        if request.range_end.seconds <= request.range_start.seconds:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "range_end must be greater than range_start",
            )
        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured",
            )

        symbol = request.canonical_id.removesuffix(".US")
        try:
            payload = await self._fetch_price_history_payload(request, symbol)
        except (HTTPError, SchwabHTTPError) as exc:
            if _http_status_code(exc) != 401:
                await self._abort_for_history_http(context, exc)
            await self._refresh_token_for_history_retry()
            try:
                payload = await self._fetch_price_history_payload(request, symbol)
            except (HTTPError, SchwabHTTPError) as retry_exc:
                if _http_status_code(retry_exc) == 401:
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "schwab price_history returned 401 invalid_token",
                    )
                await self._abort_for_history_http(context, retry_exc)

        candles = payload.get("candles") or []
        bars = [
            broker_pb2.HistoricalBar(
                bucket_start=Timestamp(seconds=int(candle["datetime"]) // 1000),
                open=str(Decimal(str(candle["open"]))),
                high=str(Decimal(str(candle["high"]))),
                low=str(Decimal(str(candle["low"]))),
                close=str(Decimal(str(candle["close"]))),
                volume=str(Decimal(str(candle["volume"]))),
                trade_count=0,
            )
            for candle in candles
        ]
        truncated = request.limit > 0 and len(candles) >= request.limit
        return broker_pb2.GetHistoricalBarsResponse(
            bars=bars,
            truncated=truncated,
        )

    async def _fetch_price_history_payload(
        self,
        request: broker_pb2.GetHistoricalBarsRequest,
        symbol: str,
    ) -> dict[str, Any]:
        response = await asyncio.to_thread(
            self._price_history_callable(),
            symbol=symbol,
            periodType="day",
            frequencyType="minute",
            frequency=1,
            startDate=int(request.range_start.seconds * 1000),
            endDate=int(request.range_end.seconds * 1000),
            needExtendedHoursData=True,
        )
        if asyncio.iscoroutine(response):
            response = await response
        status_code = int(
            getattr(
                response,
                "status_code",
                getattr(response, "status", 200),
            )
        )
        if status_code >= 400:
            from sidecar_schwab.client import SchwabHTTPError

            raise SchwabHTTPError(
                f"/pricehistory returned status={status_code}",
                status_code=status_code,
                endpoint="/pricehistory",
            )
        if hasattr(response, "json"):
            payload = response.json()
            if asyncio.iscoroutine(payload):
                payload = await payload
            return dict(payload)
        return dict(response)

    async def _abort_for_history_http(
        self,
        context: grpc.aio.ServicerContext,
        exc: BaseException,
    ) -> None:
        status_code = _http_status_code(exc)
        body = f"schwab HTTP {status_code or 'unknown'}"
        if status_code == 403:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, body)
            return
        if status_code == 429:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, body)
            return
        if status_code is not None and 400 <= status_code < 500:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, body)
            return
        await context.abort(grpc.StatusCode.UNAVAILABLE, body)

    async def _refresh_token_for_history_retry(self) -> None:
        async with self._token_refresh_lock:
            if self._token_cache is not None:
                self._token_cache._access_issued_at = None
            ensure_fresh_token = getattr(self._client, "ensure_fresh_token", None)
            if ensure_fresh_token is None:
                return
            result = ensure_fresh_token()
            if asyncio.iscoroutine(result):
                await result

    def _price_history_callable(self) -> Callable[..., Any]:
        direct = getattr(self._client, "price_history", None)
        if direct is not None:
            return direct
        wrapped = getattr(self._client, "_client", None)
        nested = getattr(wrapped, "price_history", None)
        if nested is not None:
            return nested
        raise RuntimeError("schwab price_history client method unavailable")

    async def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.OrderEventMessage]:
        if self._poller is None:
            await context.abort(
                grpc.StatusCode.UNAVAILABLE,
                "schwab order-event stream not yet wired (Phase 8a D4)",
            )
            return

        fan_out = self._poller.fan_out_for(account_number=request.account_number)
        queue: asyncio.Queue[broker_pb2.OrderEventMessage | None] = fan_out.subscribe()
        try:
            while context.is_active():
                ev = await queue.get()
                if ev is None:
                    break
                yield ev
        finally:
            fan_out.unsubscribe(queue)

    async def _get_or_init_schwab_streamer(self):
        lock = self.__dict__.setdefault("_streamer_lock", asyncio.Lock())
        async with lock:
            streamer = getattr(self, "_streamer", None)
            if streamer is not None:
                return streamer
            if self._token_cache is None:
                raise RuntimeError("schwab sidecar token cache not configured")

            from sidecar_schwab.streamer import SchwabStreamer

            tokens_refreshed = asyncio.Event()
            self._token_cache.set_refresh_event(tokens_refreshed)
            streamer = SchwabStreamer(
                token_cache=self._token_cache,
                tokens_refreshed=tokens_refreshed,
            )
            try:
                await streamer.start()
            except (Exception,) as _exc:
                # Init failure must not leak a partially-started streamer
                # — orphaned sockets / refcount tables would accumulate
                # under repeated transient network errors.
                with contextlib.suppress(Exception):
                    await streamer.stop()
                raise
            self._streamer = streamer
            return streamer

    async def StreamQuotes(  # noqa: N802
        self,
        request_iterator: AsyncIterator[broker_pb2.StreamQuotesRequest],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[broker_pb2.QuoteMessage]:
        try:
            streamer = await self._get_or_init_schwab_streamer()
        except (RuntimeError,) as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return

        # Bound the per-call queue so a slow gRPC consumer cannot exhaust
        # sidecar memory under high-frequency tick streams. On overflow we
        # drop the OLDEST tick — fresh quotes are more useful than backlog.
        queue: asyncio.Queue[broker_pb2.QuoteMessage] = asyncio.Queue(
            maxsize=_STREAM_QUEUE_MAX
        )
        call_subs: set[str] = set()

        def tick_callback(message: broker_pb2.QuoteMessage) -> None:
            try:
                queue.put_nowait(message)
            except (asyncio.QueueFull,) as _exc:
                # Drop oldest, then enqueue. Best-effort — a competing
                # producer could refill before our get_nowait, in which
                # case this tick is silently dropped.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(message)

        self._add_streamer_tick_callback(streamer, tick_callback)
        consumer_task = asyncio.create_task(
            self._consume_stream_quote_requests(
                request_iterator,
                streamer,
                call_subs,
            ),
            name="schwab-stream-quotes-consumer",
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
                            "schwab.stream_quotes.call_subs_cap_hit",
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
                            "schwab.stream_quotes.resync_cap_hit",
                            requested=len(symbols),
                            cap=_CALL_SUBS_MAX,
                        )
                        continue
                    await streamer.on_resync(symbols)
                    call_subs.clear()
                    call_subs.update(_canonical_id(s) for s in symbols)
                # heartbeat (and any unknown op) — keep-alive only, no-op
            except (Exception,) as exc:  # noqa: BLE001
                # Malformed frame must not tear the bidi call down — log
                # and continue draining the iterator.
                log.warning(
                    "schwab.stream_quotes.request_dispatch_error",
                    error=str(exc),
                )

    def _add_streamer_tick_callback(
        self,
        streamer: Any,
        callback: _TickCallback,
    ) -> None:
        # SchwabStreamer exposes a singleton tick_callback slot, so the
        # servicer installs a per-RPC fan-out dispatcher around that slot.
        callbacks = getattr(streamer, "_broker_servicer_tick_callbacks", None)
        if callbacks is None:
            callbacks = set()
            streamer._broker_servicer_tick_callbacks = callbacks
            previous = getattr(streamer, "tick_callback", None)
            streamer._broker_servicer_previous_tick_callback = previous

            def dispatch(message: broker_pb2.QuoteMessage) -> None:
                # Per-callback isolation — one bad consumer must not block
                # the others on this tick. Snapshot via tuple(...) so a
                # concurrent register/unregister doesn't mutate mid-loop.
                if previous is not None:
                    try:
                        previous(message)
                    except (Exception,) as exc:  # noqa: BLE001
                        log.warning(
                            "schwab.stream_quotes.previous_callback_error",
                            error=str(exc),
                        )
                for registered in tuple(callbacks):
                    try:
                        registered(message)
                    except (Exception,) as exc:  # noqa: BLE001
                        log.warning(
                            "schwab.stream_quotes.tick_callback_error",
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

    async def _fetch_account_with_404_retry(self, account_number: str) -> dict:
        """H3 -- on typed SchwabAccountHashStaleError, invalidate cache + retry once."""
        from sidecar_schwab.client import SchwabAccountHashStaleError

        h = self._client.hash_for(account_number)
        try:
            return await self._client.get_account_details(h)
        except (SchwabAccountHashStaleError,) as _exc:
            await self._client.refresh_hashes(reason="404_retry")
            h = self._client.hash_for(account_number)
            return await self._client.get_account_details(h)

    @staticmethod
    def _fingerprint(meta: dict[str, str]) -> str:
        """MED-6: return a SHA-256 digest of the config keys — never store raw secrets in heap."""
        keys = (
            "app_key",
            "app_secret",
            "access_token",
            "refresh_token",
            "access_issued_at",
        )
        plaintext = "|".join(meta.get(k, "") for k in keys)
        return hashlib.sha256(plaintext.encode()).hexdigest()

    @staticmethod
    def _parse_iso(s: str) -> datetime:
        if not s:
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(s).astimezone(UTC)
        except (ValueError,) as _exc:
            return datetime.now(UTC)


def _canonical_id(symbol: broker_pb2.SymbolRef) -> str:
    return symbol.canonical_id or symbol.raw_symbol


def _symbol_refs(canonical_ids: set[str]) -> list[broker_pb2.SymbolRef]:
    return [
        broker_pb2.SymbolRef(canonical_id=canonical_id)
        for canonical_id in sorted(canonical_ids)
    ]


def _http_status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return int(status_code)
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return int(status_code)
    return None
