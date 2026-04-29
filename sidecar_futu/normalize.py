"""Normalize Futu SDK payloads into broker proto messages."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from sidecar_futu._generated.broker.v1 import broker_pb2


class AccountSkipReason(StrEnum):
    UNKNOWN_TRD_ENV = "unknown_trd_env"


def account_from_futu_row(
    row: dict[str, Any],
) -> tuple[broker_pb2.Account | None, AccountSkipReason | None]:
    trd_env = row.get("trd_env")
    if trd_env == "REAL":
        mode = broker_pb2.TradingMode.LIVE
    elif trd_env == "SIMULATE":
        mode = broker_pb2.TradingMode.PAPER
    else:
        return None, AccountSkipReason.UNKNOWN_TRD_ENV

    return (
        broker_pb2.Account(
            account_number=str(row["acc_id"]),
            mode=mode,
            gateway_label="futu",
        ),
        None,
    )
