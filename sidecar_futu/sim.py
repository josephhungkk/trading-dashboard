"""Synthetic SIM-mode order events."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu._generated.broker.v1 import broker_pb2


def make_sim_id() -> str:
    return f"SIM-{uuid.uuid4()}"


def _now_timestamp() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(UTC))
    return ts


def synthetic_place_event(
    *, broker_order_id: str, client_order_id: str
) -> broker_pb2.OrderEventMessage:
    return broker_pb2.OrderEventMessage(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        status="submitted",
        event_at=_now_timestamp(),
        kind="order",
    )


def synthetic_cancel_event(
    *, broker_order_id: str, client_order_id: str
) -> broker_pb2.OrderEventMessage:
    return broker_pb2.OrderEventMessage(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        status="cancelled",
        event_at=_now_timestamp(),
        kind="order",
    )


def dispatch(
    queues: list[asyncio.Queue[broker_pb2.OrderEventMessage]],
    event: broker_pb2.OrderEventMessage,
) -> None:
    for queue in queues:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
