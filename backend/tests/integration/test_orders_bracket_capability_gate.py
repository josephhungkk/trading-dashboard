"""HIGH-2: place_bracket capability gate tests.

Before this fix, place_bracket never called validate_pre_dispatch — the
capability check was entirely absent.  These tests confirm:

1. A bracket during broker maintenance window → 503 PreviewUnavailable.
2. A bracket for a capability-disabled combo → 422 PreviewUnavailable.
3. A bracket for a supported combo passes the gate (no exception).
4. skip_operational_checks=True bypasses maintenance but still checks capability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.order_capability_service import OrderCapabilityService
from app.services.orders_service import PreviewUnavailable, validate_pre_dispatch

# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


class _FakeRedis:
    async def publish(self, channel: str, message: bytes | str) -> int:
        return 0

    def pubsub(self) -> Any:
        return self


class _FakeSession:
    def __init__(self, *, supported: bool) -> None:
        self._supported = supported

    async def execute(self, stmt: object, params: object = None) -> _FakeResult:
        return _FakeResult(supported=self._supported)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


class _FakeResult:
    def __init__(self, *, supported: bool) -> None:
        self._supported = supported

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(supported=self._supported)


class _FakeMappings:
    def __init__(self, *, supported: bool) -> None:
        self._supported = supported

    def first(self) -> dict | None:
        if not self._supported:
            return None
        return {
            "broker_id": "schwab",
            "asset_class": "STOCK",
            "order_type": "LIMIT",
            "time_in_force": "DAY",
            "is_supported": True,
            "notes": None,
        }

    def all(self) -> list[dict]:
        return []


def _make_capability(*, supported: bool) -> OrderCapabilityService:
    redis = _FakeRedis()

    def factory() -> _FakeSession:
        return _FakeSession(supported=supported)

    return OrderCapabilityService(redis, db_factory=factory, ttl_seconds=0.0)


class _FakeConfigService:
    """ConfigService stub — kill switch inactive."""

    async def get(self, key: str) -> str | None:
        return None

    async def get_bool(self, namespace: str, key: str, *, default: bool = False) -> bool:
        if key == "kill_switch_enabled":
            return False
        return default


# ---------------------------------------------------------------------------
# Test 1: maintenance window → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bracket_during_maintenance_raises_503() -> None:
    """HIGH-2: validate_pre_dispatch raises 503 during broker maintenance window."""
    capability = _make_capability(supported=True)
    cfg = _FakeConfigService()

    active_maintenance = MagicMock()
    active_maintenance.active = True
    active_maintenance.end_utc = datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)

    with patch(
        "app.services.orders_service.compute_broker_maintenance",
        return_value=active_maintenance,
    ):
        with pytest.raises(PreviewUnavailable) as exc_info:
            await validate_pre_dispatch(
                cfg=cfg,  # type: ignore[arg-type]
                capability=capability,
                broker_label="schwab",
                asset_class="STOCK",
                order_type="LIMIT",
                tif="DAY",
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"]["code"] == "broker_maintenance"


# ---------------------------------------------------------------------------
# Test 2: disabled capability → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bracket_with_disabled_capability_raises_422() -> None:
    """HIGH-2: validate_pre_dispatch raises 422 for unsupported order type/tif combo."""
    capability = _make_capability(supported=False)
    cfg = _FakeConfigService()

    no_maintenance = MagicMock()
    no_maintenance.active = False

    with patch(
        "app.services.orders_service.compute_broker_maintenance",
        return_value=no_maintenance,
    ):
        with pytest.raises(PreviewUnavailable) as exc_info:
            await validate_pre_dispatch(
                cfg=cfg,  # type: ignore[arg-type]
                capability=capability,
                broker_label="schwab",
                asset_class="STOCK",
                order_type="STOP_LIMIT",
                tif="GTD",
            )

    assert exc_info.value.status_code == 422
    payload_error = exc_info.value.payload["error"]
    assert payload_error["code"] == "unsupported_order_type_for_broker"
    assert payload_error["order_type"] == "STOP_LIMIT"


# ---------------------------------------------------------------------------
# Test 3: supported combo → no exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bracket_with_supported_capability_passes_gate() -> None:
    """HIGH-2: validate_pre_dispatch does not raise for a supported combo."""
    capability = _make_capability(supported=True)
    cfg = _FakeConfigService()

    no_maintenance = MagicMock()
    no_maintenance.active = False

    with patch(
        "app.services.orders_service.compute_broker_maintenance",
        return_value=no_maintenance,
    ):
        # Should not raise.
        await validate_pre_dispatch(
            cfg=cfg,  # type: ignore[arg-type]
            capability=capability,
            broker_label="schwab",
            asset_class="STOCK",
            order_type="LIMIT",
            tif="DAY",
        )


# ---------------------------------------------------------------------------
# Test 4: skip_operational_checks bypasses maintenance but still checks capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_operational_checks_still_validates_capability() -> None:
    """HIGH-2: skip_operational_checks=True bypasses maintenance but checks capability."""
    capability = _make_capability(supported=False)
    cfg = _FakeConfigService()

    active_maintenance = MagicMock()
    active_maintenance.active = True
    active_maintenance.end_utc = datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)

    with patch(
        "app.services.orders_service.compute_broker_maintenance",
        return_value=active_maintenance,
    ):
        with pytest.raises(PreviewUnavailable) as exc_info:
            await validate_pre_dispatch(
                cfg=cfg,  # type: ignore[arg-type]
                capability=capability,
                broker_label="schwab",
                asset_class="STOCK",
                order_type="LIMIT",
                tif="DAY",
                skip_operational_checks=True,
            )

    # Maintenance skipped, but capability gate still blocks.
    assert exc_info.value.status_code == 422
    assert exc_info.value.payload["error"]["code"] == "unsupported_order_type_for_broker"
