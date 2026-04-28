"""Shared fixtures for Alembic migration tests.

The ``session`` fixture is registered globally by the top-level
``tests/conftest.py`` via ``pytest_plugins = ("tests.fixtures.db_session",)``.
Pytest 9 forbids ``pytest_plugins`` in nested conftests; this file is kept
as a marker for migration-specific fixtures in the future.
"""

from __future__ import annotations
