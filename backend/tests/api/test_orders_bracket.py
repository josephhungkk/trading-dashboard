from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.services import orders_service


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@dataclass
class _AccountRow:
    account_id: UUID
    gateway_label: str = "isa-paper"
    account_number: str = "TEST_BRK_001"
    mode: str = "paper"
    currency_base: str = "USD"


@dataclass
class _OrderRow:
    id: UUID
    account_id: UUID
    client_order_id: UUID
    conid: str
    symbol: str
    side: str
    order_type: str
    tif: str
    qty: str
    limit_price: Decimal | None
    stop_price: Decimal | None
    status: str
    notional: Decimal
    broker_order_id: str | None = None
    parent_order_id: UUID | None = None
    oca_group: str | None = None
    cancel_requested_at: datetime | None = None
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    created_at: datetime = datetime(2026, 4, 27, 14, 45, tzinfo=UTC)
    updated_at: datetime = datetime(2026, 4, 27, 14, 45, tzinfo=UTC)
    last_event_at: datetime | None = None


class _Result:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or ([] if row is None else [row])

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row

    def first(self) -> dict[str, Any] | None:
        return self._row

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _BeginContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Session:
    def __init__(self, account: _AccountRow) -> None:
        self.account = account
        self.orders: dict[UUID, _OrderRow] = {}
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        if "FROM broker_accounts" in sql:
            return _Result(
                {
                    "gateway_label": self.account.gateway_label,
                    "mode": self.account.mode,
                    "currency_base": self.account.currency_base,
                    "account_number": self.account.account_number,
                    "last_nlv_currency": self.account.currency_base,
                }
            )
        if "FROM orders" in sql and "client_order_id = :client_order_id" in sql:
            row = next(
                (
                    order
                    for order in self.orders.values()
                    if order.client_order_id == params["client_order_id"]
                    and order.account_id == params["account_id"]
                ),
                None,
            )
            if row is None:
                return _Result(None)
            return _Result(
                {
                    "id": row.id,
                    "client_order_id": row.client_order_id,
                    "broker_order_id": row.broker_order_id,
                    "status": row.status,
                    "oca_group": row.oca_group,
                }
            )
        if "FROM orders" in sql and "parent_order_id = :parent_id" in sql:
            rows = [
                {
                    "id": order.id,
                    "broker_order_id": order.broker_order_id,
                    "status": order.status,
                    "order_type": order.order_type,
                }
                for order in self.orders.values()
                if order.parent_order_id == params["parent_id"]
            ]
            return _Result(rows=rows)
        if "DELETE FROM orders" in sql and "parent_order_id = :parent_id" in sql:
            parent_id = params["parent_id"]
            self.orders = {
                order_id: order
                for order_id, order in self.orders.items()
                if order.parent_order_id != parent_id
            }
            return _Result()
        if "DELETE FROM orders" in sql and "id = :parent_id" in sql:
            self.orders.pop(params["parent_id"], None)
            return _Result()
        if "INSERT INTO orders" in sql:
            now = datetime(2026, 4, 27, 14, 45, tzinfo=UTC)
            row = _OrderRow(
                id=params["id"],
                account_id=params.get("a", self.account.account_id),
                client_order_id=params["coid"],
                conid=params["conid"],
                symbol=params["symbol"],
                side=params["side"],
                order_type=params.get("ot", "STOP" if "stop_price" in sql else "LIMIT"),
                tif=params["tif"],
                qty=str(params["qty"]),
                limit_price=_decimal_or_none(params.get("lp", params.get("tp"))),
                stop_price=_decimal_or_none(params.get("sp")),
                status="pending_submit" if "pending_submit" in sql else "submitted",
                notional=Decimal(str(params["n"])),
                broker_order_id=params.get("bo"),
                parent_order_id=params.get("pid"),
                oca_group=params.get("oca"),
                created_at=now,
                updated_at=now,
            )
            self.orders[row.id] = row
            return _Result({"id": row.id})
        if "UPDATE orders SET broker_order_id = :bo, status = 'submitted' WHERE id = :id" in sql:
            row = self.orders[params["id"]]
            row.broker_order_id = params["bo"]
            row.status = "submitted"
            row.updated_at = datetime(2026, 4, 27, 14, 46, tzinfo=UTC)
            return _Result({"id": row.id})
        if "UPDATE orders" in sql and "SET status = 'rejected'" in sql:
            for row in self.orders.values():
                if row.id == params["id"] or row.parent_order_id == params["id"]:
                    row.status = "rejected"
            return _Result()
        if "FROM orders o" in sql and "FOR UPDATE NOWAIT" in sql:
            row = self.orders.get(params["order_id"])
            if row is None:
                return _Result(None)
            return _Result(
                {
                    "id": row.id,
                    "account_id": row.account_id,
                    "broker_order_id": row.broker_order_id,
                    "status": row.status,
                    "cancel_requested_at": row.cancel_requested_at,
                    "account_number": self.account.account_number,
                    "gateway_label": self.account.gateway_label,
                }
            )
        if "SET cancel_requested_at = :cancel_requested_at" in sql:
            row = self.orders[params["order_id"]]
            row.cancel_requested_at = params["cancel_requested_at"]
            return _Result(None)
        if "SET cancel_requested_at = NULL" in sql:
            row = self.orders[params["order_id"]]
            row.cancel_requested_at = None
            return _Result(None)
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1

    def begin(self) -> _BeginContext:
        return _BeginContext()


@dataclass
class _Config:
    kill_switch: bool = False
    max_notional_per_order: str = "20000"
    daily_notional_cap: str = "50000"
    trade_enabled: bool = True
    simulator_only: bool = False

    async def get_bool(self, namespace: str, key: str, *, default: bool) -> bool:
        if namespace != "broker":
            return default
        if key == "kill_switch_enabled":
            return self.kill_switch
        if key == "isa-paper.trade_enabled":
            return self.trade_enabled
        if key == "isa-paper.simulator_only":
            return self.simulator_only
        return default

    async def get(self, namespace: str, key: str, *, default: str) -> str:
        if namespace != "broker":
            return default
        if key == "isa-paper.max_notional_per_order":
            return self.max_notional_per_order
        if key == "isa-paper.daily_notional_cap":
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
        self.place_bracket_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self.raise_unavailable = False

    async def get_contract(self, conid: str) -> base.Contract:
        assert conid == self.contract.conid
        return self.contract

    async def place_bracket(
        self,
        *,
        parent_request_proto: Any,
        stop_loss_proto: Any,
        take_profit_proto: Any,
        oca_group: str,
    ) -> base.BracketResult:
        if self.raise_unavailable:
            from app.services.brokers import BrokerSidecarUnavailable

            raise BrokerSidecarUnavailable("isa-paper")
        self.place_bracket_calls.append(
            {
                "parent": parent_request_proto,
                "stop_loss": stop_loss_proto,
                "take_profit": take_profit_proto,
                "oca_group": oca_group,
            }
        )
        return base.BracketResult(
            parent_broker_order_id="BRK-PARENT-123",
            stop_loss_broker_order_id="BRK-SL-123" if stop_loss_proto is not None else "",
            take_profit_broker_order_id="BRK-TP-123" if take_profit_proto is not None else "",
            status="Submitted",
        )

    async def cancel_order(self, account_number: str, broker_order_id: str) -> bool:
        self.cancel_calls.append((account_number, broker_order_id))
        return True


class _Registry:
    def __init__(self, sidecar: _Sidecar) -> None:
        self.sidecar = sidecar

    async def get_client(self, label: str) -> _Sidecar:
        assert label == "isa-paper"
        return self.sidecar


@pytest.fixture
async def bracket_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    account_id = uuid4()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session = _Session(_AccountRow(account_id))
    config = _Config()
    contract = base.Contract(
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        asset_class="STOCK",
        conid="265598",
        local_symbol="AAPL",
    )
    sidecar = _Sidecar(contract)

    monkeypatch.setattr(
        orders_service,
        "_utcnow",
        lambda: datetime(2026, 4, 27, 14, 45, tzinfo=UTC),
    )

    # Phase 10a.5.1 C1.5: bypass risk gate for stub-Session tests.
    # Bracket reuses place_order's gate evaluator.
    from app.schemas.risk import GateVerdict

    async def _allow_verdict(*_args: Any, **_kwargs: Any) -> GateVerdict:
        return GateVerdict(final_verdict="allow", blockers=[], warnings=[], latency_ms=1)

    async def _none_instrument_id(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _noop_audit(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(orders_service, "_evaluate_risk_for_place_order", _allow_verdict)
    monkeypatch.setattr(orders_service, "_resolve_instrument_id", _none_instrument_id)
    monkeypatch.setattr(orders_service, "_audit_risk_decision_with_dedupe", _noop_audit)

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
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_broker_registry] = override_registry
    from app.api import orders as orders_api

    app.dependency_overrides[orders_api.get_order_capability_service] = override_capability
    app.dependency_overrides[orders_api.get_orders_redis] = override_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "account_id": account_id,
            "redis": redis,
            "session": session,
            "config": config,
            "sidecar": sidecar,
        }

    app.dependency_overrides.clear()


def _payload(account_id: UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "nonce": str(uuid4()),
        "account_id": str(account_id),
        "client_order_id": str(uuid4()),
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "DAY",
        "qty": "100",
        "limit_price": "150",
        "stop_price": "145",
        "target_price": "160",
    }
    body.update(overrides)
    return body


async def _store_nonce(redis: fakeredis.aioredis.FakeRedis, payload: dict[str, Any]) -> None:
    key = f"nonce:order:{payload['account_id']}:{payload['nonce']}"
    value = json.dumps(
        {
            "payload_hash": orders_service._modify_nonce_payload_hash(
                account_id=payload["account_id"],
                qty=payload["qty"],
                limit_price=payload["limit_price"],
            ),
            "rth_at_mint": True,
        },
        sort_keys=True,
    )
    await redis.set(key, value, ex=30)


def _rows(session: _Session) -> list[_OrderRow]:
    return sorted(
        session.orders.values(),
        key=lambda row: (
            row.parent_order_id is not None,
            str(row.parent_order_id or ""),
            str(row.id),
        ),
    )


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


@pytest.mark.asyncio
async def test_bracket_full_three_legs_writes_three_rows(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"])
    await _store_nonce(bracket_client["redis"], payload)

    response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert response.status_code == 200
    body = response.json()
    rows = _rows(bracket_client["session"])
    assert len(rows) == 3
    parent = next(row for row in rows if row.parent_order_id is None)
    children = [row for row in rows if row.parent_order_id == parent.id]
    assert len(children) == 2
    assert parent.broker_order_id == "BRK-PARENT-123"
    assert parent.oca_group is not None and parent.oca_group.startswith("BRK-")
    assert body["oca_group"] == parent.oca_group
    assert {child.order_type for child in children} == {"STOP", "LIMIT"}
    assert {child.parent_order_id for child in children} == {parent.id}
    assert {child.oca_group for child in rows} == {parent.oca_group}


@pytest.mark.asyncio
async def test_bracket_entry_plus_sl_only_writes_two_rows(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"], target_price=None)
    await _store_nonce(bracket_client["redis"], payload)

    response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert response.status_code == 200
    rows = _rows(bracket_client["session"])
    assert len(rows) == 2
    parent = next(row for row in rows if row.parent_order_id is None)
    children = [row for row in rows if row.parent_order_id == parent.id]
    assert len(children) == 1
    assert children[0].order_type == "STOP"
    assert children[0].broker_order_id == "BRK-SL-123"


@pytest.mark.asyncio
async def test_bracket_replay_same_client_order_id_returns_cached_rows(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"])
    await _store_nonce(bracket_client["redis"], payload)

    first = await bracket_client["client"].post("/api/orders/bracket", json=payload)
    second = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(_rows(bracket_client["session"])) == 3
    assert len(bracket_client["sidecar"].place_bracket_calls) == 1


@pytest.mark.asyncio
async def test_bracket_entry_plus_tp_only_writes_two_rows(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"], stop_price=None)
    await _store_nonce(bracket_client["redis"], payload)

    response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert response.status_code == 200
    rows = _rows(bracket_client["session"])
    assert len(rows) == 2
    parent = next(row for row in rows if row.parent_order_id is None)
    children = [row for row in rows if row.parent_order_id == parent.id]
    assert len(children) == 1
    assert children[0].order_type == "LIMIT"
    assert children[0].broker_order_id == "BRK-TP-123"


@pytest.mark.asyncio
async def test_bracket_invalid_buy_sl_price_rejected(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"], stop_price="150")

    response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "bracket_invalid_prices"


@pytest.mark.asyncio
async def test_bracket_no_legs_rejected(bracket_client: dict[str, Any]) -> None:
    payload = _payload(bracket_client["account_id"], stop_price=None, target_price=None)

    response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "bracket_invalid_legs"


@pytest.mark.asyncio
async def test_bracket_cancel_parent_leaves_children_for_broker_cascade(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"])
    await _store_nonce(bracket_client["redis"], payload)

    place_response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert place_response.status_code == 200
    parent_id = UUID(place_response.json()["parent"]["id"])

    cancel_response = await bracket_client["client"].delete(f"/api/orders/{parent_id}")

    assert cancel_response.status_code == 202
    rows = _rows(bracket_client["session"])
    children = [row for row in rows if row.parent_order_id == parent_id]
    assert len(children) == 2
    assert {child.status for child in children} == {"submitted"}
    assert bracket_client["sidecar"].cancel_calls == [("TEST_BRK_001", "BRK-PARENT-123")]


@pytest.mark.asyncio
async def test_bracket_sidecar_unavailable_rejects_pending_rows(
    bracket_client: dict[str, Any],
) -> None:
    payload = _payload(bracket_client["account_id"])
    await _store_nonce(bracket_client["redis"], payload)
    bracket_client["sidecar"].raise_unavailable = True

    response = await bracket_client["client"].post("/api/orders/bracket", json=payload)

    assert response.status_code == 503
    assert response.json() == {"error": "sidecar_unavailable"}
    assert response.headers["retry-after"] == "1"
    assert {row.status for row in _rows(bracket_client["session"])} == {"rejected"}
