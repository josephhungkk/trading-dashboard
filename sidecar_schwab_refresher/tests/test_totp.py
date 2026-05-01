"""Phase 7a E2 — pyotp wrapper produces 6-digit TOTP."""
import pytest

from sidecar_schwab_refresher.totp import current_totp, TOTPError


def test_current_totp_returns_6_digits():
    code = current_totp("JBSWY3DPEHPK3PXP")
    assert len(code) == 6
    assert code.isdigit()


def test_invalid_base32_raises():
    with pytest.raises(TOTPError):
        current_totp("not-base32!")


def test_clock_skew_returns_valid_6_digit_code(monkeypatch):
    """At any future time, current_totp still returns a 6-digit numeric code."""
    import time
    base = time.time()
    monkeypatch.setattr("time.time", lambda: base + 25)
    code = current_totp("JBSWY3DPEHPK3PXP")
    assert len(code) == 6
    assert code.isdigit()
