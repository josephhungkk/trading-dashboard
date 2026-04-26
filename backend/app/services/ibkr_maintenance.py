"""IBKR maintenance-window helpers.

Pure timezone-aware predicates for the documented IBKR weekend and daily reset
windows. Window definitions are ported from ``SidecarLib.ps1``
``Test-InResetWindow`` so backend maintenance behavior matches the NUC
watchdog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

RegionName = Literal["na", "eu", "apac-1", "apac-2"]

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Berlin")
HKT = ZoneInfo("Asia/Hong_Kong")

FRIDAY = 4
SATURDAY = 5

WEEKEND_START_HOUR_ET = 23
WEEKEND_END_HOUR_ET = 3
WEEKEND_END_MINUTE_ET = 0
WEEKEND_END_DAY_OFFSET = 1

DAILY_START_SECOND = 0
DAILY_END_OFFSET_MINUTES = 1
SECONDS_PER_MINUTE = 60
NO_SECONDS_REMAINING = 0

NA_START_HOUR_ET = 0
NA_START_MINUTE_ET = 15
NA_END_HOUR_ET = 1
NA_END_MINUTE_ET = 45

EU_START_HOUR_CET = 6
EU_START_MINUTE_CET = 25
EU_END_HOUR_CET = 7
EU_END_MINUTE_CET = 45

APAC_1_START_HOUR_HKT = 4
APAC_1_START_MINUTE_HKT = 45
APAC_1_END_HOUR_HKT = 6
APAC_1_END_MINUTE_HKT = 5

APAC_2_START_HOUR_HKT = 20
APAC_2_START_MINUTE_HKT = 15
APAC_2_END_HOUR_HKT = 21
APAC_2_END_MINUTE_HKT = 15


class BrokerMaintenance(BaseModel):
    """Maintenance-window envelope. Single source of truth for both the
    list endpoint (broker_maintenance field on AccountListResponse) and
    the legacy 503 envelope used by _classify_sidecar_failure.
    """

    active: bool
    window: Literal["weekend", "daily"] | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class DailyWindow:
    region: RegionName
    zone: ZoneInfo
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int


DAILY_WINDOWS: tuple[DailyWindow, ...] = (
    DailyWindow(
        region="na",
        zone=ET,
        start_hour=NA_START_HOUR_ET,
        start_minute=NA_START_MINUTE_ET,
        end_hour=NA_END_HOUR_ET,
        end_minute=NA_END_MINUTE_ET,
    ),
    DailyWindow(
        region="eu",
        zone=CET,
        start_hour=EU_START_HOUR_CET,
        start_minute=EU_START_MINUTE_CET,
        end_hour=EU_END_HOUR_CET,
        end_minute=EU_END_MINUTE_CET,
    ),
    DailyWindow(
        region="apac-1",
        zone=HKT,
        start_hour=APAC_1_START_HOUR_HKT,
        start_minute=APAC_1_START_MINUTE_HKT,
        end_hour=APAC_1_END_HOUR_HKT,
        end_minute=APAC_1_END_MINUTE_HKT,
    ),
    DailyWindow(
        region="apac-2",
        zone=HKT,
        start_hour=APAC_2_START_HOUR_HKT,
        start_minute=APAC_2_START_MINUTE_HKT,
        end_hour=APAC_2_END_HOUR_HKT,
        end_minute=APAC_2_END_MINUTE_HKT,
    ),
)


def in_weekend_reset(now: datetime) -> bool:
    """True when now is inside the IBKR weekend reset window (Fri 23:00 ET -> Sat 03:00 ET)."""
    _require_tz_aware(now)
    et = now.astimezone(ET)
    return (et.weekday() == FRIDAY and et.hour >= WEEKEND_START_HOUR_ET) or (
        et.weekday() == SATURDAY and et.hour < WEEKEND_END_HOUR_ET
    )


def in_daily_reset(now: datetime) -> tuple[bool, str]:
    """Returns (in_window, region_name).
    region_name is one of 'na', 'eu', 'apac-1', 'apac-2', or '' when not in any window.
    First-match precedence: NA > EU > APAC matches PowerShell Test-InResetWindow."""
    _require_tz_aware(now)
    window = _active_daily_window(now)
    if window is None:
        return False, ""
    return True, window.region


def seconds_until_window_ends(now: datetime) -> int:
    """0 when not in any reset window. Otherwise seconds until the active window's end."""
    _require_tz_aware(now)
    weekend_end = _weekend_end(now)
    if weekend_end is not None:
        return _positive_seconds_until(now, weekend_end)

    daily_window = _active_daily_window(now)
    if daily_window is None:
        return NO_SECONDS_REMAINING

    local_now = now.astimezone(daily_window.zone)
    local_end = _daily_end(local_now, daily_window)
    return _positive_seconds_until(now, local_end.astimezone(UTC))


def compute_broker_maintenance(now: datetime) -> BrokerMaintenance:
    """Single-evaluation envelope: predicate and until are computed
    consistently, with a min-1-second floor to ensure active implies
    until is in the future (avoids the boundary-second flicker where
    seconds_until_window_ends could return 0 for the exact closing
    second). Per spec §6 R6.
    """
    if in_weekend_reset(now):
        secs = max(seconds_until_window_ends(now), 1)
        return BrokerMaintenance(
            active=True,
            window="weekend",
            until=now + timedelta(seconds=secs),
        )
    in_daily, _region = in_daily_reset(now)
    if in_daily:
        secs = max(seconds_until_window_ends(now), 1)
        return BrokerMaintenance(
            active=True,
            window="daily",
            until=now + timedelta(seconds=secs),
        )
    return BrokerMaintenance(active=False, window=None, until=None)


def _require_tz_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("tz-aware datetime required")
    now.utcoffset()
    if now.utcoffset() is None:
        raise ValueError("tz-aware datetime required")


def _active_daily_window(now: datetime) -> DailyWindow | None:
    for window in DAILY_WINDOWS:
        local_now = now.astimezone(window.zone)
        if local_now.weekday() != SATURDAY and _inside_daily_window(local_now, window):
            return window
    return None


def _inside_daily_window(local_now: datetime, window: DailyWindow) -> bool:
    local_time = local_now.time()
    return _daily_start_time(window) <= local_time < _daily_end_time(window)


def _daily_start_time(window: DailyWindow) -> time:
    return time(window.start_hour, window.start_minute, DAILY_START_SECOND)


def _daily_end_time(window: DailyWindow) -> time:
    return (
        datetime.combine(
            datetime.min.date(),
            time(window.end_hour, window.end_minute, DAILY_START_SECOND),
        )
        + timedelta(minutes=DAILY_END_OFFSET_MINUTES)
    ).time()


def _daily_end(local_now: datetime, window: DailyWindow) -> datetime:
    return datetime.combine(local_now.date(), _daily_end_time(window), tzinfo=window.zone)


def _weekend_end(now: datetime) -> datetime | None:
    if not in_weekend_reset(now):
        return None
    et = now.astimezone(ET)
    end_date = et.date()
    if et.weekday() == FRIDAY:
        end_date += timedelta(days=WEEKEND_END_DAY_OFFSET)
    return datetime.combine(
        end_date,
        time(WEEKEND_END_HOUR_ET, WEEKEND_END_MINUTE_ET, DAILY_START_SECOND),
        tzinfo=ET,
    ).astimezone(UTC)


def _positive_seconds_until(now: datetime, end: datetime) -> int:
    seconds_remaining = int((end - now.astimezone(UTC)).total_seconds())
    return max(NO_SECONDS_REMAINING, seconds_remaining)
