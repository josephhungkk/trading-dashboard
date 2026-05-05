"""Per-key merge precedence — operator partial override doesn't drop new defaults.

Phase 7c HIGH-3. SourceRouter._priority_list_for falls back to
DEFAULT_QUOTE_SOURCE_PRIORITY per-key when the operator's
quote_source_priority override doesn't supply a value for that
asset_class.country combination. This means new defaults shipped in
later phases land for keys the operator never overrode, even if other
keys are pinned.
"""

from __future__ import annotations

from app.models.instruments import AssetClass, Instrument
from app.services.config_defaults import DEFAULT_QUOTE_SOURCE_PRIORITY
from app.services.quotes.router import SourceHealthMap, SourceRouter


def _instrument(asset_class: AssetClass, canonical_id: str, *, exchange: str = "") -> Instrument:
    inst = Instrument(
        canonical_id=canonical_id,
        asset_class=asset_class,
        primary_exchange=exchange or "",
        currency="USD",
        meta={},
    )
    return inst


async def test_per_key_merge_keeps_new_defaults_when_partial_override() -> None:
    """Operator overrode stock.UK months ago. New crypto.US default still applies."""
    operator_override = {
        "quote_source_priority": {"stock.UK": ["ibkr"]},
    }
    router = SourceRouter(operator_override, SourceHealthMap())

    crypto_btc = _instrument(AssetClass.CRYPTO, "crypto:BTC:US")
    priority = router._priority_list_for(crypto_btc)
    assert priority == DEFAULT_QUOTE_SOURCE_PRIORITY["crypto.US"]
    assert priority[0] == "alpaca"


async def test_no_operator_override_returns_pure_defaults() -> None:
    router = SourceRouter({}, SourceHealthMap())
    crypto_btc = _instrument(AssetClass.CRYPTO, "crypto:BTC:US")
    priority = router._priority_list_for(crypto_btc)
    assert priority == DEFAULT_QUOTE_SOURCE_PRIORITY["crypto.US"]


async def test_operator_full_override_wins_for_that_key() -> None:
    """Operator's full override of stock.US replaces (not merges) the default list."""
    cfg = {
        "quote_source_priority": {"stock.US": ["ibkr"]},  # operator pin: only ibkr
    }
    router = SourceRouter(cfg, SourceHealthMap())
    aapl = _instrument(AssetClass.STOCK, "stock:AAPL:US")
    priority = router._priority_list_for(aapl)
    # Operator's pin wins entirely — no merge of the default's [schwab, alpaca, ibkr]
    assert priority == ["ibkr"]


async def test_unknown_key_returns_empty_list() -> None:
    """Asset/country combinations with no default and no override → no source."""
    router = SourceRouter({}, SourceHealthMap())
    obscure = _instrument(AssetClass.WARRANT, "warrant:1234:UK")
    priority = router._priority_list_for(obscure)
    assert priority == []
