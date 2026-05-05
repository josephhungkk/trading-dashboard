"""Alpaca sidecar entrypoint."""

from __future__ import annotations

import asyncio
import logging

import grpc
import structlog

from sidecar_alpaca import config
from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2_grpc
from sidecar_alpaca.metrics import ALPACA_SIDECAR_UPTIME_SECONDS

log = structlog.get_logger(module="sidecar_alpaca.main")


async def serve() -> None:
    try:
        auth_cache = AuthCache()
    except (ValueError, RuntimeError) as exc:
        log.error("alpaca_auth_cache_init_failed", exc_info=exc)
        raise

    ALPACA_SIDECAR_UPTIME_SECONDS.labels(mode=config.MODE).set_to_current_time()

    server = grpc.aio.server()
    broker_pb2_grpc.add_BrokerServicer_to_server(AlpacaServicer(auth_cache), server)

    listen_addr = f"0.0.0.0:{config.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    log.info("sidecar_alpaca_starting", listen_addr=listen_addr)
    await server.start()
    await server.wait_for_termination()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
