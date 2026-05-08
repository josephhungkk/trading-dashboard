"""Phase 7b.1 retro — HIGH fix: CSWSH Origin header validation.

Tests for the ``_allowed_origin(ws, allowed)`` helper in
``app/api/ws_quotes.py``.

CSWSH (Cross-Site WebSocket Hijacking) mitigation:
- Connections without an Origin header are permitted only from the WireGuard
  dev gateway (10.10.0.1) — a trusted in-cluster path that browsers never use.
- Connections with an Origin header are permitted only if the origin is in the
  configured ``cors_origins`` list.
- All other combinations are rejected (return False).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api.ws_quotes import _allowed_origin

# All tests in this module are pure unit tests — no DB required.
pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws(*, origin: str | None, client_host: str = "1.2.3.4") -> MagicMock:
    """Build a minimal WebSocket mock with the given Origin header and peer IP."""
    ws = MagicMock()
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    ws.headers = headers
    ws.client = MagicMock()
    ws.client.host = client_host
    return ws


_ALLOWED = ["http://localhost:5173", "https://dash.example.com"]


# ---------------------------------------------------------------------------
# Tests: Origin header present
# ---------------------------------------------------------------------------


def test_allowed_origin_in_list() -> None:
    """Origin that matches an entry in the allowed list → True."""
    ws = _ws(origin="http://localhost:5173")
    assert _allowed_origin(ws, _ALLOWED) is True


def test_allowed_origin_second_entry_in_list() -> None:
    """Any entry in the list must be accepted (not just the first)."""
    ws = _ws(origin="https://dash.example.com")
    assert _allowed_origin(ws, _ALLOWED) is True


def test_disallowed_origin_not_in_list() -> None:
    """Origin that is NOT in the allowed list → False."""
    ws = _ws(origin="https://attacker.example.com")
    assert _allowed_origin(ws, _ALLOWED) is False


def test_disallowed_origin_empty_allowed_list() -> None:
    """Any Origin is rejected when the allowed list is empty."""
    ws = _ws(origin="http://localhost:5173")
    assert _allowed_origin(ws, []) is False


def test_disallowed_origin_partial_match() -> None:
    """Partial-match (substring) must NOT be accepted — must be exact."""
    ws = _ws(origin="http://localhost:5173.attacker.com")
    assert _allowed_origin(ws, _ALLOWED) is False


def test_disallowed_origin_case_sensitive() -> None:
    """Origin check is case-sensitive (HTTP spec treats Origin as case-sensitive)."""
    ws = _ws(origin="HTTP://LOCALHOST:5173")
    assert _allowed_origin(ws, _ALLOWED) is False


# ---------------------------------------------------------------------------
# Tests: No Origin header (no-browser path)
# ---------------------------------------------------------------------------


def test_no_origin_from_wireguard_gateway_allowed() -> None:
    """No Origin header from WireGuard dev gateway (10.10.0.1) → True.

    This is the trusted in-cluster path used by internal tooling and health
    checks — browsers always send an Origin header for WS upgrades.
    """
    ws = _ws(origin=None, client_host="10.10.0.1")
    assert _allowed_origin(ws, _ALLOWED) is True


def test_no_origin_from_untrusted_host_rejected() -> None:
    """No Origin header from any host other than 10.10.0.1 → False.

    Protects against clients that strip Origin (misconfigured proxies or
    crafted requests) by failing closed.
    """
    ws = _ws(origin=None, client_host="192.168.1.50")
    assert _allowed_origin(ws, _ALLOWED) is False


def test_no_origin_from_internet_host_rejected() -> None:
    """No Origin from a public IP → False (same fail-closed rule)."""
    ws = _ws(origin=None, client_host="88.208.197.219")
    assert _allowed_origin(ws, _ALLOWED) is False


def test_no_origin_empty_client_host_rejected() -> None:
    """No Origin and empty client host string → False."""
    ws = _ws(origin=None, client_host="")
    assert _allowed_origin(ws, _ALLOWED) is False


def test_no_origin_client_none_rejected() -> None:
    """No Origin and ws.client is None → False (no peer info at all)."""
    ws = _ws(origin=None, client_host="10.10.0.1")
    ws.client = None  # override — simulate missing ASGI scope client
    # _allowed_origin reads ws.client.host with a guard; must not raise.
    assert _allowed_origin(ws, _ALLOWED) is False


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


def test_empty_string_origin_treated_as_no_origin_trusted_host() -> None:
    """An empty string Origin header is equivalent to absent — treated as
    the no-Origin path; only 10.10.0.1 is permitted."""
    # Empty Origin from trusted host → allowed.
    ws_trusted = _ws(origin="", client_host="10.10.0.1")
    assert _allowed_origin(ws_trusted, _ALLOWED) is True

    # Empty Origin from untrusted host → rejected.
    ws_untrusted = _ws(origin="", client_host="1.2.3.4")
    assert _allowed_origin(ws_untrusted, _ALLOWED) is False


def test_allowed_with_nonempty_list_and_wireguard_no_origin() -> None:
    """Sanity: WireGuard peer with no Origin is accepted regardless of what
    the allowed list contains."""
    ws = _ws(origin=None, client_host="10.10.0.1")
    assert _allowed_origin(ws, ["https://unrelated.example.com"]) is True
