"""Phase 8b market calendar service — exchange-aware EOD + session-window awareness.

Per spec sec 1 CRIT-3 + HIGH-2 + sec 7 MED-3 + MED-7:
- today_in_exchange_tz: GTD validation must compare expiry_date against the
  exchange's local "today", not server UTC.
- eod_for_exchange: GTD wire-conversion to broker-native datetime; honors
  half-day early closes (Black Friday, Christmas Eve).
- is_trading_day / next_session_open: foundation for session-bound order
  validation (MOC cutoff, MOO open).
- is_session_window_open: HIGH-2 session-bound submission window enforcement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as ecals  # type: ignore[import-untyped]

# Exchange code mappings: project's Contract.exchange uses "NYSE", "HKEX", "LSE",
# "NASDAQ", etc. exchange_calendars expects ISO codes "XNYS", "XHKG", "XLON", "XNAS".
_EXCHANGE_CODE_MAP = {
    "NYSE": "XNYS",
    "NASDAQ": "XNAS",
    "AMEX": "XASE",
    "ARCA": "XNYS",  # ARCA shares the NYSE schedule
    "HKEX": "XHKG",
    "SEHK": "XHKG",  # alias
    "LSE": "XLON",
}


@lru_cache(maxsize=32)
def _calendar(exchange: str) -> Any:
    """Return the exchange_calendars calendar for exchange.

    Supported exchanges: NYSE, NASDAQ, AMEX, ARCA (→ XNYS), HKEX/SEHK (→ XHKG),
    LSE (→ XLON). Unknown exchange codes are passed through verbatim to
    exchange_calendars; if unrecognised a ValueError is raised with a descriptive
    message rather than leaking the raw library exception.
    """
    code = _EXCHANGE_CODE_MAP.get(exchange.upper(), exchange.upper())
    try:
        return ecals.get_calendar(code)
    except Exception as exc:
        raise ValueError(f"unsupported_exchange: {exchange}") from exc


def today_in_exchange_tz(exchange: str) -> date:
    """Return current date in the exchange's local timezone (CRIT-3).

    Server UTC may be a different calendar day than the exchange. Use the
    exchange's tz to determine 'today' for GTD min-bound validation.
    """
    cal = _calendar(exchange)
    return datetime.now(cal.tz).date()


def is_trading_day(exchange: str, d: date) -> bool:
    """True if d is a regular trading session on exchange."""
    cal = _calendar(exchange)
    return bool(cal.is_session(d.isoformat()))


def is_open(exchange: str, dt: datetime) -> bool:
    """True if exchange is open at dt.

    TODO: integrate exchange_calendars fallback for optional installs.
    """
    try:
        cal = _calendar(exchange)
    except (ValueError, ImportError) as _exc:
        return True
    when = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return bool(cal.is_open_on_minute(when.astimezone(UTC)))


def is_past_expiry(expiry: date, exchange: str) -> bool:
    """True once the exchange-local date has reached the option expiry."""
    return today_in_exchange_tz(exchange) >= expiry


def option_cutoff_time(exchange: str, expiry: date) -> datetime:
    """Return same-day option trading cutoff for exchange on expiry."""
    exchange_upper = exchange.upper()
    if exchange_upper in {"HKEX", "SEHK", "XHKG"}:
        return datetime.combine(expiry, time(16, 0), ZoneInfo("Asia/Hong_Kong"))
    return datetime.combine(expiry, time(15, 0), ZoneInfo("America/New_York"))


def next_trading_days(
    exchange: str,
    n: int,
    from_date: date | None = None,
) -> list[date]:
    """Return the next n trading days on exchange, inclusive of from_date."""
    if n <= 0:
        return []

    start = from_date or today_in_exchange_tz(exchange)
    try:
        cal = _calendar(exchange)
    except (ValueError, ImportError) as _exc:
        days: list[date] = []
        cursor = start
        while len(days) < n:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        return days

    end = start + timedelta(days=max(14, n * 3))
    days = [session.date() for session in cal.sessions_in_range(start.isoformat(), end.isoformat())]
    while len(days) < n:
        end += timedelta(days=max(14, n * 3))
        days = [
            session.date() for session in cal.sessions_in_range(start.isoformat(), end.isoformat())
        ]
    return days[:n]


def eod_for_exchange(exchange: str, expiry_date: date) -> datetime:
    """Compute end-of-day (UTC) for the exchange on expiry_date.

    Honors half-day early closes (Black Friday, Christmas Eve, etc.). Returns
    the session close converted to UTC.

    Raises ValueError if expiry_date is not a trading session.
    """
    cal = _calendar(exchange)
    iso = expiry_date.isoformat()
    if not cal.is_session(iso):
        raise ValueError(f"{expiry_date} is not a trading session on {exchange}")
    close_local = cal.session_close(iso)
    # exchange_calendars returns a tz-aware Timestamp at the exchange tz.
    # Convert to UTC.
    result = close_local.tz_convert("UTC").to_pydatetime()
    if not isinstance(result, datetime):
        raise ValueError(f"eod_for_exchange: expected datetime, got {type(result)!r}")
    return result


def next_session_open(exchange: str, after: datetime | None = None) -> datetime:
    """Return UTC datetime of the next session open at or after after.

    after defaults to now. Used by HIGH-2 session-window error responses
    to tell the client when their MOO/LOO order would be eligible again.
    """
    cal = _calendar(exchange)
    when = after or datetime.now(UTC)
    # exchange_calendars expects tz-naive UTC for date_to_session
    when_naive = when.astimezone(UTC).replace(tzinfo=None)
    next_session = cal.next_session(when_naive.date())
    open_local = cal.session_open(next_session)
    result = open_local.tz_convert("UTC").to_pydatetime()
    if not isinstance(result, datetime):
        raise ValueError(f"next_session_open: expected datetime, got {type(result)!r}")
    return result


def is_session_window_open(exchange: str, order_type: str, now: datetime | None = None) -> bool:
    """HIGH-2: True if the session-bound order_type is currently submittable on exchange.

    - MOO/LOO: submittable from session_open - 60min until session_open + 5min.
    - MOC/LOC: submittable from session_open until session_close - 10min (NYSE MOC cutoff).
    - Other order_type values: returns True (no window restriction).
    """
    if order_type not in {"MOC", "MOO", "LOC", "LOO"}:
        return True
    cal = _calendar(exchange)
    when = now or datetime.now(UTC)
    today_local = when.astimezone(cal.tz).date()
    iso = today_local.isoformat()
    if not cal.is_session(iso):
        return False
    _open_raw = cal.session_open(iso).tz_convert("UTC").to_pydatetime()
    _close_raw = cal.session_close(iso).tz_convert("UTC").to_pydatetime()
    if not isinstance(_open_raw, datetime) or not isinstance(_close_raw, datetime):
        raise ValueError(
            "is_session_window_open: unexpected types "
            f"open={type(_open_raw)!r} close={type(_close_raw)!r}"
        )
    open_utc: datetime = _open_raw
    close_utc: datetime = _close_raw

    if order_type in {"MOO", "LOO"}:
        # Submittable in [open - 60min, open + 5min] window.
        return bool(open_utc - timedelta(minutes=60) <= when <= open_utc + timedelta(minutes=5))
    # MOC, LOC: submittable [open, close - 10min].
    return bool(open_utc <= when <= close_utc - timedelta(minutes=10))


def crypto_eod(expiry_date: date) -> datetime:
    """Return tz-aware UTC EOD for a crypto expiry (crypto trades 24/7)."""
    return datetime.combine(expiry_date, time(23, 59, 59), UTC)


async def account_day_boundary_utc(db: object, account_id: object) -> datetime:
    """Phase 10a [M2]: 00:00 UTC of "today" for the broker's primary market.

    Stub returns UTC midnight regardless of account; refined in Phase 10a.5
    once per-broker primary-exchange tz lookup lands. The signature accepts
    ``db`` and ``account_id`` so the future tz-aware implementation can be
    swapped in without touching callers.
    """
    now_utc = datetime.now(UTC)
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
