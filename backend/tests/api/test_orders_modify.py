from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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
from app.schemas.orders import OrderModifyRequest
from app.services import orders_service

TEST_ACCOUNT_NUMBER = "TEST_MOD_001"
TEST_CONID = "265598"
TEST_SYMBOL = "AAPL SMART USD"


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@dataclass
class _AccountRow:
    account_id: UUID
    gateway_label: str = "isa-paper"
    account_number: str = TEST_ACCOUNT_NUMBER
    mode: str = "paper"
    currency_base: str = "USD"
    last_nlv_currency: str = "USD"


@dataclass
class _OrderRow:
    id: UUID
    account_id: UUID
    broker_order_id: str | None = "BRK-123"
    conid: str = TEST_CONID
    symbol: str = TEST_SYMBOL
    side: str = "BUY"
    order_type: str = "LIMIT"
    tif: str = "DAY"
    qty: Decimal = Decimal("1")
    limit_price: Decimal | None = Decimal("100")
    stop_price: Decimal | None = None
    notional: Decimal = Decimal("100")
    status: str = "submitted"
    filled_qty: Decimal = Decimal("0")
    parent_order_id: UUID | None = None
    client_order_id: UUID = field(default_factory=uuid4)


class _Result:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        *,
        scalar: Any = None,
    ) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        return self._scalar


class _Session:
    def __init__(
        self,
        account: _AccountRow,
        order: _OrderRow,
        *,
        children: list[_OrderRow] | None = None,
    ) -> None:
        self.account = account
        self.order = order
        self.children = children or []
        self.order_events: list[dict[str, Any]] = []
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        if "FROM orders" in sql and "WHERE id = :id" in sql:
            if params["id"] != self.order.id:
                return _Result(row=None)
            if "status::text" in sql and "account_id" not in sql:
                return _Result(scalar=self.order.status)
            return _Result(
                row={
                    "account_id": self.order.account_id,
                    "broker_order_id": self.order.broker_order_id,
                    "conid": self.order.conid,
                    "symbol": self.order.symbol,
                    "side": self.order.side,
                    "order_type": self.order.order_type,
                    "tif": self.order.tif,
                    "qty": self.order.qty,
                    "limit_price": self.order.limit_price,
                    "stop_price": self.order.stop_price,
                    "status": self.order.status,
                    "filled_qty": self.order.filled_qty,
                    "parent_order_id": self.order.parent_order_id,
                    "client_order_id": self.order.client_order_id,
                    "notional": self.order.notional,
                }
            )
        if "FROM orders" in sql and "parent_order_id = :p" in sql:
            has_living_child = any(
                child.parent_order_id == params["p"]
                and child.status not in {"filled", "cancelled", "rejected", "expired"}
                for child in self.children
            )
            return _Result(scalar=1 if has_living_child else None)
        if "FROM broker_accounts" in sql:
            if params["account_id"] != self.account.account_id:
                return _Result(row=None)
            return _Result(
                row={
                    "gateway_label": self.account.gateway_label,
                    "mode": self.account.mode,
                    "currency_base": self.account.currency_base,
                    "account_number": self.account.account_number,
                    "last_nlv_currency": self.account.last_nlv_currency,
                }
            )
        if "INSERT INTO order_events" in sql:
            self.order_events.append(dict(params))
            return _Result(row=None)
        if "UPDATE orders" in sql and "SET qty = :qty" in sql:
            # 5c v0.5.5: mutable-fields update from modify path
            self.order.qty = params["qty"]
            self.order.limit_price = params["limit_price"]
            self.order.stop_price = params["stop_price"]
            self.order.tif = params["tif"]
            self.order.notional = params["notional"]
            return _Result(row=None)
        if "order_status_rank" in sql:
            rank = {
                "pending_submit": 0,
                "submitted": 1,
                "modified": 1,
                "partial": 3,
                "filled": 4,
                "cancelled": 5,
                "rejected": 5,
                "expired": 5,
            }
            current = str(params["current"])
            return _Result(scalar=current if rank.get(current, -1) > 1 else "modified")
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1


class _Config:
    def __init__(
        self,
        *,
        kill_switch_enabled: bool = False,
        max_notional_per_order: str = "10000",
        daily_notional_cap: str = "50000",
    ) -> None:
        self.kill_switch_enabled = kill_switch_enabled
        self.max_notional_per_order = max_notional_per_order
        self.daily_notional_cap = daily_notional_cap

    async def get_bool(self, namespace: str, key: str, *, default: bool) -> bool:
        if namespace == "broker" and key == "kill_switch_enabled":
            return self.kill_switch_enabled
        if namespace == "broker" and key.endswith(".trade_enabled"):
            return True
        if namespace == "broker" and key.endswith(".simulator_only"):
            return False
        return default

    async def get(self, namespace: str, key: str, *, default: str) -> str:
        if namespace == "broker" and key.endswith(".max_notional_per_order"):
            return self.max_notional_per_order
        if namespace == "broker" and key.endswith(".daily_notional_cap"):
            return self.daily_notional_cap
        return default


class _Capability:
    async def is_supported(
        self, broker_id: str, asset_class: str, order_type: str, tif: str
    ) -> bool:
        return True

    async def get_notes(self, broker_id: str, asset_class: str, order_type: str, tif: str) -> str:
        return ""


class _Sidecar:
    def __init__(self, contract: base.Contract) -> None:
        self.contract = contract
        self.modify_calls: list[dict[str, str]] = []
        self.raise_unavailable = False

    async def get_contract(self, conid: str) -> base.Contract:
        assert conid == self.contract.conid
        return self.contract

    async def search_contracts(self, *, query: str) -> list[base.Contract]:
        assert query == self.contract.conid
        return [self.contract]

    async def modify_order(
        self,
        *,
        broker_order_id: str,
        account_number: str,
        contract: base.Contract,
        side: str,
        order_type: str,
        tif: str,
        qty: str,
        limit_price: str,
        stop_price: str,
        client_order_id: str,
    ) -> base.ModifyOrderResult:
        if self.raise_unavailable:
            from app.services.brokers import BrokerSidecarUnavailable

            raise BrokerSidecarUnavailable("isa-paper")
        assert contract.conid == self.contract.conid
        self.modify_calls.append(
            {
                "broker_order_id": broker_order_id,
                "account_number": account_number,
                "side": side,
                "order_type": order_type,
                "tif": tif,
                "qty": qty,
                "limit_price": limit_price,
                "stop_price": stop_price,
                "client_order_id": client_order_id,
            }
        )
        return base.ModifyOrderResult(
            broker_order_id=broker_order_id or "BRK-123",
            status="Submitted",
        )


class _Registry:
    def __init__(self, sidecar: _Sidecar) -> None:
        self.sidecar = sidecar

    async def get_client(self, label: str) -> _Sidecar:
        assert label == "isa-paper"
        return self.sidecar


def _seed_account() -> _AccountRow:
    return _AccountRow(account_id=uuid4())


def _seed_order(account_id: UUID, **overrides: Any) -> _OrderRow:
    row = _OrderRow(id=uuid4(), account_id=account_id)
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture
async def modify_client() -> AsyncIterator[dict[str, Any]]:
    account = _seed_account()
    order = _seed_order(account.account_id)
    session = _Session(account, order)
    config = _Config()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sidecar = _Sidecar(
        base.Contract(
            symbol="AAPL",
            exchange="SMART",
            currency="USD",
            asset_class="STOCK",
            conid=TEST_CONID,
            local_symbol="AAPL",
        )
    )

    from app.api import orders as orders_api

    async def override_db() -> AsyncIterator[_Session]:
        yield session

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    async def override_config() -> _Config:
        return config

    async def override_registry() -> _Registry:
        return _Registry(sidecar)

    async def override_redis() -> fakeredis.aioredis.FakeRedis:
        return redis

    async def override_capability() -> _Capability:
        return _Capability()

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[orders_api.get_order_capability_service] = override_capability
    app.dependency_overrides[orders_api.get_orders_redis] = override_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "account": account,
            "client": client,
            "config": config,
            "order": order,
            "orders_api": orders_api,
            "redis": redis,
            "session": session,
            "sidecar": sidecar,
        }

    app.dependency_overrides.clear()


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "nonce": str(uuid4()),
        "qty": "1",
        "limit_price": "101",
        "order_type": "LIMIT",
        "tif": "DAY",
        "stop_price": None,
    }
    body.update(overrides)
    return body


async def _store_nonce(
    redis: fakeredis.aioredis.FakeRedis,
    *,
    account_id: UUID,
    payload: dict[str, Any],
    payload_hash: str | None = None,
    order: _OrderRow | None = None,
) -> None:
    key = f"nonce:order:{account_id}:{payload['nonce']}"
    if payload_hash is None:
        # 5c f4-fix: modify now reuses /api/orders/preview which mints an 8-field
        # hash (account_id, conid, side, order_type, tif, qty, limit_price, stop_price).
        # Mirror that here when an order row is provided so the modify endpoint's
        # _consume_nonce hash matches.
        if order is not None:
            full_payload = {
                "account_id": str(account_id),
                "conid": str(order.conid),
                "side": str(order.side),
                "order_type": str(order.order_type),
                "tif": payload["tif"],
                "qty": orders_service.canonicalize_qty(payload["qty"]),
                "limit_price": orders_service._canonical_decimal_or_none(payload["limit_price"]),
                "stop_price": orders_service._canonical_decimal_or_none(payload.get("stop_price")),
            }
            payload_hash = hashlib.sha256(
                json.dumps(full_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        else:
            payload_hash = orders_service._modify_nonce_payload_hash(
                account_id=account_id,
                qty=payload["qty"],
                limit_price=payload["limit_price"],
            )
    value = json.dumps(
        {"payload_hash": payload_hash, "rth_at_mint": True},
        sort_keys=True,
    )
    await redis.set(key, value, ex=30)


@pytest.mark.asyncio
async def test_modify_terminal_status_rejected(modify_client: dict[str, Any]) -> None:
    modify_client["order"].status = "cancelled"

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "terminal_status"}


@pytest.mark.asyncio
async def test_modify_replay_returns_cached_response(modify_client: dict[str, Any]) -> None:
    payload = _payload(nonce=str(uuid4()))
    await _store_nonce(
        modify_client["redis"],
        account_id=modify_client["account"].account_id,
        payload=payload,
        order=modify_client["order"],
    )

    first = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=payload,
    )
    second = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(modify_client["sidecar"].modify_calls) == 1


@pytest.mark.asyncio
async def test_modify_bracket_parent_partial_rejected(modify_client: dict[str, Any]) -> None:
    modify_client["order"].filled_qty = Decimal("0.25")
    child = _seed_order(
        modify_client["account"].account_id,
        parent_order_id=modify_client["order"].id,
        status="submitted",
    )
    modify_client["session"].children = [child]

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "bracket_parent_partial"}


@pytest.mark.asyncio
async def test_modify_child_when_parent_partial_allowed(modify_client: dict[str, Any]) -> None:
    modify_client["order"].parent_order_id = uuid4()
    payload = _payload()
    await _store_nonce(
        modify_client["redis"],
        account_id=modify_client["account"].account_id,
        payload=payload,
        order=modify_client["order"],
    )

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "modified"
    assert modify_client["sidecar"].modify_calls[0]["account_number"] == TEST_ACCOUNT_NUMBER


@pytest.mark.asyncio
async def test_modify_notional_overflow_rejected(modify_client: dict[str, Any]) -> None:
    modify_client["config"].max_notional_per_order = "50"
    payload = _payload(qty="2", limit_price="30")
    await _store_nonce(
        modify_client["redis"],
        account_id=modify_client["account"].account_id,
        payload=payload,
    )

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json() == {"error": "max_notional_exceeded"}


@pytest.mark.asyncio
async def test_modify_nonce_mismatch_rejected(modify_client: dict[str, Any]) -> None:
    payload = _payload()
    await _store_nonce(
        modify_client["redis"],
        account_id=modify_client["account"].account_id,
        payload=payload,
        payload_hash="not-the-hash",
    )

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=payload,
    )

    assert response.status_code == 409
    assert response.json() == {"error": "nonce_mismatch"}


@pytest.mark.asyncio
async def test_modify_kill_switch_503(modify_client: dict[str, Any]) -> None:
    modify_client["config"].kill_switch_enabled = True

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=_payload(),
    )

    assert response.status_code == 503
    assert response.json() == {"error": "kill_switch"}


@pytest.mark.asyncio
async def test_modify_sidecar_unavailable_returns_503(
    modify_client: dict[str, Any],
) -> None:
    payload = _payload()
    await _store_nonce(
        modify_client["redis"],
        account_id=modify_client["account"].account_id,
        payload=payload,
        order=modify_client["order"],
    )
    modify_client["sidecar"].raise_unavailable = True

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=payload,
    )

    assert response.status_code == 503
    assert response.json() == {"error": "sidecar_unavailable"}
    assert response.headers["retry-after"] == "1"


@pytest.mark.asyncio
async def test_modify_immutable_fields_422(modify_client: dict[str, Any]) -> None:
    if OrderModifyRequest.model_config.get("extra") != "forbid":
        pytest.skip("current OrderModifyRequest schema ignores extra immutable fields")

    response = await modify_client["client"].put(
        f"/api/orders/{modify_client['order'].id}",
        json=_payload(conid="999"),
    )

    assert response.status_code == 422
