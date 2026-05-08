from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.accounts import BrokerMaintenance
from app.schemas.orders import (
    ContractSummary,
    OrderListResponse,
    OrderResponse,
    PlaceOrderRequest,
    PositionSanityResult,
    PreviewRequest,
    PreviewResponse,
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    return None


def _preview_request(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "account_id": uuid4(),
        "conid": "265598",
        "side": "BUY",
        "order_type": "MARKET",
        "tif": "DAY",
        "qty": "1",
    }
    data.update(overrides)
    return data


def _contract_summary() -> ContractSummary:
    return ContractSummary(
        conid=265598,
        description="AAPL NASDAQ USD STK",
    )


def _position_sanity() -> PositionSanityResult:
    return PositionSanityResult(
        current_qty="10",
        new_qty_after_fill="11",
        sanity_multiplier="1.1",
        status="ok",
        requires_extra_attestation=False,
    )


def _order_response(**overrides: object) -> OrderResponse:
    now = datetime(2026, 4, 27, tzinfo=UTC)
    data: dict[str, object] = {
        "id": uuid4(),
        "account_id": uuid4(),
        "broker_order_id": "1001",
        # OrderResponse.conid was added in Phase 8b; required field.
        "conid": "265598",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "tif": "DAY",
        "qty": "1",
        "limit_price": None,
        "stop_price": None,
        "status": "pending_submit",
        "filled_qty": "0",
        "avg_fill_price": None,
        "notional": "0",
        "created_at": now,
        "updated_at": now,
        "last_event_at": None,
        "events": [],
    }
    data.update(overrides)
    return OrderResponse(**data)


def test_preview_request_qty_regex_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest(**_preview_request(qty="-1"))


def test_preview_request_qty_regex_accepts_8_decimals() -> None:
    request = PreviewRequest(**_preview_request(qty="1.00000001"))

    assert request.qty == "1.00000001"


def test_preview_request_market_no_prices() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest(**_preview_request(order_type="MARKET", limit_price="100"))


def test_preview_request_limit_requires_limit_price() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest(**_preview_request(order_type="LIMIT", limit_price=None))


def test_preview_response_serializes_decimal_as_fixed_point_8_digits() -> None:
    response = PreviewResponse(
        nonce="nonce-1",
        notional=Decimal("0"),
        notional_currency="USD",
        notional_filled_today=Decimal("1"),
        daily_notional_cap=Decimal("50000"),
        max_notional_per_order=Decimal("10000"),
        cap_status="ok",
        daily_cap_status="ok",
        position_sanity=_position_sanity(),
        contract_summary=_contract_summary(),
        warnings=[],
    )

    assert response.model_dump(mode="json")["notional"] == "0.00000000"


def test_position_sanity_classifies_high_at_5x() -> None:
    ok = PositionSanityResult.classify(Decimal("1"), Decimal("4"), "BUY")
    high = PositionSanityResult.classify(Decimal("1"), Decimal("4.1"), "BUY")

    assert ok.status == "ok"
    assert high.status == "high"


def test_position_sanity_classifies_extreme_at_10x() -> None:
    result = PositionSanityResult.classify(Decimal("10"), Decimal("101"), "BUY")

    assert result.status == "extreme"
    assert result.requires_extra_attestation is True


def test_place_order_request_uuid_validation() -> None:
    with pytest.raises(ValidationError):
        PlaceOrderRequest(**_preview_request(client_order_id="not-a-uuid"))


def test_order_response_strips_gateway_label_account_number() -> None:
    fields = OrderResponse.model_fields

    assert "gateway_label" not in fields
    assert "account_number" not in fields


def test_order_list_response_includes_kill_switch_active() -> None:
    response = OrderListResponse(
        orders=[_order_response()],
        broker_maintenance=BrokerMaintenance(active=False, window=None, until=None),
        kill_switch_active=False,
    )

    assert response.kill_switch_active is False
    assert isinstance(response.orders[0].id, UUID)


def test_position_sanity_sign_aware_for_sell() -> None:
    result = PositionSanityResult.classify(Decimal("10"), Decimal("3"), "SELL")

    assert result.new_qty_after_fill == "7.00000000"
    assert result.status == "ok"


def test_order_response_serializes_decimals_as_string() -> None:
    response = _order_response(
        qty=Decimal("10"),
        limit_price=Decimal("20"),
        order_type="LIMIT",
        filled_qty=Decimal("3"),
        avg_fill_price=Decimal("20"),
        notional=Decimal("200"),
    )

    payload = response.model_dump(mode="json")

    assert payload["qty"] == "10.00000000"
    assert payload["limit_price"] == "20.00000000"
    assert payload["filled_qty"] == "3.00000000"
    assert payload["avg_fill_price"] == "20.00000000"
    assert payload["notional"] == "200.00000000"
