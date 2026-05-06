from decimal import Decimal

import pytest

from sidecar_schwab.normalize import (
    FillEvent,
    NormalizedOrder,
    StatusMapping,
    schwab_status_to_wire,
    schwab_to_wire_order,
    to_schwab_order_payload,
)


@pytest.mark.parametrize(
    ("schwab_status", "expected_wire", "expected_rank", "expected_terminal"),
    [
        ("AWAITING_PARENT_ORDER", "pending_submit", 0, False),
        ("PENDING_ACTIVATION", "pending_submit", 0, False),
        ("QUEUED", "submitted", 1, False),
        ("WORKING", "submitted", 1, False),
        ("PENDING_CANCEL", "cancel_requested", 2, False),
        ("PENDING_REPLACE", "modify_requested", 2, False),
        ("FILLED", "filled", 4, True),
        ("CANCELED", "cancelled", 4, True),
        ("REPLACED", "cancelled", 4, True),
        ("REJECTED", "rejected", 4, True),
        ("EXPIRED", "expired", 4, True),
    ],
)
def test_status_mapping(
    schwab_status: str,
    expected_wire: str,
    expected_rank: int,
    expected_terminal: bool,
) -> None:
    m = schwab_status_to_wire(schwab_status)
    assert m.wire_status == expected_wire
    assert m.rank == expected_rank
    assert m.terminal == expected_terminal


def test_replaced_status_emits_replaced_kind() -> None:
    m = schwab_status_to_wire("REPLACED")
    assert m.kind == "replaced"


def test_unknown_schwab_status_raises_warning() -> None:
    with pytest.warns(UserWarning, match="unknown schwab status"):
        m = schwab_status_to_wire("BOGUS_STATUS")
    assert m.wire_status == "submitted"


def test_executionleg_extracted_as_fill_event() -> None:
    order = {
        "orderId": 123,
        "status": "FILLED",
        "orderActivityCollection": [
            {
                "executionType": "FILL",
                "executionLegs": [
                    {
                        "legId": 1,
                        "price": 42.5,
                        "quantity": 10,
                        "time": "2026-05-06T12:00:00Z",
                    },
                ],
            }
        ],
    }
    result = schwab_to_wire_order(order, client_order_id="c-001")
    assert isinstance(result, NormalizedOrder)
    assert isinstance(result.status_mapping, StatusMapping)
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert isinstance(fill, FillEvent)
    assert fill.exec_id == "1"
    assert fill.price == Decimal("42.5")
    assert fill.avg_fill_price_inferred is False


def test_avg_fill_price_inferred_when_leg_price_null() -> None:
    order = {
        "orderId": 456,
        "status": "FILLED",
        "quantity": 100,
        "marketValue": 4250,
        "orderActivityCollection": [
            {
                "executionType": "FILL",
                "executionLegs": [
                    {
                        "legId": 2,
                        "price": None,
                        "quantity": 100,
                        "time": "2026-05-06T13:00:00Z",
                    },
                ],
            }
        ],
    }
    result = schwab_to_wire_order(order, client_order_id="c-002")
    assert len(result.fills) == 1
    assert result.fills[0].avg_fill_price_inferred is True
    assert result.fills[0].price == Decimal("42.5")


def test_to_schwab_order_payload_market_buy() -> None:
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
        symbol="AAPL",
    )

    assert payload == {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": "100",
                "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            }
        ],
    }


def test_to_schwab_order_payload_limit_includes_price() -> None:
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty="100",
        symbol="AAPL",
        limit_price="50.25",
    )

    assert payload["price"] == "50.25"


def test_to_schwab_order_payload_gtc_maps_to_good_till_cancel() -> None:
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="MARKET",
        tif="GTC",
        qty="100",
        symbol="AAPL",
    )

    assert payload["duration"] == "GOOD_TILL_CANCEL"


# ---------------------------------------------------------------------------
# Phase 8b order-type tests (T-S.1, T-S.2, T-S.5)
# ---------------------------------------------------------------------------

import datetime
from unittest.mock import MagicMock, patch


def test_trail_amount_payload() -> None:
    """TRAIL with trail_offset_type=AMOUNT → TRAILING_STOP + VALUE."""
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="TRAIL",
        tif="DAY",
        qty="100",
        symbol="AAPL",
        trail_offset="0.10",
        trail_offset_type="AMOUNT",
    )
    assert payload["orderType"] == "TRAILING_STOP"
    assert payload["trailingStopOffset"] == "0.10"
    assert payload["stopPriceLinkType"] == "VALUE"


def test_trail_percent_payload() -> None:
    """TRAIL with trail_offset_type=PERCENT → stopPriceLinkType==PERCENT."""
    payload = to_schwab_order_payload(
        side="SELL",
        order_type="TRAIL",
        tif="GTC",
        qty="50",
        symbol="TSLA",
        trail_offset="2.5",
        trail_offset_type="PERCENT",
    )
    assert payload["orderType"] == "TRAILING_STOP"
    assert payload["trailingStopOffset"] == "2.5"
    assert payload["stopPriceLinkType"] == "PERCENT"


def test_trail_limit_payload() -> None:
    """TRAIL_LIMIT maps to TRAILING_STOP_LIMIT with stopPrice set."""
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="TRAIL_LIMIT",
        tif="DAY",
        qty="10",
        symbol="MSFT",
        trail_offset="0.10",
        trail_offset_type="AMOUNT",
        trail_limit_offset="0.05",
    )
    assert payload["orderType"] == "TRAILING_STOP_LIMIT"
    assert payload["trailingStopOffset"] == "0.10"
    assert payload["stopPrice"] == "0.05"
    assert payload["stopPriceLinkType"] == "VALUE"


def test_moc_payload() -> None:
    """MOC → MARKET_ON_CLOSE, session NORMAL."""
    payload = to_schwab_order_payload(
        side="SELL",
        order_type="MOC",
        tif="DAY",
        qty="200",
        symbol="SPY",
    )
    assert payload["orderType"] == "MARKET_ON_CLOSE"
    assert payload["session"] == "NORMAL"


def test_moo_payload() -> None:
    """MOO → MARKET_ON_OPEN, session AM."""
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="MOO",
        tif="DAY",
        qty="100",
        symbol="QQQ",
    )
    assert payload["orderType"] == "MARKET_ON_OPEN"
    assert payload["session"] == "AM"


def test_loc_payload() -> None:
    """LOC → LIMIT_ON_CLOSE, session NORMAL, price set."""
    payload = to_schwab_order_payload(
        side="SELL",
        order_type="LOC",
        tif="DAY",
        qty="50",
        symbol="AAPL",
        limit_price="10.00",
    )
    assert payload["orderType"] == "LIMIT_ON_CLOSE"
    assert payload["session"] == "NORMAL"
    assert payload["price"] == "10.00"


def test_loo_payload() -> None:
    """LOO → LIMIT_ON_OPEN, session AM, price set."""
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="LOO",
        tif="DAY",
        qty="25",
        symbol="NVDA",
        limit_price="10.00",
    )
    assert payload["orderType"] == "LIMIT_ON_OPEN"
    assert payload["session"] == "AM"
    assert payload["price"] == "10.00"


def _make_mock_calendar(close_dt: datetime.datetime) -> MagicMock:
    """Build a minimal exchange_calendars mock (no pandas dependency)."""
    # Build a minimal Timestamp-like object so the normalize code can call
    # session_close.to_pydatetime() without importing pandas in tests.
    ts_mock = MagicMock()
    ts_mock.to_pydatetime.return_value = close_dt

    session_row = MagicMock()
    session_row.__getitem__ = lambda self, key: ts_mock

    mock_cal = MagicMock()
    mock_cal.schedule.loc.__getitem__ = lambda self, key: session_row
    return mock_cal


def test_gtd_payload_has_cancel_time() -> None:
    """LIMIT + GTD → duration==GOOD_TILL_CANCEL + non-empty cancelTime with 'T'."""
    close_dt = datetime.datetime(2026, 5, 29, 20, 0, 0, tzinfo=datetime.timezone.utc)
    mock_cal = _make_mock_calendar(close_dt)

    mock_xcals = MagicMock()
    mock_xcals.get_calendar.return_value = mock_cal

    with patch.dict("sys.modules", {"exchange_calendars": mock_xcals}):
        payload = to_schwab_order_payload(
            side="BUY",
            order_type="LIMIT",
            tif="GTD",
            qty="100",
            symbol="AAPL",
            limit_price="50.00",
            exchange="NYSE",
            expiry_date=datetime.date(2026, 5, 29),
        )

    assert payload["duration"] == "GOOD_TILL_CANCEL"
    assert "cancelTime" in payload
    cancel_time = payload["cancelTime"]
    assert "T" in cancel_time
    assert len(cancel_time) > 0


def test_gtd_missing_expiry_raises() -> None:
    """LIMIT + GTD without expiry_date → ValueError('gtd_order_missing_expiry_date')."""
    with pytest.raises(ValueError, match="gtd_order_missing_expiry_date"):
        to_schwab_order_payload(
            side="BUY",
            order_type="LIMIT",
            tif="GTD",
            qty="100",
            symbol="AAPL",
            limit_price="50.00",
            # no expiry_date
        )
