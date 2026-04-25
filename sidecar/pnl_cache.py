from __future__ import annotations

import asyncio
from decimal import Decimal
from math import isnan
from typing import TYPE_CHECKING, cast

import structlog

if TYPE_CHECKING:
    from ib_async import (  # type: ignore[import-untyped, unused-ignore]  # ib_async ships no type stubs.
        IB,
        PnLSingle,
    )

_LOG = structlog.get_logger(__name__)


class PnLCache:
    """
    Cache live-updating PnLSingle proxies keyed by (account, conid).

    First call to .get(account, conid) issues ib.reqPnLSingleAsync(account, '', conid)
    and caches the resulting PnLSingle. Its .unrealizedPnL/.realizedPnL/.dailyPnL
    auto-update via ib_async events. Returns NaN until ~30s after first subscribe.
    """

    def __init__(self, ib: IB) -> None:
        self._ib = ib
        self._cache: dict[tuple[str, int], PnLSingle] = {}
        # HIGH-1: in-flight subscriptions keyed by (account, conid). When two
        # concurrent gRPC handlers race for the same key, only one issues
        # reqPnLSingleAsync; the other awaits the shared future. Without this,
        # both fire reqPnLSingleAsync and the first PnLSingle leaks (it keeps
        # updating but is never cancelled by cancel_all).
        self._inflight: dict[tuple[str, int], asyncio.Future[PnLSingle]] = {}

    async def get(self, account: str, conid: int) -> PnLSingle:
        key = (account, conid)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[PnLSingle] = loop.create_future()
        self._inflight[key] = fut
        _LOG.debug("pnl_subscribe", account=account, conid=conid)
        try:
            raw = await self._ib.reqPnLSingleAsync(account, "", conid)  # type: ignore[attr-defined]  # ib_async is untyped.
            pnl: PnLSingle = cast("PnLSingle", raw)
            self._cache[key] = pnl
            fut.set_result(pnl)
            return pnl
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)

    async def cancel_all(self) -> None:
        # CR-6: per-iteration try/except so cancel_all clears the cache even
        # when the gateway socket is already dead (cancelPnLSingle then raises
        # ConnectionError). MED-7: dropped the asyncio.sleep(0) yield — it
        # suggested rate-limiting/IO-flushing intent it never accomplished.
        for account, conid in list(self._cache):
            try:
                self._ib.cancelPnLSingle(account, "", conid)
            except Exception:
                _LOG.warning("pnl_cancel_failed", account=account, conid=conid)
        self._cache.clear()

    def snapshot(
        self, account: str, conid: int
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        key = (account, conid)
        if key not in self._cache:
            return (None, None, None)
        pnl = self._cache[key]

        def _d(value: float | None) -> Decimal | None:
            if value is None or isnan(value):
                return None
            return Decimal(str(value))

        return (_d(pnl.unrealizedPnL), _d(pnl.realizedPnL), _d(pnl.dailyPnL))
