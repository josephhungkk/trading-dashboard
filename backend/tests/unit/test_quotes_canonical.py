"""Phase 7b.1 B1 — canonical_id helpers + UK pence guard + exchange map.

Validates the format ``<asset_class>:<symbol>:<country>(:<exchange>)?`` that
the streaming-quote engine routes on, and the Decimal-preserving GBp →
GBP scaler that LSE-listed equities require for downstream NLV math.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.quotes.base import (
    NotEntitled,
    NotSupported,
    ProviderDown,
    QuoteError,
    SourceId,
    canonical_id_components,
    canonical_id_with_exchange,
    canonical_key,
    country_for_exchange,
    scale_gbx_if_needed,
)

# ── canonical_key ────────────────────────────────────────────────────────


def test_canonical_key_basic_us_stock() -> None:
    cid = canonical_key(asset_class="STOCK", symbol="AAPL", country="US")
    assert cid == "stock:AAPL:US"


def test_canonical_key_lowercases_asset_class_uppercases_others() -> None:
    cid = canonical_key(asset_class="stock", symbol="aapl", country="us")
    assert cid == "stock:AAPL:US"


def test_canonical_key_dual_listing_with_exchange_suffix() -> None:
    """HIGH-5 dual-listing disambiguation — second listing wins :EXCHANGE suffix."""
    primary = canonical_key(asset_class="STOCK", symbol="AAPL", country="US")
    secondary = canonical_key(asset_class="STOCK", symbol="AAPL", country="US", exchange="NYSE")
    assert primary == "stock:AAPL:US"
    assert secondary == "stock:AAPL:US:NYSE"
    assert primary != secondary


def test_canonical_key_hk_stock() -> None:
    assert canonical_key(asset_class="STOCK", symbol="0700", country="HK") == "stock:0700:HK"


def test_canonical_key_uk_stock() -> None:
    assert canonical_key(asset_class="STOCK", symbol="VOD", country="UK") == "stock:VOD:UK"


def test_canonical_key_returns_string() -> None:
    cid = canonical_key(asset_class="STOCK", symbol="MSFT", country="US")
    assert isinstance(cid, str)


# ── canonical_id_components ──────────────────────────────────────────────


def test_components_basic() -> None:
    assert canonical_id_components("stock:AAPL:US") == ("stock", "AAPL", "US")


def test_components_drops_exchange_suffix() -> None:
    """Plan: dual-listing form returns base triple; exchange via separate API."""
    assert canonical_id_components("stock:AAPL:US:NYSE") == ("stock", "AAPL", "US")


def test_components_index_with_dollar_symbol() -> None:
    """Schwab indices use a literal ``$`` prefix — ``$SPX`` survives intact."""
    assert canonical_id_components("idx:$SPX:US") == ("idx", "$SPX", "US")


def test_components_rejects_too_few_segments() -> None:
    with pytest.raises(ValueError, match="malformed"):
        canonical_id_components("stock:AAPL")


def test_components_rejects_empty_field() -> None:
    with pytest.raises(ValueError, match="malformed"):
        canonical_id_components("stock::US")


def test_components_rejects_trailing_colon() -> None:
    """Trailing ``:`` produces an empty 4th segment — reject (was silently
    accepted before B1 reviewer pass)."""
    with pytest.raises(ValueError, match="malformed"):
        canonical_id_components("stock:AAPL:US:")


def test_components_rejects_five_segments() -> None:
    """5+ segments are nonsense (no canonical form has them) — reject."""
    with pytest.raises(ValueError, match="malformed"):
        canonical_id_components("stock:AAPL:US:NYSE:EXTRA")


def test_with_exchange_returns_none_when_absent() -> None:
    assert canonical_id_with_exchange("stock:AAPL:US") == ("stock", "AAPL", "US", None)


def test_with_exchange_returns_exchange_when_present() -> None:
    assert canonical_id_with_exchange("stock:AAPL:US:NYSE") == (
        "stock",
        "AAPL",
        "US",
        "NYSE",
    )


# ── scale_gbx_if_needed ──────────────────────────────────────────────────


def test_scale_gbx_lse_gbp_decimal() -> None:
    """LSE GBP price is in pence by convention — divide by 100, preserve Decimal."""
    assert scale_gbx_if_needed(Decimal("12345.67"), currency="GBP", exchange="LSE") == Decimal(
        "123.4567"
    )


def test_scale_gbx_lse_gbx_explicit() -> None:
    assert scale_gbx_if_needed(Decimal("100"), currency="GBX", exchange="LSE") == Decimal("1")


def test_scale_gbx_lseetf() -> None:
    assert scale_gbx_if_needed(Decimal("500"), currency="GBP", exchange="LSEETF") == Decimal("5")


def test_scale_gbx_non_uk_passthrough() -> None:
    assert scale_gbx_if_needed(Decimal("150.00"), currency="USD", exchange="NASDAQ") == Decimal(
        "150.00"
    )


def test_scale_gbx_lse_usd_passthrough() -> None:
    """USD-denominated LSE listing — currency mismatch, unchanged."""
    assert scale_gbx_if_needed(Decimal("150"), currency="USD", exchange="LSE") == Decimal("150")


def test_scale_gbx_none_returns_none() -> None:
    assert scale_gbx_if_needed(None, currency="GBP", exchange="LSE") is None


def test_scale_gbx_preserves_float_type() -> None:
    """Plan: input type round-trips. float in → float out."""
    out = scale_gbx_if_needed(100.0, currency="GBP", exchange="LSE")
    assert out == 1.0
    assert isinstance(out, float)


def test_scale_gbx_lowercase_inputs() -> None:
    assert scale_gbx_if_needed(Decimal("100"), currency="gbp", exchange="lse") == Decimal("1")


def test_scale_gbx_rejects_nan() -> None:
    """Non-finite floats corrupt downstream NLV math — fail fast."""
    with pytest.raises(ValueError, match="non-finite"):
        scale_gbx_if_needed(float("nan"), currency="GBP", exchange="LSE")


def test_scale_gbx_rejects_inf() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        scale_gbx_if_needed(float("inf"), currency="GBP", exchange="LSE")


def test_scale_gbx_zero_decimal_passthrough() -> None:
    assert scale_gbx_if_needed(Decimal("0"), currency="GBP", exchange="LSE") == Decimal("0")


# ── country_for_exchange ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exchange,country",
    [
        ("NASDAQ", "US"),
        ("NYSE", "US"),
        ("ARCA", "US"),
        ("CBOE", "US"),
        ("LSE", "UK"),
        ("LSEETF", "UK"),
        ("SEHK", "HK"),
        ("HKEX", "HK"),
        ("XETRA", "DE"),
        ("PAXOS", "CRYPTO"),
        ("nasdaq", "US"),
    ],
)
def test_country_for_exchange_known(exchange: str, country: str) -> None:
    assert country_for_exchange(exchange) == country


def test_country_for_exchange_unknown_returns_none() -> None:
    assert country_for_exchange("MADEUP") is None


def test_country_for_exchange_empty_returns_none() -> None:
    assert country_for_exchange("") is None
    assert country_for_exchange(None) is None


# ── Exception hierarchy + SourceId constants ─────────────────────────────


def test_exception_hierarchy() -> None:
    assert issubclass(NotSupported, QuoteError)
    assert issubclass(NotEntitled, QuoteError)
    assert issubclass(ProviderDown, QuoteError)


def test_source_ids_are_lowercase_strings() -> None:
    """Every member's string value must equal its lowercased form — sidecars
    emit these verbatim into ``QuoteMessage.source``. Iterates the StrEnum
    so adding a new member without lowercasing fails the test automatically.
    """
    members = list(SourceId)
    assert len(members) >= 13  # current floor; new sources may extend
    for member in members:
        assert isinstance(member.value, str)
        assert member.value == member.value.lower()
        assert member == member.value  # StrEnum equality contract


def test_source_id_strenum_membership() -> None:
    """``SourceId.IBKR == 'ibkr'`` — callers can pass the enum or a plain
    lowercase string interchangeably."""
    assert SourceId.IBKR == "ibkr"
    assert SourceId.SCHWAB == "schwab"
    assert SourceId.ALPACA == "alpaca"
