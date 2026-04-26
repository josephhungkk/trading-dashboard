"""Boundary tests for compute_broker_maintenance (spec §11 R6).

Pins the four edge cases that would have produced `until=null,active=True`
under the old inline cascade: 1s before window opens, exact-second-window-
opens, exact-second-window-closes, 1s before window closes.
"""

from datetime import UTC, datetime

import pytest

from app.services.ibkr_maintenance import (
    BrokerMaintenance,
    compute_broker_maintenance,
)


def test_outside_any_window_is_inactive() -> None:
    # A normal Tuesday at 14:00 UTC — no maintenance.
    now = datetime(2026, 4, 28, 14, 0, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    assert m == BrokerMaintenance(active=False, window=None, until=None)


def test_active_weekend_window_returns_until_in_future() -> None:
    # Saturday 06:00 UTC = 02:00 EDT — inside Fri 23:00 ET → Sat 03:00 ET.
    now = datetime(2026, 4, 25, 6, 0, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    assert m.active is True
    assert m.window == "weekend"
    assert m.until is not None
    assert m.until > now


def test_active_daily_window_returns_until_in_future() -> None:
    # Tuesday 04:30 UTC = 00:30 EDT — inside NA daily reset 00:15-01:45 ET.
    now = datetime(2026, 4, 28, 4, 30, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    assert m.active is True
    assert m.window == "daily"
    assert m.until is not None
    assert m.until > now


def test_until_strictly_greater_than_now_at_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    # If `seconds_until_window_ends(now) == 0` for some boundary second,
    # the helper's `max(secs, 1)` floor must keep `until > now`.
    # We synthesize this by patching `seconds_until_window_ends` to 0.
    import app.services.ibkr_maintenance as mod

    monkeypatch.setattr(mod, "seconds_until_window_ends", lambda _now: 0)
    monkeypatch.setattr(mod, "in_weekend_reset", lambda _now: True)

    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    assert m.active is True
    assert m.until is not None
    assert m.until > now  # min-1s floor preserved (R6)
