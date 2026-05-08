"""Schwab sidecar entrypoint — asyncio gRPC server, plain TCP (no mTLS;
sidecar lives on same docker network as backend per spec §3.1)."""
# ruff: noqa: E402
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

# The auto-generated broker_pb2_grpc.py does `from broker.v1 import broker_pb2`
# (a sibling-relative import without the sidecar_schwab prefix). Add the
# _generated directory to sys.path so that bare import resolves at module
# load time. Mirrors sidecar_alpaca/handlers.py.
_GENERATED_ROOT = Path(__file__).resolve().parent / "_generated"
if str(_GENERATED_ROOT) not in sys.path:
    sys.path.insert(0, str(_GENERATED_ROOT))

import grpc
import structlog
from grpc_reflection.v1alpha import reflection

from sidecar_schwab._generated.broker.v1 import (
    broker_pb2,
    broker_pb2_grpc,
)
from sidecar_schwab.config import resolve_port
from sidecar_schwab.handlers import BrokerServicer

log = structlog.get_logger(module="sidecar_schwab.main")


async def serve() -> None:
    port = resolve_port()
    server = grpc.aio.server()

    servicer = BrokerServicer()
    broker_pb2_grpc.add_BrokerServicer_to_server(servicer, server)

    SERVICE_NAMES = (
        broker_pb2.DESCRIPTOR.services_by_name["Broker"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)

    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)
    log.info("sidecar_schwab_starting", listen_addr=listen_addr)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    log.info("sidecar_schwab_stopping")
    await server.stop(grace=10.0)
    # HIGH-code-3: close the backend gRPC channel on clean shutdown to prevent fd leaks.
    if servicer._backend_channel is not None:
        import contextlib as _cl
        with _cl.suppress(Exception):
            await servicer._backend_channel.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
