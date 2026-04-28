from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_config, get_db, require_admin_jwt
from app.main import app
from app.services.ibkr_maintenance import BrokerMaintenance


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@dataclass
class _AccountRow:
    account_id: UUID
    gateway_label: str = "isa-paper"
    mode: str = "paper"
    currency_base: str = "USD"


class _Result:
    def __init__(self, row: dict[str, Any] | None = None, scalar: Any = None) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Session:
    def __init__(
        self,
        account: _AccountRow,
        *,
        filled_today: Decimal = Decimal("0"),
        position_qty: Decimal = Decimal("0"),
    ) -> None:
        self.account = account
        self.filled_today = filled_today
        self.position_qty = position_qty

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        if "FROM broker_accounts" in sql:
            return _Result(
                {
                    "id": self.account.account_id,
                    "gateway_label": self.account.gateway_label,
                    "mode": self.account.mode,
                    "currency_base": self.account.currency_base,
                }
            )
        if "FROM orders" in sql:
            return _Result(scalar=self.filled_today)
        if "FROM positions" in sql:
            return _Result(scalar=self.position_qty)
        raise AssertionError(f"unexpected SQL: {sql}")


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        pass


class _Config:
    def __init__(self, *, kill_switch: bool = False) -> None:
        self.kill_switch = kill_switch

    async def get_bool(self, namespace: str, key: str, *, default: bool) -> bool:
        if namespace == "broker" and key == "kill_switch_enabled":
            return self.kill_switch
        return default

    async def get(self, namespace: str, key: str, *, default: str) -> str:
        # Per-gateway keys are now dotted: "<label>.daily_notional_cap" etc.
        # (see app/services/orders_policy.py for the layout fix).
        if key.endswith(".daily_notional_cap"):
            return "1000"
        if key.endswith(".max_notional_per_order"):
            return "10000"
        return default


class _Sidecar:
    def __init__(self, contract: base.Contract) -> None:
        self.contract = contract

    async def search_contracts(self, query: str) -> list[base.Contract]:
        assert query == self.contract.conid
        return [self.contract]


class _Registry:
    def __init__(self, sidecar: _Sidecar) -> None:
        self.sidecar = sidecar

    async def get_client(self, label: str) -> _Sidecar:
        assert label == "isa-paper"
        return self.sidecar


@pytest.fixture
async def preview_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    account_id = uuid4()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session = _Session(_AccountRow(account_id))
    contract = base.Contract(
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        asset_class="STOCK",
        conid="265598",
        local_symbol="AAPL",
    )

    from app.api import orders as orders_api

    async def override_db() -> AsyncIterator[_Session]:
        yield session

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    async def override_config() -> _Config:
        return _Config()

    async def override_registry() -> _Registry:
        return _Registry(_Sidecar(contract))

    async def override_redis() -> fakeredis.aioredis.FakeRedis:
        return redis

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[orders_api.get_orders_redis] = override_redis

    monkeypatch.setattr(
        orders_api.orders_service,
        "compute_broker_maintenance",
        lambda _now: BrokerMaintenance(active=False, window=None, until=None),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "account_id": account_id,
            "redis": redis,
            "session": session,
            "contract": contract,
        }

    app.dependency_overrides.clear()


def _payload(account_id: UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "account_id": str(account_id),
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "DAY",
        "qty": "1",
        "limit_price": "100",
        "stop_price": None,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_preview_kill_switch_returns_503_first(
    preview_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import orders as orders_api

    async def override_config() -> _Config:
        return _Config(kill_switch=True)

    app.dependency_overrides[get_config] = override_config
    monkeypatch.setattr(
        orders_api.orders_service,
        "compute_broker_maintenance",
        lambda now: BrokerMaintenance(
            active=True,
            window="daily",
            until=now + timedelta(seconds=60),
        ),
    )

    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"], qty="not-valid"),
    )

    assert response.status_code == 503
    assert response.json() == {"error": "kill_switch_active"}


@pytest.mark.asyncio
async def test_preview_maintenance_returns_503_with_retry_after(
    preview_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import orders as orders_api

    now = datetime(2026, 4, 27, 12, tzinfo=UTC)
    monkeypatch.setattr(orders_api.orders_service, "_utcnow", lambda: now)
    monkeypatch.setattr(
        orders_api.orders_service,
        "compute_broker_maintenance",
        lambda _: BrokerMaintenance(
            active=True,
            window="daily",
            until=now + timedelta(seconds=45),
        ),
    )

    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"]),
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "45"
    assert response.json()["broker_maintenance"]["active"] is True


@pytest.mark.asyncio
async def test_preview_canonicalizes_qty(preview_client: dict[str, Any]) -> None:
    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"], qty="01.00"),
    )

    assert response.status_code == 200
    nonce = response.json()["nonce"]
    stored = await preview_client["redis"].get(
        f"nonce:order:{preview_client['account_id']}:{nonce}"
    )
    payload_hash = _hash_payload(preview_client["account_id"], qty="1.00000000")
    decoded = json.loads(stored)
    assert decoded["payload_hash"] == payload_hash
    assert isinstance(decoded["rth_at_mint"], bool)


@pytest.mark.asyncio
async def test_preview_market_notional_includes_5pct_slippage_buffer(
    preview_client: dict[str, Any],
) -> None:
    await preview_client["redis"].set("mkt:mid:265598", "10", ex=3600)

    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(
            preview_client["account_id"],
            order_type="MARKET",
            qty="100",
            limit_price=None,
        ),
    )

    assert response.status_code == 200
    assert response.json()["notional"] == "1050.00000000"


@pytest.mark.asyncio
async def test_preview_position_sanity_extreme(preview_client: dict[str, Any]) -> None:
    preview_client["session"].position_qty = Decimal("10")

    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"], qty="200"),
    )

    assert response.status_code == 200
    sanity = response.json()["position_sanity"]
    assert sanity["status"] == "extreme"
    assert sanity["requires_extra_attestation"] is True


@pytest.mark.asyncio
async def test_preview_daily_cap_status_near_at_81pct(preview_client: dict[str, Any]) -> None:
    preview_client["session"].filled_today = Decimal("810")

    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"], qty="1", limit_price="1"),
    )

    assert response.status_code == 200
    assert response.json()["daily_cap_status"] == "near"


@pytest.mark.asyncio
async def test_preview_mints_redis_nonce_with_canonicalized_payload(
    preview_client: dict[str, Any],
) -> None:
    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"], qty="01.00"),
    )

    assert response.status_code == 200
    nonce = response.json()["nonce"]
    key = f"nonce:order:{preview_client['account_id']}:{nonce}"
    assert await preview_client["redis"].ttl(key) == 30
    decoded = json.loads(await preview_client["redis"].get(key))
    assert decoded["payload_hash"] == _hash_payload(
        preview_client["account_id"],
        qty="1.00000000",
    )
    assert isinstance(decoded["rth_at_mint"], bool)


@pytest.mark.asyncio
async def test_preview_503_when_fx_cache_cold_and_sidecar_unavailable(
    preview_client: dict[str, Any],
) -> None:
    preview_client["contract"].currency = "GBP"

    response = await preview_client["client"].post(
        "/api/orders/preview",
        json=_payload(preview_client["account_id"]),
    )

    assert response.status_code == 503
    assert response.json() == {"error": "fx_rate_unavailable", "pair": "GBP:USD"}


def _hash_payload(account_id: UUID, *, qty: str) -> str:
    payload = {
        "account_id": str(account_id),
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "DAY",
        "qty": qty,
        "limit_price": "100.00000000",
        "stop_price": None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
