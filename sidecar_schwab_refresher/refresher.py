"""Tier-2 Playwright OAuth refresh flow.

C1 invariant: the browser MUST NOT follow the redirect. We intercept the
request via page.on("request") and POST the captured `code` directly to
backend admin via config_writer.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any
from urllib.parse import parse_qs, urlparse

from sidecar_schwab_refresher.selectors import (
    SELECTOR_LOGIN_BUTTON,
    SELECTOR_PASSWORD,
    SELECTOR_TOTP_INPUT,
    SELECTOR_TOTP_SUBMIT,
    SELECTOR_USERNAME,
    probe_selectors,
)
from sidecar_schwab_refresher.totp import current_totp

log = logging.getLogger(__name__)

REDIRECT_TIMEOUT_SEC = 30


async def perform_refresh(
    page: Any,
    *,
    username: str,
    password: str,
    totp_secret: str,
    callback_url_prefix: str,
) -> tuple[str, str]:
    """Complete the OAuth login + return (code, state) without following redirect."""
    await probe_selectors(page)

    await page.locator(SELECTOR_USERNAME).fill("")
    await _type_slowly(page, SELECTOR_USERNAME, username)
    await _type_slowly(page, SELECTOR_PASSWORD, password)
    await page.locator(SELECTOR_LOGIN_BUTTON).click()

    await page.locator(SELECTOR_TOTP_INPUT).wait_for(timeout=10_000)
    code_value = current_totp(totp_secret)
    await page.locator(SELECTOR_TOTP_INPUT).fill(code_value)

    captured: dict[str, str] = {}
    redirect_event = asyncio.Event()

    async def on_request(req: Any) -> None:
        try:
            is_nav = req.is_navigation_request()
        except Exception:
            is_nav = True
        if is_nav and str(req.url).startswith(callback_url_prefix):
            parsed = urlparse(req.url)
            qs = parse_qs(parsed.query)
            captured["code"] = qs.get("code", [""])[0]
            captured["state"] = qs.get("state", [""])[0]
            try:
                await req.abort()
            except Exception:
                log.exception("redirect_abort_failed")
            redirect_event.set()

    page.on("request", on_request)

    await page.locator(SELECTOR_TOTP_SUBMIT).click()

    try:
        await asyncio.wait_for(redirect_event.wait(), timeout=REDIRECT_TIMEOUT_SEC)
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            f"Tier-2 refresh: redirect not observed within {REDIRECT_TIMEOUT_SEC}s"
        ) from e

    # HIGH-sec-1: state is optional — v0.7.4 hotfix dropped state from authorize URL;
    # Schwab may not echo it back. Only code is required.
    if not captured.get("code"):
        raise RuntimeError(f"Tier-2 refresh: missing code in capture {captured}")

    return captured["code"], captured["state"]


async def _type_slowly(page: Any, selector: str, text: str) -> None:
    locator = page.locator(selector)
    for ch in text:
        await locator.type(ch)
        await asyncio.sleep(random.uniform(0.08, 0.2))
