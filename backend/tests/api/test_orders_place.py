from __future__ import annotations

import asyncio
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
from app.services.brokers import BrokerSidecarTimeout
from app.services.ibkr_maintenance import BrokerMaintenance


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@dataclass
class _AccountRow:
    account_id: UUID
    gateway_label: str = "isa-paper"
    account_number: str = "DUA0000000"
    mode: str = "paper"
    currency_base: str = "USD"


class _Result:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        scalar: Any = None,
    ) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row

    def first(self) -> dict[str, Any] | None:
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
        self.orders: dict[tuple[str, str], dict[str, Any]] = {}
        self.insert_attempts = 0
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        if "FROM broker_accounts" in sql:
            return _Result(
                {
                    "id": self.account.account_id,
                    "gateway_label": self.account.gateway_label,
                    "account_number": self.account.account_number,
                    "mode": self.account.mode,
                    "currency_base": self.account.currency_base,
                }
            )
        if "COALESCE(SUM(notional)" in sql:
            return _Result(scalar=self.filled_today)
        if "FROM positions" in sql:
            return _Result(scalar=self.position_qty)
        if "INSERT INTO orders" in sql:
            self.insert_attempts += 1
            key = (str(params["account_id"]), str(params["client_order_id"]))
            if key in self.orders:
                return _Result(row=None)
            now = datetime(2026, 4, 27, 14, 45, tzinfo=UTC)
            row = {
                "id": params["id"],
                "account_id": params["account_id"],
                "client_order_id": params["client_order_id"],
                "broker_order_id": None,
                "conid": params["conid"],
                "symbol": params["symbol"],
                "side": params["side"],
                "order_type": params["order_type"],
                "tif": params["tif"],
                "qty": Decimal(params["qty"]),
                "limit_price": _decimal_or_none(params["limit_price"]),
                "stop_price": _decimal_or_none(params["stop_price"]),
                "status": "pending_submit",
                "filled_qty": Decimal("0"),
                "avg_fill_price": None,
                "notional": Decimal(params["notional"]),
                "created_at": now,
                "updated_at": now,
                "last_event_at": None,
            }
            self.orders[key] = row
            return _Result(row=row)
        if "FROM orders" in sql and "client_order_id = :client_order_id" in sql:
            key = (str(params["account_id"]), str(params["client_order_id"]))
            return _Result(row=self.orders.get(key))
        if "UPDATE orders" in sql:
            for row in self.orders.values():
                if row["id"] == params["id"]:
                    row["broker_order_id"] = params["broker_order_id"]
                    row["status"] = params["status"]
                    row["updated_at"] = datetime(2026, 4, 27, 14, 46, tzinfo=UTC)
                    return _Result(row=row)
            return _Result(row=None)
        # Phase 5c+ daily-notional cap query — stub returning 0 used today.
        # App calls .scalar_one_or_none() so populate the scalar field.
        if "SUM(notional_filled)" in sql:
            return _Result(scalar=Decimal("0"))
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1


class _Config:
    def __init__(self, *, kill_switch_values: list[bool] | None = None) -> None:
        self.kill_switch_values = kill_switch_values or [False]

    async def get_bool(self, namespace: str, key: str, *, default: bool) -> bool:
        if namespace == "broker" and key == "kill_switch_enabled":
            if len(self.kill_switch_values) > 1:
                return self.kill_switch_values.pop(0)
            return self.kill_switch_values[0]
        if key in {"trade_enabled", "simulator_only"}:
            return key == "trade_enabled"
        return default

    async def get(self, namespace: str, key: str, *, default: str) -> str:
        if key == "daily_notional_cap":
            return "1000"
        if key == "max_notional_per_order":
            return "10000"
        return default


class _Sidecar:
    def __init__(self, contract: base.Contract) -> None:
        self.contract = contract
        self.place_calls: list[dict[str, str]] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self.place_error: Exception | None = None

    async def search_contracts(self, *, query: str) -> list[base.Contract]:
        assert query == self.contract.conid
        return [self.contract]

    async def get_contract(self, conid: str) -> base.Contract:
        assert conid == self.contract.conid
        return self.contract

    async def place_order(
        self,
        account_number: str,
        client_order_id: str,
        conid: str,
        side: str,
        order_type: str,
        tif: str,
        qty: str,
        limit_price: str = "",
        stop_price: str = "",
    ) -> base.PlaceOrderResult:
        self.place_calls.append(
            {
                "account_number": account_number,
                "client_order_id": client_order_id,
                "conid": conid,
                "side": side,
                "order_type": order_type,
                "tif": tif,
                "qty": qty,
                "limit_price": limit_price,
                "stop_price": stop_price,
            }
        )
        if self.place_error is not None:
            raise self.place_error
        return base.PlaceOrderResult(broker_order_id="BRK-123", status="Submitted")

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
async def place_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    account_id = uuid4()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session = _Session(_AccountRow(account_id))
    cfg = _Config()
    contract = base.Contract(
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        asset_class="STOCK",
        conid="265598",
        local_symbol="AAPL",
    )
    sidecar = _Sidecar(contract)

    from app.api import orders as orders_api

    monkeypatch.setattr(
        orders_api.orders_service,
        "_utcnow",
        lambda: datetime(2026, 4, 27, 14, 45, tzinfo=UTC),
    )

    async def override_db() -> AsyncIterator[_Session]:
        yield session

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    async def override_config() -> _Config:
        return cfg

    async def override_registry() -> _Registry:
        return _Registry(sidecar)

    async def override_redis() -> fakeredis.aioredis.FakeRedis:
        return redis

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[orders_api.get_orders_redis] = override_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "account_id": account_id,
            "redis": redis,
            "session": session,
            "config": cfg,
            "contract": contract,
            "sidecar": sidecar,
            "orders_api": orders_api,
        }

    app.dependency_overrides.clear()


def _payload(account_id: UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "account_id": str(account_id),
        "client_order_id": str(uuid4()),
        "nonce": str(uuid4()),
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


async def _store_nonce(
    redis: fakeredis.aioredis.FakeRedis,
    payload: dict[str, Any],
    *,
    rth_at_mint: bool = True,
    payload_hash: str | None = None,
) -> None:
    key = f"nonce:order:{payload['account_id']}:{payload['nonce']}"
    value = json.dumps(
        {
            "payload_hash": payload_hash or _hash_payload(payload),
            "rth_at_mint": rth_at_mint,
        },
        sort_keys=True,
    )
    await redis.set(key, value, ex=30)


@pytest.mark.asyncio
async def test_place_kill_switch_first(
    place_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    place_client["config"].kill_switch_values = [True]
    monkeypatch.setattr(
        place_client["orders_api"].orders_service,
        "compute_broker_maintenance",
        lambda now: BrokerMaintenance(True, "daily", now + timedelta(seconds=60)),
    )
    payload = _payload(place_client["account_id"], nonce=str(uuid4()))

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 503
    assert response.json() == {"error": "kill_switch_active"}


@pytest.mark.asyncio
async def test_place_consumes_redis_nonce_via_getdel(place_client: dict[str, Any]) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload)
    key = f"nonce:order:{payload['account_id']}:{payload['nonce']}"

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 200
    assert await place_client["redis"].get(key) is None


@pytest.mark.asyncio
async def test_place_rejects_unknown_nonce_with_422(place_client: dict[str, Any]) -> None:
    response = await place_client["client"].post(
        "/api/orders",
        json=_payload(place_client["account_id"]),
    )

    assert response.status_code == 422
    assert response.json()["error"] == "unknown_nonce"


@pytest.mark.asyncio
async def test_place_rejects_payload_mismatch_with_422(place_client: dict[str, Any]) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload, payload_hash="not-the-hash")

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 422
    assert response.json()["error"] == "payload_mismatch"


@pytest.mark.asyncio
async def test_place_rth_changed_between_preview_and_post_returns_422(
    place_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload, rth_at_mint=True)
    monkeypatch.setattr(
        place_client["orders_api"].orders_service,
        "_utcnow",
        lambda: datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
    )

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 422
    assert response.json() == {"error": "rth_changed", "detail": "re-preview required"}


@pytest.mark.asyncio
async def test_place_inserts_via_on_conflict_do_nothing(place_client: dict[str, Any]) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload)

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "submitted"
    assert body["broker_order_id"] == "BRK-123"
    assert body["submission_state"] == "submitted"
    assert place_client["session"].insert_attempts == 1


@pytest.mark.asyncio
async def test_place_idempotent_retry_returns_existing_row(place_client: dict[str, Any]) -> None:
    client_order_id = str(uuid4())
    first = _payload(place_client["account_id"], client_order_id=client_order_id)
    second = _payload(place_client["account_id"], client_order_id=client_order_id)
    await _store_nonce(place_client["redis"], first)
    await _store_nonce(place_client["redis"], second)

    first_response = await place_client["client"].post("/api/orders", json=first)
    second_response = await place_client["client"].post("/api/orders", json=second)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["id"] == first_response.json()["id"]
    assert second_response.json()["submission_state"] == "idempotent_retry"
    assert len(place_client["sidecar"].place_calls) == 1


@pytest.mark.asyncio
async def test_place_sidecar_timeout_marks_pending_unknown(place_client: dict[str, Any]) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload)
    place_client["sidecar"].place_error = BrokerSidecarTimeout("deadline")

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_submit"
    assert body["broker_order_id"] is None
    assert body["submission_state"] == "pending_unknown"


@pytest.mark.asyncio
async def test_place_kill_switch_flipped_post_sidecar_attempts_cancel(
    place_client: dict[str, Any],
) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload)
    place_client["config"].kill_switch_values = [False, True]

    response = await place_client["client"].post("/api/orders", json=payload)

    assert response.status_code == 200
    assert response.json()["submission_state"] == "submitted"
    assert place_client["sidecar"].cancel_calls == [("DUA0000000", "BRK-123")]


@pytest.mark.asyncio
async def test_concurrent_post_with_same_nonce_one_succeeds_one_422(
    place_client: dict[str, Any],
) -> None:
    payload = _payload(place_client["account_id"])
    await _store_nonce(place_client["redis"], payload)

    responses = await asyncio.gather(
        place_client["client"].post("/api/orders", json=payload),
        place_client["client"].post("/api/orders", json=payload),
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 422]
    assert [response.status_code for response in responses].count(200) == 1
    assert [response.status_code for response in responses].count(422) == 1


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical_payload = {
        "account_id": payload["account_id"],
        "conid": payload["conid"],
        "side": payload["side"],
        "order_type": payload["order_type"],
        "tif": payload["tif"],
        "qty": "1.00000000",
        "limit_price": "100.00000000",
        "stop_price": None,
    }
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
