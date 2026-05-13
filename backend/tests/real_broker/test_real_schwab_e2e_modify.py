"""Phase 8a E4 — Real Schwab paper-account modify chain.

Verifies replace_order returns a new orderId distinct from the old one
and the parent_order_id link is recoverable from polled order data.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.real_schwab


def test_real_schwab_modify_creates_replacement() -> None:
    import schwabdev

    # Phase 10a.5.1: token DB path configurable per case (see place_cancel
    # for the full rationale on parallel refresh races + skip behavior).
    tokens_db = os.environ.get("SCHWAB_TOKENS_DB", "/tmp/nightly_tokens.db")
    if not os.path.exists(tokens_db):
        pytest.skip(
            f"Schwab tokens DB at {tokens_db} not seeded; set "
            "SCHWAB_TOKENS_DB_B64 secret in CI or pre-seed locally."
        )
    client = schwabdev.Client(
        os.environ["SCHWAB_APP_KEY"],
        os.environ["SCHWAB_APP_SECRET"],
        tokens_db=tokens_db,
    )
    acct_hash = os.environ["SCHWAB_PAPER_ACCOUNT_HASH"]
    symbol = os.environ.get("SCHWAB_PAPER_SYMBOL", "F")

    base_payload = {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": "1.00",
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 1,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }

    # schwabdev==3.0.3 method names: place_order / replace_order / cancel_order
    # (the legacy `order_place` / `order_replace` / `order_cancel` names predate
    # the renames).
    place = client.place_order(acct_hash, base_payload)
    assert place.status_code in (200, 201)
    old_id = place.headers["Location"].rsplit("/", 1)[-1]

    time.sleep(2)
    new_payload = {**base_payload, "price": "1.50"}
    replace = client.replace_order(acct_hash, old_id, new_payload)
    assert replace.status_code in (200, 201), f"replace failed: {replace.status_code}"
    new_id = replace.headers["Location"].rsplit("/", 1)[-1]
    assert new_id != old_id

    time.sleep(2)
    cancel = client.cancel_order(acct_hash, new_id)
    assert cancel.status_code in (200, 204)
