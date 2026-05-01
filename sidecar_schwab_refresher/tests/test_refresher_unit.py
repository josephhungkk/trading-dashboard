"""Phase 7a E5 — refresher fills creds + intercepts redirect WITHOUT navigation."""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_refresher_intercepts_redirect_without_navigation(monkeypatch):
    from sidecar_schwab_refresher.refresher import perform_refresh

    # Patch sleep to no-op so the typing-delay loop runs fast.
    import sidecar_schwab_refresher.refresher as refresher_mod
    async def fast_sleep(_sec: float) -> None:
        return None
    monkeypatch.setattr(refresher_mod.asyncio, "sleep", fast_sleep)

    # Page mock — locator returns an object with async fill/type/click/wait_for.
    page = MagicMock()
    locator = MagicMock()
    locator.wait_for = AsyncMock()
    locator.fill = AsyncMock()
    locator.type = AsyncMock()
    locator.click = AsyncMock()
    page.locator = MagicMock(return_value=locator)

    captured_handler: dict[str, object] = {}

    def on_event(event: str, handler: object) -> None:
        captured_handler[event] = handler

    page.on = on_event

    # Drive the redirect simulation by invoking the captured handler manually
    # right before perform_refresh's wait_for.
    async def trigger() -> None:
        # Wait briefly for perform_refresh to register the handler.
        import asyncio as _asyncio
        await _asyncio.sleep(0.05)
        req = MagicMock()
        req.url = "https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=AUTH_CODE&state=STATE"
        req.is_navigation_request = MagicMock(return_value=True)
        req.abort = AsyncMock()
        handler = captured_handler.get("request")
        if handler is None:
            raise RuntimeError("page.on('request') was never registered")
        await handler(req)  # type: ignore[misc]

    import asyncio
    trigger_task = asyncio.create_task(trigger())
    try:
        code_value, state_value = await perform_refresh(
            page,
            username="u",
            password="p",
            totp_secret="JBSWY3DPEHPK3PXP",
            callback_url_prefix="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
        )
    finally:
        await trigger_task

    assert code_value == "AUTH_CODE"
    assert state_value == "STATE"
