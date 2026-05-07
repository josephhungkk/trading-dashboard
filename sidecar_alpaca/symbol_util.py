"""Symbol normalization helpers for Alpaca-sidecar order paths.

QUOTE_CURRENCIES intentionally widens beyond
backend/app/brokers/symbol_normalize._QUOTE_CURRENCIES to include BTC and ETH
because Alpaca's crypto stream emits BTC- and ETH-quoted pairs that the sidecar
must canonicalize on egress. The backend module only covers ingress validation
where USD/USDT/USDC/EUR/GBP are the supported quotes.
"""

from __future__ import annotations

QUOTE_CURRENCIES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC", "ETH")
_MAX_SAFE_REPR_LEN = 16


def canonical_crypto_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if not raw:
        raise ValueError("symbol is required")
    if "/" in raw:
        base, quote = raw.split("/", 1)
        if not base or not quote:
            raise ValueError(f"invalid crypto symbol: {raw[:_MAX_SAFE_REPR_LEN]!r}")
        return f"{base}/{quote}"
    for quote in QUOTE_CURRENCIES:
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[: -len(quote)]}/{quote}"
    # Bound length + repr to neutralize log-injection chars (chunk-C sec H-2).
    raise ValueError(
        f"unsupported crypto quote currency: {raw[:_MAX_SAFE_REPR_LEN]!r}"
    )


def normalize_order_symbol(request):
    if getattr(request, "asset_class", "") == "CRYPTO":
        request.symbol = canonical_crypto_symbol(request.symbol)
    return request
