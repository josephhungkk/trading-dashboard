"""Owns OpenSecTradeContext lifecycle, cred caching, in-flight init cancellation."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import structlog
from cryptography.hazmat.primitives.serialization import load_pem_private_key

log = structlog.get_logger(__name__)

_MD5_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass(frozen=True)
class FutuCreds:
    unlock_pwd_md5: str
    rsa_priv_pem: str
    opend_host: str
    opend_port: int
    connection_id: str


class FutuClient:
    """Holds creds and the OpenD connection init task."""

    def __init__(self) -> None:
        self._creds: FutuCreds | None = None
        self._init_task: asyncio.Task[None] | None = None
        self._trade_ctx: Any | None = None
        self.gateway_connected: bool = False
        self._order_event_queues: dict[str, list[asyncio.Queue[Any]]] = {}

    def validate(self, request: Any) -> str | None:
        """Return error detail string on rejection, None on success."""
        if not _MD5_PATTERN.match(request.unlock_pwd_md5):
            return "invalid_unlock_pwd_md5"
        try:
            load_pem_private_key(request.rsa_priv_pem.encode(), password=None)
        except Exception:  # noqa: BLE001, RUF100
            return "invalid_rsa_pem"
        return None

    async def configure(self, request: Any) -> None:
        """Cache creds and restart the InitConnect background task."""
        if self._init_task is not None and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("futu_init_task_cleanup_error", error=str(exc))

        self._creds = FutuCreds(
            unlock_pwd_md5=request.unlock_pwd_md5,
            rsa_priv_pem=request.rsa_priv_pem,
            opend_host=request.opend_host,
            opend_port=request.opend_port,
            connection_id=request.connection_id,
        )
        self.gateway_connected = False
        self._init_task = asyncio.create_task(
            self._init_loop(),
            name="futu-init-connect",
        )

    async def _init_loop(self) -> None:
        """Stub: B4 replaces this with the real InitConnect loop."""
        log.info("futu_init_loop_stub", host=self._creds.opend_host if self._creds else None)
        await asyncio.sleep(60)
