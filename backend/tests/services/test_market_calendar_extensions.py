"""Tests for Phase 12 market calendar helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

import app.services.market_calendar as market_calendar
from app.services.market_calendar import (
    is_open,
    is_past_expiry,
    next_trading_days,
    option_cutoff_time,
)

pytestmark = pytest.mark.no_db


def test_is_open_detects_regular_session_and_closed_time() -> None:
    assert is_open("NYSE", datetime(2026, 6, 16, 14, 0, tzinfo=UTC)) is True
    assert is_open("NYSE", datetime(2026, 6, 16, 22, 0, tzinfo=UTC)) is False


def test_is_past_expiry_uses_exchange_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        market_calendar,
        "today_in_exchange_tz",
        lambda exchange: date(2026, 6, 20),
    )

    assert is_past_expiry(date(2026, 6, 20), "NYSE") is True
    assert is_past_expiry(date(2026, 6, 21), "NYSE") is False


def test_option_cutoff_time_uses_us_and_hk_defaults() -> None:
    us_cutoff = option_cutoff_time("NYSE", date(2026, 6, 20))
    hk_cutoff = option_cutoff_time("HKEX", date(2026, 6, 20))

    assert us_cutoff.hour == 15
    assert us_cutoff.tzname() == "EDT"
    assert hk_cutoff.hour == 16
    assert hk_cutoff.tzname() == "HKT"


def test_next_trading_days_returns_sessions_from_start_date() -> None:
    days = next_trading_days("NYSE", 3, from_date=date(2026, 6, 15))

    assert days == [date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17)]
