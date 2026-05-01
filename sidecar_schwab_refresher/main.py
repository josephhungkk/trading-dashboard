"""Tier-2 entrypoint — cron loop or one-shot invocation.

Architectural invariants:
  - H2: 3 consecutive failures → auto-disable.
  - Skip if tier2_refresh_enabled=false (silent no-op).
  - Run every REFRESH_INTERVAL_HOURS (default 72 = 3 days).
  - This container does NOT import backend code — backend reachable only over
    HTTP via BackendAdminClient (CF Access service-token headers).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx
import structlog
from playwright.async_api import async_playwright

from sidecar_schwab_refresher.admin_client import BackendAdminClient
from sidecar_schwab_refresher.config_writer import post_oauth_callback
from sidecar_schwab_refresher.refresher import perform_refresh
from sidecar_schwab_refresher.selectors import SelectorHealthError
from sidecar_schwab_refresher.stealth import apply_stealth

log = structlog.get_logger(module="sidecar_schwab_refresher.main")

BACKEND_URL = os.environ.get("BACKEND_ADMIN_URL", "https://dashboard.kiusinghung.com")
REFRESH_INTERVAL_HOURS = int(os.environ.get("REFRESH_INTERVAL_HOURS", "72"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

AUTO_DISABLE_THRESHOLD = 3


async def handle_failure(client: Any, *, reason: str) -> None:
    raw = await client.get_config("tier2_consecutive_failures", default="0")
    n = int(raw or "0") + 1
    await client.set_config("tier2_consecutive_failures", str(n), value_type="int")
    if n >= AUTO_DISABLE_THRESHOLD:
        await client.set_config("tier2_refresh_enabled", "false", value_type="bool")
        log.error("tier2_auto_disabled", failures=n, reason=reason)


async def handle_success(client: Any) -> None:
    await client.set_config("tier2_consecutive_failures", "0", value_type="int")


async def fetch_credentials(client: Any) -> dict[str, str]:
    return {
        "username":    await client.reveal_secret("username"),
        "password":    await client.reveal_secret("password"),
        "totp_secret": await client.reveal_secret("totp_secret"),
    }


async def get_oauth_start_url(client: Any) -> str:
    """GET /api/admin/brokers/schwab/oauth-start with CF-Access headers; return Location."""
    headers = getattr(client, "_headers", {})
    base = getattr(client, "_url", BACKEND_URL)
    async with httpx.AsyncClient(
        timeout=10.0, follow_redirects=False, headers=headers,
    ) as http:
        resp = await http.get(f"{base}/api/admin/brokers/schwab/oauth-start")
        if resp.status_code != 302:
            raise RuntimeError(
                f"oauth-start returned {resp.status_code}, expected 302"
            )
        return resp.headers["location"]


def _classify_failure(exc: Exception) -> str:
    msg = str(exc).lower()
    if isinstance(exc, SelectorHealthError):
        return "dom_changed"
    if "totp" in msg or "mfa" in msg:
        return "mfa_failed"
    if "login" in msg:
        return "login_failed"
    return "network_error"


async def run_once() -> None:
    """One Tier-2 refresh attempt."""
    client = BackendAdminClient.from_env()

    enabled_raw = await client.get_config("tier2_refresh_enabled", default="false")
    enabled = (enabled_raw or "false").lower() == "true"
    if not enabled:
        log.info("tier2_disabled_skip")
        return

    if DRY_RUN:
        log.info("tier2_dry_run_skip")
        return

    try:
        try:
            creds = await fetch_credentials(client)
            consent_url = await get_oauth_start_url(client)
        except Exception as exc:
            log.exception("tier2_setup_failed")
            await handle_failure(client, reason=_classify_failure(exc))
            return

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            try:
                context = await browser.new_context()
                await apply_stealth(context)
                page = await context.new_page()
                try:
                    await page.goto(consent_url, wait_until="domcontentloaded")
                    code, state = await perform_refresh(
                        page,
                        username=creds["username"],
                        password=creds["password"],
                        totp_secret=creds["totp_secret"],
                        callback_url_prefix="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
                    )
                    await post_oauth_callback(
                        backend_url=BACKEND_URL,
                        code=code,
                        state=state,
                        cf_headers=getattr(client, "_headers", {}),
                    )
                    await handle_success(client)
                    log.info("tier2_refresh_success")
                except Exception as exc:
                    reason = _classify_failure(exc)
                    log.exception("tier2_refresh_failed", reason=reason)
                    await handle_failure(client, reason=reason)
                finally:
                    await context.close()
            finally:
                await browser.close()
    finally:
        try:
            await client.push_tier2_metric(time.time())
        except Exception:
            log.exception("tier2_metric_push_failed")


async def main_loop() -> None:
    while True:
        await run_once()
        await asyncio.sleep(REFRESH_INTERVAL_HOURS * 3600)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
