"""Symbol normalization helpers for Alpaca-sidecar order paths."""

from __future__ import annotations

QUOTE_CURRENCIES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC", "ETH")


def canonical_crypto_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if not raw:
        raise ValueError("symbol is required")
    if "/" in raw:
        base, quote = raw.split("/", 1)
        if not base or not quote:
            raise ValueError(f"invalid crypto symbol: {symbol}")
        return f"{base}/{quote}"
    for quote in QUOTE_CURRENCIES:
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[: -len(quote)]}/{quote}"
    raise ValueError(f"unsupported crypto quote currency: {symbol}")


def normalize_order_symbol(request):
    if getattr(request, "asset_class", "") == "CRYPTO":
        request.symbol = canonical_crypto_symbol(request.symbol)
    return request
