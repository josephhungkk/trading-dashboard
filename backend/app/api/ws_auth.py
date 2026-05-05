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
    forwarded = ws.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return ws.client.host if ws.client else ""


async def require_admin_jwt_ws(ws: WebSocket) -> str:
    """Verify CF Access JWT on a WebSocket upgrade request.

    Returns the admin email on success. The WireGuard dev bypass is intentionally
    narrower than the HTTP helper because Phase 7b.1 calls out 10.10.0.1
    explicitly for this gateway.
    """
    client_ip = _ws_client_ip(ws)
    if client_ip.startswith(_WG_DEV_BYPASS_HOST):
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
