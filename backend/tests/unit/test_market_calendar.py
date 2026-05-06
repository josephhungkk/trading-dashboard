"""Phase 8b T-0.3 — market calendar tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.services.market_calendar import (
    eod_for_exchange,
    is_session_window_open,
    is_trading_day,
    next_session_open,
    today_in_exchange_tz,
)

# DST


def test_nyse_edt_summer_close_is_2000_utc() -> None:
    # 2026-07-15: NYSE summer EDT, close 16:00 ET = 20:00 UTC.
    eod = eod_for_exchange("NYSE", date(2026, 7, 15))
    assert eod.hour == 20 and eod.minute == 0


def test_nyse_est_winter_close_is_2100_utc() -> None:
    # 2026-12-15: NYSE winter EST, close 16:00 ET = 21:00 UTC.
    eod = eod_for_exchange("NYSE", date(2026, 12, 15))
    assert eod.hour == 21 and eod.minute == 0


def test_hkex_no_dst_close_is_0800_utc() -> None:
    # HKEX: HKT = UTC+8 year-round; close 16:00 HKT = 08:00 UTC.
    for d in (date(2026, 1, 15), date(2026, 7, 15)):
        eod = eod_for_exchange("HKEX", d)
        assert eod.hour == 8 and eod.minute == 0


def test_lse_bst_summer_close_is_1530_utc() -> None:
    # LSE summer BST: close 16:30 GMT+1 = 15:30 UTC.
    eod = eod_for_exchange("LSE", date(2026, 7, 15))
    assert eod.hour == 15 and eod.minute == 30


# Half-day early closes


def test_nyse_black_friday_early_close_is_1800_utc() -> None:
    # 2026-11-27 NYSE early close 13:00 ET = 18:00 UTC.
    eod = eod_for_exchange("NYSE", date(2026, 11, 27))
    assert eod.hour == 18 and eod.minute == 0


def test_nyse_christmas_eve_early_close_is_1800_utc() -> None:
    # 2026-12-24 NYSE early close 13:00 ET = 18:00 UTC.
    eod = eod_for_exchange("NYSE", date(2026, 12, 24))
    assert eod.hour == 18 and eod.minute == 0


# Holidays


def test_nyse_thanksgiving_2026_not_trading() -> None:
    # 2026-11-26 (Thursday) — Thanksgiving.
    assert not is_trading_day("NYSE", date(2026, 11, 26))


def test_nyse_july_4_weekday_not_trading() -> None:
    # 2026-07-03 Friday observed (4th falls on Saturday).
    assert not is_trading_day("NYSE", date(2026, 7, 3))


def test_hkex_lunar_new_year_2026_not_trading() -> None:
    # 2026 Lunar New Year falls Feb 17-19.
    assert not is_trading_day("HKEX", date(2026, 2, 17))


# today_in_exchange_tz (CRIT-3)


def test_today_in_exchange_tz_returns_local_date() -> None:
    # Confirm shape: returns a date object that's either today_utc or +/- 1 day.
    d = today_in_exchange_tz("HKEX")
    today_utc = datetime.now(UTC).date()
    assert abs((d - today_utc).days) <= 1


# is_session_window_open (HIGH-2)


def test_moc_window_open_during_rth() -> None:
    # NYSE 2026-07-15 (summer) at 18:00 UTC = 14:00 ET, before MOC cutoff (15:50 ET = 19:50 UTC).
    when = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    assert is_session_window_open("NYSE", "MOC", now=when) is True


def test_moc_window_closed_after_cutoff() -> None:
    # 2026-07-15 at 19:55 UTC = 15:55 ET, past MOC cutoff (15:50 ET).
    when = datetime(2026, 7, 15, 19, 55, tzinfo=UTC)
    assert is_session_window_open("NYSE", "MOC", now=when) is False


def test_moo_window_open_just_before_open() -> None:
    # 2026-07-15 NYSE summer open 13:30 UTC. MOO submittable from 12:30 UTC.
    when = datetime(2026, 7, 15, 13, 0, tzinfo=UTC)
    assert is_session_window_open("NYSE", "MOO", now=when) is True


def test_non_session_bound_always_open() -> None:
    when = datetime(2026, 7, 15, 23, 0, tzinfo=UTC)
    assert is_session_window_open("NYSE", "LIMIT", now=when) is True


# next_session_open


def test_next_session_open_returns_utc_datetime() -> None:
    nxt = next_session_open("NYSE")
    assert nxt.tzinfo is not None
    assert nxt > datetime.now(UTC) - timedelta(days=2)
