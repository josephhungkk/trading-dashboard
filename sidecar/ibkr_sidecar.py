"""Entrypoint skeleton for a single IBKR gateway sidecar."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import signal
import socket
import sys
import time
from collections.abc import Callable
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import Any

import grpc.aio  # type: ignore[import-untyped]
import structlog

from sidecar import __version__
from sidecar._generated.broker.v1 import broker_pb2_grpc
from sidecar.backoff import (
    apply_startup_backoff,
    clear_failure,
    read_previous_delay,
    record_failure,
)
from sidecar.handlers import BrokerHandlers
from sidecar.pnl_cache import PnLCache
from sidecar.tls import (
    assert_key_file_permissions,
    build_grpc_server_credentials,
    server_options_for_tls13,
    start_crl_reloader,
)

SIDECAR_VERSION = __version__
_REDACT_KEY = re.compile(r"^(password|secret|token|tls_key|private_key|api_key)$")
_REDACTED = "[REDACTED]"
_LOG = structlog.get_logger(__name__)


def _env(name: str) -> str | None:
    return os.environ.get(name) or os.environ.get(f"IBKR_SIDECAR_{name}")


def _env_int(name: str) -> int | None:
    value = _env(name)
    if value is None:
        return None
    return int(value)


def _default_log_dir(label: str) -> Path:
    if os.name == "nt":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(base) / "dashboard" / f"sidecar-{label}"
    return Path(f"/tmp/sidecar-{label}")


def _redact_value(value: object) -> object:
    """Recursively redact secret-named keys inside dict/list values.

    MED-2: a flat top-level redactor missed nested secrets like
    `log.info("config_loaded", broker={"password": "secret"})`.
    """
    if isinstance(value, dict):
        return {
            key: _REDACTED if _REDACT_KEY.match(str(key)) else _redact_value(inner)
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _redact_processor(
    logger: logging.Logger, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    del logger, method_name
    return {
        key: _REDACTED if _REDACT_KEY.match(str(key)) else _redact_value(value)
        for key, value in event_dict.items()
    }


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        log_dir / "sidecar.log",
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IBKR gRPC sidecar")
    parser.add_argument("--label", default=_env("LABEL"))
    parser.add_argument("--gateway-port", type=int, default=_env_int("GATEWAY_PORT"))
    parser.add_argument("--grpc-port", type=int, default=_env_int("GRPC_PORT"))
    parser.add_argument("--tls-cert-pem", type=Path, default=_path_env("TLS_CERT_PEM"))
    parser.add_argument("--tls-key-pem", type=Path, default=_path_env("TLS_KEY_PEM"))
    parser.add_argument("--tls-ca-bundle-pem", type=Path, default=_path_env("TLS_CA_BUNDLE_PEM"))
    parser.add_argument("--tls-crl-pem", type=Path, default=_path_env("TLS_CRL_PEM"))
    parser.add_argument("--log-dir", type=Path, default=_path_env("LOG_DIR"))
    parser.add_argument("--state-dir", type=Path, default=_path_env("STATE_DIR"))
    return parser


def _path_env(name: str) -> Path | None:
    value = _env(name)
    if value is None:
        return None
    return Path(value)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    label = args.label or "default"
    if args.log_dir is None:
        args.log_dir = _default_log_dir(label)
    if args.state_dir is None:
        args.state_dir = args.log_dir / "state"
    return args


def _is_client_id_in_use_str(exc: BaseException) -> bool:
    """Substring fallback while run() is a stub.

    TODO(task14): replace with a listener on `ib.client.apiError` that captures
    the IBKR error message out-of-band (per CR-2: ib_async raises
    asyncio.TimeoutError on clientId collisions whose str(...) is empty,
    so this substring match returns False for every real collision).
    Until Task 14 wires the real connection, this fallback is harmless
    because run() never connects.
    """
    message = str(exc).lower()
    return "clientid" in message and "in use" in message


def _fnv1a32(data: bytes) -> int:
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


async def _disconnect_watchdog(ib: Any, stop: asyncio.Event) -> None:
    last_connected = time.time()
    while not stop.is_set():
        try:
            if ib.isConnected():
                last_connected = time.time()
            elif time.time() - last_connected > 30:
                _LOG.warning("disconnect_watchdog_timeout")
                sys.exit(64)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            continue
        return


async def run(args: argparse.Namespace) -> None:
    log = structlog.get_logger(__name__).bind(label=args.label)

    cert_pem = args.tls_cert_pem.read_bytes()
    key_pem = args.tls_key_pem.read_bytes()
    ca_bundle_pem = args.tls_ca_bundle_pem.read_bytes()
    crl_pem = args.tls_crl_pem.read_bytes()

    from ib_async import IB  # type: ignore[import-untyped, unused-ignore]

    client_id = (_fnv1a32((socket.gethostname() + "|" + args.label).encode()) % 900) + 100

    ib = IB()
    pnl_cache = PnLCache(ib)
    last_tick_ref: dict[str, datetime] = {}
    accounts: list[str] = []

    server: grpc.aio.Server | None = None
    watchdog_task: asyncio.Task[None] | None = None
    crl_task: asyncio.Task[None] | None = None

    try:
        await ib.connectAsync("127.0.0.1", args.gateway_port, clientId=client_id, timeout=30)
        log.info("ibkr_connected", clientId=client_id, gateway_port=args.gateway_port)

        # ib_async populates managedAccounts() during connectAsync; modern API
        # has no reqManagedAccountsAsync(). Brief sleep ensures the initial
        # accountList message has been parsed before we read.
        await asyncio.sleep(0.5)
        accounts = list(ib.managedAccounts())
        # No-arg reqAccountSummaryAsync() — modern ib_async dropped the
        # group/tags kwargs; the default subscription includes the tags the
        # handlers care about (NetLiquidation, TotalCashValue, BASE, etc.).
        await ib.reqAccountSummaryAsync()

        server = grpc.aio.server(options=server_options_for_tls13())
        creds = build_grpc_server_credentials(cert_pem, key_pem, ca_bundle_pem, crl_pem)
        bind_addr = f"10.10.0.2:{args.grpc_port}"
        port = server.add_secure_port(bind_addr, creds)
        if port == 0:
            raise RuntimeError(f"failed to bind {bind_addr}")
        broker_pb2_grpc.add_BrokerServicer_to_server(
            BrokerHandlers(
                ib=ib,
                pnl_cache=pnl_cache,
                label=args.label,
                version=SIDECAR_VERSION,
                last_tick_ref=last_tick_ref,
            ),
            server,
        )

        crl_task = await start_crl_reloader(args.tls_crl_pem, ca_bundle_pem, server)

        await server.start()
        log.info("grpc_server_started", bind=bind_addr, accounts=len(accounts))

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda _sig, _frame: stop.set())

        watchdog_task = asyncio.create_task(
            _disconnect_watchdog(ib, stop), name="ibkr-watchdog"
        )

        await stop.wait()

    finally:
        log.info("shutdown_start")
        try:
            await pnl_cache.cancel_all()
        except Exception as exc:
            log.error("pnl_cancel_failed", error=str(exc))
        for account in accounts:
            try:
                ib.cancelAccountSummary(account)  # type: ignore[attr-defined]
            except Exception as exc:
                log.error("summary_cancel_failed", account=account, error=str(exc))
        try:
            ib.disconnect()
        except Exception as exc:
            log.error("ib_disconnect_failed", error=str(exc))
        if server is not None:
            try:
                await server.stop(grace=5)
            except Exception as exc:
                log.error("grpc_drain_failed", error=str(exc))
        if watchdog_task is not None:
            watchdog_task.cancel()
        if crl_task is not None:
            crl_task.cancel()
        log.info("shutdown_complete")


def _sync_signal_handler(stop: asyncio.Event) -> Callable[[int, FrameType | None], None]:
    def _handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    return _handler


def main() -> int:
    args = _parse_args()
    configure_logging(args.log_dir)
    log = structlog.get_logger(__name__).bind(label=args.label, version=SIDECAR_VERSION)
    state_dir = args.state_dir
    prev_delay = read_previous_delay(state_dir)  # HIGH-10: single parser source-of-truth

    # HIGH-5: refuse to start if the private key file is world-readable.
    # provision-sidecar-mtls.ps1 sets restrictive ACLs at provisioning time;
    # this is the runtime guard against ACL drift.
    if args.tls_key_pem is not None:
        try:
            assert_key_file_permissions(args.tls_key_pem)
        except RuntimeError as exc:
            record_failure(state_dir, prev_delay)
            log.critical("private_key_permissions_unsafe", error=str(exc))
            return 1

    try:
        apply_startup_backoff(state_dir)
        asyncio.run(run(args))
    except KeyboardInterrupt:
        clear_failure(state_dir)
        return 0
    except SystemExit as exc:
        # CR-5: clean-relaunch signals (code 64 from CRL rotation, future
        # clientId-collision exits) must NOT trigger backoff. Only mangled
        # codes go through record_failure.
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            clear_failure(state_dir)
            return 0
        if code == 64:
            log.info("sidecar_relaunch_requested", code=code)
            return 64
        record_failure(state_dir, prev_delay)
        log.error("sidecar_exited_unclean", code=code)
        return code
    except Exception as exc:
        if _is_client_id_in_use_str(exc):
            log.error("client_id_in_use", error=str(exc))
            return 64
        record_failure(state_dir, prev_delay)
        log.exception("sidecar_failed", error=str(exc))
        return 1

    clear_failure(state_dir)
    log.info("sidecar_shutdown_clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
