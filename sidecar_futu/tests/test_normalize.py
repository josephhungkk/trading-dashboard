"""B5 — futu acc_list row -> proto Account mapping + unknown-trd_env skip."""

from __future__ import annotations

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.normalize import AccountSkipReason, account_from_futu_row


def test_account_real_trd_env_maps_to_live() -> None:
    acc, skip = account_from_futu_row(
        {"acc_id": 12345678, "trd_env": "REAL", "acc_type": "MARGIN"}
    )
    assert skip is None
    assert acc is not None
    assert acc.account_number == "12345678"
    assert acc.mode == broker_pb2.TradingMode.LIVE
    assert acc.gateway_label == "futu"


def test_account_simulate_trd_env_maps_to_paper() -> None:
    acc, skip = account_from_futu_row(
        {"acc_id": 99999999, "trd_env": "SIMULATE", "acc_type": "CASH"}
    )
    assert skip is None
    assert acc is not None
    assert acc.account_number == "99999999"
    assert acc.mode == broker_pb2.TradingMode.PAPER


def test_account_unknown_trd_env_skipped() -> None:
    acc, skip = account_from_futu_row(
        {"acc_id": 1, "trd_env": "PAPER_PROD", "acc_type": "CASH"}
    )
    assert skip == AccountSkipReason.UNKNOWN_TRD_ENV
    assert acc is None


def test_account_missing_trd_env_skipped() -> None:
    """Defensive: a row without trd_env (SDK contract change) must skip, not crash."""
    acc, skip = account_from_futu_row({"acc_id": 2, "acc_type": "CASH"})
    assert skip == AccountSkipReason.UNKNOWN_TRD_ENV
    assert acc is None
