"""Tests for Windows ACL check in sidecar.tls (H4: _assert_windows_acl).

``_assert_windows_acl`` is always callable (no os.name guard) so these tests
run on Linux/macOS CI too — subprocess.run is monkeypatched to avoid needing
a real Windows icacls binary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sidecar_ibkr.tls import _assert_windows_acl


def _make_completed(stdout: str, returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = ""
    return cp


def test_windows_acl_rejects_builtin_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H4: BUILTIN\\Users in icacls output must raise RuntimeError."""
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")

    monkeypatch.setattr(
        "sidecar_ibkr.tls.subprocess.run",
        lambda *args, **kwargs: _make_completed(
            "C:\\k.pem BUILTIN\\Users:(F)\nSuccessfully processed 1 files"
        ),
    )

    with pytest.raises(RuntimeError, match="overly-permissive"):
        _assert_windows_acl(key)


def test_windows_acl_rejects_everyone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H4: 'Everyone:' in icacls output must raise RuntimeError."""
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")

    monkeypatch.setattr(
        "sidecar_ibkr.tls.subprocess.run",
        lambda *args, **kwargs: _make_completed(
            "C:\\k.pem Everyone:(R)\nSuccessfully processed 1 files"
        ),
    )

    with pytest.raises(RuntimeError, match="overly-permissive"):
        _assert_windows_acl(key)


def test_windows_acl_accepts_admin_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H4: only BUILTIN\\Administrators in icacls output must NOT raise."""
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")

    monkeypatch.setattr(
        "sidecar_ibkr.tls.subprocess.run",
        lambda *args, **kwargs: _make_completed(
            "C:\\k.pem BUILTIN\\Administrators:(F)\nSuccessfully processed 1 files"
        ),
    )

    _assert_windows_acl(key)  # must not raise


def test_windows_acl_raises_on_icacls_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H4: non-zero icacls returncode must raise RuntimeError."""
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")

    failed = MagicMock(spec=subprocess.CompletedProcess)
    failed.returncode = 1
    failed.stdout = ""
    failed.stderr = "Access denied"

    monkeypatch.setattr(
        "sidecar_ibkr.tls.subprocess.run",
        lambda *args, **kwargs: failed,
    )

    with pytest.raises(RuntimeError, match="icacls check failed"):
        _assert_windows_acl(key)
