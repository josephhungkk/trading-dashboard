"""Phase 15a: ForexCalendar + CryptoCalendar tests."""

from datetime import UTC, datetime

from app.services.market_calendar import is_forex_session_open, next_forex_session_open


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def test_forex_open_monday_noon_et():
    # Monday 17:00 UTC = Monday 12:00 ET (UTC-5 winter) — should be open
    assert is_forex_session_open(_dt("2026-01-05T17:00:00+00:00")) is True


def test_forex_closed_saturday():
    assert is_forex_session_open(_dt("2026-01-03T12:00:00+00:00")) is False


def test_forex_closed_friday_close():
    # Friday 22:00 ET = Saturday 03:00 UTC — closed
    assert is_forex_session_open(_dt("2026-01-02T22:00:00-05:00")) is False


def test_forex_closed_during_daily_gap():
    # Weekday 17:05 ET = in the 17:00-17:15 gap
    assert is_forex_session_open(_dt("2026-01-05T22:05:00+00:00")) is False


def test_next_forex_session_open_from_gap():
    # During 17:05 ET gap → next open is 17:15 ET same day
    result = next_forex_session_open(_dt("2026-01-05T22:05:00+00:00"))
    assert result.hour == 22 and result.minute == 15  # 17:15 ET = 22:15 UTC in winter


def test_next_forex_session_open_from_saturday():
    # Saturday → next open is Sunday 17:00 ET = 22:00 UTC
    result = next_forex_session_open(_dt("2026-01-03T12:00:00+00:00"))
    assert result.weekday() == 6  # Sunday
