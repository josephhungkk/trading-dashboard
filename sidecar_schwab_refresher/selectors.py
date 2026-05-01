"""Schwab login DOM selectors. Versioned — last verified 2026-04-30.

H2 invariant: probe_selectors() runs BEFORE any credential submission. If any
selector is missing, the function raises and Tier-2 fails fast with
result=dom_changed without ever entering credentials.
"""
from __future__ import annotations

from typing import Any

LAST_VERIFIED = "2026-04-30"

SELECTOR_USERNAME = "input#loginIdInput"
SELECTOR_PASSWORD = "input#passwordInput"
SELECTOR_LOGIN_BUTTON = "button#btnLogin"
SELECTOR_TOTP_INPUT = "input#otpCode"
SELECTOR_TOTP_SUBMIT = "button#btnContinue"


class SelectorHealthError(RuntimeError):
    pass


async def probe_selectors(page: Any, timeout_sec: float = 5.0) -> bool:
    """Confirm all expected pre-login selectors exist within timeout. Raises on missing."""
    selectors: list[tuple[str, str]] = [
        ("username", SELECTOR_USERNAME),
        ("password", SELECTOR_PASSWORD),
        ("login_btn", SELECTOR_LOGIN_BUTTON),
    ]
    for name, sel in selectors:
        try:
            await page.locator(sel).wait_for(timeout=timeout_sec * 1000)
        except Exception as e:
            raise SelectorHealthError(
                f"selector missing: {name} ({sel}) — DOM may have changed since {LAST_VERIFIED}: {e}"
            ) from e
    return True
