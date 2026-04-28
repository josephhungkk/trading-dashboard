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
    missing: list[str] = []
    for acct in accounts:
        base = next(
            (v.value for v in ib.accountValues() if v.tag == "BASE" and v.account == acct),
            None,
        )
        print(f"  {acct}: BASE = {base!r}")
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
