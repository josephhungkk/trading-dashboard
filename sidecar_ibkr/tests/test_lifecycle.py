"""Tests for sidecar.ibkr_sidecar lifecycle wiring (Task 14).

Anchors three classes of behavior:

  1. clientId hash determinism — same (hostname, label) -> same id; distinct
     labels collide rarely. Without this, two sidecars on the same NUC
     could fight for the same IBKR clientId every restart.

  2. Disconnect watchdog — ib.isConnected() False for >30s exits 64
     (clean Task Scheduler relaunch). Connected resets the timer.

  3. main() exit-code dispatch chassis — the four documented paths
     (clean -> 0 + clear, KeyboardInterrupt -> 0 + clear,
      SystemExit(64) -> 64 + no record, generic Exception -> 1 + record).
     This is the contract that lets backoff.py + Task Scheduler cooperate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from sidecar_ibkr import backoff
from sidecar_ibkr.ibkr_sidecar import (
    _disconnect_watchdog,
    _fnv1a32,
    main,
)

# ---------- _fnv1a32 ----------


def test_fnv1a32_deterministic() -> None:
    assert _fnv1a32(b"nuc15pro|ibgw_live_us") == _fnv1a32(b"nuc15pro|ibgw_live_us")


def test_fnv1a32_distinct_labels_differ() -> None:
    """Two sidecars on the same NUC must NOT collide on clientId."""
    a = _fnv1a32(b"nuc15pro|ibgw_live_us")
    b = _fnv1a32(b"nuc15pro|ibgw_live_hk")
    c = _fnv1a32(b"nuc15pro|ibgw_paper_us")
    d = _fnv1a32(b"nuc15pro|ibgw_paper_hk")
    assert len({a, b, c, d}) == 4


def test_fnv1a32_classic_seed() -> None:
    """Sanity check against the FNV-1a 32-bit reference offset."""
    # Empty input returns the offset basis 0x811c9dc5.
    assert _fnv1a32(b"") == 0x811C9DC5


def test_fnv1a32_clientid_in_range() -> None:
    """clientId formula `(_fnv1a32(...) % 900) + 100` lands in [100, 999]."""
    for label in ("ibgw_live_us", "ibgw_live_hk", "ibgw_paper_us", "ibgw_paper_hk"):
        client_id = (_fnv1a32(f"nuc15pro|{label}".encode()) % 900) + 100
        assert 100 <= client_id <= 999


# ---------- _disconnect_watchdog ----------


@dataclass
class FakeWatchdogIB:
    """Tracks isConnected() return values + how often it's queried."""

    sequence: list[bool] = field(default_factory=lambda: [True])
    calls: int = 0

    def isConnected(self) -> bool:  # noqa: N802
        # Cycle through the sequence; once exhausted, stick on the last.
        self.calls += 1
        idx = min(self.calls - 1, len(self.sequence) - 1)
        return self.sequence[idx]


@pytest.mark.asyncio
async def test_disconnect_watchdog_exits_when_event_set() -> None:
    """Setting the stop event must terminate the watchdog cleanly."""
    ib = FakeWatchdogIB(sequence=[True])
    stop = asyncio.Event()
    stop.set()
    # Should return promptly without sys.exit.
    await asyncio.wait_for(_disconnect_watchdog(ib, stop), timeout=2.0)


@pytest.mark.asyncio
async def test_disconnect_watchdog_exits_after_30s_disconnect() -> None:
    """ib.isConnected()=False sustained >30s must trigger sys.exit(64).

    Drives the watchdog's first iteration by feeding two time.time() values
    31s apart and patching sys.exit to a sentinel — the real SystemExit
    inside asyncio.Task can be swallowed depending on harness, so we assert
    on the captured exit code instead of pytest.raises.
    """
    ib = FakeWatchdogIB(sequence=[False])
    stop = asyncio.Event()

    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        # Mirror real sys.exit semantics so the watchdog loop terminates.
        raise SystemExit(code)

    # Two calls: 1) initial last_connected, 2) elif comparison +31s.
    # Provide a long tail of identical timestamps in case the watchdog
    # iterates more than once before the SystemExit takes hold.
    times = [1_000_000.0, 1_000_031.0] + [1_000_031.0] * 100

    with patch("sidecar_ibkr.ibkr_sidecar.time.time", side_effect=times), patch(
        "sidecar_ibkr.ibkr_sidecar.sys.exit", side_effect=fake_exit
    ):
        try:
            await asyncio.wait_for(_disconnect_watchdog(ib, stop), timeout=2.0)
        except SystemExit:
            pass

    assert 64 in exit_calls, "expected sys.exit(64) when disconnect window > 30s"


@pytest.mark.asyncio
async def test_disconnect_watchdog_resets_on_reconnect() -> None:
    """A reconnect (isConnected -> True) within 30s must reset the timer."""
    # Sequence: disconnected briefly, then reconnected, then stop.
    ib = FakeWatchdogIB(sequence=[False, False, True, True, True])
    stop = asyncio.Event()

    async def stop_after_a_few_ticks() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    # No SystemExit expected — the reconnect resets the timer.
    await asyncio.wait_for(
        asyncio.gather(_disconnect_watchdog(ib, stop), stop_after_a_few_ticks()),
        timeout=2.0,
    )


# ---------- main() exit-code dispatch chassis ----------


def _argv(state_dir: Path, log_dir: Path) -> list[str]:
    """Minimum argv that lets main() get past arg parsing.

    Keeps tls_key_pem unset so assert_key_file_permissions skip kicks in.
    """
    return [
        "ibkr-sidecar",
        "--label", "test",
        "--gateway-port", "4002",
        "--grpc-port", "18002",
        "--log-dir", str(log_dir),
        "--state-dir", str(state_dir),
    ]


def test_main_clean_run_returns_0_and_clears_failure(tmp_path: Path) -> None:
    """Happy path: run() returns cleanly -> exit 0, last_fail.txt removed."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()
    # Pre-seed a stale failure marker; main() must clear it on clean exit.
    backoff.record_failure(state_dir, prev_delay=1.0)
    state_file = state_dir / "last_fail.txt"
    assert state_file.exists()

    async def fake_run(_args: object) -> None:
        return None

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 0

    assert not state_file.exists(), "clean shutdown must remove the failure marker"


def test_main_keyboard_interrupt_returns_0_and_clears_failure(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()
    backoff.record_failure(state_dir, prev_delay=1.0)
    state_file = state_dir / "last_fail.txt"

    async def fake_run(_args: object) -> None:
        raise KeyboardInterrupt

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 0

    assert not state_file.exists()


def test_main_systemexit_64_returns_64_without_recording_failure(tmp_path: Path) -> None:
    """CR-5: code 64 (CRL rotation, clientId collision) must NOT backoff."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()

    async def fake_run(_args: object) -> None:
        raise SystemExit(64)

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 64

    state_file = state_dir / "last_fail.txt"
    assert not state_file.exists(), "exit 64 must not record a failure"


def test_main_systemexit_0_returns_0_and_clears_failure(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()
    backoff.record_failure(state_dir, prev_delay=1.0)
    state_file = state_dir / "last_fail.txt"

    async def fake_run(_args: object) -> None:
        raise SystemExit(0)

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 0

    assert not state_file.exists()


def test_main_systemexit_1_returns_1_and_records_failure(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()

    async def fake_run(_args: object) -> None:
        raise SystemExit(1)

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 1

    state_file = state_dir / "last_fail.txt"
    assert state_file.exists(), "exit 1 must record a failure for backoff"


def test_main_unhandled_exception_returns_1_and_records_failure(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()

    async def fake_run(_args: object) -> None:
        raise RuntimeError("ibkr gateway exploded")

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 1

    state_file = state_dir / "last_fail.txt"
    assert state_file.exists()


def test_main_clientid_collision_string_returns_64(tmp_path: Path) -> None:
    """A RuntimeError whose message contains 'clientId' + 'in use' must exit 64."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "log"
    state_dir.mkdir()

    async def fake_run(_args: object) -> None:
        raise RuntimeError("clientId 123 is already in use")

    with patch("sidecar_ibkr.ibkr_sidecar.sys.argv", _argv(state_dir, log_dir)), patch(
        "sidecar_ibkr.ibkr_sidecar.run", fake_run
    ):
        assert main() == 64

    state_file = state_dir / "last_fail.txt"
    assert not state_file.exists(), "exit 64 path must not record a failure"
