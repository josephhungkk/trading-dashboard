"""Phase 7a C8 — account_hash absent from AccountResponse boundary output."""

from app.services.brokers import _ACCOUNT_BOUNDARY_STRIP_FIELDS


def test_account_hash_in_boundary_strip_set() -> None:
    assert "account_hash" in _ACCOUNT_BOUNDARY_STRIP_FIELDS
    assert "gateway_label" in _ACCOUNT_BOUNDARY_STRIP_FIELDS
    assert "account_number" in _ACCOUNT_BOUNDARY_STRIP_FIELDS
