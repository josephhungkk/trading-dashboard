"""Credential cache for Alpaca Configure payloads."""

from __future__ import annotations

import asyncio
from typing import NamedTuple

import structlog

from sidecar_alpaca import config

log = structlog.get_logger(module="sidecar_alpaca.auth")


class Credentials(NamedTuple):
    api_key: str
    api_secret: str


class AuthCache:
    """Atomic in-memory credential holder."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._credentials: Credentials | None = None
        self._mode = config.MODE

    @property
    def mode(self) -> str:
        return self._mode

    async def set_credentials(self, api_key: str, api_secret: str) -> None:
        try:
            credentials = self._validate_credentials(api_key, api_secret)
        except (ValueError, RuntimeError) as exc:
            await self.clear()
            log.warning("alpaca_credentials_rejected", exc_info=exc)
            raise

        async with self._lock:
            self._credentials = credentials

    async def get_credentials(self) -> Credentials:
        async with self._lock:
            if self._credentials is None:
                raise RuntimeError("alpaca credentials not configured")
            return self._credentials

    async def clear(self) -> None:
        async with self._lock:
            self._credentials = None

    @staticmethod
    def _validate_credentials(api_key: str, api_secret: str) -> Credentials:
        if not api_key:
            raise ValueError("missing alpaca api_key")
        if not api_secret:
            raise ValueError("missing alpaca api_secret")
        return Credentials(api_key=api_key, api_secret=api_secret)
