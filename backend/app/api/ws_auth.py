"""WebSocket auth helpers for CF Access protected upgrade requests."""

from __future__ import annotations

import structlog
from fastapi import WebSocket, WebSocketException, status
from jwt.exceptions import PyJWTError

from app.core import deps, metrics
from app.core.cf_access import NoIdentityClaimError

_log = structlog.get_logger(__name__)

_WG_DEV_BYPASS_HOST = "10.10.0.1"


def _ws_client_ip(ws: WebSocket) -> str:
    """Return the immediate connection peer IP — NOT the leftmost
    X-Forwarded-For value.

    Sec H1 fix: an attacker can spoof ``X-Forwarded-For: 10.10.0.1`` from
    the public internet; nginx appends the real client IP to the right
    of the supplied chain via ``$proxy_add_x_forwarded_for``, so the
    leftmost value is attacker-controlled. The trustworthy signal is
    ``ws.client.host`` — for production traffic that's the nginx
    container IP (CF Tunnel → nginx → backend); for the WG dev path
    it's the literal ``10.10.0.1`` because nginx's WG-bypass listener
    binds that IP and forwards directly without the proxy hop.
    """
    return ws.client.host if ws.client else ""


async def require_admin_jwt_ws(ws: WebSocket) -> str:
    """Verify CF Access JWT on a WebSocket upgrade request.

    Returns the admin email on success. The WireGuard dev bypass is
    intentionally narrower than the HTTP helper because Phase 7b.1 calls
    out 10.10.0.1 explicitly for this gateway.
    """
    client_ip = _ws_client_ip(ws)
    if client_ip == _WG_DEV_BYPASS_HOST:
        # Exact match (not startswith) — guards against e.g. "10.10.0.100"
        # silently inheriting the bypass.
        metrics.cf_jwt_verification_total.labels(result="dev_bypass").inc()
        return "dev-bypass"

    token = ws.headers.get("cf-access-jwt-assertion")
    if not token:
        metrics.cf_jwt_verification_total.labels(result="missing_header").inc()
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="auth")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="auth")

    try:
        identity = deps._verifier.verify(token, client_ip=client_ip)
    except NoIdentityClaimError:
        metrics.cf_jwt_verification_total.labels(result="no_identity").inc()
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="auth")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="auth") from None
    except PyJWTError:
        metrics.cf_jwt_verification_total.labels(result="other_jwt_error").inc()
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="auth")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="auth") from None
    except Exception:
        _log.warning("ws_cf_access_verify_failed")
        metrics.cf_jwt_verification_total.labels(result="other_jwt_error").inc()
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="auth")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="auth") from None

    metrics.cf_jwt_verification_total.labels(result="ok").inc()
    return identity.email
