"""Phase 7a C9 - Schwab-specific logging redaction patterns."""

import pytest

from app.core import logging as logging_config


@pytest.mark.parametrize(
    "field_name",
    [
        "schwab_password",
        "schwab_totp_secret",
        "schwab_app_secret",
        "schwab_refresh_token",
        "schwab_access_token",
        "schwab.password",
    ],
)
def test_schwab_secret_pattern_matches_required_fields(field_name: str) -> None:
    assert any(pattern.search(field_name) for pattern in logging_config._SECRET_PATTERNS)
