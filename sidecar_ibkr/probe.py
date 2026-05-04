"""Standalone gRPC health probe for the IBKR sidecar."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import grpc  # type: ignore[import-untyped]
import grpc.aio  # type: ignore[import-untyped]

from sidecar_ibkr._generated.broker.v1 import broker_pb2, broker_pb2_grpc


async def _close_channel(channel: object) -> None:
    close = getattr(channel, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await cast("Awaitable[object]", result)


async def probe(
    *,
    label: str,
    host: str,
    port: int,
    client_cert: bytes,
    client_key: bytes,
    ca: bytes,
    timeout: float = 3.0,  # noqa: ASYNC109 - forwarded to the gRPC call timeout.
    print_fn: Callable[[str], None] = print,
    channel_factory: Callable[[str, grpc.ChannelCredentials], grpc.aio.Channel] | None = None,
) -> int:
    """Returns process exit code: 0 ok, 1 degraded/down."""
    target = f"{host}:{port}"
    credentials = grpc.ssl_channel_credentials(
        root_certificates=ca,
        private_key=client_key,
        certificate_chain=client_cert,
    )
    channel = (
        grpc.aio.secure_channel(target, credentials)
        if channel_factory is None
        else channel_factory(target, credentials)
    )

    try:
        stub = broker_pb2_grpc.BrokerStub(channel)
        response = await stub.Health(
            broker_pb2.HealthRequest(),
            timeout=timeout,
        )
    except grpc.aio.AioRpcError as exc:
        print_fn(f"[down] label={label} gw=false ver= reason={exc}")
        return 1
    except Exception as exc:
        print_fn(f"[down] label={label} gw=false ver= reason={exc}")
        return 1
    finally:
        await _close_channel(channel)

    gateway_version = str(response.gateway_version)
    if bool(response.gateway_connected):
        print_fn(f"[ok] label={label} gw=true ver={gateway_version}")
        return 0

    print_fn(f"[degraded] label={label} gw=false ver={gateway_version}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe an IBKR sidecar gRPC health endpoint")
    parser.add_argument("--label", required=True)
    parser.add_argument("--host", default="10.10.0.2")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-cert", required=True, type=Path)
    parser.add_argument("--client-key", required=True, type=Path)
    parser.add_argument("--ca", required=True, type=Path)
    parser.add_argument("--timeout", default=3.0, type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client_cert = cast("Path", args.client_cert).read_bytes()
    client_key = cast("Path", args.client_key).read_bytes()
    ca = cast("Path", args.ca).read_bytes()

    return asyncio.run(
        probe(
            label=cast("str", args.label),
            host=cast("str", args.host),
            port=cast("int", args.port),
            client_cert=client_cert,
            client_key=client_key,
            ca=ca,
            timeout=cast("float", args.timeout),
        )
    )


if __name__ == "__main__":
    sys.exit(main())
