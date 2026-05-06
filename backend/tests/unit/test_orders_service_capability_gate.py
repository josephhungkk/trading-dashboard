"""Phase 8a B4 - order capability gate before broker dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services import orders_service
from app.services.ibkr_maintenance import BrokerMaintenance
from app.services.orders_service import PreviewUnavailable


def _request_data() -> dict[str, str]:
    return {
        "account_id": str(uuid4()),
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "DAY",
        "qty": "1",
        "limit_price": "100",
    }


def _config(kill_switch: bool) -> MagicMock:
    cfg = MagicMock()
    cfg.get_bool = AsyncMock(return_value=kill_switch)
    return cfg


def _capability(supported: bool = True) -> MagicMock:
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=supported)
    capability.get_notes = AsyncMock(return_value="unsupported by broker")
    return capability


@pytest.mark.asyncio
async def test_kill_switch_runs_first() -> None:
    capability = _capability(supported=True)
    registry = MagicMock()

    with pytest.raises(PreviewUnavailable) as exc_info:
        await orders_service.preview_order(
            cfg=_config(kill_switch=True),
            db=AsyncMock(),
            redis=AsyncMock(),
            registry=registry,
            capability=capability,
            request_data=_request_data(),
            user_key="unit@example.com",
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload == {"error": "kill_switch_active"}
    capability.is_supported.assert_not_awaited()
    registry.get_client.assert_not_called()


@pytest.mark.asyncio
async def test_maintenance_runs_before_capability() -> None:
    capability = _capability(supported=True)
    registry = MagicMock()
    active = BrokerMaintenance(
        active=True,
        window="daily",
        until=datetime.now(UTC) + timedelta(minutes=5),
    )

    with patch.object(orders_service, "compute_broker_maintenance", return_value=active):
        with pytest.raises(PreviewUnavailable) as exc_info:
            await orders_service.preview_order(
                cfg=_config(kill_switch=False),
                db=AsyncMock(),
                redis=AsyncMock(),
                registry=registry,
                capability=capability,
                request_data=_request_data(),
                user_key="unit@example.com",
            )

    assert exc_info.value.status_code == 503
    capability.is_supported.assert_not_awaited()
    registry.get_client.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_combo_raises_422() -> None:
    capability = _capability(supported=False)
    registry = MagicMock()

    with (
        patch.object(orders_service, "compute_broker_maintenance") as maintenance,
        patch.object(orders_service, "_check_rate_limit", new=AsyncMock()),
        patch.object(
            orders_service,
            "resolve_account",
            new=AsyncMock(return_value=orders_service._Account("isa-paper", "paper", "USD")),
        ),
    ):
        maintenance.return_value = BrokerMaintenance(active=False)
        with pytest.raises(PreviewUnavailable) as exc_info:
            await orders_service.preview_order(
                cfg=_config(kill_switch=False),
                db=AsyncMock(),
                redis=AsyncMock(),
                registry=registry,
                capability=capability,
                request_data=_request_data(),
                user_key="unit@example.com",
            )

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload["error"]["code"] == "unsupported_order_type_for_broker"
    assert exc_info.value.payload["error"]["broker_id"] == "ibkr"
    assert exc_info.value.payload["error"]["order_type"] == "LIMIT"
    assert exc_info.value.payload["error"]["tif"] == "DAY"
    registry.get_client.assert_not_called()


@pytest.mark.asyncio
async def test_supported_combo_proceeds_to_dispatch() -> None:
    capability = _capability(supported=True)
    registry = MagicMock()
    registry.get_client = AsyncMock(return_value=MagicMock())

    with (
        patch.object(orders_service, "compute_broker_maintenance") as maintenance,
        patch.object(orders_service, "_check_rate_limit", new=AsyncMock()),
        patch.object(
            orders_service,
            "resolve_account",
            new=AsyncMock(return_value=orders_service._Account("isa-paper", "paper", "USD")),
        ),
        patch.object(
            orders_service,
            "_resolve_contract",
            new=AsyncMock(side_effect=RuntimeError("dispatch reached")),
        ),
    ):
        maintenance.return_value = BrokerMaintenance(active=False)
        with pytest.raises(RuntimeError, match="dispatch reached"):
            await orders_service.preview_order(
                cfg=_config(kill_switch=False),
                db=AsyncMock(),
                redis=AsyncMock(),
                registry=registry,
                capability=capability,
                request_data=_request_data(),
                user_key="unit@example.com",
            )

    registry.get_client.assert_awaited_once_with("isa-paper")
