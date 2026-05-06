"""Real-broker test gates.

Tests under backend/tests/real_broker/ require live broker credentials and
sandbox access. They are gated behind pytest markers (``real_schwab``,
``real_futu``) and auto-skip when env vars are missing — local ``pytest``
runs stay green even without secrets configured.
"""

from __future__ import annotations

import os

import pytest

_REQUIRED_SCHWAB_ENV = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_PAPER_ACCOUNT_HASH")
_REQUIRED_FUTU_ENV = ("FUTU_HOST", "FUTU_PORT")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--case",
        action="store",
        default="market_spy",
        help="real-broker scenario name (market_spy | trail_amount_spy | gtd_limit_spy)",
    )


@pytest.fixture
def case(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--case")  # type: ignore[no-any-return]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if any(os.environ.get(k, "") == "" for k in _REQUIRED_SCHWAB_ENV):
        skip_schwab = pytest.mark.skip(
            reason=f"real_schwab tests require env vars: {', '.join(_REQUIRED_SCHWAB_ENV)}"
        )
        for item in items:
            if "real_schwab" in item.keywords:
                item.add_marker(skip_schwab)

    if any(os.environ.get(k, "") == "" for k in _REQUIRED_FUTU_ENV):
        skip_futu = pytest.mark.skip(
            reason=f"real_futu tests require env vars: {', '.join(_REQUIRED_FUTU_ENV)}"
        )
        for item in items:
            if "real_futu" in item.keywords:
                item.add_marker(skip_futu)
