"""SSE endpoint — forwards Redis pub/sub `config:invalidate:<ns>` to clients.

Hardened against Redis disconnects + client cancellation per architect HIGH-4.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.core.deps import get_redis, require_admin_jwt

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin", "sse"])


@router.get("/config/stream", dependencies=[Depends(require_admin_jwt)])
async def config_stream(
    request: Request,
    ns: Annotated[str, Query(pattern=r"^[a-z0-9_]{1,32}$")],
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    async def event_gen() -> AsyncIterator[str]:
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"config:invalidate:{ns}")
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    log.debug("sse_client_disconnect ns=%s", ns)
                    return
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=30.0,
                    )
                except (ConnectionError, TimeoutError) as e:
                    log.warning("sse_pubsub_error ns=%s err=%s", ns, e)
                    return
                except asyncio.CancelledError:
                    raise
                if msg is None:
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps({"ns": ns, "event": "invalidate"})
                yield f"data: {payload}\n\n"
        finally:
            try:
                await pubsub.unsubscribe(f"config:invalidate:{ns}")
                await pubsub.aclose()  # type: ignore[no-untyped-call]
            except Exception:
                log.exception("sse_pubsub_cleanup_failed ns=%s", ns)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
