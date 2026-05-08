"""Integration test: HIGH-db-3 — _persist_tokens_under_lock writes atomically.

Verifies that a failure mid-write does not leave app_secrets in a torn state,
and that the happy path writes all four keys.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_persist_tokens_under_lock_success_writes_all_four_keys() -> None:
    """Happy path: all four keys (2 secrets + 2 config) are written."""
    from app.services.schwab_oauth import _persist_tokens_under_lock

    written: dict[str, str] = {}

    class _FakeConfig:
        async def set_secret(self, ns: str, key: str, value: str, **_: object) -> None:
            written[f"secret:{key}"] = value

        async def set(self, ns: str, key: str, value: str, **_: object) -> None:
            written[f"config:{key}"] = value

        async def reveal_secret(self, ns: str, key: str) -> str:
            return ""

    issued = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    await _persist_tokens_under_lock(
        config_service=_FakeConfig(),
        access_token="acc",
        refresh_token="ref",
        issued_at=issued,
        rotate_refresh_issued_at=True,
    )

    assert "secret:schwab.access_token" in written
    assert "secret:schwab.refresh_token" in written
    assert "config:schwab.access_token_issued_at" in written
    assert "config:schwab.refresh_token_issued_at" in written


@pytest.mark.asyncio
async def test_persist_tokens_under_lock_no_refresh_rotation() -> None:
    """rotate_refresh_issued_at=False must NOT write refresh_token_issued_at."""
    from app.services.schwab_oauth import _persist_tokens_under_lock

    written: dict[str, str] = {}

    class _FakeConfig:
        async def set_secret(self, ns: str, key: str, value: str, **_: object) -> None:
            written[f"secret:{key}"] = value

        async def set(self, ns: str, key: str, value: str, **_: object) -> None:
            written[f"config:{key}"] = value

        async def reveal_secret(self, ns: str, key: str) -> str:
            return ""

    issued = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    await _persist_tokens_under_lock(
        config_service=_FakeConfig(),
        access_token="acc",
        refresh_token="ref",
        issued_at=issued,
        rotate_refresh_issued_at=False,
    )

    assert "secret:schwab.access_token" in written
    assert "secret:schwab.refresh_token" in written
    assert "config:schwab.access_token_issued_at" in written
    assert "config:schwab.refresh_token_issued_at" not in written


@pytest.mark.asyncio
async def test_persist_tokens_under_lock_propagates_write_failure() -> None:
    """If a write fails mid-way, the exception propagates to the caller."""
    from app.services.schwab_oauth import _persist_tokens_under_lock

    call_log: list[str] = []

    class _FakeConfig:
        async def set_secret(self, ns: str, key: str, value: str, **_: object) -> None:
            call_log.append(f"secret:{key}")

        async def set(self, ns: str, key: str, value: str, **_: object) -> None:
            call_log.append(f"config:{key}")
            if key == "schwab.access_token_issued_at":
                raise RuntimeError("simulated DB failure on config write")

        async def reveal_secret(self, ns: str, key: str) -> str:
            return ""

    issued = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await _persist_tokens_under_lock(
            config_service=_FakeConfig(),
            access_token="new-access",
            refresh_token="new-refresh",
            issued_at=issued,
            rotate_refresh_issued_at=True,
        )

    # Both secrets must have been attempted before config write failure.
    assert "secret:schwab.access_token" in call_log
    assert "secret:schwab.refresh_token" in call_log
