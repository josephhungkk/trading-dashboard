"""Order preview business logic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import base
from app.core.ids import uuid7
from app.schemas.orders import (
    ContractSummary,
    OrderEvent,
    OrderListResponse,
    OrderResponse,
    PlaceOrderRequest,
    PolicyResponse,
    PositionSanityResult,
    PreviewRequest,
    PreviewResponse,
    _format_decimal_8,
)
from app.services.brokers import BrokerRegistry, BrokerSidecarTimeout, BrokerSidecarUnavailable
from app.services.config import ConfigService
from app.services.ibkr_maintenance import BrokerMaintenance, compute_broker_maintenance
from app.services.orders_policy import get_account_policy, is_kill_switch_active


class RedisLike(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None: ...

    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, time: int) -> bool: ...

    async def execute_command(self, *args: object) -> Any: ...


@dataclass(frozen=True)
class PreviewUnavailable(Exception):  # noqa: N818  # signals an HTTP-503-style preview rejection, not a generic Error subclass
    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class CancelUnavailable(Exception):  # noqa: N818
    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class _Account:
    gateway_label: str
    mode: str
    currency_base: str
    account_number: str = ""


@dataclass(frozen=True)
class CancelOrderResult:
    status: Literal["cancel_requested", "cancel_already_in_flight"]


class _CancelOrderClient(Protocol):
    async def cancel_order(self, account_number: str, broker_order_id: str) -> bool: ...


TERMINAL_STATUSES = ("filled", "cancelled", "rejected", "expired")


def canonicalize_qty(qty: str) -> str:
    return format(Decimal(qty).quantize(Decimal("1e-8")), "f")


_canonicalize_qty = canonicalize_qty


def cap_status(filled: Decimal, cap: Decimal) -> Literal["ok", "near", "exceeded"]:
    if filled > cap:
        return "exceeded"
    if cap > 0 and filled / cap >= Decimal("0.8"):
        return "near"
    return "ok"


async def preview_order(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    registry: BrokerRegistry,
    request_data: dict[str, Any],
    user_key: str,
) -> PreviewResponse:
    if await is_kill_switch_active(cfg):
        raise PreviewUnavailable(503, {"error": "kill_switch_active"})

    now = _utcnow()
    maintenance = compute_broker_maintenance(now)
    if maintenance.active:
        raise PreviewUnavailable(
            503,
            {
                "detail": f"IBKR {maintenance.window} maintenance window in progress",
                "broker_maintenance": maintenance.model_dump(mode="json"),
            },
            {"Retry-After": str(_retry_after(now, maintenance))},
        )

    request = PreviewRequest.model_validate(request_data)
    request = request.model_copy(update={"qty": canonicalize_qty(request.qty)})
    await _check_rate_limit(redis, user_key)

    account = await _resolve_account(db, request.account_id)
    client = await registry.get_client(account.gateway_label)
    contract = await _resolve_contract(client, request.conid)
    qty = Decimal(request.qty)
    notional_native = await _native_notional(redis, request, contract, qty)
    fx_rate = await _fx_rate(redis, contract.currency, account.currency_base)
    notional = (notional_native * fx_rate).quantize(Decimal("1e-8"))

    policy = await get_account_policy(cfg, gateway_label=account.gateway_label, mode=account.mode)
    filled_today = await _notional_filled_today(db, request.account_id)
    current_qty = await _position_qty(db, request.account_id, request.conid)
    nonce, payload_hash = _nonce_and_payload_hash(request)
    nonce_key = f"nonce:order:{request.account_id}:{nonce}"
    nonce_value = json.dumps(
        {"payload_hash": payload_hash, "rth_at_mint": _is_regular_trading_hours(now)}
    )
    await redis.set(nonce_key, nonce_value, ex=30, nx=True)

    return PreviewResponse(
        nonce=nonce,
        notional=_format_decimal_8(notional),
        notional_currency=account.currency_base,
        notional_filled_today=_format_decimal_8(filled_today),
        daily_notional_cap=_format_decimal_8(policy.daily_notional_cap),
        max_notional_per_order=_format_decimal_8(policy.max_notional_per_order),
        cap_status=cap_status(notional, policy.max_notional_per_order),
        daily_cap_status=cap_status(filled_today, policy.daily_notional_cap),
        position_sanity=PositionSanityResult.classify(current_qty, qty, request.side),
        contract_summary=ContractSummary(
            conid=int(contract.conid),
            description=_contract_description(contract),
        ),
        warnings=[],
    )


async def place_order(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    registry: BrokerRegistry,
    request_data: dict[str, Any],
) -> OrderResponse:
    if await is_kill_switch_active(cfg):
        raise PreviewUnavailable(503, {"error": "kill_switch_active"})

    now = _utcnow()
    maintenance = compute_broker_maintenance(now)
    if maintenance.active:
        raise PreviewUnavailable(
            503,
            {
                "detail": f"IBKR {maintenance.window} maintenance window in progress",
                "broker_maintenance": maintenance.model_dump(mode="json"),
            },
            {"Retry-After": str(_retry_after(now, maintenance))},
        )

    request = PlaceOrderRequest.model_validate(request_data)
    request = request.model_copy(update={"qty": _canonicalize_qty(request.qty)})
    account = await _resolve_account(db, request.account_id)
    client = await registry.get_client(account.gateway_label)
    contract = await _resolve_contract(client, request.conid)
    qty = Decimal(request.qty)
    notional_native = await _native_notional(redis, request, contract, qty)
    fx_rate = await _fx_rate(redis, contract.currency, account.currency_base)
    notional = (notional_native * fx_rate).quantize(Decimal("1e-8"))

    policy = await get_account_policy(cfg, gateway_label=account.gateway_label, mode=account.mode)
    filled_today = await _notional_filled_today(db, request.account_id)
    if cap_status(notional, policy.max_notional_per_order) == "exceeded":
        raise PreviewUnavailable(422, {"error": "max_notional_exceeded"})
    if cap_status(filled_today + notional, policy.daily_notional_cap) == "exceeded":
        raise PreviewUnavailable(422, {"error": "daily_notional_exceeded"})

    nonce_key = f"nonce:order:{request.account_id}:{request.nonce}"
    current_nonce_value = await redis.get(nonce_key)
    if current_nonce_value is None:
        raise PreviewUnavailable(422, {"error": "unknown_nonce"})
    current_nonce_payload = _decode_nonce_payload(current_nonce_value)
    rth_at_mint = current_nonce_payload.get("rth_at_mint")
    if rth_at_mint is not None and bool(rth_at_mint) != _is_regular_trading_hours(now):
        raise PreviewUnavailable(422, {"error": "rth_changed", "detail": "re-preview required"})

    consumed_nonce_value = await redis.execute_command("GETDEL", nonce_key)
    if consumed_nonce_value is None:
        raise PreviewUnavailable(422, {"error": "unknown_nonce"})
    consumed_nonce_payload = _decode_nonce_payload(consumed_nonce_value)
    if consumed_nonce_payload["payload_hash"] != _nonce_and_payload_hash(request)[1]:
        raise PreviewUnavailable(422, {"error": "payload_mismatch"})

    row = await _insert_order(db, request=request, contract=contract, notional=notional)
    if row is None:
        existing = await _select_order_by_client_id(db, request)
        if existing is None:
            raise PreviewUnavailable(
                422,
                {"error": "order_state_inconsistent", "detail": "retry preview"},
            )
        return _order_response_from_mapping(existing, submission_state="idempotent_retry")

    order_client = _as_order_sidecar_client(client)
    try:
        sidecar_result = await order_client.place_order(
            account.account_number,
            str(request.client_order_id),
            request.conid,
            request.side,
            request.order_type,
            request.tif,
            request.qty,
            request.limit_price or "",
            request.stop_price or "",
        )
    except BrokerSidecarTimeout, BrokerSidecarUnavailable:
        await db.commit()
        return _order_response_from_mapping(row, submission_state="pending_unknown")

    submitted = await _mark_order_submitted(
        db,
        order_id=row["id"],
        broker_order_id=sidecar_result.broker_order_id,
    )
    await db.commit()

    if await is_kill_switch_active(cfg):
        try:
            await order_client.cancel_order(account.account_number, sidecar_result.broker_order_id)
        except BrokerSidecarTimeout, BrokerSidecarUnavailable:
            pass

    return _order_response_from_mapping(submitted or row, submission_state="submitted")


async def list_orders(
    *,
    db: AsyncSession,
    cfg: ConfigService,
    status: str | None = None,
) -> OrderListResponse:
    if status is None:
        where_clause = "WHERE status NOT IN ('filled', 'cancelled', 'rejected', 'expired')"
        params: dict[str, object] = {}
    else:
        where_clause = "WHERE status = CAST(:status AS order_status_enum)"
        params = {"status": status}

    result = await db.execute(
        text(
            f"""
            SELECT id, account_id, broker_order_id, symbol, side, order_type, tif, qty,
                   limit_price, stop_price, status, filled_qty, avg_fill_price, notional,
                   created_at, updated_at, last_event_at
              FROM orders
              {where_clause}
             ORDER BY created_at DESC;
            """
        ),
        params,
    )
    orders = [_order_response_from_mapping(row) for row in result.mappings().all()]
    kill_switch_active = await cfg.get_bool("broker", "kill_switch", default=False)

    return OrderListResponse(
        orders=orders,
        broker_maintenance=compute_broker_maintenance(_utcnow()),
        kill_switch_active=kill_switch_active is True,
    )


async def get_order_by_id(*, db: AsyncSession, order_id: UUID) -> OrderResponse | None:
    order_result = await db.execute(
        text(
            """
            SELECT id, account_id, broker_order_id, symbol, side, order_type, tif, qty,
                   limit_price, stop_price, status, filled_qty, avg_fill_price, notional,
                   created_at, updated_at, last_event_at
              FROM orders
             WHERE id = :order_id;
            """
        ),
        {"order_id": order_id},
    )
    order_row = order_result.mappings().one_or_none()
    if order_row is None:
        return None

    events_result = await db.execute(
        text(
            """
            SELECT COALESCE(broker_order_id, '') AS broker_order_id,
                   COALESCE(raw_payload->>'client_order_id', '') AS client_order_id,
                   status,
                   COALESCE(filled_qty, 0) AS filled_qty,
                   COALESCE(avg_fill_price, 0) AS avg_fill_price,
                   broker_event_at,
                   COALESCE(raw_payload::text, '{}') AS raw_payload
              FROM order_events
             WHERE order_id = :order_id
             ORDER BY broker_event_at DESC;
            """
        ),
        {"order_id": order_id},
    )
    events = [OrderEvent.model_validate(row) for row in events_result.mappings().all()]
    return _order_response_from_mapping(order_row, events=events)


async def get_account_policy_response(
    *,
    db: AsyncSession,
    cfg: ConfigService,
    account_id: UUID,
) -> PolicyResponse | None:
    account = await _get_account_for_policy(db, account_id)
    if account is None:
        return None

    policy = await get_account_policy(
        cfg,
        gateway_label=account.gateway_label,
        mode=account.mode,
    )
    notional_today = await _active_notional_today(db, account_id)
    position_count = await _position_count(db, account_id)

    return PolicyResponse(
        account_id=account_id,
        max_notional_per_order=_format_decimal_8(policy.max_notional_per_order),
        daily_notional_cap=_format_decimal_8(policy.daily_notional_cap),
        notional_filled_today=_format_decimal_8(notional_today),
        trade_enabled=policy.trade_enabled,
        simulator_only=policy.simulator_only,
        position_count=position_count,
    )


async def cancel_order(
    *,
    db: AsyncSession,
    registry: BrokerRegistry,
    order_id: UUID,
) -> CancelOrderResult:
    now = _utcnow()
    try:
        row = await _locked_order_for_cancel(db, order_id)
    except Exception as exc:
        if _is_lock_not_available(exc):
            raise CancelUnavailable(
                423,
                {"error": "locked", "detail": "order is locked; retry shortly"},
                {"Retry-After": "1"},
            ) from exc
        raise

    if row is None:
        raise CancelUnavailable(404, {"error": "not_found", "detail": f"order {order_id}"})

    status = str(row["status"])
    if status in TERMINAL_STATUSES:
        raise CancelUnavailable(409, {"error": "already_finalized", "status": status})

    if _cancel_request_in_flight(row["cancel_requested_at"], now):
        return CancelOrderResult(status="cancel_already_in_flight")

    broker_order_id = row["broker_order_id"]
    if broker_order_id is None or str(broker_order_id) == "":
        raise CancelUnavailable(409, {"error": "broker_order_id_unavailable"})

    await db.execute(
        text(
            """
            UPDATE orders
               SET cancel_requested_at = :cancel_requested_at,
                   updated_at = now()
             WHERE id = :order_id;
            """
        ),
        {"order_id": order_id, "cancel_requested_at": now},
    )
    await db.commit()

    # Architect-review a81e7988 H2: if the sidecar call fails, reset
    # cancel_requested_at to NULL so the next DELETE retries the forward
    # instead of returning the 5s-cooldown false-positive
    # "cancel_already_in_flight" (R31 cooldown intent is "5s after a
    # SUCCESSFUL forward").
    client = await registry.get_client(str(row["gateway_label"]))
    try:
        await _as_cancel_order_client(client).cancel_order(
            str(row["account_number"]),
            str(broker_order_id),
        )
    except BrokerSidecarTimeout, BrokerSidecarUnavailable:
        await db.execute(
            text(
                """
                UPDATE orders
                   SET cancel_requested_at = NULL,
                       updated_at = now()
                 WHERE id = :order_id;
                """
            ),
            {"order_id": order_id},
        )
        await db.commit()
        raise CancelUnavailable(
            503,
            {"error": "sidecar_unavailable", "detail": "cancel forward failed; retry"},
            {"Retry-After": "1"},
        ) from None
    return CancelOrderResult(status="cancel_requested")


async def _check_rate_limit(redis: RedisLike, user_key: str) -> None:
    key = f"rl:orders-preview:{user_key}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > 10:
        raise PreviewUnavailable(429, {"error": "rate_limited"}, {"Retry-After": "60"})


async def _resolve_account(db: AsyncSession, account_id: object) -> _Account:
    result = await db.execute(
        text(
            """
            SELECT gateway_label, mode, currency_base, account_number
              FROM broker_accounts
             WHERE id = :account_id AND deleted_at IS NULL;
            """
        ),
        {"account_id": account_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise PreviewUnavailable(404, {"error": "not_found", "detail": f"account {account_id}"})
    return _Account(
        gateway_label=str(row["gateway_label"]),
        mode=str(row["mode"]),
        currency_base=str(row["currency_base"]),
        account_number=str(row["account_number"]) if "account_number" in row else "",
    )


async def _get_account_for_policy(db: AsyncSession, account_id: UUID) -> _Account | None:
    result = await db.execute(
        text(
            """
            SELECT gateway_label, account_number, mode, currency_base
              FROM broker_accounts
             WHERE id = :account_id AND deleted_at IS NULL;
            """
        ),
        {"account_id": account_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return _Account(
        gateway_label=str(row["gateway_label"]),
        account_number=str(row["account_number"]) if "account_number" in row else "",
        mode=str(row["mode"]),
        currency_base=str(row["currency_base"]),
    )


async def _locked_order_for_cancel(
    db: AsyncSession,
    order_id: UUID,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            SELECT o.id,
                   o.account_id,
                   o.broker_order_id,
                   o.status,
                   o.cancel_requested_at,
                   ba.account_number,
                   ba.gateway_label
             FROM orders o
              JOIN broker_accounts ba ON ba.id = o.account_id
             WHERE o.id = :order_id
             FOR UPDATE NOWAIT;
            """
        ),
        {"order_id": order_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


def _cancel_request_in_flight(value: object, now: datetime) -> bool:
    if not isinstance(value, datetime):
        return False
    requested_at = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return requested_at >= now - timedelta(seconds=5)


def _as_cancel_order_client(client: object) -> _CancelOrderClient:
    return cast(_CancelOrderClient, client)


def _is_lock_not_available(exc: BaseException) -> bool:
    if isinstance(exc, asyncpg.exceptions.LockNotAvailableError):
        return True
    if isinstance(exc, DBAPIError):
        return isinstance(exc.orig, asyncpg.exceptions.LockNotAvailableError)
    return False


class _ContractSearchClient(Protocol):
    """Minimal protocol for _resolve_contract — accepts any object with the
    expected search_contracts signature (BrokerSidecarClient + test mocks)."""

    async def search_contracts(self, *, query: str) -> list[base.Contract]: ...


class _OrderSidecarClient(_ContractSearchClient, Protocol):
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
    ) -> base.PlaceOrderResult: ...

    async def cancel_order(self, account_number: str, broker_order_id: str) -> bool: ...


def _as_order_sidecar_client(client: object) -> _OrderSidecarClient:
    return client  # type: ignore[return-value]


async def _resolve_contract(client: _ContractSearchClient, conid: str) -> base.Contract:
    contracts = await client.search_contracts(query=conid)
    for contract in contracts:
        if contract.conid == conid:
            return contract
    raise PreviewUnavailable(404, {"error": "contract_not_found", "conid": conid})


async def _native_notional(
    redis: RedisLike,
    request: PreviewRequest,
    contract: base.Contract,
    qty: Decimal,
) -> Decimal:
    if request.order_type == "LIMIT" and request.limit_price is not None:
        return qty * Decimal(request.limit_price)
    if request.order_type == "STOP" and request.stop_price is not None:
        return qty * Decimal(request.stop_price)
    mid = await _get_market_mid(redis, request.conid)
    return qty * mid * Decimal("1.05")


async def _get_market_mid(redis: RedisLike, conid: str) -> Decimal:
    # Namespace mkt:mid:<conid> distinct from FX-pair fx:mid:<from>:<to> below
    # (architect-review aa2071a6 — collision risk if both used fx:mid:).
    cached = await redis.get(f"mkt:mid:{conid}")
    if cached is None:
        raise PreviewUnavailable(503, {"error": "market_mid_unavailable", "conid": conid})
    return Decimal(_redis_text(cached))


async def _fx_rate(redis: RedisLike, from_currency: str, to_currency: str) -> Decimal:
    if from_currency == to_currency:
        return Decimal("1")
    pair = f"{from_currency}:{to_currency}"
    cached = await redis.get(f"fx:mid:{pair}")
    if cached is None:
        raise PreviewUnavailable(503, {"error": "fx_rate_unavailable", "pair": pair})
    return Decimal(_redis_text(cached))


async def _notional_filled_today(db: AsyncSession, account_id: object) -> Decimal:
    result = await db.execute(
        text(
            """
            SELECT COALESCE(SUM(notional), 0)
              FROM orders
             WHERE account_id = :account_id
               AND created_at > date_trunc('day', now())
               AND status NOT IN ('cancelled', 'rejected');
            """
        ),
        {"account_id": account_id},
    )
    return Decimal(str(result.scalar_one_or_none() or "0")).quantize(Decimal("1e-8"))


async def _position_qty(db: AsyncSession, account_id: object, conid: str) -> Decimal:
    result = await db.execute(
        text(
            """
            SELECT qty
              FROM positions
             WHERE account_id = :account_id AND conid = :conid;
            """
        ),
        {"account_id": account_id, "conid": conid},
    )
    return Decimal(str(result.scalar_one_or_none() or "0"))


async def _active_notional_today(db: AsyncSession, account_id: UUID) -> Decimal:
    result = await db.execute(
        text(
            """
            SELECT COALESCE(SUM(notional), 0)
              FROM orders
             WHERE account_id = :account_id
               AND DATE(created_at) = CURRENT_DATE
               AND status NOT IN ('filled', 'cancelled', 'rejected', 'expired');
            """
        ),
        {"account_id": account_id},
    )
    return Decimal(str(result.scalar_one_or_none() or "0")).quantize(Decimal("1e-8"))


async def _position_count(db: AsyncSession, account_id: UUID) -> int:
    exists_result = await db.execute(text("SELECT to_regclass('public.positions')"))
    if exists_result.scalar_one_or_none() is None:
        return 0

    result = await db.execute(
        text(
            """
            SELECT COUNT(*)
              FROM positions
             WHERE account_id = :account_id;
            """
        ),
        {"account_id": account_id},
    )
    return int(result.scalar_one())


async def _insert_order(
    db: AsyncSession,
    *,
    request: PlaceOrderRequest,
    contract: base.Contract,
    notional: Decimal,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            INSERT INTO orders (
                id, account_id, client_order_id, conid, symbol, side, order_type, tif,
                qty, limit_price, stop_price, notional
            )
            VALUES (
                :id, :account_id, :client_order_id, :conid, :symbol, :side, :order_type, :tif,
                :qty, :limit_price, :stop_price, :notional
            )
            ON CONFLICT (account_id, client_order_id) DO NOTHING
            RETURNING id, account_id, client_order_id, broker_order_id, conid, symbol,
                side, order_type, tif, qty, limit_price, stop_price, status, filled_qty,
                avg_fill_price, notional, created_at, updated_at, last_event_at;
            """
        ),
        {
            "id": uuid7(),
            "account_id": request.account_id,
            "client_order_id": request.client_order_id,
            "conid": request.conid,
            "symbol": contract.symbol or contract.local_symbol or request.conid,
            "side": request.side,
            "order_type": request.order_type,
            "tif": request.tif,
            "qty": request.qty,
            "limit_price": request.limit_price,
            "stop_price": request.stop_price,
            "notional": _format_decimal_8(notional),
        },
    )
    return _mapping_or_none(result)


async def _select_order_by_client_id(
    db: AsyncSession,
    request: PlaceOrderRequest,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            SELECT id, account_id, client_order_id, broker_order_id, conid, symbol,
                   side, order_type, tif, qty, limit_price, stop_price, status, filled_qty,
                   avg_fill_price, notional, created_at, updated_at, last_event_at
              FROM orders
             WHERE account_id = :account_id AND client_order_id = :client_order_id;
            """
        ),
        {"account_id": request.account_id, "client_order_id": request.client_order_id},
    )
    return _mapping_or_none(result)


async def _mark_order_submitted(
    db: AsyncSession,
    *,
    order_id: object,
    broker_order_id: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            UPDATE orders
               SET broker_order_id = :broker_order_id,
                   status = :status,
                   updated_at = now()
             WHERE id = :id
            RETURNING id, account_id, client_order_id, broker_order_id, conid, symbol,
                side, order_type, tif, qty, limit_price, stop_price, status, filled_qty,
                avg_fill_price, notional, created_at, updated_at, last_event_at;
            """
        ),
        {"id": order_id, "broker_order_id": broker_order_id, "status": "submitted"},
    )
    return _mapping_or_none(result)


def _mapping_or_none(result: Any) -> dict[str, Any] | None:
    row = result.mappings().first()
    if row is None:
        return None
    return dict(row)


def _order_response_from_mapping(
    row: Any,
    *,
    events: list[OrderEvent] | None = None,
    submission_state: Literal["submitted", "pending_unknown", "idempotent_retry"] = "submitted",
) -> OrderResponse:
    return OrderResponse(
        id=row["id"],
        account_id=row["account_id"],
        broker_order_id=row["broker_order_id"],
        symbol=row["symbol"],
        side=row["side"],
        order_type=row["order_type"],
        tif=row["tif"],
        qty=row["qty"],
        limit_price=row["limit_price"],
        stop_price=row["stop_price"],
        status=row["status"],
        filled_qty=row["filled_qty"],
        avg_fill_price=row["avg_fill_price"],
        notional=row["notional"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_event_at=row["last_event_at"],
        submission_state=submission_state,
        events=events or [],
    )


def _nonce_and_payload_hash(request: PreviewRequest) -> tuple[str, str]:
    nonce = str(uuid4())
    payload = {
        "account_id": str(request.account_id),
        "conid": request.conid,
        "side": request.side,
        "order_type": request.order_type,
        "tif": request.tif,
        "qty": request.qty,
        "limit_price": _canonical_decimal_or_none(request.limit_price),
        "stop_price": _canonical_decimal_or_none(request.stop_price),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return nonce, hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_decimal_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    return canonicalize_qty(value)


def _contract_description(contract: base.Contract) -> str:
    return " ".join(
        part for part in (contract.symbol, contract.exchange, contract.currency) if part
    )


def _redis_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _decode_nonce_payload(value: object) -> dict[str, object]:
    text_value = _redis_text(value)
    try:
        parsed = json.loads(text_value)
    except json.JSONDecodeError:
        return {"payload_hash": text_value, "rth_at_mint": None}
    if isinstance(parsed, dict) and isinstance(parsed.get("payload_hash"), str):
        return parsed
    return {"payload_hash": text_value, "rth_at_mint": None}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_regular_trading_hours(now: datetime) -> bool:
    utc_now = now.astimezone(UTC)
    if utc_now.weekday() >= 5:
        return False
    minutes = utc_now.hour * 60 + utc_now.minute
    return 14 * 60 + 30 <= minutes < 21 * 60


def _retry_after(now: datetime, maintenance: BrokerMaintenance) -> int:
    if maintenance.until is None:
        return 30
    return max(1, int((maintenance.until - now).total_seconds()))
