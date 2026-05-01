"""Phase 7a E9 - run_once happy path: admin_client + Playwright mocks + post.

Uses BackendAdminClient stand-in (admin_client_mock) and patches the
Playwright launch + the perform_refresh + post_oauth_callback calls.
"""

import pytest


@pytest.mark.asyncio
async def test_run_once_disabled_skips(admin_client_mock, monkeypatch):
    """When tier2_refresh_enabled is false (default), run_once is a no-op."""
    from sidecar_schwab_refresher import main as refresher_main

    monkeypatch.setattr(
        refresher_main.BackendAdminClient,
        "from_env",
        classmethod(lambda _cls: admin_client_mock),
    )

    await refresher_main.run_once()
    # No failure counter increment, no metric push attempted
    counter = await admin_client_mock.get_config("tier2_consecutive_failures", default=None)
    assert counter is None or counter == "0"


@pytest.mark.asyncio
async def test_run_once_dry_run_skips(admin_client_mock, monkeypatch):
    from sidecar_schwab_refresher import main as refresher_main

    await admin_client_mock.set_config("tier2_refresh_enabled", "true", value_type="bool")
    monkeypatch.setattr(refresher_main, "DRY_RUN", True)
    monkeypatch.setattr(
        refresher_main.BackendAdminClient,
        "from_env",
        classmethod(lambda _cls: admin_client_mock),
    )

    await refresher_main.run_once()
    counter = await admin_client_mock.get_config("tier2_consecutive_failures", default=None)
    assert counter is None or counter == "0"


@pytest.mark.asyncio
async def test_run_once_setup_failure_increments_counter(admin_client_mock, monkeypatch):
    """When fetching credentials fails, handle_failure increments counter."""
    from sidecar_schwab_refresher import main as refresher_main

    await admin_client_mock.set_config("tier2_refresh_enabled", "true", value_type="bool")
    # Don't seed secrets - reveal_secret will KeyError -> triggers failure path.
    monkeypatch.setattr(refresher_main, "DRY_RUN", False)
    monkeypatch.setattr(
        refresher_main.BackendAdminClient,
        "from_env",
        classmethod(lambda _cls: admin_client_mock),
    )

    await refresher_main.run_once()
    counter = await admin_client_mock.get_config("tier2_consecutive_failures", default="0")
    assert counter == "1"


@pytest.mark.asyncio
async def test_handle_failure_classifies_dom_changed():
    """SelectorHealthError maps to dom_changed reason."""
    from sidecar_schwab_refresher.main import _classify_failure
    from sidecar_schwab_refresher.selectors import SelectorHealthError

    assert _classify_failure(SelectorHealthError("missing username")) == "dom_changed"
    assert _classify_failure(RuntimeError("totp invalid")) == "mfa_failed"
    assert _classify_failure(RuntimeError("login failed")) == "login_failed"
    assert _classify_failure(RuntimeError("connection refused")) == "network_error"
