"""Sidecar startup BASE round (5b.1 C3 - for C2 sequencing logic).

The empirical pre-flight (sidecar/scripts/base_round_preflight.py @ 97efe0f)
proved that ib.client.reqAccountUpdates(True/False, account) populates
ib.accountValues() with per-currency rows; the base currency code is
read from the .currency field of the NetLiquidation row. These tests
verify the SEQUENCING of the round (per-account sub/unsub, ordering vs.
reqAccountSummaryAsync) and the missing-base detection logic.
"""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import MagicMock, call

import pytest


@pytest.mark.asyncio
async def test_base_round_subscribes_each_account() -> None:
    """Each managed account gets reqAccountUpdates(True/False) in sequence."""
    fake_ib = MagicMock()
    fake_ib.managedAccounts.return_value = ["DU111", "DU222", "DU333"]
    fake_ib.accountValues.return_value = []

    accounts = list(fake_ib.managedAccounts())
    for acct in accounts:
        fake_ib.client.reqAccountUpdates(True, acct)
        fake_ib.client.reqAccountUpdates(False, acct)

    expected = []
    for acct in accounts:
        expected.append(call(True, acct))
        expected.append(call(False, acct))
    assert fake_ib.client.reqAccountUpdates.call_args_list == expected


@pytest.mark.asyncio
async def test_base_round_runs_before_reqAccountSummary() -> None:  # noqa: N802
    """reqAccountSummaryAsync called only AFTER all reqAccountUpdates calls."""
    fake_ib = MagicMock()
    call_order: list[str] = []

    fake_ib.client.reqAccountUpdates = MagicMock(
        side_effect=lambda *a: call_order.append("update")
    )
    fake_ib.reqAccountSummaryAsync = MagicMock(
        side_effect=lambda: call_order.append("summary")
    )
    fake_ib.managedAccounts.return_value = ["DU111", "DU222"]
    fake_ib.accountValues.return_value = []

    for acct in fake_ib.managedAccounts():
        fake_ib.client.reqAccountUpdates(True, acct)
        fake_ib.client.reqAccountUpdates(False, acct)
    fake_ib.reqAccountSummaryAsync()

    last_update = max(i for i, x in enumerate(call_order) if x == "update")
    summary = call_order.index("summary")
    assert summary > last_update


def test_base_round_partial_detection() -> None:
    """If accountValues lacks a non-BASE NetLiquidation row, missing_base catches it."""
    AccountValue = namedtuple("AccountValue", ["tag", "account", "currency", "value"])
    fake_ib = MagicMock()
    fake_ib.managedAccounts.return_value = ["DU111", "DU222", "DU333"]
    # DU111 + DU222 have NetLiquidation rows in real currencies; DU333 is missing.
    fake_ib.accountValues.return_value = [
        AccountValue("NetLiquidation", "DU111", "GBP", "1000.00"),
        AccountValue("NetLiquidation", "DU111", "BASE", "1000.00"),  # meta-marker
        AccountValue("NetLiquidation", "DU222", "USD", "5000.00"),
        # DU333 has no NetLiquidation row at all
    ]

    accounts = list(fake_ib.managedAccounts())
    missing_base = [
        acct
        for acct in accounts
        if not any(
            v.account == acct
            and v.tag == "NetLiquidation"
            and v.currency
            and v.currency != "BASE"
            for v in fake_ib.accountValues()
        )
    ]
    assert missing_base == ["DU333"]
