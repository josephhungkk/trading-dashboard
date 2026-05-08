"""Integration test: HIGH-db-1 — discoverer writes account_hash to broker_accounts.

Verifies that _account_from_proto preserves account_hash from the proto,
and that base.Account carries the field with the correct default.
"""

from __future__ import annotations

from app._generated.broker.v1 import broker_pb2
from app.brokers import base


def test_account_from_proto_preserves_account_hash() -> None:
    """_account_from_proto must carry account_hash through to base.Account."""
    from app.services.brokers import _account_from_proto

    proto = broker_pb2.Account(
        account_number="12345678",
        mode=broker_pb2.TradingMode.Value("PAPER"),
        gateway_label="schwab",
        currency_base="USD",
        account_hash="abc123hashvalue",
    )
    account = _account_from_proto(proto)
    assert account.account_hash == "abc123hashvalue"


def test_account_from_proto_empty_hash_for_non_schwab() -> None:
    """Non-Schwab protos have no account_hash — must default to empty string."""
    from app.services.brokers import _account_from_proto

    proto = broker_pb2.Account(
        account_number="U99999999",
        mode=broker_pb2.TradingMode.Value("LIVE"),
        gateway_label="ibkr-isa-live",
        currency_base="USD",
    )
    account = _account_from_proto(proto)
    assert account.account_hash == ""


def test_base_account_has_account_hash_field_with_value() -> None:
    """base.Account must accept account_hash and store it."""
    acct = base.Account(
        account_number="12345678",
        mode="PAPER",
        gateway_label="schwab",
        currency_base="USD",
        account_hash="hashval",
    )
    assert acct.account_hash == "hashval"


def test_base_account_account_hash_defaults_to_empty_string() -> None:
    """base.Account must default account_hash to empty string for non-Schwab."""
    acct = base.Account(
        account_number="U1234",
        mode="LIVE",
        gateway_label="ibkr-isa-live",
        currency_base="GBP",
    )
    assert acct.account_hash == ""
