"""Tests for telegram order_flow module."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db


def test_telegram_order_metrics_registered() -> None:
    from app.core import metrics

    assert hasattr(metrics, "TELEGRAM_ORDER_ATTEMPTS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_PREVIEWS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_CONFIRMS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_CANCELS_TOTAL")
    assert hasattr(metrics, "TELEGRAM_RATE_LIMITER_TRADE_BLOCK_TOTAL")
    assert hasattr(metrics, "TELEGRAM_ORDER_E2E_SECONDS")


def test_parse_market_order() -> None:
    from app.services.telegram.order_flow import ParsedOrder, parse_place_order

    result = parse_place_order("/place_order AAPL BUY 10")
    assert result == ParsedOrder(
        symbol="AAPL",
        side="BUY",
        qty="10",
        order_type="MARKET",
        tif="DAY",
        limit_price=None,
        stop_price=None,
    )


def test_parse_limit_order() -> None:
    from app.services.telegram.order_flow import ParsedOrder, parse_place_order

    result = parse_place_order("/place_order MSFT SELL 5 --limit 380.50")
    assert result == ParsedOrder(
        symbol="MSFT",
        side="SELL",
        qty="5",
        order_type="LIMIT",
        tif="DAY",
        limit_price="380.50",
        stop_price=None,
    )


def test_parse_stop_limit_order() -> None:
    from app.services.telegram.order_flow import ParsedOrder, parse_place_order

    result = parse_place_order("/place_order TSLA BUY 2 --stop 200.00 --limit 199.50")
    assert result == ParsedOrder(
        symbol="TSLA",
        side="BUY",
        qty="2",
        order_type="STOP_LIMIT",
        tif="DAY",
        limit_price="199.50",
        stop_price="200.00",
    )


def test_parse_gtc_tif() -> None:
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order AAPL BUY 1 --tif GTC")
    assert result is not None
    assert result.tif == "GTC"


def test_parse_stop_only_rejected() -> None:
    from app.services.telegram.order_flow import parse_place_order

    assert parse_place_order("/place_order AAPL BUY 5 --stop 150.00") is None


def test_parse_invalid_qty() -> None:
    from app.services.telegram.order_flow import parse_place_order

    assert parse_place_order("/place_order AAPL BUY notanumber") is None


def test_parse_unknown_flag() -> None:
    from app.services.telegram.order_flow import parse_place_order

    assert parse_place_order("/place_order AAPL BUY 10 --foo bar") is None


def test_parse_limit_too_many_decimals_rejected() -> None:
    from app.services.telegram.order_flow import parse_place_order

    assert parse_place_order("/place_order AAPL BUY 10 --limit 100.123456789") is None


def test_parse_html_injection_in_symbol_rejected() -> None:
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order <script>alert(1)</script> BUY 1")
    assert result is None


def test_parse_invalid_side() -> None:
    from app.services.telegram.order_flow import parse_place_order

    assert parse_place_order("/place_order AAPL HOLD 10") is None


def test_parse_unsupported_tif_rejected() -> None:
    from app.services.telegram.order_flow import parse_place_order

    assert parse_place_order("/place_order AAPL BUY 10 --tif IOC") is None


@pytest.mark.asyncio
async def test_resolve_instrument_from_db() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.telegram.order_flow import resolve_instrument

    mock_db = AsyncMock()
    row = MagicMock()
    row.conid = "265598"
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=row)))

    result = await resolve_instrument("AAPL", db=mock_db, registry=MagicMock(), broker_label="ibkr")
    assert result == "265598"


@pytest.mark.asyncio
async def test_resolve_instrument_fallback_broker() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.brokers.base import Contract
    from app.services.telegram.order_flow import resolve_instrument

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))

    contract = Contract(
        symbol="NVDA",
        exchange="SMART",
        currency="USD",
        asset_class="STOCK",
        conid="4815",
        local_symbol="NVDA",
    )
    mock_client = AsyncMock()
    mock_client.search_contracts = AsyncMock(return_value=[contract])
    mock_registry = MagicMock()
    mock_registry.get_client = AsyncMock(return_value=mock_client)

    result = await resolve_instrument(
        "NVDA", db=mock_db, registry=mock_registry, broker_label="ibkr"
    )
    assert result == "4815"
    assert mock_db.execute.call_count >= 2


@pytest.mark.asyncio
async def test_resolve_instrument_not_found() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.telegram.order_flow import resolve_instrument

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
    mock_client = AsyncMock()
    mock_client.search_contracts = AsyncMock(return_value=[])
    mock_registry = MagicMock()
    mock_registry.get_client = AsyncMock(return_value=mock_client)

    result = await resolve_instrument(
        "FAKE", db=mock_db, registry=mock_registry, broker_label="ibkr"
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_instrument_ambiguous_rejects() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.brokers.base import Contract
    from app.services.telegram.order_flow import resolve_instrument

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))

    contracts = [
        Contract(
            symbol="VOD",
            exchange="LSE",
            currency="GBP",
            asset_class="STOCK",
            conid="1",
            local_symbol="VOD",
        ),
        Contract(
            symbol="VOD",
            exchange="NASDAQ",
            currency="USD",
            asset_class="STOCK",
            conid="2",
            local_symbol="VOD",
        ),
    ]
    mock_client = AsyncMock()
    mock_client.search_contracts = AsyncMock(return_value=contracts)
    mock_registry = MagicMock()
    mock_registry.get_client = AsyncMock(return_value=mock_client)

    result = await resolve_instrument(
        "VOD", db=mock_db, registry=mock_registry, broker_label="ibkr"
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_instrument_broker_unavailable() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.brokers import BrokerSidecarUnavailable
    from app.services.telegram.order_flow import resolve_instrument

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
    mock_client = AsyncMock()
    mock_client.search_contracts = AsyncMock(side_effect=BrokerSidecarUnavailable("down"))
    mock_registry = MagicMock()
    mock_registry.get_client = AsyncMock(return_value=mock_client)

    result = await resolve_instrument(
        "AAPL", db=mock_db, registry=mock_registry, broker_label="ibkr"
    )
    assert result is None
