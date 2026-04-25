"""Tests for sidecar.backoff (Phase 4 Task 7)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sidecar.backoff import (
    INITIAL_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    apply_startup_backoff,
    clear_failure,
    read_previous_delay,
    record_failure,
)


def test_no_state_file_does_not_sleep(tmp_path: Path) -> None:
    t0 = time.time()
    apply_startup_backoff(tmp_path)
    assert time.time() - t0 < 0.1


def test_record_then_apply_sleeps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_failure(tmp_path, prev_delay=0.5)  # next delay = 1.0
    sleeps: list[float] = []
    monkeypatch.setattr("sidecar.backoff.time.sleep", lambda s: sleeps.append(s))
    apply_startup_backoff(tmp_path)
    assert len(sleeps) == 1
    assert 0.5 < sleeps[0] <= 1.0


def test_clear_failure_removes_file(tmp_path: Path) -> None:
    record_failure(tmp_path, 1.0)
    state_file = tmp_path / "last_fail.txt"
    assert state_file.exists()
    clear_failure(tmp_path)
    assert not state_file.exists()


def test_clear_failure_idempotent_on_missing(tmp_path: Path) -> None:
    # No state file. Must not raise.
    clear_failure(tmp_path)
    clear_failure(tmp_path)


def test_record_caps_delay_at_max(tmp_path: Path) -> None:
    record_failure(tmp_path, prev_delay=100.0)
    txt = (tmp_path / "last_fail.txt").read_text(encoding="ascii").strip()
    _, delay_str = txt.split(",")
    assert float(delay_str) == MAX_DELAY_SECONDS


def test_read_previous_delay_default(tmp_path: Path) -> None:
    """HIGH-10: read_previous_delay returns INITIAL_DELAY_SECONDS when no prior failure."""
    assert read_previous_delay(tmp_path) == INITIAL_DELAY_SECONDS


def test_read_previous_delay_round_trip(tmp_path: Path) -> None:
    record_failure(tmp_path, prev_delay=8.0)  # next = 16.0
    assert read_previous_delay(tmp_path) == 16.0


def test_read_previous_delay_handles_corrupt_file(tmp_path: Path) -> None:
    """A corrupt/empty file resets to INITIAL_DELAY_SECONDS — same as missing.

    Documents the parser's robustness; HIGH-8 (atomic write) prevents this case
    from happening in practice, but the parser must still degrade gracefully.
    """
    (tmp_path / "last_fail.txt").write_text("garbage", encoding="ascii")
    assert read_previous_delay(tmp_path) == INITIAL_DELAY_SECONDS


def test_record_failure_writes_atomically(tmp_path: Path) -> None:
    """HIGH-8: record_failure must use tempfile + os.replace.

    Verifies the contents are always the complete payload, never a zero-length
    truncation. We can't simulate a crash directly, but we verify the success
    path leaves a valid file with both epoch and delay set.
    """
    record_failure(tmp_path, prev_delay=1.0)
    state_file = tmp_path / "last_fail.txt"
    raw = state_file.read_text(encoding="ascii")
    assert raw.strip(), "atomic write must never leave a zero-length file"
    epoch_str, delay_str = raw.strip().split(",")
    assert float(epoch_str) > 0
    assert float(delay_str) == 2.0


def test_record_failure_no_tmp_file_left_behind(tmp_path: Path) -> None:
    """HIGH-8: After successful record_failure, os.replace renames the *.tmp
    scratch file to last_fail.txt; the dir should contain only the target."""
    record_failure(tmp_path, prev_delay=1.0)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["last_fail.txt"]
