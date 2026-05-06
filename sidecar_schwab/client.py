"""SchwabClient -- the ONLY module that imports schwabdev (M3 isolation).

Wraps Schwabdev's async client with our retry policy, rate-limit handling,
account-hash cache, and explicit exception classes (v3 -- replaces fragile
substring matches).

C2 single-writer rule (v3 -- corrected for schwabdev==3.0.3):
  - Spec expected client.tokens.update_tokens(access_token=..., refresh_token=...)
    to be a local setter. In schwabdev==3.0.3, update_tokens has signature
    update_tokens(force_access_token=False, force_refresh_token=False) and may
    hit Schwab's token endpoint. We therefore do not call it here.
  - Instead, after backend-driven refresh, we sync Schwabdev's in-process state
    by setting tokens.access_token / tokens.refresh_token and updating the
    aiohttp Authorization header.
  - ClientAsync exists, but uses tokens_db rather than tokens_file.
  - Linked accounts method is linked_accounts(), not account_linked().
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import schwabdev  # M3 -- only file in the package that imports this

from sidecar_schwab.auth import TokenCache
from sidecar_schwab.metrics import (
    SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL,
    SCHWAB_HTTP_REQUESTS_TOTAL,
)

log = logging.getLogger(__name__)

# M6 -- async semaphore caps concurrent outbound HTTP at 10.
_HTTP_CONCURRENCY = 10
_MAX_RETRY = 3
_LOCATION_RE = re.compile(r"/orders/(?P<id>\d+)")


def _extract_broker_order_id(headers: Any) -> str:
    location = None
    if hasattr(headers, "get"):
        location = headers.get("Location") or headers.get("location")
    if location is None and hasattr(headers, "items"):
        for key, value in headers.items():
            if str(key).lower() == "location":
                location = value
                break
    if not location:
        raise ValueError("missing Location header with broker order id")

    match = _LOCATION_RE.search(str(location))
    if match is None:
        raise ValueError("Location header missing broker order id")
    return match.group("id")


class SchwabHTTPError(RuntimeError):
    """Catchall for non-2xx Schwab REST responses with status_code attr."""

    def __init__(self, message: str, *, status_code: int, endpoint: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class SchwabAccountHashStaleError(SchwabHTTPError):
    """Raised on 404 from a hash-keyed path -- triggers H3 refresh+retry."""


class SchwabRateLimitedError(SchwabHTTPError):
    """Raised after _MAX_RETRY 429s -- backoff exceeded."""


class SchwabClient:
    """Wrapper around Schwabdev's async client. Owns token-driven HTTP."""

    def __init__(self, schwabdev_client: Any, token_cache: TokenCache) -> None:
        self._client = schwabdev_client
        self._tokens = token_cache
        self._sem = asyncio.Semaphore(_HTTP_CONCURRENCY)
        self._account_hashes: dict[str, str] = {}

    @classmethod
    def _seed_schwabdev_tokens_db(
        cls,
        tokens_db_path: str,
        *,
        token_cache: TokenCache,
    ) -> None:
        """Pre-write schwabdev's SQLite tokens table so its __init__ skips OAuth.

        Schema (from schwabdev/tokens.py:73-85):
            schwabdev (
              access_token_issued TEXT NOT NULL,
              refresh_token_issued TEXT NOT NULL,
              access_token TEXT NOT NULL,
              refresh_token TEXT NOT NULL,
              id_token TEXT NOT NULL,
              expires_in INTEGER, token_type TEXT, scope TEXT
            )

        We read tokens out of the TokenCache (which the backend populated via the
        Configure metadata). If access_token is empty (stale at boot), we write a
        placeholder so the NOT NULL constraint is satisfied; subsequent
        `_sync_tokens()` calls overwrite the in-memory schwabdev state with real
        tokens from the backend.
        """
        import sqlite3

        access_token = token_cache._access_token or "PLACEHOLDER_AWAITING_REFRESH"
        refresh_token = token_cache._refresh_token or ""
        if not refresh_token:
            log.warning("schwab_seed_tokens_db_skipped reason=no_refresh_token")
            return

        now = datetime.now(UTC).isoformat()
        access_issued_at = token_cache._access_issued_at
        access_issued_iso = (
            access_issued_at.isoformat() if access_issued_at is not None else now
        )

        try:
            conn = sqlite3.connect(tokens_db_path, check_same_thread=False)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schwabdev (
                    access_token_issued TEXT NOT NULL,
                    refresh_token_issued TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    id_token TEXT NOT NULL,
                    expires_in INTEGER,
                    token_type TEXT,
                    scope TEXT
                );
                """
            )
            cur.execute("DELETE FROM schwabdev")
            cur.execute(
                "INSERT INTO schwabdev VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    access_issued_iso,
                    now,  # refresh_token_issued: we don't track this; conservative.
                    access_token,
                    refresh_token,
                    "",  # id_token unused
                    1800,  # expires_in (30 min)
                    "Bearer",
                    "api",
                ),
            )
            conn.commit()
            conn.close()
            log.info("schwab_seed_tokens_db_ok path=%s", tokens_db_path)
        except sqlite3.Error as exc:
            log.warning("schwab_seed_tokens_db_failed path=%s err=%s", tokens_db_path, exc)

    @classmethod
    def from_credentials(
        cls,
        app_key: str,
        app_secret: str,
        token_cache: TokenCache,
    ) -> SchwabClient:
        """Construct a Schwabdev async client.

        schwabdev==3.0.3 has ClientAsync, but its constructor takes tokens_db,
        not tokens_file. Its Tokens.update_tokens() is not a local setter; it
        may mint tokens. C2 single-writer is preserved by never invoking it.

        BUT: schwabdev's Tokens.__init__ runs `update_tokens(force_refresh_token=True)`
        when the tokens_db is empty -- which calls `input()` and EOFErrors in our
        non-interactive container. We pre-seed the SQLite DB from token_cache so
        schwabdev's `_load_tokens_from_db()` succeeds and the OAuth path is skipped.
        """
        cls._seed_schwabdev_tokens_db(
            "/tmp/schwabdev_tokens.db",  # noqa: S108 (ephemeral, container-local)
            token_cache=token_cache,
        )
        client = schwabdev.ClientAsync(
            app_key=app_key,
            app_secret=app_secret,
            tokens_db="/tmp/schwabdev_tokens.db",
        )
        return cls(schwabdev_client=client, token_cache=token_cache)

    # Public API used by handlers

    async def ensure_fresh_token(self) -> None:
        """Pre-warm token sync. Mirrors the first half of _call."""
        access = await self._tokens.get_access_token()
        current_refresh = self._tokens._refresh_token or ""
        self._sync_tokens(access_token=access, refresh_token=current_refresh)

    async def get_account_numbers(self) -> list[dict[str, str]]:
        """GET /trader/v1/accountNumbers -- returns account_number <-> hash map."""
        return await self._call("/accountNumbers", self._client.linked_accounts)

    async def get_account_details(self, account_hash: str) -> dict[str, Any]:
        """GET /trader/v1/accounts/{hash}?fields=positions."""
        return await self._call(
            "/accounts",
            lambda: self._client.account_details(
                accountHash=account_hash,
                fields="positions",
            ),
        )

    async def get_orders(
        self,
        account_hash: str,
        from_dt: str,
        to_dt: str,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "/accounts.orders",
            lambda: self._client.account_orders(
                accountHash=account_hash,
                fromEnteredTime=from_dt,
                toEnteredTime=to_dt,
                maxResults=max_results,
            ),
        )

    async def place_order(
        self,
        *,
        account_hash: str,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        """POST /trader/v1/accounts/{hash}/orders."""
        resp = await self._call_raw(
            "/accounts.orders.place",
            lambda: self._client.place_order(accountHash=account_hash, order=payload),
        )
        return {"broker_order_id": _extract_broker_order_id(resp.headers)}

    async def cancel_order(self, *, account_hash: str, order_id: str) -> None:
        """DELETE /trader/v1/accounts/{hash}/orders/{orderId}."""
        await self._call(
            "/accounts.orders.cancel",
            lambda: self._client.cancel_order(
                accountHash=account_hash,
                orderId=order_id,
            ),
        )

    async def replace_order(
        self,
        *,
        account_hash: str,
        order_id: str,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        """PUT /trader/v1/accounts/{hash}/orders/{orderId}."""
        resp = await self._call_raw(
            "/accounts.orders.replace",
            lambda: self._client.replace_order(
                accountHash=account_hash,
                orderId=order_id,
                order=payload,
            ),
        )
        return {"broker_order_id": _extract_broker_order_id(resp.headers)}

    async def get_orders_since(
        self,
        account_hash: str,
        since_iso: str,
        max_results: int = 500,
    ) -> list[dict[str, Any]]:
        """GET orders without toEnteredTime for poller-style reads."""
        return await self._call(
            "/accounts.orders.since",
            lambda: self._client.account_orders(
                accountHash=account_hash,
                fromEnteredTime=since_iso,
                maxResults=max_results,
            ),
        )

    async def get_order(self, account_hash: str, order_id: str) -> dict[str, Any]:
        """GET /trader/v1/accounts/{hash}/orders/{orderId}."""
        return await self._call(
            "/accounts.orders.details",
            lambda: self._client.order_details(
                accountHash=account_hash,
                orderId=order_id,
            ),
        )

    async def search_instruments(
        self,
        query: str,
        projection: str = "symbol-search",
    ) -> list[dict[str, Any]]:
        raw = await self._call(
            "/instruments",
            lambda: self._client.instruments(symbols=query, projection=projection),
        )
        if isinstance(raw, dict):
            return list(raw.get("instruments") or [])
        return list(raw or [])

    # account_hash cache (H3)

    def cache_hashes(self, mapping: dict[str, str]) -> None:
        self._account_hashes = dict(mapping)

    def hash_for(self, account_number: str) -> str | None:
        return self._account_hashes.get(account_number)

    async def refresh_hashes(self, reason: str) -> dict[str, str]:
        """H3 -- refresh on rotation_detected / 404_retry."""
        SCHWAB_ACCOUNT_HASH_REFRESH_TOTAL.labels(reason=reason).inc()
        rows = await self.get_account_numbers()
        mapping = {
            r.get("accountNumber", ""): r.get("hashValue", "")
            for r in (rows or [])
            if r.get("accountNumber")
        }
        self.cache_hashes(mapping)
        return mapping

    # internals: 429 / retry / semaphore (M6)

    async def _call(self, endpoint: str, fn: Callable[[], Any]) -> Any:
        async with self._sem:
            access = await self._tokens.get_access_token()
            current_refresh = self._tokens._refresh_token or ""
            self._sync_tokens(access_token=access, refresh_token=current_refresh)

            for attempt in range(_MAX_RETRY + 1):
                resp = await fn()
                status = getattr(resp, "status_code", 200)
                SCHWAB_HTTP_REQUESTS_TOTAL.labels(
                    endpoint=endpoint,
                    status=str(status),
                ).inc()
                if status == 429:
                    if attempt == _MAX_RETRY:
                        raise SchwabRateLimitedError(
                            f"rate limit exceeded after {_MAX_RETRY} retries",
                            status_code=429,
                            endpoint=endpoint,
                        )
                    retry_after = float(resp.headers.get("Retry-After") or "1")
                    jitter = random.uniform(-0.1, 0.1)
                    await asyncio.sleep(retry_after + jitter)
                    continue
                if status == 404:
                    raise SchwabAccountHashStaleError(
                        f"{endpoint} 404 -- account_hash may have rotated",
                        status_code=404,
                        endpoint=endpoint,
                    )
                if status >= 400:
                    raise SchwabHTTPError(
                        f"{endpoint} returned status={status}",
                        status_code=status,
                        endpoint=endpoint,
                    )
                if hasattr(resp, "json"):
                    return resp.json()
                return resp
            raise SchwabRateLimitedError(
                "unreachable retry exhaustion",
                status_code=429,
                endpoint=endpoint,
            )

    async def _call_raw(self, endpoint: str, fn: Callable[[], Any]) -> Any:
        async with self._sem:
            access = await self._tokens.get_access_token()
            current_refresh = self._tokens._refresh_token or ""
            self._sync_tokens(access_token=access, refresh_token=current_refresh)

            for attempt in range(_MAX_RETRY + 1):
                resp = await fn()
                status = getattr(resp, "status_code", 200)
                SCHWAB_HTTP_REQUESTS_TOTAL.labels(
                    endpoint=endpoint,
                    status=str(status),
                ).inc()
                if status == 429:
                    if attempt == _MAX_RETRY:
                        raise SchwabRateLimitedError(
                            f"rate limit exceeded after {_MAX_RETRY} retries",
                            status_code=429,
                            endpoint=endpoint,
                        )
                    retry_after = float(resp.headers.get("Retry-After") or "1")
                    jitter = random.uniform(-0.1, 0.1)
                    await asyncio.sleep(retry_after + jitter)
                    continue
                if status == 404:
                    raise SchwabAccountHashStaleError(
                        f"{endpoint} 404 -- account_hash may have rotated",
                        status_code=404,
                        endpoint=endpoint,
                    )
                if status >= 400:
                    raise SchwabHTTPError(
                        f"{endpoint} returned status={status}",
                        status_code=status,
                        endpoint=endpoint,
                    )
                return resp
            raise SchwabRateLimitedError(
                "unreachable retry exhaustion",
                status_code=429,
                endpoint=endpoint,
            )

    def _sync_tokens(self, *, access_token: str, refresh_token: str) -> None:
        """Sync known backend tokens into schwabdev without minting new ones."""
        token_state = getattr(self._client, "tokens", None)
        if token_state is not None:
            token_state.access_token = access_token
            token_state.refresh_token = refresh_token

        session = getattr(self._client, "_session", None)
        headers = getattr(session, "headers", None)
        if headers is not None:
            headers["Authorization"] = f"Bearer {access_token}"
