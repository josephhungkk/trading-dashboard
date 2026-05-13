"""Phase 11b chunk B4: dry-run replay tests — resolution picking, sample
truncation, composite resolution propagation.
"""

from __future__ import annotations

import pytest

from app.services.alerts.dry_run import _pick_resolution, replay


@pytest.mark.parametrize(
    "window_seconds,expected_resolution",
    [
        (30, "insufficient"),
        (60, "1m"),
        (300, "1m"),
        (3600, "1m"),
        (86399, "1m"),
        (86400, "1d"),
        (86400 * 2, "1d"),
    ],
)
def test_replay_picks_resolution(window_seconds: int, expected_resolution: str) -> None:
    predicate = {
        "kind": "pct_change_window",
        "symbol": "AAPL",
        "pct": 5.0,
        "window_seconds": window_seconds,
    }
    bars_1m = [{"ts": i, "close": 100 + i, "volume": 1000} for i in range(100)]
    bars_1d = [{"ts": i, "close": 100 + i * 10, "volume": 10000} for i in range(30)]
    result = replay(predicate=predicate, bars_1m=bars_1m, bars_1d=bars_1d)
    assert result.replay_resolution == expected_resolution


def test_replay_insufficient_returns_zero_fires() -> None:
    predicate = {
        "kind": "pct_change_window",
        "symbol": "AAPL",
        "pct": 5.0,
        "window_seconds": 30,
    }
    bars_1m = [{"ts": i, "close": 100, "volume": 1} for i in range(20)]
    result = replay(predicate=predicate, bars_1m=bars_1m, bars_1d=[])
    assert result.replay_resolution == "insufficient"
    assert result.fire_count == 0
    assert result.sample_fires == []
    assert result.truncated is False


def test_replay_truncates_samples() -> None:
    """Predicate that always fires; bar count > max_samples (default 10) so
    truncated must be True and sample_fires capped at 10."""
    predicate = {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 50.0}
    bars_1m = [{"ts": i, "close": 100, "volume": 1} for i in range(20)]
    result = replay(predicate=predicate, bars_1m=bars_1m, bars_1d=[])
    assert result.replay_resolution == "1m"
    # All 20 bars fire (close=100 > 50). Replay starts at bar 0 (non-windowed
    # predicates can evaluate immediately — chunk-B reviewer HIGH-1 fix).
    assert result.fire_count == 20
    # sample_fires is the bounded list; max_samples default = 10.
    assert len(result.sample_fires) == 10
    assert result.truncated is True
    # Memory bound: sample_fires must NOT contain the full 20 — only the
    # first 10 collected (reviewer MED-4 fix).
    assert result.sample_fires[0]["ts"] == 0
    assert result.sample_fires[-1]["ts"] == 9


def test_replay_no_fires_not_truncated() -> None:
    predicate = {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 999.0}
    bars_1m = [{"ts": i, "close": 100, "volume": 1} for i in range(20)]
    result = replay(predicate=predicate, bars_1m=bars_1m, bars_1d=[])
    assert result.fire_count == 0
    assert result.truncated is False


def test_replay_volume_spike_picks_minute_resolution() -> None:
    predicate = {
        "kind": "volume_spike",
        "symbol": "AAPL",
        "multiple": 2.0,
        "vs_window_minutes": 5,
    }
    assert _pick_resolution(predicate) == "1m"


def test_pick_resolution_composite_all_daily() -> None:
    predicate = {
        "kind": "composite_and",
        "children": [
            {
                "kind": "pct_change_window",
                "symbol": "AAPL",
                "pct": 5.0,
                "window_seconds": 86400 * 7,
            },
            {
                "kind": "pct_change_window",
                "symbol": "MSFT",
                "pct": 5.0,
                "window_seconds": 86400 * 30,
            },
        ],
    }
    assert _pick_resolution(predicate) == "1d"


def test_pick_resolution_composite_mixed_promotes_to_minute() -> None:
    predicate = {
        "kind": "composite_or",
        "children": [
            {
                "kind": "pct_change_window",
                "symbol": "AAPL",
                "pct": 5.0,
                "window_seconds": 86400,
            },
            {
                "kind": "pct_change_window",
                "symbol": "MSFT",
                "pct": 5.0,
                "window_seconds": 3600,
            },
        ],
    }
    assert _pick_resolution(predicate) == "1m"


def test_pick_resolution_composite_with_insufficient_child_propagates() -> None:
    predicate = {
        "kind": "composite_and",
        "children": [
            {
                "kind": "pct_change_window",
                "symbol": "AAPL",
                "pct": 5.0,
                "window_seconds": 30,
            },
            {
                "kind": "pct_change_window",
                "symbol": "MSFT",
                "pct": 5.0,
                "window_seconds": 86400,
            },
        ],
    }
    assert _pick_resolution(predicate) == "insufficient"


def test_pick_resolution_price_threshold_has_no_window() -> None:
    predicate = {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 100.0}
    assert _pick_resolution(predicate) == "1m"


def test_replay_uses_bars_1d_when_resolution_is_daily() -> None:
    """Daily predicate must read from bars_1d, not bars_1m. Caller passes
    empty bars_1m to prove the daily path doesn't reach into it."""
    predicate = {
        "kind": "pct_change_window",
        "symbol": "AAPL",
        "pct": -50.0,  # huge drop → never fires given ascending closes
        "window_seconds": 86400 * 7,
    }
    bars_1m: list[dict[str, object]] = []  # empty — would IndexError if used
    bars_1d = [{"ts": i, "close": 100 + i, "volume": 1} for i in range(10)]
    result = replay(predicate=predicate, bars_1m=bars_1m, bars_1d=bars_1d)
    assert result.replay_resolution == "1d"
    # Doesn't crash even though bars_1m is empty.


# ── B-close reviewer fixes ─────────────────────────────────────────────


def test_pick_resolution_empty_composite_is_insufficient() -> None:
    """Reviewer MED-3: empty composite must NOT default to '1m' (which would
    let an evaluator.evaluate() short-circuit fire spuriously). Should bail
    to 'insufficient' so the FE checkbox gates user attention."""
    assert _pick_resolution({"kind": "composite_and", "children": []}) == "insufficient"
    assert _pick_resolution({"kind": "composite_or", "children": []}) == "insufficient"


def test_replay_composite_uses_each_childs_own_symbol() -> None:
    """Reviewer HIGH-2: composite with mismatched child symbols must populate
    state for EVERY referenced symbol, not just the outer composite (which has
    no symbol at all). Otherwise each child evaluator looks up its own symbol
    in `state['prices']` and finds nothing → never fires."""
    composite = {
        "kind": "composite_or",
        "children": [
            {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 50.0},
            {"kind": "price_threshold", "symbol": "MSFT", "op": "gt", "value": 50.0},
        ],
    }
    bars_1m = [{"ts": i, "close": 100, "volume": 1} for i in range(5)]
    result = replay(predicate=composite, bars_1m=bars_1m, bars_1d=[])
    # Each bar fires for both AAPL and MSFT children → composite_or fires
    # every bar. Reviewer HIGH-1 fix means we no longer skip the first 2.
    assert result.fire_count == 5
