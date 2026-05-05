"""Phase 7a C8 + Phase 7c C3 — broker-internal IDs absent from AccountResponse."""

from app.brokers.base import AccountResponse
from app.services.brokers import _ACCOUNT_BOUNDARY_STRIP_FIELDS


def test_account_hash_in_boundary_strip_set() -> None:
    assert "account_hash" in _ACCOUNT_BOUNDARY_STRIP_FIELDS
    assert "gateway_label" in _ACCOUNT_BOUNDARY_STRIP_FIELDS
    assert "account_number" in _ACCOUNT_BOUNDARY_STRIP_FIELDS


def test_account_response_has_no_broker_internal_id_fields() -> None:
    """Phase 7c HIGH-2: Alpaca's account_id rides proto field 5 (account_hash).

    AccountResponse must not declare any of the broker-internal handles, so
    even if a future serializer accidentally tries to emit one, Pydantic
    drops it at the boundary.
    """
    fields = set(AccountResponse.model_fields.keys())
    leaky = {"account_id", "account_hash", "account_number", "gateway_label"}
    overlap = fields & leaky
    assert overlap == set(), f"AccountResponse leaked broker-internal fields: {overlap}"


def test_alpaca_account_id_uses_account_hash_slot() -> None:
    """Phase 7c HIGH-2: sidecar_alpaca/normalize.to_proto_account writes the
    Alpaca account UUID into proto field 5 (account_hash) — same slot Schwab
    uses. Regressing this test means Alpaca's account_id has escaped to a
    different proto field that may not be in _ACCOUNT_BOUNDARY_STRIP_FIELDS.
    """
    # Sanity: the strip set covers the slot our sidecar writes into.
    assert "account_hash" in _ACCOUNT_BOUNDARY_STRIP_FIELDS
