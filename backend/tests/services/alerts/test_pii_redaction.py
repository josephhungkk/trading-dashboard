"""Phase 11b chunk B6: alerts PII log-redaction tests.

The redaction processor must scrub three fields that can carry portfolio
context the user mentioned in their NL alert input:

- ``original_nl``: the raw user input ("alert when my IRA at Schwab drops
  below 200K NLV") — verbatim PII.
- ``predicate_json``: the parsed predicate tree may include account_id /
  broker_id slots populated from the NL.
- ``evaluated_values``: snapshot of state at fire time — can contain NLV,
  position sizes, cost-basis values.
"""

from __future__ import annotations

from app.core import logging as logging_config


def test_account_keys_includes_alerts_pii_fields() -> None:
    for field in ("original_nl", "predicate_json", "evaluated_values"):
        assert field in logging_config._ACCOUNT_KEYS, (
            f"{field!r} must be in _ACCOUNT_KEYS so structlog redacts it"
        )


def test_redact_original_nl_at_top_level() -> None:
    event = {
        "event": "rule_eval_failed",
        "alert_id": 42,
        "original_nl": "alert when my IRA at Schwab drops below 200K NLV",
        "error": "boom",
    }
    redacted = logging_config._redact_secrets(None, "warning", event)
    assert redacted["original_nl"] == logging_config._REDACTED
    assert redacted["alert_id"] == 42  # non-PII keys survive
    assert redacted["error"] == "boom"


def test_redact_predicate_json_at_top_level() -> None:
    event = {
        "event": "rule_created",
        "predicate_json": {
            "kind": "order_event",
            "account_id": "abc-123",
            "broker_id": "schwab",
        },
    }
    redacted = logging_config._redact_secrets(None, "info", event)
    assert redacted["predicate_json"] == logging_config._REDACTED


def test_redact_evaluated_values_at_top_level() -> None:
    event = {
        "event": "alert_fired",
        "alert_id": 7,
        "evaluated_values": {
            "nlv_usd": 195_000.0,
            "position_qty": {"AAPL": 100},
        },
    }
    redacted = logging_config._redact_secrets(None, "info", event)
    assert redacted["evaluated_values"] == logging_config._REDACTED
    assert redacted["alert_id"] == 7


def test_redact_nested_predicate_json_in_dict() -> None:
    """Nested dicts also get scrubbed (the value walker recurses)."""
    event = {
        "event": "rule_create_audit",
        "rule": {
            "id": 1,
            "original_nl": "alert when my IRA drops below 200K NLV",
            "predicate_json": {"kind": "unknown", "raw_text": "..."},
            "evaluated_values": {"nlv_usd": 195_000},
        },
    }
    redacted = logging_config._redact_secrets(None, "info", event)
    rule = redacted["rule"]
    assert rule["id"] == 1  # non-PII nested keys survive
    assert rule["original_nl"] == logging_config._REDACTED
    assert rule["predicate_json"] == logging_config._REDACTED
    assert rule["evaluated_values"] == logging_config._REDACTED
