"""Phase 7a C0/E0 — shared fixtures for the Tier-2 refresher tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio


class _FakeAdminClient:
    """In-memory stand-in for sidecar_schwab_refresher.admin_client.BackendAdminClient.

    Mirrors the get_config / set_config / reveal_secret / push_tier2_metric API
    so tests don't need an httpx_mock layer for these calls.
    """

    NAMESPACE = "broker"
    KEY_PREFIX = "schwab."

    def __init__(self) -> None:
        self._config: dict[str, str] = {}
        self._secrets: dict[str, str] = {}
        self._headers = {
            "CF-Access-Client-Id": "test",
            "CF-Access-Client-Secret": "test",
        }
        self._url = "https://dashboard.kiusinghung.com"

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        return self._config.get(key, default)

    async def set_config(self, key: str, value: str, *, value_type: str = "str") -> None:
        self._config[key] = value

    async def reveal_secret(self, key: str) -> str:
        return self._secrets[key]

    def seed_secret(self, key: str, value: str) -> None:
        self._secrets[key] = value

    async def push_tier2_metric(self, last_run_seconds: float) -> None:
        pass


@pytest_asyncio.fixture
async def admin_client_mock() -> _FakeAdminClient:
    return _FakeAdminClient()


class _AsyncCM:
    def __init__(self, value):  # noqa: ANN001
        self._value = value

    async def __aenter__(self):  # noqa: ANN204
        return self._value

    async def __aexit__(self, *a):  # noqa: ANN204, ANN002
        return False


@pytest.fixture
def mock_playwright(monkeypatch):  # noqa: ANN001
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "sidecar_schwab_refresher.main.async_playwright",
        lambda: _AsyncCM(pw),
        raising=False,
    )
    return pw
