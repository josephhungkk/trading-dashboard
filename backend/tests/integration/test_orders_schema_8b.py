"""Phase 8b T-0.1 - widened Pydantic order schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.orders import (
    OrderModifyRequest,
    PlaceOrderRequest,
    PreviewRequest,
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    return None


def _base_preview(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "account_id": str(uuid4()),
        "conid": "265598",
        "side": "BUY",
        "order_type": "MARKET",
        "tif": "DAY",
        "qty": "1",
    }
    body.update(overrides)
    return body


def _base_place(**overrides: object) -> dict[str, object]:
    body = _base_preview(**overrides)
    body["client_order_id"] = str(uuid4())
    body["nonce"] = "n-1"
    return body


@pytest.mark.parametrize(
    "order_type",
    [
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    ],
)
def test_preview_accepts_full_order_type_universe(order_type: str) -> None:
    extras: dict[str, object] = {"order_type": order_type, "tif": "DAY"}
    if order_type == "LIMIT":
        extras["limit_price"] = "1.00"
    elif order_type == "STOP":
        extras["stop_price"] = "1.00"
    elif order_type == "STOP_LIMIT":
        extras["limit_price"] = "1.00"
        extras["stop_price"] = "0.90"
    elif order_type == "TRAIL":
        extras["trail_offset"] = "0.10"
        extras["trail_offset_type"] = "AMOUNT"
    elif order_type == "TRAIL_LIMIT":
        extras["trail_offset"] = "0.10"
        extras["trail_offset_type"] = "AMOUNT"
        extras["trail_limit_offset"] = "0.05"
    elif order_type in {"LOC", "LOO"}:
        extras["limit_price"] = "1.00"
    PreviewRequest.model_validate(_base_preview(**extras))


@pytest.mark.parametrize("tif", ["DAY", "GTC", "IOC", "FOK"])
def test_preview_accepts_widened_tif_for_market(tif: str) -> None:
    PreviewRequest.model_validate(_base_preview(tif=tif))


def test_gtd_requires_expiry_date() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate(
            _base_preview(order_type="LIMIT", tif="GTD", limit_price="1.00")
        )


def test_gtd_with_expiry_date_passes() -> None:
    PreviewRequest.model_validate(
        _base_preview(
            order_type="LIMIT",
            tif="GTD",
            limit_price="1.00",
            expiry_date="2026-05-09",
        )
    )


def test_expiry_date_without_gtd_rejects() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate(
            _base_preview(
                order_type="LIMIT",
                tif="DAY",
                limit_price="1.00",
                expiry_date="2026-05-09",
            )
        )


@pytest.mark.parametrize("order_type", ["MOC", "MOO", "LOC", "LOO"])
@pytest.mark.parametrize("tif", ["GTC", "IOC", "FOK", "GTD"])
def test_session_bound_non_day_rejects_with_session_window_closed(
    order_type: str,
    tif: str,
) -> None:
    extras: dict[str, object] = {"order_type": order_type, "tif": tif}
    if order_type in {"LOC", "LOO"}:
        extras["limit_price"] = "1.00"
    if tif == "GTD":
        extras["expiry_date"] = "2026-05-09"
    with pytest.raises(ValidationError) as exc:
        PreviewRequest.model_validate(_base_preview(**extras))
    assert "session_window_closed" in str(exc.value)


def test_trail_requires_offset_and_type() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate(_base_preview(order_type="TRAIL"))


def test_trail_limit_requires_limit_offset() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate(
            _base_preview(
                order_type="TRAIL_LIMIT",
                trail_offset="0.10",
                trail_offset_type="AMOUNT",
            )
        )


def test_stop_limit_requires_both_prices() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate(_base_preview(order_type="STOP_LIMIT", stop_price="0.90"))


def test_loc_without_limit_price_rejects() -> None:
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate(_base_preview(order_type="LOC", tif="DAY"))


def test_place_order_inherits_widened_universe() -> None:
    PlaceOrderRequest.model_validate(
        _base_place(
            order_type="TRAIL",
            trail_offset="0.10",
            trail_offset_type="AMOUNT",
        )
    )


def test_modify_request_supports_trail() -> None:
    body = {
        "qty": "2",
        "limit_price": None,
        "stop_price": None,
        "order_type": "TRAIL",
        "tif": "DAY",
        "trail_offset": "0.20",
        "trail_offset_type": "AMOUNT",
        "nonce": "n-1",
    }
    OrderModifyRequest.model_validate(body)
