"""Static default tables for app_config (Phase 7c HIGH-3).

Compile-time defaults that an operator can override per-key via
``POST /api/admin/config``. ``SourceRouter._priority_list_for`` falls back
to this table per-key when the operator's ``quote_source_priority``
override doesn't supply a value for that ``<asset_class>.<country>`` key.

Per-key fallback (NOT whole-table) means an operator who has previously
pinned ``stock.UK`` to a custom list still gets the new ``crypto.US`` →
alpaca default landed in v0.7.3 — no silent regression.
"""

from __future__ import annotations

from typing import Final

# Updated v0.7.3 (Phase 7c) — alpaca primary for crypto.US, fallback for stock.US.
DEFAULT_QUOTE_SOURCE_PRIORITY: Final[dict[str, list[str]]] = {
    "stock.US": ["schwab", "alpaca", "ibkr"],
    "etf.US": ["schwab", "alpaca", "ibkr"],
    "index.US": ["schwab", "ibkr"],
    "crypto.US": ["alpaca"],  # 7b.2 will append "coinbase"
    "stock.UK": ["ibkr", "yfinance"],
    "stock.HK": ["futu"],
    "etf.HK": ["futu"],
    "warrant.HK": ["futu"],
    "cbbc.HK": ["futu"],
    "index.HK": ["futu"],
    "stock.EU": ["yfinance"],
    "stock.JP": ["yfinance"],
    "stock.AU": ["yfinance"],
    "stock.CA": ["yfinance"],
    "index.EU": ["ibkr"],
    "forex": [],  # 7b.2 ships oanda
}

# MED fix: seed defaults for ibkr_gateway_quote_assignment +
# ibkr_gateway_quote_fallback so SourceRouter.route_with_gateway works
# without requiring the operator to configure these keys post-deploy.
DEFAULT_IBKR_GATEWAY_QUOTE_ASSIGNMENT: Final[dict[str, str]] = {
    "stock.US": "isa-live",
    "etf.US": "isa-live",
    "index.US": "isa-live",
    "stock.UK": "isa-live",
    "stock.EU": "normal-live",
    "index.EU": "normal-live",
    "_default": "isa-live",
}

DEFAULT_IBKR_GATEWAY_QUOTE_FALLBACK: Final[list[str]] = [
    "normal-live",
    "normal-paper",
    "isa-paper",
]
