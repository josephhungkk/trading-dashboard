"""Phase 7a E6 — H2: 3 consecutive failures flips tier2_refresh_enabled=false."""
import pytest


@pytest.mark.asyncio
async def test_three_failures_auto_disable(admin_client_mock):
    from sidecar_schwab_refresher.main import handle_failure

    await admin_client_mock.set_config("tier2_refresh_enabled", "true", value_type="bool")
    await admin_client_mock.set_config("tier2_consecutive_failures", "0", value_type="int")

    for _ in range(2):
        await handle_failure(admin_client_mock, reason="login_failed")
        assert await admin_client_mock.get_config("tier2_refresh_enabled") == "true"

    await handle_failure(admin_client_mock, reason="login_failed")
    assert await admin_client_mock.get_config("tier2_refresh_enabled") == "false"


@pytest.mark.asyncio
async def test_success_resets_failure_counter(admin_client_mock):
    from sidecar_schwab_refresher.main import handle_failure, handle_success
    await handle_failure(admin_client_mock, reason="x")
    await handle_failure(admin_client_mock, reason="x")
    await handle_success(admin_client_mock)
    assert await admin_client_mock.get_config("tier2_consecutive_failures") == "0"
