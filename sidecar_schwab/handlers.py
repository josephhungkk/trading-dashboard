"""gRPC Broker servicer for Schwab (v3 — metadata-map ConfigureRequest).

Configure mutates server state — it owns the SchwabClient instance and the
TokenCache. All other RPCs read from state populated by Configure.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

_GENERATED_ROOT = Path(__file__).resolve().parent / "_generated"
if str(_GENERATED_ROOT) not in sys.path:
    sys.path.insert(0, str(_GENERATED_ROOT))

from sidecar_schwab._generated.broker.v1 import (  # noqa: E402
    broker_pb2,
    broker_pb2_grpc,
)
from sidecar_schwab.auth import TokenCache  # noqa: E402
from sidecar_schwab.client import SchwabClient  # noqa: E402

log = logging.getLogger(__name__)

_FRESH_WINDOW = timedelta(minutes=25)  # H4 — 25min headroom inside 30min TTL

# Required metadata keys for Schwab Configure.
_REQUIRED_META_KEYS = ("app_key", "app_secret", "refresh_token")


class BrokerServicer(broker_pb2_grpc.BrokerServicer):
    """Schwab gRPC service implementation."""

    def __init__(self) -> None:
        self._configure_lock = asyncio.Lock()
        self._configure_count = 0
        self._client: SchwabClient | None = None
        self._token_cache: TokenCache | None = None
        self._last_meta_fingerprint: str | None = None
        self._configured_at: datetime | None = None
        self._hashes_loaded_once = False

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        """H4 invariant: gateway_connected = token_fresh AND _account_hashes non-empty."""
        token_fresh = (
            self._token_cache is not None
            and self._token_cache._access_issued_at is not None
            and (datetime.now(timezone.utc) - self._token_cache._access_issued_at)
            < _FRESH_WINDOW
        )
        hashes_present = (
            self._client is not None
            and bool(self._client._account_hashes)
        )
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
            return broker_pb2.ConfigureResponse(ok=False, detail=f"missing:{','.join(missing)}")

        async with self._configure_lock:
            fingerprint = self._fingerprint(meta)
            if fingerprint == self._last_meta_fingerprint:
                return broker_pb2.ConfigureResponse(ok=True)

            issued_at_str = meta.get("access_issued_at", "")
            access_issued_at = self._parse_iso(issued_at_str)

            # H4 — discard the supplied access_token if it's stale; sidecar
            # will trigger a backend RequestTokenRefresh on first outbound call.
            now = datetime.now(timezone.utc)
            if access_issued_at and (now - access_issued_at) < _FRESH_WINDOW:
                effective_access = meta.get("access_token", "")
            else:
                effective_access = ""
                access_issued_at = now - _FRESH_WINDOW * 2  # mark "definitely stale"

            backend_addr = os.environ.get("BACKEND_ADMIN_GRPC", "backend:8001")
            channel = grpc.aio.insecure_channel(backend_addr)
            refresh_client = broker_pb2_grpc.BackendCallbackStub(channel)

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
            except ValueError:
                log.warning("schwab_client_init_failed; using deferred client shell")
                self._client = SchwabClient(
                    schwabdev_client=None,
                    token_cache=self._token_cache,
                )
            self._hashes_loaded_once = False
            self._last_meta_fingerprint = fingerprint
            self._configured_at = now
            self._configure_count += 1
            log.info("schwab_configured count=%d access_was_fresh=%s",
                     self._configure_count, bool(effective_access))
            return broker_pb2.ConfigureResponse(ok=True)

    async def ListManagedAccounts(  # noqa: N802
        self,
        request: broker_pb2.Empty,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.AccountsResponse:
        """v3 -- RPC name + signature match real proto: takes Empty, returns AccountsResponse."""
        if self._client is None:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "schwab sidecar not configured (call Configure first)",
            )
            return broker_pb2.AccountsResponse()

        from sidecar_schwab.normalize import normalize_account

        # H3 -- emit 'initial' on first call after Configure; 'rotation_detected' subsequently.
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

        end = datetime.now(timezone.utc)
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
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab GetContract lands in Phase 7b")
        return broker_pb2.ContractResponse()

    async def PlaceOrder(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab PlaceOrder lands in Phase 8")
        return broker_pb2.PlaceOrderResponse()

    async def CancelOrder(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab CancelOrder lands in Phase 8")
        return broker_pb2.CancelOrderResponse()

    async def ModifyOrder(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab ModifyOrder lands in Phase 8")
        return broker_pb2.ModifyOrderResponse()

    async def PlaceBracket(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab PlaceBracket lands in Phase 8")
        return broker_pb2.PlaceBracketResponse()

    async def SearchContracts(self, request, context):  # noqa: N802
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab contract search lands in Phase 7b")
        return broker_pb2.SearchContractsResponse()

    async def OrderEvent(self, request, context):  # noqa: N802
        # Server-streaming RPC.
        await context.abort(grpc.StatusCode.UNIMPLEMENTED,
                            "Schwab OrderEvent stream lands in Phase 8")

    async def _fetch_account_with_404_retry(self, account_number: str) -> dict:
        """H3 -- on typed SchwabAccountHashStaleError, invalidate cache + retry once."""
        from sidecar_schwab.client import SchwabAccountHashStaleError

        h = self._client.hash_for(account_number)
        try:
            return await self._client.get_account_details(h)
        except SchwabAccountHashStaleError:
            await self._client.refresh_hashes(reason="404_retry")
            h = self._client.hash_for(account_number)
            return await self._client.get_account_details(h)

    @staticmethod
    def _fingerprint(meta: dict[str, str]) -> str:
        """Hash the 5 metadata keys we care about for idempotency."""
        keys = ("app_key", "app_secret", "access_token", "refresh_token", "access_issued_at")
        return "|".join(meta.get(k, "") for k in keys)

    @staticmethod
    def _parse_iso(s: str) -> datetime:
        if not s:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)
