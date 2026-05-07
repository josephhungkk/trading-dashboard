"""Integration test conftest — overrides _apply_migrations to skip for this directory.

The dev NUC DB does not have TimescaleDB installed, so Alembic cannot run
migrations 0023+ (which require CREATE EXTENSION timescaledb). The needed
tables (chart_layouts, instruments, app_config) are created manually for
development. In CI the Timescale Docker image is used and full migrations run.

This conftest replaces the autouse session-scope _apply_migrations fixture
with a no-op so integration tests can run against the already-migrated dev DB.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """No-op override — dev DB tables created manually; CI runs full migrations."""
    return
