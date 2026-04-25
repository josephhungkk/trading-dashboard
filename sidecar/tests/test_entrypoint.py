"""Tests for sidecar.ibkr_sidecar entrypoint (Phase 4 Task 6)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sidecar.ibkr_sidecar import (
    _parse_args,
    _redact_processor,
    _redact_value,
    build_parser,
)

# ---------- argparse smoke ----------


def test_build_parser_accepts_full_flag_set() -> None:
    """All 9 spec'd CLI flags must parse."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "--label", "ibgw_live_us",
            "--gateway-port", "4001",
            "--grpc-port", "18001",
            "--tls-cert-pem", "/tmp/cert.pem",
            "--tls-key-pem", "/tmp/key.pem",
            "--tls-ca-bundle-pem", "/tmp/ca.pem",
            "--tls-crl-pem", "/tmp/crl.pem",
            "--log-dir", "/tmp/sidecar-log",
            "--state-dir", "/tmp/sidecar-state",
        ]
    )
    assert args.label == "ibgw_live_us"
    assert args.gateway_port == 4001
    assert args.grpc_port == 18001
    assert args.tls_cert_pem == Path("/tmp/cert.pem")
    assert args.tls_key_pem == Path("/tmp/key.pem")
    assert args.tls_ca_bundle_pem == Path("/tmp/ca.pem")
    assert args.tls_crl_pem == Path("/tmp/crl.pem")
    assert args.log_dir == Path("/tmp/sidecar-log")
    assert args.state_dir == Path("/tmp/sidecar-state")


def test_parse_args_defaults_state_dir_under_log_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When --state-dir is omitted it lands inside --log-dir."""
    for key in (
        "LABEL",
        "LOG_DIR",
        "STATE_DIR",
        "IBKR_SIDECAR_LABEL",
        "IBKR_SIDECAR_LOG_DIR",
        "IBKR_SIDECAR_STATE_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    args = _parse_args(["--label", "x", "--log-dir", "/tmp/sc"])
    assert args.log_dir == Path("/tmp/sc")
    assert args.state_dir == Path("/tmp/sc/state")


def test_parse_args_label_falls_back_to_default_for_log_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No label + no log-dir → log-dir uses 'default' label suffix."""
    for key in (
        "LABEL",
        "LOG_DIR",
        "STATE_DIR",
        "IBKR_SIDECAR_LABEL",
        "IBKR_SIDECAR_LOG_DIR",
        "IBKR_SIDECAR_STATE_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    args = _parse_args([])
    # _default_log_dir(label="default") wins when both label and log_dir are absent.
    assert "default" in str(args.log_dir)


def test_parse_args_reads_env_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars supply argparse defaults via the IBKR_SIDECAR_ prefix."""
    monkeypatch.setenv("IBKR_SIDECAR_LABEL", "ibgw_paper_hk")
    args = _parse_args([])
    assert args.label == "ibgw_paper_hk"


def test_parse_args_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_SIDECAR_LABEL", "from_env")
    args = _parse_args(["--label", "from_cli"])
    assert args.label == "from_cli"


# ---------- _redact_value (recursive secret scrubber) ----------


def test_redact_value_scrubs_top_level_secret_keys() -> None:
    out = _redact_value({"password": "hunter2", "username": "joseph"})
    assert out == {"password": "[REDACTED]", "username": "joseph"}


def test_redact_value_scrubs_nested_dict() -> None:
    """MED-2: a flat top-level redactor would miss broker={'password':...}."""
    out = _redact_value({"broker": {"host": "10.10.0.2", "password": "ibkr"}})
    assert out == {"broker": {"host": "10.10.0.2", "password": "[REDACTED]"}}


def test_redact_value_scrubs_nested_list_of_dicts() -> None:
    out = _redact_value(
        [{"name": "live_us", "api_key": "k1"}, {"name": "paper_hk", "api_key": "k2"}]
    )
    assert out == [
        {"name": "live_us", "api_key": "[REDACTED]"},
        {"name": "paper_hk", "api_key": "[REDACTED]"},
    ]


def test_redact_value_scrubs_tuples() -> None:
    out = _redact_value(({"token": "t"}, {"value": 1}))
    assert out == ({"token": "[REDACTED]"}, {"value": 1})


def test_redact_value_passes_through_scalars() -> None:
    assert _redact_value("plain") == "plain"
    assert _redact_value(42) == 42
    assert _redact_value(None) is None


def test_redact_value_covers_all_documented_keys() -> None:
    payload = {
        "password": "p",
        "secret": "s",
        "token": "t",
        "tls_key": "k",
        "private_key": "pk",
        "api_key": "ak",
        "username": "u",
    }
    out = _redact_value(payload)
    assert out == {
        "password": "[REDACTED]",
        "secret": "[REDACTED]",
        "token": "[REDACTED]",
        "tls_key": "[REDACTED]",
        "private_key": "[REDACTED]",
        "api_key": "[REDACTED]",
        "username": "u",
    }


# ---------- _redact_processor (structlog wiring) ----------


def test_redact_processor_redacts_event_dict() -> None:
    """The structlog processor scrubs both top-level and nested secrets."""
    logger = logging.getLogger("test")
    event = {
        "event": "config_loaded",
        "broker": {"host": "10.10.0.2", "password": "ibkr"},
        "api_key": "leaked",
        "context": "ok",
    }
    result = _redact_processor(logger, "info", event)
    assert result["event"] == "config_loaded"
    assert result["broker"] == {"host": "10.10.0.2", "password": "[REDACTED]"}
    assert result["api_key"] == "[REDACTED]"
    assert result["context"] == "ok"
