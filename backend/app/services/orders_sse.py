"""SSE generator for GET /api/orders/events.

Architecture
------------
- Replay missed events from ``order_events`` (id > Last-Event-ID header).
- Subscribe to the appropriate Redis pubsub channel.
- Run a heartbeat task (``': heartbeat\\n\\n'`` every 10 s) to prevent
  CF Tunnel idle-close (R10).
- Use a per-client ``asyncio.Queue(maxsize=1000)`` for backpressure.  When
  the queue is full the slow-client guard fires: we yield a final
  ``event: error`` frame, increment ``sse_dropped_clients_total``, and exit.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

log = structlog.get_logger(__name__)

_HEARTBEAT_INTERVAL: float = 10.0


def _format_sse(event_id: int | str, data: str) -> str:
    """Return an SSE frame: ``id: …\\nevent: order.update\\ndata: …\\n\\n``."""
    return f"id: {event_id}\nevent: order.update\ndata: {data}\n\n"


async def _replay_events(
    db: AsyncSession,
    last_event_id: int,
    account_id: UUID | None,
) -> AsyncGenerator[str]:
    """Yield SSE frames for all order_events with id > last_event_id."""
    if account_id is not None:
        stmt = text(
            """
            SELECT id,
                   jsonb_build_object(
                       'id', CAST(id AS text),
                       'event_id', id,
                       'order_id', order_id,
                       'account_id', account_id,
                       'broker_order_id', broker_order_id,
                       'status', status,
                       'filled_qty', filled_qty,
                       'avg_fill_price', avg_fill_price,
                       'broker_event_at', broker_event_at,
                       'observed_at', observed_at
                   )::text AS payload
              FROM order_events
             WHERE id > :last_id
               AND account_id = :account_id
             ORDER BY id
            """
        )
        result = await db.execute(stmt, {"last_id": last_event_id, "account_id": str(account_id)})
    else:
        stmt = text(
            """
            SELECT id,
                   jsonb_build_object(
                       'id', CAST(id AS text),
                       'event_id', id,
                       'order_id', order_id,
                       'account_id', account_id,
                       'broker_order_id', broker_order_id,
                       'status', status,
                       'filled_qty', filled_qty,
                       'avg_fill_price', avg_fill_price,
                       'broker_event_at', broker_event_at,
                       'observed_at', observed_at
                   )::text AS payload
              FROM order_events
             WHERE id > :last_id
             ORDER BY id
            """
        )
        result = await db.execute(stmt, {"last_id": last_event_id})

    rows = result.fetchall()
    for row in rows:
        yield _format_sse(row[0], row[1])


async def _heartbeat_pump(
    queue: asyncio.Queue[str],
    interval: float = _HEARTBEAT_INTERVAL,
) -> None:
    """Put a heartbeat comment into *queue* every *interval* seconds."""
    while True:
        await asyncio.sleep(interval)
        try:
            queue.put_nowait(": heartbeat\n\n")
        except asyncio.QueueFull:
            # Main loop will handle the slow-client path.
            return


async def _pubsub_pump(pubsub: Any, queue: asyncio.Queue[str]) -> None:
    """Forward Redis pubsub messages to *queue* as SSE frames."""
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        data: str = message["data"]
        if isinstance(data, bytes):
            data = data.decode()
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            log.warning("orders_sse.invalid_json", raw=data[:200])
            continue
        event_id = parsed.get("event_id") or parsed.get("id") or ""
        try:
            queue.put_nowait(_format_sse(event_id, data))
        except asyncio.QueueFull:
            # Signal the main generator to drop the slow client.
            return


async def order_events_generator(
    request: Any,
    db: AsyncSession,
    redis: Any,
    last_event_id: int,
    account_id: UUID | None,
) -> AsyncGenerator[str]:
    """Main SSE generator.

    Increments ``sse_active_connections`` on entry, decrements on exit
    (including slow-client drops).
    """
    channel = f"orders:events:account:{account_id}" if account_id else "orders:events:fleet"
    metrics.sse_active_connections.inc()
    client_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
    slow_client = False

    try:
        # 1. Replay missed events.
        async for chunk in _replay_events(db, last_event_id, account_id):
            yield chunk

        # 2. Subscribe to pubsub and start background pumps.
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        heartbeat_task = asyncio.create_task(
            _heartbeat_pump(client_queue),
            name="sse-heartbeat",
        )
        pump_task = asyncio.create_task(
            _pubsub_pump(pubsub, client_queue),
            name="sse-pubsub-pump",
        )

        try:
            while True:
                if await request.is_disconnected():
                    break
                # Both pumps put into client_queue; QueueFull exits via return
                # inside the pump, which causes pump_task to finish.  We check
                # that here to detect the slow-client case.
                if pump_task.done() or heartbeat_task.done():
                    # One of the pumps returned early — queue was full.
                    slow_client = True
                    break
                try:
                    msg = client_queue.get_nowait()
                    yield msg
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)
        finally:
            heartbeat_task.cancel()
            pump_task.cancel()
            await pubsub.unsubscribe(channel)

        if slow_client:
            metrics.sse_dropped_clients_total.inc()
            yield 'event: error\ndata: {"reason":"slow_client"}\n\n'

    finally:
        metrics.sse_active_connections.dec()
