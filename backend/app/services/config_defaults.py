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


# Phase 11a-A1: AI router capability map default.
#
# Per-capability fallback chain. Each entry is an ordered list — first
# is preferred, subsequent are tried only when the first is unavailable
# (rate-limited, provider key missing, GPU contended, etc.). Cloud
# entries are auto-removed by resolve_models() when capability is
# LOCAL_ONLY or force_local_only=True.
#
# Read-only fallback table, same pattern as DEFAULT_QUOTE_SOURCE_PRIORITY:
# operators override per-capability via PUT /api/admin/config namespace
# ``ai_router.capability_map``; resolve_models() consults the override
# first, then this constant when a capability isn't overridden.
DEFAULT_AI_ROUTER_CAPABILITY_MAP: Final[dict[str, list[dict[str, str]]]] = {
    "LOCAL_ONLY": [
        {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
        {"provider": "ollama-nuc-llama", "model": "llama3.2:8b"},
        {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
    ],
    "STRUCTURED_OUTPUT": [
        {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
        {"provider": "openai-gpt4o", "model": "gpt-4o"},
    ],
    "LONG_CONTEXT": [
        {"provider": "gemini-pro", "model": "gemini-2.5-pro"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
    "REALTIME_SENTIMENT": [
        {"provider": "xai-grok", "model": "grok-2-latest"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
    "REASONING": [
        {"provider": "ollama-heavy-70b", "model": "llama3.3:70b"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
        {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
    ],
    "BULK_CHEAP": [
        {"provider": "gemini-pro", "model": "gemini-2.5-flash"},
        {"provider": "openai-gpt4o", "model": "gpt-4o-mini"},
    ],
    "NUMERICAL": [
        {"provider": "openai-gpt4o", "model": "gpt-4o"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
    "CODING": [
        {"provider": "ollama-heavy", "model": "qwen2.5-coder:32b"},
        {"provider": "anthropic-sonnet", "model": "claude-sonnet-4-6"},
    ],
}
