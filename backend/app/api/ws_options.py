"""Phase 12: WebSocket endpoint for live option chain updates."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.options.chain_service import OptionChainService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ws-options"])

_CONFLATION_HZ = 2
_HEARTBEAT_SECONDS = 30
_CONNECTION_CAP = 10
_active_connections = 0


def get_chain_service(websocket: WebSocket) -> OptionChainService:
    redis = websocket.app.state.redis
    config = getattr(websocket.app.state, "config", None)
    return OptionChainService(redis=redis, config=config, broker_registry=None)


@router.websocket("/ws/options/chain")
async def ws_options_chain(
    websocket: WebSocket,
    symbol: str = Query(...),
    expiry: str = Query(...),
) -> None:
    global _active_connections
    if _active_connections >= _CONNECTION_CAP:
        await websocket.close(code=1008, reason="connection_cap_reached")
        return

    await websocket.accept()
    _active_connections += 1
    log.info("ws_options_chain_connected", symbol=symbol, expiry=expiry)

    try:
        svc = get_chain_service(websocket)
        expiry_date = date.fromisoformat(expiry)
        last_heartbeat = time.monotonic()
        interval = 1.0 / _CONFLATION_HZ

        while True:
            try:
                chain = await svc.get_chain(symbol, expiry_date, currency="USD")
                await websocket.send_text(json.dumps({"type": "chain", **chain}))
            except Exception as exc:
                log.warning("ws_options_chain_fetch_failed", error=str(exc))
                await websocket.send_text(json.dumps({"type": "stale"}))

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
                last_heartbeat = now

            await asyncio.sleep(interval)

    except WebSocketDisconnect:
        log.info("ws_options_chain_disconnected", symbol=symbol)
    finally:
        _active_connections -= 1
