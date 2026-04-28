"""Order preview business logic."""

from __future__ import annotations

import base64
import hashlib
import json
import time
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
    OrderBracketRequest,
    OrderEvent,
    OrderListResponse,
    OrderModifyRequest,
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

_MODIFY_REPLAY_CACHE: dict[tuple[UUID, str], tuple[float, dict[str, Any]]] = {}
_MODIFY_REPLAY_TTL_SECONDS = 60.0


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


async def modify_order(
    db: AsyncSession,
    redis: RedisLike,
    config: ConfigService,
    registry: BrokerRegistry,
    *,
    order_id: UUID,
    request: OrderModifyRequest,
) -> dict[str, Any]:
    cached = _modify_replay_lookup(order_id, request.nonce)
    if cached is not None:
        return cached

    result = await db.execute(
        text(
            """
            SELECT account_id,
                   broker_order_id,
                   conid,
                   symbol,
                   side,
                   order_type,
                   tif,
                   qty,
                   limit_price,
                   stop_price,
                   status::text AS status,
                   filled_qty,
                   parent_order_id,
                   client_order_id
              FROM orders
             WHERE id = :id;
            """
        ),
        {"id": order_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise PreviewUnavailable(404, {"error": "not_found"})

    status = str(row["status"])
    if status in TERMINAL_STATUSES:
        raise PreviewUnavailable(409, {"error": "terminal_status"})

    filled_qty = Decimal(str(row["filled_qty"] or "0"))
    if row["parent_order_id"] is None and filled_qty > 0:
        child_result = await db.execute(
            text(
                """
                SELECT 1
                  FROM orders
                 WHERE parent_order_id = :p
                   AND status NOT IN ('filled', 'cancelled', 'rejected', 'expired')
                 LIMIT 1;
                """
            ),
            {"p": order_id},
        )
        if child_result.scalar_one_or_none() is not None:
            raise PreviewUnavailable(409, {"error": "bracket_parent_partial"})

    if await config.get_bool("broker", "kill_switch_enabled", default=False):
        raise PreviewUnavailable(503, {"error": "kill_switch"})

    account = await _resolve_account(db, row["account_id"])
    qty_text = canonicalize_qty(request.qty)
    existing_limit_price = str(row["limit_price"]) if row["limit_price"] is not None else None
    new_limit_price = request.limit_price or existing_limit_price
    new_notional = Decimal(qty_text) * Decimal(new_limit_price or "0")
    await _check_trade_policy(
        config,
        account.gateway_label,
        notional=new_notional,
        currency_base=account.currency_base,
        redis=redis,
        mode=account.mode,
    )
    await _consume_nonce(
        redis,
        request.nonce,
        account_id=row["account_id"],
        qty=qty_text,
        limit_price=request.limit_price,
    )

    raw_payload = {
        "client_order_id": str(row["client_order_id"]),
        "qty": qty_text,
        "limit_price": _canonical_decimal_or_none(request.limit_price),
        "stop_price": _canonical_decimal_or_none(request.stop_price),
        "tif": request.tif,
    }
    await db.execute(
        text(
            """
            INSERT INTO order_events (
                order_id,
                account_id,
                broker_order_id,
                status,
                filled_qty,
                avg_fill_price,
                broker_event_at,
                raw_payload
            )
            VALUES (
                :order_id,
                :account_id,
                :broker_order_id,
                CAST(:status AS order_status_enum),
                NULL,
                NULL,
                now() - interval '100 milliseconds',
                CAST(:raw_payload AS jsonb)
            );
            """
        ),
        {
            "order_id": order_id,
            "account_id": row["account_id"],
            "broker_order_id": row["broker_order_id"],
            "status": "modified",
            "raw_payload": json.dumps(raw_payload),
        },
    )
    await db.commit()

    client = await registry.get_client(account.gateway_label)
    contract = await client.get_contract(str(row["conid"]))
    modify_result = await _as_order_sidecar_client(client).modify_order(
        broker_order_id=str(row["broker_order_id"] or ""),
        account_number=account.account_number,
        contract=contract,
        side=str(row["side"]),
        order_type=str(row["order_type"]),
        tif=request.tif,
        qty=qty_text,
        limit_price=request.limit_price or "",
        stop_price=request.stop_price or "",
        client_order_id=str(row["client_order_id"]),
    )

    projected = {
        "id": order_id,
        "client_order_id": str(row["client_order_id"]),
        "broker_order_id": modify_result.broker_order_id or str(row["broker_order_id"] or ""),
        "status": "modified",
        "qty": qty_text,
        "limit_price": request.limit_price,
        "stop_price": request.stop_price,
        "tif": request.tif,
    }
    _modify_replay_store(order_id, request.nonce, projected)
    return projected


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
            SELECT gateway_label, mode, currency_base, account_number,
                   last_nlv_currency
              FROM broker_accounts
             WHERE id = :account_id AND deleted_at IS NULL;
            """
        ),
        {"account_id": account_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise PreviewUnavailable(404, {"error": "not_found", "detail": f"account {account_id}"})
    # 5b.1 update: sidecar now runs a per-account reqAccountUpdates round
    # at startup BEFORE reqAccountSummaryAsync (the IBKR API allows only one
    # active reqAccountUpdates subscription per connection at a time, so the
    # round MUST complete first). _base_currency reads the .currency field
    # of the NetLiquidation row, so currency_base normally arrives populated.
    # The last_nlv_currency fallback is retained as defence-in-depth: a new
    # account added mid-run won't have BASE cached until the next sidecar
    # restart, but the discoverer's NLV fan-out still fills last_nlv_currency
    # within one 30s tick.
    currency_base = str(row["currency_base"]) or str(row["last_nlv_currency"] or "")
    return _Account(
        gateway_label=str(row["gateway_label"]),
        mode=str(row["mode"]),
        currency_base=currency_base,
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
    """Minimal protocol for _resolve_contract — accepts any object exposing
    both the symbol-search RPC (autocomplete) and the conid-resolve RPC.
    BrokerSidecarClient + test mocks satisfy this."""

    async def search_contracts(self, *, query: str) -> list[base.Contract]: ...

    async def get_contract(self, conid: str) -> base.Contract: ...


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
    ) -> base.ModifyOrderResult: ...


def _as_order_sidecar_client(client: object) -> _OrderSidecarClient:
    return client  # type: ignore[return-value]


async def _resolve_contract(client: _ContractSearchClient, conid: str) -> base.Contract:
    # search_contracts is symbol-name autocomplete (reqMatchingSymbols);
    # it cannot resolve a numeric conid. Use the explicit GetContract RPC
    # (qualifyContractsAsync on the sidecar) so a numeric conid like 265598
    # round-trips correctly.
    try:
        return await client.get_contract(conid)
    except BrokerSidecarUnavailable, BrokerSidecarTimeout:
        raise
    except Exception as exc:  # contract not found / sidecar logic error
        raise PreviewUnavailable(404, {"error": "contract_not_found", "conid": conid}) from exc


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
    # As of 5b.1 the positions table is guaranteed by Alembic 0005 + populated
    # by BrokerDiscoverer fan-out within 30s of bootstrap. Returns Decimal("0")
    # for accounts with no holdings (no row), as designed.
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


def _modify_nonce_payload_hash(
    *,
    account_id: object,
    qty: str,
    limit_price: str | None,
) -> str:
    canonical = json.dumps(
        {
            "account_id": str(account_id),
            "qty": canonicalize_qty(qty),
            "limit_price": _canonical_decimal_or_none(limit_price),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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


def _modify_replay_lookup(order_id: UUID, nonce: str) -> dict[str, Any] | None:
    now = time.monotonic()
    expired_keys = [key for key, (deadline, _) in _MODIFY_REPLAY_CACHE.items() if deadline <= now]
    for key in expired_keys:
        _MODIFY_REPLAY_CACHE.pop(key, None)

    entry = _MODIFY_REPLAY_CACHE.get((order_id, nonce))
    if entry is None:
        return None
    deadline, response = entry
    if deadline <= now:
        _MODIFY_REPLAY_CACHE.pop((order_id, nonce), None)
        return None
    return dict(response)


def _modify_replay_store(order_id: UUID, nonce: str, response: dict[str, Any]) -> None:
    _MODIFY_REPLAY_CACHE[(order_id, nonce)] = (
        time.monotonic() + _MODIFY_REPLAY_TTL_SECONDS,
        dict(response),
    )


async def _check_trade_policy(
    cfg: ConfigService,
    gateway_label: str,
    *,
    notional: Decimal,
    currency_base: str,
    redis: RedisLike,
    mode: str = "live",
) -> None:
    del currency_base, redis
    policy = await get_account_policy(cfg, gateway_label=gateway_label, mode=mode)
    if not policy.trade_enabled:
        raise PreviewUnavailable(422, {"error": "trade_disabled"})
    if policy.simulator_only and mode == "live":
        raise PreviewUnavailable(422, {"error": "simulator_only"})
    if cap_status(notional, policy.max_notional_per_order) == "exceeded":
        raise PreviewUnavailable(422, {"error": "max_notional_exceeded"})


async def _consume_nonce(
    redis: RedisLike,
    nonce: str,
    *,
    account_id: object,
    qty: str,
    limit_price: str | None,
) -> None:
    nonce_key = f"nonce:order:{account_id}:{nonce}"
    consumed_nonce_value = await redis.execute_command("GETDEL", nonce_key)
    if consumed_nonce_value is None:
        raise PreviewUnavailable(422, {"error": "unknown_nonce"})
    consumed_nonce_payload = _decode_nonce_payload(consumed_nonce_value)
    if consumed_nonce_payload["payload_hash"] != _modify_nonce_payload_hash(
        account_id=account_id,
        qty=qty,
        limit_price=limit_price,
    ):
        raise PreviewUnavailable(422, {"error": "payload_mismatch"})


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


# 5c C3: bracket order placement - HIGH-2 two-phase commit.
async def place_bracket(
    db: AsyncSession,
    redis: RedisLike,
    config: ConfigService,
    registry: BrokerRegistry,
    *,
    request: OrderBracketRequest,
) -> dict[str, Any]:
    """POST /api/orders/bracket - HIGH-2 two-phase commit.

    Step 1: validation + INSERT parent only (status=pending_submit).
    Step 2: PlaceBracket RPC.
    Step 3: On success - INSERT 2 children + UPDATE parent.broker_order_id (one tx).
    """
    if request.stop_price is None and request.target_price is None:
        raise PreviewUnavailable(400, {"error": "bracket_invalid_legs"})
    entry = Decimal(request.limit_price)
    if request.side == "BUY":
        if request.stop_price and Decimal(request.stop_price) >= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})
        if request.target_price and Decimal(request.target_price) <= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})
    else:
        if request.stop_price and Decimal(request.stop_price) <= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})
        if request.target_price and Decimal(request.target_price) >= entry:
            raise PreviewUnavailable(400, {"error": "bracket_invalid_prices"})

    if await is_kill_switch_active(config):
        raise PreviewUnavailable(503, {"error": "kill_switch"})
    account = await _resolve_account(db, request.account_id)
    parent_qty = canonicalize_qty(request.qty)
    parent_notional = Decimal(parent_qty) * entry
    policy = await get_account_policy(
        config,
        gateway_label=account.gateway_label,
        mode=account.mode,
    )
    if cap_status(parent_notional, policy.max_notional_per_order) == "exceeded":
        raise PreviewUnavailable(422, {"error": "max_notional_exceeded"})
    await _consume_nonce(
        redis,
        request.nonce,
        account_id=request.account_id,
        qty=parent_qty,
        limit_price=request.limit_price,
    )

    parent_id = uuid7()
    oca_group = f"BRK-{parent_id.hex[:8]}"
    contract = await _resolve_contract(
        await registry.get_client(account.gateway_label), request.conid
    )
    symbol = _contract_description(contract)
    await db.execute(
        text(
            "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, side, "
            "order_type, tif, qty, limit_price, status, notional, parent_order_id, oca_group) "
            "VALUES (:id, :a, :coid, :conid, :symbol, :side, :ot, :tif, :qty, :lp, "
            "'pending_submit', :n, NULL, :oca)"
        ),
        {
            "id": parent_id,
            "a": request.account_id,
            "coid": request.client_order_id,
            "conid": request.conid,
            "symbol": symbol,
            "side": request.side,
            "ot": request.order_type,
            "tif": request.tif,
            "qty": parent_qty,
            "lp": request.limit_price,
            "n": parent_notional,
            "oca": oca_group,
        },
    )
    await db.commit()

    client = await registry.get_client(account.gateway_label)
    bracket_result = await client.place_bracket(
        parent_request_proto=_build_place_proto(
            request,
            request.side,
            request.order_type,
            str(request.client_order_id),
            parent_qty,
            limit_price=request.limit_price,
            stop_price=None,
            account_number=account.account_number,
            conid=request.conid,
        ),
        stop_loss_proto=(
            _build_place_proto(
                request,
                "SELL" if request.side == "BUY" else "BUY",
                "STOP",
                str(uuid7()),
                parent_qty,
                limit_price=None,
                stop_price=request.stop_price,
                account_number=account.account_number,
                conid=request.conid,
            )
            if request.stop_price
            else None
        ),
        take_profit_proto=(
            _build_place_proto(
                request,
                "SELL" if request.side == "BUY" else "BUY",
                "LIMIT",
                str(uuid7()),
                parent_qty,
                limit_price=request.target_price,
                stop_price=None,
                account_number=account.account_number,
                conid=request.conid,
            )
            if request.target_price
            else None
        ),
        oca_group=oca_group,
    )

    children: list[dict[str, Any]] = []
    async with db.begin():
        await db.execute(
            text("UPDATE orders SET broker_order_id = :bo, status = 'submitted' WHERE id = :id"),
            {"bo": bracket_result.parent_broker_order_id, "id": parent_id},
        )
        if request.stop_price and bracket_result.stop_loss_broker_order_id:
            sl_id = uuid7()
            await db.execute(
                text(
                    "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, "
                    "side, order_type, tif, qty, stop_price, status, notional, "
                    "broker_order_id, parent_order_id, oca_group) "
                    "VALUES (:id, :a, :coid, :conid, :symbol, :side, 'STOP', :tif, :qty, "
                    ":sp, 'submitted', :n, :bo, :pid, :oca)"
                ),
                {
                    "id": sl_id,
                    "a": request.account_id,
                    "coid": uuid7(),
                    "conid": request.conid,
                    "symbol": symbol,
                    "side": "SELL" if request.side == "BUY" else "BUY",
                    "tif": request.tif,
                    "qty": parent_qty,
                    "sp": request.stop_price,
                    "n": Decimal(parent_qty) * Decimal(request.stop_price),
                    "bo": bracket_result.stop_loss_broker_order_id,
                    "pid": parent_id,
                    "oca": oca_group,
                },
            )
            children.append(
                {
                    "id": str(sl_id),
                    "leg": "stop_loss",
                    "broker_order_id": bracket_result.stop_loss_broker_order_id,
                    "status": "submitted",
                }
            )
        if request.target_price and bracket_result.take_profit_broker_order_id:
            tp_id = uuid7()
            await db.execute(
                text(
                    "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, "
                    "side, order_type, tif, qty, limit_price, status, notional, "
                    "broker_order_id, parent_order_id, oca_group) "
                    "VALUES (:id, :a, :coid, :conid, :symbol, :side, 'LIMIT', :tif, "
                    ":qty, :tp, 'submitted', :n, :bo, :pid, :oca)"
                ),
                {
                    "id": tp_id,
                    "a": request.account_id,
                    "coid": uuid7(),
                    "conid": request.conid,
                    "symbol": symbol,
                    "side": "SELL" if request.side == "BUY" else "BUY",
                    "tif": request.tif,
                    "qty": parent_qty,
                    "tp": request.target_price,
                    "n": Decimal(parent_qty) * Decimal(request.target_price),
                    "bo": bracket_result.take_profit_broker_order_id,
                    "pid": parent_id,
                    "oca": oca_group,
                },
            )
            children.append(
                {
                    "id": str(tp_id),
                    "leg": "take_profit",
                    "broker_order_id": bracket_result.take_profit_broker_order_id,
                    "status": "submitted",
                }
            )

    return {
        "parent": {
            "id": str(parent_id),
            "client_order_id": str(request.client_order_id),
            "broker_order_id": bracket_result.parent_broker_order_id,
            "status": "submitted",
        },
        "children": children,
        "oca_group": oca_group,
    }


def _build_place_proto(
    request: OrderBracketRequest,
    side: str,
    order_type: str,
    client_order_id: str,
    qty: str,
    *,
    limit_price: str | None,
    stop_price: str | None,
    account_number: str,
    conid: str,
) -> Any:
    """Helper - builds a broker_pb2.PlaceOrderRequest for the bracket legs."""
    from app._generated.broker.v1 import broker_pb2

    return broker_pb2.PlaceOrderRequest(
        account_number=account_number,
        client_order_id=client_order_id,
        conid=conid,
        side=side,
        order_type=order_type,
        tif=request.tif,
        qty=qty,
        limit_price=limit_price or "",
        stop_price=stop_price or "",
    )


# 5c C4: fills history - cursor-paginated by (executed_at DESC, id DESC).
async def list_fills(
    db: AsyncSession,
    *,
    account_id: UUID,
    from_ts: datetime,
    to_ts: datetime,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """GET /api/fills - cursor-paginated by (executed_at DESC, id DESC).

    Cursor encodes (executed_at, id) as base64-JSON. JOIN orders for account
    scoping (fills don't carry account_id directly; orders does).
    """
    cursor_executed_at: datetime | None = None
    cursor_id: UUID | None = None
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            cursor_executed_at = datetime.fromisoformat(decoded["executed_at"])
            cursor_id = UUID(decoded["id"])
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise PreviewUnavailable(400, {"error": "invalid_cursor"}) from exc

    query = (
        "SELECT f.id, f.order_id, f.exec_id, f.qty, f.price, f.currency, f.executed_at, "
        "       f.commission, f.commission_currency "
        "  FROM fills f "
        "  JOIN orders o ON o.id = f.order_id "
        " WHERE o.account_id = :a "
        "   AND f.executed_at BETWEEN :f AND :t "
    )
    params: dict[str, Any] = {"a": account_id, "f": from_ts, "t": to_ts, "lim": limit + 1}
    if cursor_executed_at and cursor_id:
        query += " AND (f.executed_at, f.id) < (:cea, :cid) "
        params["cea"] = cursor_executed_at
        params["cid"] = cursor_id
    query += " ORDER BY f.executed_at DESC, f.id DESC LIMIT :lim"

    result = await db.execute(text(query), params)
    rows = list(result.mappings())

    next_cursor: str | None = None
    if len(rows) > limit:
        last_kept = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "executed_at": last_kept["executed_at"].isoformat(),
                    "id": str(last_kept["id"]),
                }
            ).encode()
        ).decode()
        rows = rows[:limit]

    return {
        "fills": [
            {
                "id": str(r["id"]),
                "order_id": str(r["order_id"]),
                "exec_id": r["exec_id"],
                "qty": str(r["qty"]),
                "price": str(r["price"]),
                "currency": r["currency"].strip(),
                "executed_at": r["executed_at"].isoformat(),
                "commission": str(r["commission"]) if r["commission"] is not None else None,
                "commission_currency": r["commission_currency"].strip()
                if r["commission_currency"]
                else None,
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }
