"""Tests for telegram order_flow module."""

from __future__ import annotations

from typing import Any

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


def _make_msg(text: str, chat_id: int = 111, from_user_id: int = 222) -> Any:
    from unittest.mock import AsyncMock, MagicMock

    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.from_user.id = from_user_id
    msg.answer = AsyncMock()
    return msg


def _make_entry(chat_id: int = 111, from_user_id: int = 222) -> Any:
    from app.services.telegram.allowlist import AllowlistEntry

    return AllowlistEntry(
        chat_id=chat_id, from_user_id=from_user_id, jwt_subject="user@test", label="Alice"
    )


@pytest.mark.asyncio
async def test_single_account_no_disambiguation() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_place_order

    msg = _make_msg("/place_order AAPL BUY 1")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    account_row = MagicMock()
    account_row.id = "acct-uuid-1"
    account_row.alias = "IBKR1"
    account_row.broker = "IBKR"
    account_row.mode = "paper"
    account_row.currency = "USD"
    account_row.gateway_label = "ibkr"

    mock_db = AsyncMock()
    instr_row = MagicMock()
    instr_row.conid = "265598"
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(fetchall=MagicMock(return_value=[account_row])),
            MagicMock(fetchone=MagicMock(return_value=instr_row)),
        ]
    )

    mock_preview = MagicMock()
    mock_preview.risk_blockers = []
    mock_preview.risk_warnings = []
    mock_preview.position_sanity = MagicMock(requires_extra_attestation=False)
    mock_preview.notional = "1820.00"
    mock_preview.notional_currency = "USD"
    mock_preview.nonce = "testnonce"

    with patch(
        "app.services.telegram.order_flow.preview_order", AsyncMock(return_value=mock_preview)
    ):
        await handle_place_order(
            msg,
            entry=entry,
            db=mock_db,
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert any("pending" in c for c in set_calls)
    assert not any("acct_select" in c for c in set_calls)


@pytest.mark.asyncio
async def test_multi_account_disambiguation_written() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_place_order

    msg = _make_msg("/place_order AAPL BUY 1")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    def _acct(alias: str) -> MagicMock:
        r = MagicMock()
        r.id = f"uuid-{alias}"
        r.alias = alias
        r.broker = "IBKR"
        r.mode = "paper"
        r.currency = "USD"
        r.gateway_label = "ibkr"
        return r

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            fetchall=MagicMock(return_value=[_acct("IBKR1"), _acct("IBKR2"), _acct("FUTU1")])
        )
    )

    with patch("app.services.telegram.order_flow.preview_order", AsyncMock()):
        await handle_place_order(
            msg,
            entry=entry,
            db=mock_db,
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    reply = msg.answer.call_args.args[0]
    assert "1." in reply
    assert "IBKR1" in reply
    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert any("acct_select" in c for c in set_calls)


@pytest.mark.asyncio
async def test_preview_with_blockers_no_pending_written() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_place_order

    msg = _make_msg("/place_order AAPL BUY 1")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    account_row = MagicMock()
    account_row.id = "acct-uuid-1"
    account_row.alias = "IBKR1"
    account_row.broker = "IBKR"
    account_row.mode = "paper"
    account_row.currency = "USD"
    account_row.gateway_label = "ibkr"

    instr_row = MagicMock()
    instr_row.conid = "265598"
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(fetchall=MagicMock(return_value=[account_row])),
            MagicMock(fetchone=MagicMock(return_value=instr_row)),
        ]
    )

    mock_preview = MagicMock()
    mock_preview.risk_blockers = [{"code": "max_notional_exceeded", "message": "Too large"}]
    mock_preview.risk_warnings = []
    mock_preview.position_sanity = MagicMock(requires_extra_attestation=False)

    with patch(
        "app.services.telegram.order_flow.preview_order", AsyncMock(return_value=mock_preview)
    ):
        await handle_place_order(
            msg,
            entry=entry,
            db=mock_db,
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert not any("pending" in c for c in set_calls)
    reply = msg.answer.call_args.args[0]
    assert "BLOCKED" in reply or "blocked" in reply.lower()


@pytest.mark.asyncio
async def test_extreme_position_change_rejected_at_telegram() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_place_order

    msg = _make_msg("/place_order TSLA SELL 100")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    account_row = MagicMock()
    account_row.id = "acct-uuid-1"
    account_row.alias = "IBKR1"
    account_row.broker = "IBKR"
    account_row.mode = "paper"
    account_row.currency = "USD"
    account_row.gateway_label = "ibkr"

    instr_row = MagicMock()
    instr_row.conid = "76792991"
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            MagicMock(fetchall=MagicMock(return_value=[account_row])),
            MagicMock(fetchone=MagicMock(return_value=instr_row)),
        ]
    )

    mock_preview = MagicMock()
    mock_preview.risk_blockers = []
    mock_preview.risk_warnings = []
    mock_preview.position_sanity = MagicMock(requires_extra_attestation=True)

    with patch(
        "app.services.telegram.order_flow.preview_order", AsyncMock(return_value=mock_preview)
    ):
        await handle_place_order(
            msg,
            entry=entry,
            db=mock_db,
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert not any("pending" in c for c in set_calls)
    reply = msg.answer.call_args.args[0]
    assert "web" in reply.lower()


import json as _json  # noqa: E402


@pytest.mark.asyncio
async def test_account_selection_valid_reply() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_account_selection

    msg = _make_msg("2")
    entry = _make_entry()
    mock_redis = AsyncMock()

    acct_select_data = {
        "order": {
            "symbol": "AAPL",
            "side": "BUY",
            "qty": "10",
            "order_type": "MARKET",
            "tif": "DAY",
            "limit_price": None,
            "stop_price": None,
        },
        "accounts": [
            {
                "id": "uuid-1",
                "alias": "IBKR1",
                "broker": "IBKR",
                "mode": "paper",
                "currency": "USD",
                "gateway_label": "ibkr",
            },
            {
                "id": "uuid-2",
                "alias": "FUTU1",
                "broker": "Futu",
                "mode": "live",
                "currency": "HKD",
                "gateway_label": "futu",
            },
        ],
    }
    mock_redis.get = AsyncMock(return_value=_json.dumps(acct_select_data).encode())
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    instr_row = MagicMock()
    instr_row.conid = "265598"
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=instr_row)))

    mock_preview = MagicMock()
    mock_preview.risk_blockers = []
    mock_preview.risk_warnings = []
    mock_preview.position_sanity = MagicMock(requires_extra_attestation=False)
    mock_preview.notional = "500.00"
    mock_preview.notional_currency = "HKD"

    with patch(
        "app.services.telegram.order_flow.preview_order",
        AsyncMock(return_value=mock_preview),
    ):
        consumed = await handle_account_selection(
            msg,
            entry=entry,
            db=mock_db,
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    assert consumed is True
    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert any("pending" in c for c in set_calls)


@pytest.mark.asyncio
async def test_account_selection_out_of_range() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.telegram.order_flow import handle_account_selection

    msg = _make_msg("5")
    entry = _make_entry()
    mock_redis = AsyncMock()

    acct_select_data = {
        "order": {
            "symbol": "AAPL",
            "side": "BUY",
            "qty": "10",
            "order_type": "MARKET",
            "tif": "DAY",
            "limit_price": None,
            "stop_price": None,
        },
        "accounts": [
            {
                "id": "uuid-1",
                "alias": "IBKR1",
                "broker": "IBKR",
                "mode": "paper",
                "currency": "USD",
                "gateway_label": "ibkr",
            },
        ],
    }
    mock_redis.get = AsyncMock(return_value=_json.dumps(acct_select_data).encode())
    mock_redis.set = AsyncMock()

    consumed = await handle_account_selection(
        msg,
        entry=entry,
        db=AsyncMock(),
        redis=mock_redis,
        registry=MagicMock(),
        capability=MagicMock(),
        cfg=MagicMock(),
    )

    assert consumed is True
    reply = msg.answer.call_args.args[0]
    assert "invalid" in reply.lower() or "range" in reply.lower() or "1" in reply


@pytest.mark.asyncio
async def test_acct_select_ttl_expires_then_user_replies_number() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.telegram.order_flow import handle_account_selection

    msg = _make_msg("1")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    consumed = await handle_account_selection(
        msg,
        entry=entry,
        db=AsyncMock(),
        redis=mock_redis,
        registry=MagicMock(),
        capability=MagicMock(),
        cfg=MagicMock(),
    )
    assert consumed is False


@pytest.mark.asyncio
async def test_confirm_places_order() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_confirm

    msg = _make_msg("/confirm")
    entry = _make_entry()
    mock_redis = AsyncMock()

    pending_data = {
        "account_id": "acct-uuid-1",
        "account_alias": "IBKR1",
        "account_mode": "paper",
        "account_gateway_label": "ibkr",
        "conid": "265598",
        "symbol": "AAPL",
        "side": "BUY",
        "qty": "10",
        "order_type": "MARKET",
        "tif": "DAY",
        "limit_price": None,
        "stop_price": None,
    }
    mock_redis.execute_command = AsyncMock(return_value=_json.dumps(pending_data).encode())
    mock_redis.set = AsyncMock()

    mock_order = MagicMock()
    mock_order.id = "order-uuid-123"

    with patch("app.services.telegram.order_flow.place_order", AsyncMock(return_value=mock_order)):
        await handle_confirm(
            msg,
            entry=entry,
            db=AsyncMock(),
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    reply = msg.answer.call_args.args[0]
    assert "order-uuid-123" in reply
    assert "✅" in reply


@pytest.mark.asyncio
async def test_confirm_order_id_prefixed_telegram() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.telegram.order_flow import handle_confirm

    msg = _make_msg("/confirm")
    entry = _make_entry()
    mock_redis = AsyncMock()

    pending_data = {
        "account_id": "acct-uuid-1",
        "account_alias": "IBKR1",
        "account_mode": "paper",
        "account_gateway_label": "ibkr",
        "conid": "265598",
        "symbol": "AAPL",
        "side": "BUY",
        "qty": "5",
        "order_type": "MARKET",
        "tif": "DAY",
        "limit_price": None,
        "stop_price": None,
    }
    mock_redis.execute_command = AsyncMock(return_value=_json.dumps(pending_data).encode())
    mock_redis.set = AsyncMock()

    captured: dict[str, Any] = {}

    async def _mock_place_order(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        result = MagicMock()
        result.id = "order-abc"
        return result

    with patch("app.services.telegram.order_flow.place_order", _mock_place_order):
        await handle_confirm(
            msg,
            entry=entry,
            db=AsyncMock(),
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    client_order_id = captured["request_data"]["client_order_id"]
    assert client_order_id.startswith("telegram-")


@pytest.mark.asyncio
async def test_confirm_expired() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.telegram.order_flow import handle_confirm

    msg = _make_msg("/confirm")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=None)

    await handle_confirm(
        msg,
        entry=entry,
        db=AsyncMock(),
        redis=mock_redis,
        registry=MagicMock(),
        capability=MagicMock(),
        cfg=MagicMock(),
    )

    reply = msg.answer.call_args.args[0]
    assert "dashboard" in reply.lower() or "expired" in reply.lower()


@pytest.mark.asyncio
async def test_confirm_risk_gate_blocked() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.orders_service import PreviewUnavailable
    from app.services.telegram.order_flow import handle_confirm

    msg = _make_msg("/confirm")
    entry = _make_entry()
    mock_redis = AsyncMock()

    pending_data = {
        "account_id": "acct-uuid-1",
        "account_alias": "IBKR1",
        "account_mode": "paper",
        "account_gateway_label": "ibkr",
        "conid": "265598",
        "symbol": "AAPL",
        "side": "BUY",
        "qty": "10",
        "order_type": "MARKET",
        "tif": "DAY",
        "limit_price": None,
        "stop_price": None,
    }
    mock_redis.execute_command = AsyncMock(return_value=_json.dumps(pending_data).encode())
    mock_redis.set = AsyncMock()

    exc = PreviewUnavailable(
        422,
        {
            "error": "risk_gate_blocked",
            "blockers": [{"code": "kill_switch", "message": "Blocked"}],
        },
    )

    with patch("app.services.telegram.order_flow.place_order", AsyncMock(side_effect=exc)):
        await handle_confirm(
            msg,
            entry=entry,
            db=AsyncMock(),
            redis=mock_redis,
            registry=MagicMock(),
            capability=MagicMock(),
            cfg=MagicMock(),
        )

    reply = msg.answer.call_args.args[0]
    assert "blocked" in reply.lower() or "Blocked" in reply


@pytest.mark.asyncio
async def test_confirm_live_account_requires_live_token() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.telegram.order_flow import handle_confirm

    msg = _make_msg("/confirm")
    entry = _make_entry()
    mock_redis = AsyncMock()

    pending_data = {
        "account_id": "acct-uuid-1",
        "account_alias": "IBKR_LIVE",
        "account_mode": "live",
        "account_gateway_label": "ibkr",
        "conid": "265598",
        "symbol": "AAPL",
        "side": "BUY",
        "qty": "10",
        "order_type": "MARKET",
        "tif": "DAY",
        "limit_price": None,
        "stop_price": None,
    }
    mock_redis.execute_command = AsyncMock(return_value=_json.dumps(pending_data).encode())
    mock_redis.set = AsyncMock()

    await handle_confirm(
        msg,
        entry=entry,
        db=AsyncMock(),
        redis=mock_redis,
        registry=MagicMock(),
        capability=MagicMock(),
        cfg=MagicMock(),
    )

    reply = msg.answer.call_args.args[0]
    assert "LIVE" in reply
    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert any("pending" in c for c in set_calls)


@pytest.mark.asyncio
async def test_cancel_clears_both_keys() -> None:
    from unittest.mock import AsyncMock

    from app.services.telegram.order_flow import handle_cancel_order

    msg = _make_msg("/cancel_order")
    entry = _make_entry()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"something")
    mock_redis.delete = AsyncMock()

    await handle_cancel_order(msg, entry=entry, redis=mock_redis)

    assert mock_redis.delete.called
    deleted_keys = str(mock_redis.delete.call_args)
    assert "pending" in deleted_keys
    assert "acct_select" in deleted_keys
    reply = msg.answer.call_args.args[0]
    assert "cancel" in reply.lower() or "Cancel" in reply
