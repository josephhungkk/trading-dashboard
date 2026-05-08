"""Token cache + outbound RequestTokenRefresh.

Architectural invariants (spec §3.6):
  - C2 single-writer: this sidecar does NOT call Schwab's /oauth/token
    endpoint. It calls the backend's gRPC RequestTokenRefresh, which
    holds the PG advisory lock and is the only writer of refresh tokens.
  - M6 lock granularity: _token_lock is held only for the freshness
    check; the outbound gRPC call fires with the lock RELEASED.
  - H4 freshness: token is considered fresh for 25 of 30 mins (5-min
    headroom for clock skew + RPC latency).
  - HIGH-sec-3: bearer token sent as gRPC metadata ``x-backend-bearer``
    derived from APP_SECRET_KEY — backend rejects calls without it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta

from sidecar_schwab.metrics import SCHWAB_ACCESS_TOKEN_AGE_SECONDS

log = logging.getLogger(__name__)

# H4 — 25 minutes of fresh window inside Schwab's 30-minute TTL.
_FRESH_WINDOW = timedelta(minutes=25)


def _backend_callback_bearer() -> str:
    """Derive bearer token from APP_SECRET_KEY via SHA-256 (mirrors backend)."""
    key = os.environ.get("APP_SECRET_KEY", "")
    return hashlib.sha256(f"backend_callback:{key}".encode()).hexdigest()


class RequestTokenRefreshError(RuntimeError):
    pass


class TokenCache:
    """In-memory access_token cache with backend-side refresh callback."""

    def __init__(self, refresh_client) -> None:
        self._refresh_client = refresh_client
        self._token_lock = asyncio.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_issued_at: datetime | None = None
        self._refresh_event: asyncio.Event | None = None
        # MED-thundering-herd: concurrent stale-token callers await this event
        # while one refresh RPC is in flight, then re-read the updated token.
        self._refresh_in_flight: asyncio.Event | None = None

    def set_refresh_event(self, event: asyncio.Event) -> None:
        self._refresh_event = event

    def set_tokens(
        self,
        access_token: str,
        refresh_token: str,
        access_issued_at: datetime,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._access_issued_at = access_issued_at

    def access_token_age(self) -> float:
        if self._access_issued_at is None:
            return float("inf")
        delta = datetime.now(UTC) - self._access_issued_at
        return delta.total_seconds()

    async def get_access_token(self) -> str:
        """Return current access_token, refreshing via backend if stale.

        Lock is held only for the freshness check; if a refresh is needed,
        we release the lock before the gRPC RPC (M6).
        MED-thundering-herd: concurrent stale callers await _refresh_in_flight
        rather than each issuing a separate backend RPC.
        """
        async with self._token_lock:
            fresh = (
                self._access_token is not None
                and self._access_issued_at is not None
                and (datetime.now(UTC) - self._access_issued_at)
                < _FRESH_WINDOW
            )
            cached_access = self._access_token
            in_flight = self._refresh_in_flight

        SCHWAB_ACCESS_TOKEN_AGE_SECONDS.set(self.access_token_age())
        if fresh and cached_access is not None:
            return cached_access

        # If another coroutine is already refreshing, wait for it to complete
        # then return the updated token rather than issuing a duplicate RPC.
        if in_flight is not None:
            await in_flight.wait()
            async with self._token_lock:
                if self._access_token is not None:
                    return self._access_token

        # Mark refresh in-flight so concurrent callers coalesce.
        flight_event = asyncio.Event()
        async with self._token_lock:
            self._refresh_in_flight = flight_event

        # Lock NOT held during outbound RPC.
        from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb

        _metadata = (("x-backend-bearer", _backend_callback_bearer()),)
        try:
            resp = await self._refresh_client.RequestTokenRefresh(
                pb.TokenRefreshRequest(broker_id="schwab"),
                metadata=_metadata,
            )
        except Exception as e:
            async with self._token_lock:
                self._refresh_in_flight = None
            flight_event.set()
            raise RequestTokenRefreshError(
                f"backend RequestTokenRefresh failed: {e}"
            ) from e

        # Re-acquire lock to write back.
        async with self._token_lock:
            self._access_token = resp.access_token
            self._refresh_token = resp.refresh_token
            self._access_issued_at = resp.access_issued_at.ToDatetime(tzinfo=UTC)
            self._refresh_in_flight = None
            if self._refresh_event is not None:
                self._refresh_event.set()
        flight_event.set()
        return resp.access_token
