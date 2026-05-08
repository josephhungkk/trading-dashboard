"""mTLS helpers for the IBKR sidecar gRPC server."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import grpc  # type: ignore[import-untyped]  # grpcio does not ship upstream type stubs.
import grpc.aio  # type: ignore[import-untyped]  # grpcio does not ship upstream type stubs.
import structlog
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from sidecar_ibkr import metrics

_LOG = structlog.get_logger(__name__)

# MED-4: keep strong refs to background tasks so they aren't GC'd before completion.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _assert_windows_acl(key_path: Path) -> None:
    """Verify that the private key file has no broad-access ACEs on Windows.

    H4: runs ``icacls`` and rejects if "Everyone:" or "BUILTIN\\Users:" appear
    in the output, which indicates the key is accessible to non-privileged
    accounts. Raises ``RuntimeError`` on both ACL violations and icacls
    subprocess failures. Always callable (no os.name guard) so unit tests can
    exercise it on any platform via monkeypatching.
    """
    result = subprocess.run(
        ["icacls", str(key_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"icacls check failed: {result.stderr}")
    for bad in ("Everyone:", "BUILTIN\\Users:"):
        if bad in result.stdout:
            raise RuntimeError(
                f"Private key {key_path} has overly-permissive ACL ({bad!r} found); aborting."
            )


def assert_key_file_permissions(key_path: Path) -> None:
    """Refuse to load a private key that is world-readable.

    HIGH-5: provision-sidecar-mtls.ps1 sets restrictive ACLs at provisioning
    time; this is the runtime guard against ACL drift.
    - POSIX: mode-bit check (S_IRWXO).
    - Windows: icacls ACL check via ``_assert_windows_acl`` (H4).
    """
    if os.name == "nt":
        _assert_windows_acl(key_path)
        return
    mode = key_path.stat().st_mode
    if mode & stat.S_IRWXO:
        raise RuntimeError(
            f"Private key {key_path} is world-readable (mode={oct(mode)}); aborting."
        )


def server_options_for_tls13() -> list[tuple[str, int | str]]:
    """gRPC server channel options enforcing TLS 1.3 minimum.

    CR-4: must be passed to ``grpc.aio.server(options=...)`` at server creation;
    the credentials object alone does not constrain the protocol version.
    grpcio uses BoringSSL internally and does not accept an ssl.SSLContext.

    Caller (Task 14 wiring) is responsible for applying these options:

        server = grpc.aio.server(options=server_options_for_tls13())
    """
    # 1 = TLS_1_3 in grpc C-core enum (0 = TLS_1_2 default).
    return [("grpc.tls_minimum_version", 1)]


def _verify_crl(crl_pem: bytes, ca_bundle_pem: bytes) -> x509.CertificateRevocationList:
    """Load and verify a CRL is signed by the CA bundle and isn't expired.

    CR-3: parsing alone does not authenticate; an attacker with write access
    to crl.pem could install a forged/empty CRL otherwise.
    HIGH-4: a missing or expired ``next_update`` indicates a stale or
    malformed CRL — refuse missing; warn loud on expired.
    """
    ca_certs = x509.load_pem_x509_certificates(ca_bundle_pem)
    if not ca_certs:
        raise ValueError("Empty CA bundle")
    ca_cert = ca_certs[0]
    crl = x509.load_pem_x509_crl(crl_pem)

    if crl.issuer != ca_cert.subject:
        raise ValueError(f"CRL issuer {crl.issuer} does not match CA subject {ca_cert.subject}")
    # cryptography accepts DSA/RSA/EC/Ed25519/Ed448 here; the static return type
    # of public_key() also includes X25519/X448 (key-agreement only) which would
    # never appear on a real CA. Ignore the union mismatch.
    if not crl.is_signature_valid(ca_cert.public_key()):  # type: ignore[arg-type]
        raise ValueError("CRL signature verification failed (possible forgery)")

    next_update = crl.next_update_utc
    if next_update is None:
        raise ValueError("CRL has no nextUpdate field; refusing")
    now = datetime.now(tz=UTC)
    if next_update < now:
        # Stale CRL is operationally dangerous (revocations after this_update
        # would be missed) but hard-failing here would block the sidecar from
        # starting just because nobody rotated the CRL on time. Warn loud
        # instead and let the operator notice via metrics + log alerts.
        _LOG.warning(
            "crl_expired",
            next_update=next_update.isoformat(),
            now=now.isoformat(),
        )
        # M5: expose staleness so Prometheus/alerting can fire on aged CRLs.
        metrics.crl_stale_seconds.set((now - next_update).total_seconds())
    else:
        metrics.crl_stale_seconds.set(0)

    return crl


def _validate_pem_material(
    cert_pem: bytes,
    key_pem: bytes,
    ca_bundle_pem: bytes,
    crl_pem: bytes,
) -> None:
    """Validate startup PEM material end-to-end.

    HIGH-3: load_pem_x509_certificates (plural) so multi-cert CA bundles get
    every cert validated, not just the first.
    HIGH-11: confirm the server cert and private key form a matching pair.
    CR-3: verify the CRL signature against the CA.
    """
    server_cert = x509.load_pem_x509_certificate(cert_pem)
    ca_certs = x509.load_pem_x509_certificates(ca_bundle_pem)
    if not ca_certs:
        raise ValueError("Empty CA bundle")
    private_key = serialization.load_pem_private_key(key_pem, password=None)

    cert_pub_der = server_cert.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    key_pub_der = private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    if cert_pub_der != key_pub_der:
        raise ValueError("Server cert and private key do not form a matching pair")

    _verify_crl(crl_pem, ca_bundle_pem)


def build_grpc_server_credentials(
    cert_pem: bytes,
    key_pem: bytes,
    ca_bundle_pem: bytes,
    crl_pem: bytes,
) -> grpc.ServerCredentials:
    """Build mTLS-required gRPC server credentials.

    Note: TLS 1.3 minimum is NOT enforced by these credentials; it must be
    set via ``grpc.aio.server(options=server_options_for_tls13())`` at server
    creation (CR-4). This function only sets up the certificate/key/CA chain
    and validates the CRL signature against the CA (via ``_validate_pem_material``).
    """
    _validate_pem_material(cert_pem, key_pem, ca_bundle_pem, crl_pem)
    return grpc.ssl_server_credentials(
        private_key_certificate_chain_pairs=((key_pem, cert_pem),),
        root_certificates=ca_bundle_pem,
        require_client_auth=True,
    )


async def start_crl_reloader(
    crl_path: Path,
    ca_bundle_pem: bytes,
    server: grpc.aio.Server,
    every_seconds: int = 60,
) -> asyncio.Task[None]:
    """Start a CRL polling task that exits the process when revocation material changes.

    HIGH-7: takes ``ca_bundle_pem`` so the reload loop can verify each new CRL
    is signed by the same CA (CR-3) before honoring it.
    HIGH-6: catches all exceptions in the loop and registers a done-callback
    that escalates a permanent task death by exiting 64 — Task Scheduler
    then relaunches the sidecar.
    MED-3: dropped repr(server) from the WARNING log field (multi-line value
    breaks single-line JSON parsers).
    MED-5: previous-CRL state is closure-captured here, not a module global.
    """
    del server  # accepted for caller-side documentation; not used in the loop

    def _revoked_serial_set(pem: bytes) -> frozenset[int]:
        crl = x509.load_pem_x509_crl(pem)
        return frozenset(entry.serial_number for entry in crl)

    initial_pem = await asyncio.to_thread(crl_path.read_bytes)
    current_revoked: list[frozenset[int]] = [_revoked_serial_set(initial_pem)]

    async def _reload_loop() -> None:
        while True:
            await asyncio.sleep(every_seconds)
            try:
                next_crl = await asyncio.to_thread(crl_path.read_bytes)
                _verify_crl(next_crl, ca_bundle_pem)
                next_revoked = _revoked_serial_set(next_crl)
            except Exception as exc:  # HIGH-6: must catch cryptography.* + OS errors
                _LOG.error(
                    "crl_reload_failed",
                    crl_path=str(crl_path),
                    error=str(exc),
                    exc_info=True,
                )
                continue

            # Compare the revoked-serial SET, not raw bytes. Re-signing the
            # CRL with the same revocation list produces different bytes
            # every time (CRL signatures are time-stamped via lastUpdate /
            # nextUpdate), which would false-positive a relaunch every time
            # the operator's mTLS-rotation tooling regenerates the file.
            # Only an actual revocation change matters.
            if next_revoked == current_revoked[0]:
                continue

            current_revoked[0] = next_revoked
            _LOG.warning("crl_changed_relaunching", crl_path=str(crl_path))
            # grpcio has no hot-swap API for server credentials. Exit 64 so
            # Task Scheduler relaunches the sidecar with freshly-built creds
            # and the new CRL. main()'s SystemExit handler passes 64 through
            # without backoff (CR-5).
            sys.exit(64)

    task = asyncio.create_task(_reload_loop(), name="crl-reloader")
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_crl_task_done)
    await asyncio.sleep(0)
    return task


def _on_crl_task_done(task: asyncio.Task[Any]) -> None:
    """Done-callback for the CRL reloader task.

    HIGH-6: if the reloader dies with an unhandled exception, escalate by
    exiting 64. Without this, the task's exception sits unread inside asyncio
    and CRL enforcement stops silently. Plain cancellation (during clean
    shutdown) is benign.
    """
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _LOG.critical("crl_reloader_died", error=str(exc), exc_info=exc)
        sys.exit(64)


def clientcert_sha256(der: bytes) -> str:
    """Return the hex SHA-256 digest of an X.509 cert DER.

    MED-1: callers MUST log only the digest, never paired with the raw DER
    bytes. The whole point of this helper is to provide a non-reversible
    fingerprint suitable for `cert_verify_fail` log lines (per spec §7).
    """
    return hashlib.sha256(der).hexdigest()


class PeerCnInterceptor(grpc.aio.ServerInterceptor):
    """H2: per-RPC interceptor that validates the mTLS peer Common Name.

    Rejects RPCs whose client cert CN is not in ``expected_cns``. When
    ``expected_cns`` is empty the interceptor is disabled (backwards-compat
    for deployments that haven't configured CNs yet) and logs a one-time
    warning at construction time.
    """

    def __init__(self, expected_cns: frozenset[str]) -> None:
        self._expected_cns = expected_cns
        if not expected_cns:
            _LOG.warning("peer_cn_check_disabled")

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        expected_cns = self._expected_cns

        if not expected_cns:
            # Allowlist empty → pass-through (disabled mode).
            return handler

        async def _unary_unary_wrapper(request: Any, context: grpc.aio.ServicerContext) -> Any:
            if not _peer_cn_allowed(context, expected_cns):
                await context.abort(grpc.StatusCode.PERMISSION_DENIED, "peer cn not allowed")
            return await handler.unary_unary(request, context)

        async def _unary_stream_wrapper(request: Any, context: grpc.aio.ServicerContext) -> Any:
            if not _peer_cn_allowed(context, expected_cns):
                await context.abort(grpc.StatusCode.PERMISSION_DENIED, "peer cn not allowed")
            return handler.unary_stream(request, context)

        async def _stream_unary_wrapper(
            request_iterator: Any, context: grpc.aio.ServicerContext
        ) -> Any:
            if not _peer_cn_allowed(context, expected_cns):
                await context.abort(grpc.StatusCode.PERMISSION_DENIED, "peer cn not allowed")
            return await handler.stream_unary(request_iterator, context)

        async def _stream_stream_wrapper(
            request_iterator: Any, context: grpc.aio.ServicerContext
        ) -> Any:
            if not _peer_cn_allowed(context, expected_cns):
                await context.abort(grpc.StatusCode.PERMISSION_DENIED, "peer cn not allowed")
            return handler.stream_stream(request_iterator, context)

        if handler is None:
            return handler

        if handler.unary_unary is not None:
            return handler._replace(unary_unary=_unary_unary_wrapper)
        if handler.unary_stream is not None:
            return handler._replace(unary_stream=_unary_stream_wrapper)
        if handler.stream_unary is not None:
            return handler._replace(stream_unary=_stream_unary_wrapper)
        if handler.stream_stream is not None:
            return handler._replace(stream_stream=_stream_stream_wrapper)
        return handler


def _peer_cn_allowed(context: grpc.aio.ServicerContext, expected_cns: frozenset[str]) -> bool:
    """Return True if the peer CN from auth_context is in the allowlist."""
    auth = context.auth_context()
    cn_bytes_list: list[bytes] = auth.get("x509_common_name", [])
    for cn_bytes in cn_bytes_list:
        try:
            cn = cn_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if cn in expected_cns:
            return True
    return False
