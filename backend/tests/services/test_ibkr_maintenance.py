"""Tests for app.services.ibkr_maintenance (Phase 4 Task 30)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.ibkr_maintenance import (
    in_daily_reset,
    in_weekend_reset,
    seconds_until_window_ends,
)

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Berlin")
HKT = ZoneInfo("Asia/Hong_Kong")


# --- in_weekend_reset --------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # Friday 22:59:59 ET -> not yet weekend.
        (datetime(2026, 4, 24, 22, 59, 59, tzinfo=ET), False),
        # Friday 23:00:00 ET -> weekend starts.
        (datetime(2026, 4, 24, 23, 0, 0, tzinfo=ET), True),
        # Friday 23:30 ET -> deep in weekend.
        (datetime(2026, 4, 24, 23, 30, 0, tzinfo=ET), True),
        # Saturday 02:59:59 ET -> still weekend.
        (datetime(2026, 4, 25, 2, 59, 59, tzinfo=ET), True),
        # Saturday 03:00:00 ET -> weekend ends.
        (datetime(2026, 4, 25, 3, 0, 0, tzinfo=ET), False),
        # Saturday 12:00 ET -> long past weekend.
        (datetime(2026, 4, 25, 12, 0, 0, tzinfo=ET), False),
        # Sunday 02:00 ET -> not weekend.
        (datetime(2026, 4, 26, 2, 0, 0, tzinfo=ET), False),
        # Tuesday afternoon -> not weekend.
        (datetime(2026, 4, 21, 14, 0, 0, tzinfo=ET), False),
    ],
)
def test_in_weekend_reset_transitions(when: datetime, expected: bool) -> None:
    assert in_weekend_reset(when) is expected


def test_in_weekend_reset_accepts_utc_input() -> None:
    """UTC input must convert to ET internally and yield identical result."""
    et_local = datetime(2026, 4, 24, 23, 30, 0, tzinfo=ET)
    utc_equivalent = et_local.astimezone(ZoneInfo("UTC"))
    assert in_weekend_reset(utc_equivalent) is True


def test_in_weekend_reset_rejects_naive_datetime() -> None:
    naive = datetime(2026, 4, 24, 23, 0, 0)
    with pytest.raises(ValueError):
        in_weekend_reset(naive)


# --- in_daily_reset ----------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "expected_in", "expected_region"),
    [
        # Sunday 00:30 ET -> NA daily.
        (datetime(2026, 4, 26, 0, 30, 0, tzinfo=ET), True, "na"),
        # Sunday 02:00 ET -> outside NA daily.
        (datetime(2026, 4, 26, 2, 0, 0, tzinfo=ET), False, ""),
        # Monday 00:14:59 ET -> just before NA daily start.
        (datetime(2026, 4, 27, 0, 14, 59, tzinfo=ET), False, ""),
        # Monday 00:15:00 ET -> NA daily begins.
        (datetime(2026, 4, 27, 0, 15, 0, tzinfo=ET), True, "na"),
        # Saturday 00:30 ET -> Saturday is skipped in all regions.
        (datetime(2026, 4, 25, 0, 30, 0, tzinfo=ET), False, ""),
        # Wednesday 06:30 CET -> NA daily window in DST overlaps EU window;
        # NA wins per first-match precedence.
        (datetime(2026, 4, 22, 6, 30, 0, tzinfo=CET), True, "na"),
        # Thursday 05:00 HKT -> APAC-1 daily.
        (datetime(2026, 4, 23, 5, 0, 0, tzinfo=HKT), True, "apac-1"),
        # Wednesday 20:30 HKT -> APAC-2 daily.
        (datetime(2026, 4, 22, 20, 30, 0, tzinfo=HKT), True, "apac-2"),
        # Wednesday 22:00 HKT -> between APAC windows.
        (datetime(2026, 4, 22, 22, 0, 0, tzinfo=HKT), False, ""),
    ],
)
def test_in_daily_reset_transitions(
    when: datetime, expected_in: bool, expected_region: str
) -> None:
    in_window, region = in_daily_reset(when)
    assert (in_window, region) == (expected_in, expected_region)


def test_in_daily_reset_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        in_daily_reset(datetime(2026, 4, 26, 0, 30, 0))


# --- DST boundary ------------------------------------------------------------


def test_dst_spring_forward_2026() -> None:
    """Mar 8 2026 02:00 ET -> 03:00 EDT (skip). Verify NA window
    transitions correctly across the spring-forward gap."""
    # 01:30 EST (Sun, before spring-forward) -> NA window 00:15-01:45 -> in window.
    pre_dst = datetime(2026, 3, 8, 1, 30, 0, tzinfo=ET)
    in_window, region = in_daily_reset(pre_dst)
    assert in_window is True
    assert region == "na"

    # 03:30 EDT (Sun, after spring-forward) -> outside NA window.
    post_dst = datetime(2026, 3, 8, 3, 30, 0, tzinfo=ET)
    assert in_daily_reset(post_dst)[0] is False


def test_dst_fall_back_2026() -> None:
    """Nov 1 2026 02:00 EDT -> 01:00 EST (clocks repeat 01:00-01:59).
    The NA daily window 00:15-01:45 ET overlaps the duplicated hour;
    behaviour follows real America/New_York (EDT/EST handled by ZoneInfo)."""
    # 00:30 EDT on Nov 1 (before fall-back) -> in NA daily.
    pre_fall_back = datetime(2026, 11, 1, 0, 30, 0, tzinfo=ET, fold=0)
    in_window, region = in_daily_reset(pre_fall_back)
    assert in_window is True
    assert region == "na"


# --- seconds_until_window_ends ----------------------------------------------


def test_seconds_until_window_ends_zero_when_outside() -> None:
    outside = datetime(2026, 4, 21, 14, 0, 0, tzinfo=ET)
    assert seconds_until_window_ends(outside) == 0


def test_seconds_until_window_ends_positive_in_weekend() -> None:
    # Friday 23:30 ET -> weekend reset ends Saturday 03:00 ET, so ~3h30m left.
    when = datetime(2026, 4, 24, 23, 30, 0, tzinfo=ET)
    seconds = seconds_until_window_ends(when)
    expected_min = 3 * 3600 + 25 * 60  # at least 3h25m
    expected_max = 3 * 3600 + 31 * 60  # at most 3h31m (1 min slack)
    assert expected_min <= seconds <= expected_max


def test_seconds_until_window_ends_positive_in_daily_na() -> None:
    # Monday 00:30 ET -> NA daily ends 01:46 ET, so ~76 min left.
    when = datetime(2026, 4, 27, 0, 30, 0, tzinfo=ET)
    seconds = seconds_until_window_ends(when)
    assert 60 * 60 < seconds < 90 * 60


def test_seconds_until_window_ends_rejects_naive() -> None:
    with pytest.raises(ValueError):
        seconds_until_window_ends(datetime(2026, 4, 24, 23, 30, 0))
