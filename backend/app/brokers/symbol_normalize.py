"""Helpers for normalizing broker symbol formats.

Warning: ValueError messages include the raw user-supplied symbol. Callers that
surface errors to clients must catch and sanitize before returning.
"""

_QUOTE_CURRENCIES = ("USDT", "USDC", "USD", "EUR", "GBP")
_SEPARATORS = ("/", "-", "_")


def canonical_crypto_symbol(s: str) -> str:
    """Normalize a crypto pair to the canonical BTC/USD wire format."""
    original = s
    normalized = s.upper()

    if not normalized:
        raise ValueError(f"invalid_crypto_symbol: {original}")

    for separator in _SEPARATORS:
        if separator in normalized:
            parts = normalized.split(separator)
            if len(parts) != 2:
                raise ValueError(f"invalid_crypto_symbol: {original}")

            base, quote = parts
            if _is_valid_part(base) and _is_valid_quote(quote):
                return f"{base}/{quote}"

            raise ValueError(f"invalid_crypto_symbol: {original}")

    for quote in _QUOTE_CURRENCIES:
        if normalized.endswith(quote):
            base = normalized[: -len(quote)]
            if _is_valid_part(base):
                return f"{base}/{quote}"

    raise ValueError(f"invalid_crypto_symbol: {original}")


def _is_valid_part(value: str) -> bool:
    return bool(value) and value.isalpha()


def _is_valid_quote(value: str) -> bool:
    return value in _QUOTE_CURRENCIES
