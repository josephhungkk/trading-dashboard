"""Phase 7a F5 — Schwab JSON test data factories.

Forked from /mnt/c/Dashboard_old/backend/tests/test_schwab_*.py.
Returns dicts in the exact shape that schwabapi.com /trader/v1/* endpoints
return, for use in sidecar handler unit tests + backend integration tests.
"""

from __future__ import annotations

from typing import Any


def make_account_summary_json(
    *,
    account_number: str = "12345678",
    account_type: str = "MARGIN",
    nlv: float = 100_000.0,
    cash: float = 25_000.0,
    buying_power: float = 200_000.0,
    prev_nlv: float = 99_500.0,
) -> dict[str, Any]:
    """Schwab /trader/v1/accounts/{hash}?fields=positions response shape."""
    return {
        "securitiesAccount": {
            "accountNumber": account_number,
            "type": account_type,
            "currentBalances": {
                "liquidationValue": nlv,
                "cashBalance": cash,
                "buyingPower": buying_power,
            },
            "initialBalances": {
                "liquidationValue": prev_nlv,
            },
        },
    }


def make_position_json(
    *,
    symbol: str = "AAPL",
    asset_type: str = "EQUITY",
    cusip: str = "037833100",
    long_qty: int = 100,
    short_qty: int = 0,
    avg_price: float = 150.0,
    market_value: float = 17_500.0,
    daily_pnl: float = 250.0,
) -> dict[str, Any]:
    """Schwab position element inside securitiesAccount.positions[]."""
    return {
        "instrument": {
            "symbol": symbol,
            "assetType": asset_type,
            "cusip": cusip,
        },
        "longQuantity": long_qty,
        "shortQuantity": short_qty,
        "averagePrice": avg_price,
        "marketValue": market_value,
        "currentDayProfitLoss": daily_pnl,
    }


def make_order_json(
    *,
    order_id: int = 100,
    status: str = "WORKING",
    order_type: str = "LIMIT",
    duration: str = "DAY",
    price: float = 150.0,
    quantity: int = 10,
    filled_quantity: int = 0,
    symbol: str = "AAPL",
    asset_type: str = "EQUITY",
    instruction: str = "BUY",
) -> dict[str, Any]:
    """Schwab /trader/v1/accounts/{hash}/orders element (no executionLegs)."""
    return {
        "orderId": order_id,
        "status": status,
        "orderType": order_type,
        "duration": duration,
        "price": price,
        "quantity": quantity,
        "filledQuantity": filled_quantity,
        "orderLegCollection": [
            {
                "instrument": {"symbol": symbol, "assetType": asset_type},
                "instruction": instruction,
            }
        ],
    }


def make_order_with_activity_json(
    *,
    order_id: int = 100,
    avg_fill_price: float = 149.50,
    quantity: int = 10,
    filled_quantity: int = 10,
    symbol: str = "AAPL",
    instruction: str = "BUY",
) -> dict[str, Any]:
    """FILLED order with executionLegs (M2 — avg_fill_price source)."""
    base = make_order_json(
        order_id=order_id,
        status="FILLED",
        quantity=quantity,
        filled_quantity=filled_quantity,
        symbol=symbol,
        instruction=instruction,
    )
    base["orderActivityCollection"] = [
        {
            "activityType": "EXECUTION",
            "executionLegs": [
                {"price": avg_fill_price, "quantity": filled_quantity},
            ],
        }
    ]
    return base


def make_account_numbers_response(
    *,
    accounts: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Schwab /trader/v1/accountNumbers response."""
    if accounts is None:
        accounts = [("12345678", "HASH_A"), ("87654321", "HASH_B")]
    return [{"accountNumber": n, "hashValue": h} for n, h in accounts]
