from uuid import uuid4

import pytest

from app.schemas.orders import OrderModifyRequest, PlaceOrderRequest, PreviewRequest

pytestmark = pytest.mark.no_db


def test_preview_request_accepts_algo_fields():
    req = PreviewRequest(
        account_id="00000000-0000-0000-0000-000000000001",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
        algo_strategy="TWAP",
        algo_params={"start_time": "10:00", "end_time": "14:00"},
    )
    assert req.algo_strategy == "TWAP"
    assert req.algo_params == {"start_time": "10:00", "end_time": "14:00"}


def test_place_order_request_inherits_algo():
    req = PlaceOrderRequest(
        account_id="00000000-0000-0000-0000-000000000001",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
        algo_strategy="ADAPTIVE",
        algo_params={"urgency": "URGENT"},
        client_order_id=uuid4(),
        nonce="abc",
    )
    assert req.algo_strategy == "ADAPTIVE"


def test_order_modify_request_accepts_algo_fields():
    req = OrderModifyRequest(
        nonce="abc",
        qty="100",
        order_type="MARKET",
        tif="DAY",
        algo_strategy="TWAP",
        algo_params={"start_time": "10:00", "end_time": "14:00"},
    )
    assert req.algo_strategy == "TWAP"


def test_order_modify_request_extra_fields_rejected():
    with pytest.raises(Exception, match="Extra inputs"):
        OrderModifyRequest(
            nonce="abc",
            qty="100",
            order_type="MARKET",
            tif="DAY",
            totally_unknown_field="x",
        )


def test_preview_request_no_algo_is_fine():
    req = PreviewRequest(
        account_id="00000000-0000-0000-0000-000000000001",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
    )
    assert req.algo_strategy is None
    assert req.algo_params is None
