from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.orders_service import place_order_for_bot


@pytest.mark.asyncio
async def test_place_order_for_bot_sets_attempt_kind(db_session, redis):
    """place_order_for_bot must use attempt_kind='bot_place_order'."""
    bot_id = uuid4()
    account_id = uuid4()
    captured: dict = {}

    async def fake_place_order(*, request_data, attempt_kind, **kw):
        captured["attempt_kind"] = attempt_kind
        captured["nonce"] = request_data.get("nonce", "")
        return MagicMock(order_id=uuid4())

    with patch("app.services.orders_service.place_order", fake_place_order):
        await place_order_for_bot(
            cfg=MagicMock(),
            db=db_session,
            redis=redis,
            registry=MagicMock(),
            capability=MagicMock(),
            bot_id=bot_id,
            account_id=account_id,
            conid=12345,
            side="BUY",
            qty=Decimal("10"),
            order_type="MKT",
            limit_price=None,
            stop_price=None,
            tif="DAY",
            algo_strategy=None,
            position_effect="OPEN",
        )

    assert captured["attempt_kind"] == "bot_place_order"
    assert f"bot:{bot_id}" in captured["nonce"]


@pytest.mark.asyncio
async def test_place_order_for_bot_nonce_format(db_session, redis):
    bot_id = uuid4()
    nonces: list[str] = []

    async def fake_place_order(*, request_data, attempt_kind, **kw):
        nonces.append(request_data.get("nonce", ""))
        return MagicMock(order_id=uuid4())

    with patch("app.services.orders_service.place_order", fake_place_order):
        await place_order_for_bot(
            cfg=MagicMock(),
            db=db_session,
            redis=redis,
            registry=MagicMock(),
            capability=MagicMock(),
            bot_id=bot_id,
            account_id=uuid4(),
            conid=12345,
            side="SELL",
            qty=Decimal("5"),
            order_type="LMT",
            limit_price=Decimal("100.50"),
            stop_price=None,
            tif="GTC",
            algo_strategy=None,
            position_effect="CLOSE",
        )

    assert nonces[0].startswith(f"bot:{bot_id}:")
