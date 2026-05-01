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

    # Health stub (replaces the A4 stub; B5 will override with real impl)

    async def Health(  # noqa: N802
        self,
        request: broker_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,
    ) -> broker_pb2.HealthResponse:
        return broker_pb2.HealthResponse(
            label="schwab",
            broker_id="schwab",
            gateway_version="",
            gateway_connected=False,
            sidecar_version="0.7.0-stub",
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
            self._last_meta_fingerprint = fingerprint
            self._configured_at = now
            self._configure_count += 1
            log.info("schwab_configured count=%d access_was_fresh=%s",
                     self._configure_count, bool(effective_access))
            return broker_pb2.ConfigureResponse(ok=True)

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
