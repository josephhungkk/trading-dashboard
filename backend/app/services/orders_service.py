"""Order preview business logic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import base
from app.schemas.orders import (
    ContractSummary,
    PositionSanityResult,
    PreviewRequest,
    PreviewResponse,
    _format_decimal_8,
)
from app.services.brokers import BrokerRegistry
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


@dataclass(frozen=True)
class PreviewUnavailable(Exception):  # noqa: N818  # signals an HTTP-503-style preview rejection, not a generic Error subclass
    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class _Account:
    gateway_label: str
    mode: str
    currency_base: str


def canonicalize_qty(qty: str) -> str:
    return format(Decimal(qty).quantize(Decimal("1e-8")), "f")


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
    await redis.set(nonce_key, payload_hash, ex=30, nx=True)

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
            SELECT gateway_label, mode, currency_base
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
    )


class _ContractSearchClient(Protocol):
    """Minimal protocol for _resolve_contract — accepts any object with the
    expected search_contracts signature (BrokerSidecarClient + test mocks)."""

    async def search_contracts(self, *, query: str) -> list[base.Contract]: ...


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


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _retry_after(now: datetime, maintenance: BrokerMaintenance) -> int:
    if maintenance.until is None:
        return 30
    return max(1, int((maintenance.until - now).total_seconds()))
