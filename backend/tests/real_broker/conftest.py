"""Real-broker test gates.

Tests under backend/tests/real_broker/ require live broker credentials and
sandbox access. They are gated behind the `real_schwab` pytest marker and
auto-skip when env vars are missing — local `pytest` runs stay green even
without secrets configured.
"""

from __future__ import annotations

import os

import pytest

_REQUIRED_SCHWAB_ENV = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_PAPER_ACCOUNT_HASH")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if any(os.environ.get(k, "") == "" for k in _REQUIRED_SCHWAB_ENV):
        skip = pytest.mark.skip(
            reason=f"real_schwab tests require env vars: {', '.join(_REQUIRED_SCHWAB_ENV)}"
        )
        for item in items:
            if "real_schwab" in item.keywords:
                item.add_marker(skip)
