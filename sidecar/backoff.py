"""Self-throttled startup backoff for Task Scheduler relaunch loops."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

STATE_FILE = "last_fail.txt"
MAX_DELAY_SECONDS = 60.0
INITIAL_DELAY_SECONDS = 1.0


def _state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE


def _read_failure(state_dir: Path) -> tuple[float, float] | None:
    try:
        raw = _state_path(state_dir).read_text(encoding="ascii").strip()
        epoch_raw, delay_raw = raw.split(",", maxsplit=1)
        return (float(epoch_raw), float(delay_raw))
    except (FileNotFoundError, OSError, ValueError):
        return None


def apply_startup_backoff(state_dir: Path) -> None:
    """Sleep synchronously if a previous failure's delay window is still active."""
    failure = _read_failure(state_dir)
    if failure is None:
        return

    recorded_epoch, delay_seconds = failure
    remaining = (recorded_epoch + delay_seconds) - time.time()
    if remaining > 0:
        time.sleep(remaining)


def read_previous_delay(state_dir: Path) -> float:
    """Return the recorded delay or INITIAL_DELAY_SECONDS if no prior failure.

    HIGH-10: single source of truth for the backoff state file format —
    callers like ibkr_sidecar.main() should NOT re-implement parsing.
    """
    failure = _read_failure(state_dir)
    if failure is None:
        return INITIAL_DELAY_SECONDS
    return failure[1]


def record_failure(state_dir: Path, prev_delay: float) -> None:
    """Record a non-clean exit and the next capped exponential delay.

    HIGH-8: write atomically via tempfile + os.replace so a crash mid-write
    doesn't leave a zero-length file (which would silently reset the backoff
    chain to INITIAL_DELAY_SECONDS).
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    next_delay = min(prev_delay * 2, MAX_DELAY_SECONDS)
    target = _state_path(state_dir)
    payload = f"{time.time()},{next_delay}\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="ascii",
        dir=str(state_dir),
        delete=False,
        suffix=".tmp",
    ) as fh:
        fh.write(payload)
        tmp_path = Path(fh.name)
    os.replace(tmp_path, target)  # atomic on POSIX + Windows


def clear_failure(state_dir: Path) -> None:
    """Clear the failure marker after a clean shutdown.

    HIGH-9: catch broader OSError so a transient PermissionError on Windows
    (e.g. AV scan holding the file) does not turn a clean shutdown into a
    failure-with-backoff via an unhandled exception.
    """
    try:
        _state_path(state_dir).unlink()
    except (FileNotFoundError, OSError):
        return
