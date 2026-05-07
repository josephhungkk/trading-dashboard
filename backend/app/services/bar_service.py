"""BarService orchestrator skeleton — full impl lands in Chunk D."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

_SOURCE_PRIORITY: Final[Mapping[str, int]] = {
    "schwab": 1,
    "alpaca": 2,
    "ibkr": 3,
    "futu": 4,
    "aggregator-schwab": 99,
    "aggregator-alpaca": 99,
    "aggregator-ibkr": 99,
    "aggregator-futu": 99,
}


def _priority_for_source(source: str) -> int:
    """Single chokepoint mapping source → source_priority for UPSERT WHERE clause."""
    if source not in _SOURCE_PRIORITY:
        raise ValueError(f"unknown bar source: {source!r}")
    return _SOURCE_PRIORITY[source]


class BarService:
    """Orchestrator skeleton; full impl arrives in Chunk D."""

    def __init__(self) -> None:
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            logger.info("bar_service.start")
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            logger.info("bar_service.stop")
            self._started = False
