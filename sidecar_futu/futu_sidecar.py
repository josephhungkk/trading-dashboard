"""futu-sidecar entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import signal
from datetime import UTC, datetime
from pathlib import Path

import structlog
from grpc.aio import server as grpc_server  # type: ignore[import-untyped]

from sidecar_futu._generated.broker.v1 import broker_pb2_grpc
from sidecar_futu.handlers import BrokerHandlers
from sidecar_futu.tls import (
    assert_key_file_permissions,
    build_grpc_server_credentials,
    server_options_for_tls13,
    start_crl_reloader,
)

log = structlog.get_logger(__name__)
BIND_ADDRESS = "10.10.0.2:18005"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Futu gRPC sidecar")
    p.add_argument("--tls-cert-pem", type=Path, required=True)
    p.add_argument("--tls-key-pem", type=Path, required=True)
    p.add_argument("--tls-ca-bundle-pem", type=Path, required=True)
    p.add_argument("--tls-crl-pem", type=Path, required=True)
    p.add_argument(
        "--simulator",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="SIM mode (default ON for safety; --no-simulator for real placement)",
    )
    return p.parse_args()


async def _serve(args: argparse.Namespace) -> None:
    started_at = datetime.now(UTC)
    handlers = BrokerHandlers(started_at=started_at, simulator=args.simulator)

    # Permission guard before any read so a world-readable key isn't loaded.
    assert_key_file_permissions(args.tls_key_pem)
    cert_pem = args.tls_cert_pem.read_bytes()
    key_pem = args.tls_key_pem.read_bytes()
    ca_bundle_pem = args.tls_ca_bundle_pem.read_bytes()
    crl_pem = args.tls_crl_pem.read_bytes()
    creds = build_grpc_server_credentials(cert_pem, key_pem, ca_bundle_pem, crl_pem)

    server = grpc_server(options=server_options_for_tls13())
    broker_pb2_grpc.add_BrokerServicer_to_server(handlers, server)
    server.add_secure_port(BIND_ADDRESS, creds)

    log.info(
        "futu_sidecar_start",
        bind=BIND_ADDRESS,
        simulator=args.simulator,
        started_at=started_at.isoformat(),
    )
    await server.start()

    crl_task = await start_crl_reloader(args.tls_crl_pem, ca_bundle_pem, server)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows asyncio doesn't support add_signal_handler — fall back to
        # the synchronous signal.signal handler. Mirrors sidecar_ibkr/ibkr_sidecar.py.
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _sig, _frame: stop.set())
    await stop.wait()

    crl_task.cancel()
    try:
        await crl_task
    except asyncio.CancelledError:
        pass
    await server.stop(grace=5)


def main() -> None:
    args = _parse_args()
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
