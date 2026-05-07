"""Real-broker test gates.

Tests under backend/tests/real_broker/ require live broker credentials and
sandbox access. They are gated behind pytest markers (``real_schwab``,
``real_futu``, ``real_ibkr``, ``real_alpaca_equity``) and auto-skip when env
vars are missing — local ``pytest`` runs stay green even without secrets
configured.
"""

from __future__ import annotations

import os

import pytest

_REQUIRED_SCHWAB_ENV = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_PAPER_ACCOUNT_HASH")
_REQUIRED_FUTU_ENV = ("FUTU_HOST", "FUTU_PORT")
_REQUIRED_ALPACA_EQUITY_ENV = ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET")
_REQUIRED_IBKR_ENV = ("IBKR_PAPER_ACCOUNT",)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--case",
        action="store",
        default="market_spy",
        help=(
            "real-broker scenario name "
            "(market_spy | trail_amount_spy | gtd_limit_spy | "
            "trail_percent_spy | moc_spy | gtd_limit_spy | limit_spy | trail_spy)"
        ),
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

    if any(os.environ.get(k, "") == "" for k in _REQUIRED_ALPACA_EQUITY_ENV):
        skip_alpaca_equity = pytest.mark.skip(
            reason=(
                "real_alpaca_equity tests require env vars: "
                f"{', '.join(_REQUIRED_ALPACA_EQUITY_ENV)}"
            )
        )
        for item in items:
            if "real_alpaca_equity" in item.keywords:
                item.add_marker(skip_alpaca_equity)

    if any(os.environ.get(k, "") == "" for k in _REQUIRED_IBKR_ENV):
        skip_ibkr = pytest.mark.skip(
            reason=f"real_ibkr tests require env vars: {', '.join(_REQUIRED_IBKR_ENV)}"
        )
        for item in items:
            if "real_ibkr" in item.keywords:
                item.add_marker(skip_ibkr)
