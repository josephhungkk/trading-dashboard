"""BrokerId Literal must include alpaca (Phase 7c A1)."""

from __future__ import annotations

from typing import get_args

from app.services.brokers import BrokerId


def test_alpaca_in_broker_id_literal() -> None:
    assert "alpaca" in get_args(BrokerId)


def test_existing_brokers_still_present() -> None:
    args = set(get_args(BrokerId))
    assert {"ibkr", "futu", "schwab"}.issubset(args)
