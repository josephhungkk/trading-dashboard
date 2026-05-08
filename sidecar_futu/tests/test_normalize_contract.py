"""Contract normalization regressions."""
from __future__ import annotations

from sidecar_futu.normalize import contract_from_futu_row


def test_contract_from_futu_row_sets_hk_conid() -> None:
    contract = contract_from_futu_row(
        {
            "code": "HK.00700",
            "stock_name": "TENCENT",
            "security_type": "STOCK",
        }
    )

    assert contract.symbol == "HK.00700"
    assert contract.conid == "HK.00700"


def test_contract_from_futu_row_sets_us_conid() -> None:
    contract = contract_from_futu_row(
        {
            "code": "US.AAPL",
            "stock_name": "APPLE",
            "security_type": "STOCK",
            "currency": "USD",
        }
    )

    assert contract.symbol == "US.AAPL"
    assert contract.conid == "US.AAPL"
