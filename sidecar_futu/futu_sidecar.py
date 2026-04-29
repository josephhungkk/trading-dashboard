"""futu-sidecar entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import signal
from datetime import UTC, datetime

import structlog
from grpc.aio import server as grpc_server

# Import for side-effect: registers BrokerServicer descriptors so the
# generated stubs are importable here. Handler registration lands in B2/B6.
from sidecar_futu._generated.broker.v1 import broker_pb2_grpc  # noqa: F401

log = structlog.get_logger(__name__)
BIND_ADDRESS = "10.10.0.2:18005"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--cert-dir", default=r"C:\dashboard\secrets")
    p.add_argument(
        "--simulator",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="SIM mode (default ON for safety; --no-simulator for real placement)",
    )
    return p.parse_args()


async def _serve(args: argparse.Namespace) -> None:
    started_at = datetime.now(UTC)
    server = grpc_server()
    server.add_insecure_port(BIND_ADDRESS)  # replaced with mTLS in B6
    log.info(
        "futu_sidecar_start",
        bind=BIND_ADDRESS,
        simulator=args.simulator,
        started_at=started_at.isoformat(),
    )
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.stop(grace=5)


def main() -> None:
    args = _parse_args()
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
