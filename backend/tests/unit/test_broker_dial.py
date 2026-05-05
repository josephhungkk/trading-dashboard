"""resolve_dial — config-driven gateway_label → dial-address table (Phase 7c A2)."""

from __future__ import annotations

import pytest

from app.services.broker_dial import resolve_dial


def test_resolve_alpaca_live() -> None:
    config = {
        "broker_gateway_dial": {
            "alpaca-live": "alpaca-sidecar-live:9091",
            "alpaca-paper": "alpaca-sidecar-paper:9092",
        },
    }
    assert resolve_dial(config, "alpaca-live") == "alpaca-sidecar-live:9091"


def test_resolve_alpaca_paper() -> None:
    config = {
        "broker_gateway_dial": {"alpaca-paper": "alpaca-sidecar-paper:9092"},
    }
    assert resolve_dial(config, "alpaca-paper") == "alpaca-sidecar-paper:9092"


def test_unknown_label_raises() -> None:
    with pytest.raises(KeyError, match="alpaca-unknown"):
        resolve_dial({"broker_gateway_dial": {}}, "alpaca-unknown")


def test_missing_table_returns_default_for_legacy_label() -> None:
    """Schwab + IBKR don't enter this table this phase — caller falls back."""
    assert resolve_dial({}, "schwab", default=None) is None


def test_missing_table_with_no_default_raises() -> None:
    with pytest.raises(KeyError, match="schwab"):
        resolve_dial({}, "schwab")
