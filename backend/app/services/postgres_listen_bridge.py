"""Postgres LISTEN → Redis PUBLISH bridge.

Subscribes to Postgres LISTEN channels and republishes payloads to Redis so
that in-process caches (ConfigCache etc.) can be invalidated cluster-wide.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)

_CHANNEL = "app_config:invalidate"
_BACKOFF_MAX = 30.0


def _sanitize_dsn(dsn: str) -> str:
    """Return ``host:port/database`` stripped of any credentials."""
    try:
        parts = urllib.parse.urlsplit(dsn)
        return f"{parts.hostname}:{parts.port}/{parts.path.lstrip('/')}"
    except Exception:
        return "<dsn-parse-error>"


@dataclass
class PostgresListenBridge:
    """Bridges Postgres LISTEN notifications to Redis PUBLISH."""

    dsn: str
    redis: Any
    _connected: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    def is_connected(self) -> bool:
        """Return True if currently connected to Postgres."""
        return self._connected

    def stop(self) -> None:
        """Signal the run loop to exit after the current reconnect cycle."""
        self._stopped = True
        self._stop_event.set()

    async def _on_notify(
        self,
        connection: Any,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        """Republish a Postgres notification to Redis.

        Failures are logged and swallowed so the listener is never crashed by
        a transient Redis error (Pattern C — per-callback isolation).
        """
        try:
            await self.redis.publish(_CHANNEL, payload)
            log.debug(
                "pg_listen_bridge.republished",
                channel=_CHANNEL,
                pid=pid,
            )
        except Exception as exc:
            log.warning(
                "pg_listen_bridge.publish_failed",
                channel=_CHANNEL,
                exc=str(exc),
            )

    async def run(self) -> None:
        """Reconnect loop with exponential backoff capped at _BACKOFF_MAX seconds."""
        backoff = 1.0
        while not self._stopped:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(self.dsn)
                self._connected = True
                backoff = 1.0
                log.info("pg_listen_bridge.connected", channel=_CHANNEL)

                await conn.add_listener(_CHANNEL, self._on_notify)
                # Block until stop() is called; asyncpg drives _on_notify in the
                # background. CancelledError is caught at the outer try.
                await self._stop_event.wait()

                await conn.remove_listener(_CHANNEL, self._on_notify)
            except (TimeoutError, asyncpg.PostgresError, OSError) as exc:
                self._connected = False
                log.warning(
                    "pg_listen_bridge.connection_error",
                    exc=str(exc),
                    dsn=_sanitize_dsn(self.dsn),
                    backoff=backoff,
                )
            except asyncio.CancelledError:
                self._connected = False
                log.info("pg_listen_bridge.cancelled")
                break
            finally:
                if conn is not None and not conn.is_closed():
                    try:
                        await conn.close()
                    except (asyncpg.PostgresError, OSError) as exc:
                        log.warning("pg_listen_bridge.close_error", exc=str(exc))
                self._connected = False

            if self._stopped:
                break

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                break
            except TimeoutError:
                pass
            backoff = min(backoff * 2, _BACKOFF_MAX)

        log.info("pg_listen_bridge.stopped")
