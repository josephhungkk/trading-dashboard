"""Shared types + helpers for the streaming quote engine.

Phase 7b.1. The legacy ``QuoteProvider`` ABC + ``ProviderId`` enum from
``dashboard_old`` are retired — the sidecar gRPC interface (`StreamQuotes`
RPC + `QuoteMessage` proto, see ``proto/broker/v1/broker.proto``) replaces
them. What survives:

* canonical_id helpers — ``<asset_class>:<symbol>:<country>`` with an
  optional ``:<exchange>`` suffix for HIGH-5 dual-listing disambiguation
  (spec §4.1 line 285).
* UK pence guard — ``scale_gbx_if_needed`` for LSE GBp → GBP normalization.
* Exception hierarchy used by the engine for source-fallback decisions.
* Open-set source-id constants (strings) — matches the proto
  ``QuoteMessage.source`` string field.
* Exchange→country map — single source of truth, consumed by
  ``InstrumentResolver.from_legacy`` and the engine's source-router.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NewType

CanonicalId = NewType("CanonicalId", str)
SubscriptionToken = str


class SourceId:
    """Open-set source identifiers — lowercase strings emitted by sidecars
    in :class:`QuoteMessage.source`. Adding a new source is a constant
    addition here + a new sidecar streamer; never a proto change.
    """

    IBKR = "ibkr"
    FUTU = "futu"
    SCHWAB = "schwab"
    YFINANCE = "yfinance"
    COINBASE = "coinbase"
    OANDA = "oanda"
    FINNHUB = "finnhub"
    TWELVE_DATA = "twelve_data"
    ALPACA = "alpaca"
    POLYGON = "polygon"
    BINANCE = "binance"
    EODHD = "eodhd"
    TRADIER = "tradier"


class QuoteError(Exception):
    """Base class for quote-engine errors."""


class NotSupported(QuoteError):  # noqa: N818 — name preserved from spec / dashboard_old API
    """Source can't serve this (asset_class, country) combination — engine
    falls through to the next source. Not user-facing."""


class NotEntitled(QuoteError):  # noqa: N818 — name preserved from spec / dashboard_old API
    """Source could serve this symbol but the account lacks market-data
    entitlement (IBKR 10089/10090/354, Schwab feed miss). Engine falls
    through to the next source; operator may need to subscribe."""


class ProviderDown(QuoteError):  # noqa: N818 — name preserved from spec / dashboard_old API
    """Source is unreachable (gRPC AioRpcError, sidecar disconnected,
    OAuth refresh failed). Engine falls through to the next source."""


_UK_PENCE_EXCHANGES: frozenset[str] = frozenset({"LSE", "LSEETF"})
_PENCE_CURRENCIES: frozenset[str] = frozenset({"GBP", "GBX", "GBP."})


def scale_gbx_if_needed(
    value: Decimal | float | int | None,
    *,
    currency: str,
    exchange: str,
) -> Decimal | float | int | None:
    """Convert pence → pounds when (exchange, currency) indicate GBp pricing.

    LSE-listed GBP equities are quoted in pence by IBKR + yfinance + the
    Schwab international feed; a raw price needs ``/100`` for downstream
    arithmetic to stay sane (notional, NLV, allocation totals). Idempotent
    on non-UK / non-GBP / None inputs. Preserves Decimal vs float vs int
    input type — tests assert exact Decimal equality.
    """
    if value is None:
        return None
    if currency.upper() not in _PENCE_CURRENCIES:
        return value
    if exchange.upper() not in _UK_PENCE_EXCHANGES:
        return value
    if isinstance(value, Decimal):
        return value / Decimal("100")
    return value / 100


def canonical_key(
    *,
    asset_class: str,
    symbol: str,
    country: str,
    exchange: str | None = None,
) -> CanonicalId:
    """Build a canonical_id string.

    Format: ``<asset_class_lower>:<symbol_upper>:<country_upper>`` with an
    optional ``:<exchange_upper>`` suffix for HIGH-5 dual-listing
    disambiguation (e.g. ``stock:AAPL:US`` vs the rare second listing
    ``stock:AAPL:US:NYSE``).
    """
    base = f"{asset_class.lower()}:{symbol.upper()}:{country.upper()}"
    if exchange:
        return CanonicalId(f"{base}:{exchange.upper()}")
    return CanonicalId(base)


def canonical_id_components(canonical_id: str) -> tuple[str, str, str]:
    """Parse a canonical_id → ``(asset_class, symbol, country)``.

    Drops the optional 4th exchange component for callers that only need
    the base triple. Use :func:`canonical_id_with_exchange` to retain it.
    Raises :class:`ValueError` on a malformed canonical_id.
    """
    parts = canonical_id.split(":")
    if len(parts) < 3 or not all(parts[:3]):
        raise ValueError(f"malformed canonical_id: {canonical_id!r}")
    return parts[0], parts[1], parts[2]


def canonical_id_with_exchange(
    canonical_id: str,
) -> tuple[str, str, str, str | None]:
    """Parse a canonical_id → ``(asset_class, symbol, country, exchange | None)``."""
    parts = canonical_id.split(":")
    if len(parts) < 3 or not all(parts[:3]):
        raise ValueError(f"malformed canonical_id: {canonical_id!r}")
    exchange = parts[3] if len(parts) >= 4 and parts[3] else None
    return parts[0], parts[1], parts[2], exchange


_EXCHANGE_TO_COUNTRY: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "ARCA": "US",
    "AMEX": "US",
    "BATS": "US",
    "IEX": "US",
    "CBOE": "US",
    "SMART": "US",
    "LSE": "UK",
    "LSEETF": "UK",
    "SEHK": "HK",
    "HKEX": "HK",
    "TSE": "JP",
    "TSEJ": "JP",
    "XETRA": "DE",
    "IBIS": "DE",
    "AEB": "NL",
    "SBF": "FR",
    "SSE": "CN",
    "SZSE": "CN",
    "PAXOS": "CRYPTO",
    "CRYPTO": "CRYPTO",
}


def country_for_exchange(exchange: str | None) -> str | None:
    """Map a broker-native exchange code to a 2-letter country (or
    ``CRYPTO`` for venueless crypto). Returns ``None`` on unknown.

    Single source of truth for exchange→country resolution; consumed by
    :class:`InstrumentResolver.from_legacy` and the engine's source-router.
    """
    if not exchange:
        return None
    return _EXCHANGE_TO_COUNTRY.get(exchange.upper())
