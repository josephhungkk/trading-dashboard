"""B5 — futu acc_list row -> proto Account mapping + unknown-trd_env skip."""

from __future__ import annotations

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.normalize import (
    AccountMapped,
    AccountSkipped,
    AccountSkipReason,
    account_from_futu_row,
)


def test_account_real_trd_env_maps_to_live() -> None:
    result = account_from_futu_row(
        {"acc_id": 12345678, "trd_env": "REAL", "acc_type": "MARGIN"}
    )
    assert isinstance(result, AccountMapped)
    assert result.account.account_number == "12345678"
    assert result.account.mode == broker_pb2.TradingMode.LIVE
    assert result.account.gateway_label == "futu"


def test_account_simulate_trd_env_maps_to_paper() -> None:
    result = account_from_futu_row(
        {"acc_id": 99999999, "trd_env": "SIMULATE", "acc_type": "CASH"}
    )
    assert isinstance(result, AccountMapped)
    assert result.account.account_number == "99999999"
    assert result.account.mode == broker_pb2.TradingMode.PAPER


def test_account_unknown_trd_env_skipped() -> None:
    result = account_from_futu_row(
        {"acc_id": 1, "trd_env": "PAPER_PROD", "acc_type": "CASH"}
    )
    assert isinstance(result, AccountSkipped)
    assert result.reason == AccountSkipReason.UNKNOWN_TRD_ENV


def test_account_missing_trd_env_skipped() -> None:
    result = account_from_futu_row({"acc_id": 2, "acc_type": "CASH"})
    assert isinstance(result, AccountSkipped)
    assert result.reason == AccountSkipReason.UNKNOWN_TRD_ENV
