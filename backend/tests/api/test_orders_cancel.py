from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_db, require_admin_jwt
from app.main import app
from app.services import orders_service


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@dataclass
class _OrderRow:
    id: UUID
    account_id: UUID
    account_number: str = "DUA0000000"
    gateway_label: str = "isa-paper"
    broker_order_id: str | None = "BRK-123"
    status: str = "submitted"
    qty: Decimal = Decimal("10")
    filled_qty: Decimal = Decimal("0")
    cancel_requested_at: datetime | None = None


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class _Session:
    def __init__(self, row: _OrderRow, *, lock_contention: bool = False) -> None:
        self.row = row
        self.lock_contention = lock_contention
        self.sql: list[str] = []
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        self.sql.append(sql)
        if "FOR UPDATE NOWAIT" in sql and self.lock_contention:
            raise _LockNotAvailableError("row is locked")
        if "FROM orders" in sql:
            if params["order_id"] != self.row.id:
                return _Result(None)
            return _Result(
                {
                    "id": self.row.id,
                    "account_id": self.row.account_id,
                    "account_number": self.row.account_number,
                    "gateway_label": self.row.gateway_label,
                    "broker_order_id": self.row.broker_order_id,
                    "status": self.row.status,
                    "qty": self.row.qty,
                    "filled_qty": self.row.filled_qty,
                    "cancel_requested_at": self.row.cancel_requested_at,
                }
            )
        if "UPDATE orders" in sql:
            # Two UPDATE shapes: setting cooldown (cancel_requested_at = :ts)
            # OR resetting it to NULL (no :cancel_requested_at param —
            # architect-review a81e7988 H2 sidecar-failure rollback).
            self.row.cancel_requested_at = params.get("cancel_requested_at")
            return _Result(None)
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1


class _LockNotAvailableError(Exception):
    pass


class _Sidecar:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.calls: list[tuple[str, str]] = []

    async def cancel_order(self, account_number: str, broker_order_id: str) -> bool:
        self.calls.append((account_number, broker_order_id))
        if self.session.row.status == "partial":
            self.session.row.status = "cancelled"
        return True


class _Registry:
    def __init__(self, sidecar: _Sidecar) -> None:
        self.sidecar = sidecar

    async def get_client(self, label: str) -> _Sidecar:
        assert label == "isa-paper"
        return self.sidecar


@pytest.fixture
async def cancel_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    account_id = uuid4()
    row = _OrderRow(id=uuid4(), account_id=account_id)
    session = _Session(row)
    sidecar = _Sidecar(session)

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    async def override_db() -> AsyncIterator[_Session]:
        yield session

    async def override_registry() -> _Registry:
        return _Registry(sidecar)

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_broker_registry] = override_registry
    monkeypatch.setattr(orders_service, "_is_lock_not_available", _is_test_lock_error)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "row": row, "session": session, "sidecar": sidecar}

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_terminal_returns_409(cancel_client: dict[str, Any]) -> None:
    for status in ("filled", "cancelled", "rejected", "expired"):
        cancel_client["row"].status = status
        cancel_client["sidecar"].calls.clear()

        response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

        assert response.status_code == 409
        assert response.json()["error"] == "already_finalized"
        assert cancel_client["sidecar"].calls == []


@pytest.mark.asyncio
async def test_cancel_partial_then_cancel_models_correctly(
    cancel_client: dict[str, Any],
) -> None:
    cancel_client["row"].status = "partial"
    cancel_client["row"].filled_qty = Decimal("4")

    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 202
    assert cancel_client["row"].status == "cancelled"
    assert cancel_client["row"].filled_qty < cancel_client["row"].qty


@pytest.mark.asyncio
async def test_cancel_idempotent_within_5s_returns_202(
    cancel_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(orders_service, "_utcnow", lambda: now)
    cancel_client["row"].cancel_requested_at = now - timedelta(seconds=4)

    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 202
    assert response.json() == {"status": "cancel_already_in_flight"}
    assert cancel_client["sidecar"].calls == []


@pytest.mark.asyncio
async def test_cancel_after_5s_re_forwards_to_sidecar(
    cancel_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(orders_service, "_utcnow", lambda: now)
    cancel_client["row"].cancel_requested_at = now - timedelta(seconds=6)

    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 202
    assert response.json() == {"status": "cancel_requested"}
    assert cancel_client["sidecar"].calls == [("DUA0000000", "BRK-123")]


@pytest.mark.asyncio
async def test_cancel_uses_for_update_nowait_row_lock(cancel_client: dict[str, Any]) -> None:
    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 202
    assert any("FOR UPDATE NOWAIT" in sql for sql in cancel_client["session"].sql)


@pytest.mark.asyncio
async def test_cancel_forwards_account_number_and_broker_order_id(
    cancel_client: dict[str, Any],
) -> None:
    cancel_client["row"].account_number = "DUA9999999"
    cancel_client["row"].broker_order_id = "BRK-999"

    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 202
    assert cancel_client["sidecar"].calls == [("DUA9999999", "BRK-999")]


@pytest.mark.asyncio
async def test_cancel_under_lock_contention_returns_423(
    cancel_client: dict[str, Any],
) -> None:
    cancel_client["session"].lock_contention = True

    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 423
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"] == "locked"
    assert cancel_client["sidecar"].calls == []


def _is_test_lock_error(exc: BaseException) -> bool:
    return isinstance(exc, _LockNotAvailableError)


@pytest.mark.asyncio
async def test_cancel_resets_cooldown_when_sidecar_unavailable(
    cancel_client: dict[str, Any],
) -> None:
    """Architect-review a81e7988 H2: if sidecar.cancel_order raises
    BrokerSidecarUnavailable, cancel_requested_at must reset to NULL so the
    next DELETE retries the forward — otherwise R31's 5s cooldown blocks
    recovery from a transient sidecar failure with a false-positive
    "cancel_already_in_flight"."""

    async def failing_cancel(account_number: str, broker_order_id: str) -> bool:
        from app.services.brokers import BrokerSidecarUnavailable

        raise BrokerSidecarUnavailable("sidecar 503", label="isa-paper")

    cancel_client["sidecar"].cancel_order = failing_cancel  # type: ignore[method-assign]

    response = await cancel_client["client"].delete(f"/api/orders/{cancel_client['row'].id}")

    assert response.status_code == 503
    assert response.json()["error"] == "sidecar_unavailable"
    assert response.headers["Retry-After"] == "1"
    # The row's cancel_requested_at must be NULL so the operator can retry.
    assert cancel_client["row"].cancel_requested_at is None
