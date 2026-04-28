"""5b.1 C1 - empirical pre-flight for the BASE-round design.

Run on the dev box (paper gateway 4002 must be up). Validates that:
1. client.reqAccountUpdates(True, account) populates accountValues with BASE
2. accountValues retains BASE after client.reqAccountUpdates(False, account)
3. The sequence works for ALL managed accounts, not just the first one

ib_async API note: the high-level IB.reqAccountUpdates wrapper subscribes
once and reuses a single-keyed future, so cycling sub/unsub via the wrapper
hangs (the second future never resolves). The underlying raw client at
ib.client.reqAccountUpdates(subscribe: bool, acctCode: str) supports the
cycle: just send the protocol message and let the wrapper buffer
account_values events into ib.accountValues() between subscribe/unsubscribe.

Usage:
    cd sidecar && uv run python scripts/base_round_preflight.py
Exit code: 0 if BASE present for all accounts after the round, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import sys

from ib_async import IB


async def main() -> int:
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=999, timeout=30)
    print(f"connected; managed_accounts = {ib.managedAccounts()}")

    accounts = list(ib.managedAccounts())
    for acct in accounts:
        print(f"-- subscribing BASE for {acct} --")
        ib.client.reqAccountUpdates(True, acct)
        await asyncio.sleep(2.0)
        ib.client.reqAccountUpdates(False, acct)
        await asyncio.sleep(0.3)

    print("-- inspecting accountValues after round --")
    # Per ib_async/wrapper.py:527 updateAccountValue stores key=(account, tag, currency).
    # BASE is a CURRENCY, not a tag. The base currency code lands in the VALUE
    # field of rows where tag in {"Currency", "AccountCurrency", ...} and
    # currency == "BASE". Print all distinct (tag, currency) shapes for a single
    # account first to discover the right filter empirically.
    av = list(ib.accountValues())
    print(f"  total AccountValue rows: {len(av)}")
    if av:
        sample_acct = accounts[0]
        sample_rows = [v for v in av if v.account == sample_acct]
        print(f"  distinct (tag, currency) for {sample_acct}:")
        seen: set[tuple[str, str]] = set()
        for v in sample_rows:
            key = (v.tag, v.currency)
            if key in seen:
                continue
            seen.add(key)
            print(f"    tag={v.tag!r:32s} currency={v.currency!r:8s} value={v.value!r}")

    missing: list[str] = []
    for acct in accounts:
        # The (tag='Currency', currency='BASE', value='BASE') row is just a
        # meta-marker. The real base currency code is the currency on the
        # NetLiquidation row (which IBKR reports in the account's base
        # currency only), or equivalently the currency where ExchangeRate=1.00
        # and currency != 'BASE'.
        base = next(
            (
                v.currency
                for v in av
                if v.account == acct
                and v.tag == "NetLiquidation"
                and v.currency
                and v.currency != "BASE"
            ),
            None,
        )
        print(f"  {acct}: base_currency = {base!r}")
        if not base:
            missing.append(acct)

    ib.disconnect()
    if missing:
        print(f"FAIL: BASE missing for {missing}", file=sys.stderr)
        return 1
    print("PASS: BASE present for all accounts")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
