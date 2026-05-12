"""Order preview business logic."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

if TYPE_CHECKING:
    from app.schemas.risk import GateVerdict
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import base
from app.core import metrics
from app.core.db import SessionLocal
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
from app.services.order_capability_service import KNOWN_BROKERS, OrderCapabilityService
from app.services.orders_policy import get_account_policy, is_kill_switch_active
from app.services.risk_inflight_counters import (
    commit_bp,
    commit_bp_finalize,
    commit_pdt,
    decrement_pdt,
    revert_bp,
    revert_pdt,
)

_MODIFY_REPLAY_TTL_SECONDS = 300

log = structlog.get_logger(__name__)


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


TERMINAL_STATUSES = ("filled", "cancelled", "rejected", "expired", "inactive")


def canonicalize_qty(qty: str | None) -> str:
    # T-0.7 widened PreviewRequest.qty to `str | None` (XOR with cash_amount).
    # cash_amount routing comes in chunk C; until then, reject the new path here.
    if qty is None:
        raise NotImplementedError("cash_amount path not yet wired (Phase 8c chunk C)")
    return format(Decimal(qty).quantize(Decimal("1e-8")), "f")


_canonicalize_qty = canonicalize_qty


def cap_status(filled: Decimal, cap: Decimal) -> Literal["ok", "near", "exceeded"]:
    if filled > cap:
        return "exceeded"
    if cap > 0 and filled / cap >= Decimal("0.8"):
        return "near"
    return "ok"


async def validate_pre_dispatch(
    *,
    cfg: ConfigService,
    capability: OrderCapabilityService,
    broker_label: str,
    asset_class: str,
    order_type: str,
    tif: str,
    skip_operational_checks: bool = False,
) -> None:
    """Validate order controls that must run before sidecar dispatch."""
    broker_id = capability_broker_id(broker_label)
    if not skip_operational_checks:
        if await is_kill_switch_active(cfg):
            raise PreviewUnavailable(
                503,
                {"error": {"code": "broker_kill_switch_enabled", "broker": broker_id}},
            )

        now = _utcnow()
        maintenance = compute_broker_maintenance(now)
        if maintenance.active:
            raise PreviewUnavailable(
                503,
                {"error": {"code": "broker_maintenance", "broker": broker_id}},
                {"Retry-After": str(_retry_after(now, maintenance))},
            )

    if await capability.is_supported(broker_id, asset_class, order_type, tif):
        return

    notes = await capability.get_notes(broker_id, asset_class, order_type, tif)
    raise PreviewUnavailable(
        422,
        {
            "error": {
                "code": "unsupported_order_type_for_broker",
                "broker_id": broker_id,
                "order_type": order_type,
                "tif": tif,
                "notes": notes,
            }
        },
    )


async def _check_kill_switch(cfg: ConfigService) -> None:
    if await is_kill_switch_active(cfg):
        raise PreviewUnavailable(503, {"error": "kill_switch_active"})


def capability_broker_id(broker_label: str) -> str:
    if broker_label in KNOWN_BROKERS:
        return broker_label
    prefix = broker_label.split("-", 1)[0]
    if prefix in KNOWN_BROKERS:
        return prefix
    # Legacy IBKR labels are account-scope labels (e.g. isa-paper), not broker ids.
    return "ibkr"


async def preview_order(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    registry: BrokerRegistry,
    capability: OrderCapabilityService,
    request_data: dict[str, Any],
    user_key: str,
    quote_engine: object | None = None,
) -> PreviewResponse:
    await _check_kill_switch(cfg)

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
    canonical_qty = canonicalize_qty(request.qty)
    request = request.model_copy(update={"qty": canonical_qty})
    await _check_rate_limit(redis, user_key)

    account = await resolve_account(db, request.account_id)
    await validate_pre_dispatch(
        cfg=cfg,
        capability=capability,
        broker_label=account.gateway_label,
        asset_class="STOCK",
        order_type=request.order_type,
        tif=request.tif,
        skip_operational_checks=True,
    )
    client = await registry.get_client(account.gateway_label)
    contract = await _resolve_contract(client, request.conid)
    qty = Decimal(canonical_qty)
    notional_native = await _native_notional(
        redis, request, contract, qty, quote_engine=quote_engine
    )
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

    # Phase 10a D3: insert risk gate at station 4 (after capability/maintenance
    # checks; before response shaping). instrument_id is None at this surface
    # because PreviewRequest carries conid not instrument_id; concentration
    # check (which is the only one that consults instrument_id) skips on None
    # per its own contract. Wiring conid -> instrument_id is deferred to
    # Phase 10a.5.1 C2.1: isinstance(db, AsyncSession) guard removed. Tests
    # that use stub Sessions monkeypatch _resolve_instrument_id +
    # _evaluate_risk_for_preview (see backend/tests/api/test_orders_preview.py
    # fixture). Production always passes a real AsyncSession.
    # Phase 10a.5 B2: resolve instrument_id once for both the gate ctx
    # and audit row. preview path passes client=None — must NOT author
    # instruments at evaluation time.
    instrument_id = await _resolve_instrument_id(
        db,
        broker_id=capability_broker_id(account.gateway_label),
        conid=request.conid,
        client=None,
    )
    risk_verdict = await _evaluate_risk_for_preview(
        cfg=cfg,
        db=db,
        redis=redis,
        client=client,
        request=request,
        account=account,
        qty=qty,
        instrument_id=instrument_id,
        symbol=contract.symbol,
        # AssetClass is a Literal[str] at runtime, not an Enum — pass it
        # through directly.
        asset_class=str(contract.asset_class),
    )
    risk_warnings = [w.model_dump(mode="json") for w in risk_verdict.warnings]
    risk_blockers = [b.model_dump(mode="json") for b in risk_verdict.blockers]

    # Phase 10a.5.1: preview WARN+BLOCK audit (no ALLOW — HIGH-4 volume
    # control). Mirrors _audit_risk_decision's session-isolation + fail-OPEN
    # pattern. Generated via Qwen3-Coder-Next with sibling-mimic prompt.
    if risk_verdict.final_verdict != "allow":
        try:
            from app.models.risk import RiskDecision

            async with SessionLocal() as audit_db:
                decision = RiskDecision(
                    account_id=request.account_id,
                    instrument_id=instrument_id,
                    side=str(request.side).lower(),
                    qty=qty,
                    price=Decimal(request.limit_price) if request.limit_price else None,
                    order_type=str(request.order_type),
                    time_in_force=str(request.tif),
                    verdict=risk_verdict.final_verdict,
                    blockers=risk_blockers,
                    warnings=risk_warnings,
                    latency_ms=risk_verdict.latency_ms,
                    attempt_kind="preview",
                    request_id=str(uuid4()),
                    order_id=None,
                )
                audit_db.add(decision)
                await audit_db.commit()
        except Exception as exc:
            log.exception(
                "risk.audit_insert_failed",
                account_id=str(request.account_id),
                attempt_kind="preview",
                verdict=risk_verdict.final_verdict,
                error=str(exc),
            )
            with contextlib.suppress(Exception):
                metrics.risk_audit_insert_failures_total.labels(attempt_kind="preview").inc()

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
            # Pass through the raw conid — IBKR is numeric, Futu is dotted (HK.00700).
            conid=contract.conid,
            description=_contract_description(contract),
        ),
        warnings=[],
        risk_warnings=risk_warnings,
        risk_blockers=risk_blockers,
    )


async def _evaluate_risk_for_preview(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    client: object,
    request: PreviewRequest,
    account: _Account,
    qty: Decimal,
    instrument_id: int | None,
    symbol: str | None = None,
    asset_class: str | None = None,
) -> GateVerdict:
    """Phase 10a D3: build EvaluationContext + run RiskService gate (preview mode).

    Phase 10a.5 B2: instrument_id resolved via _resolve_instrument_id at the
    call site (gate must NOT author instruments — client=None on preview).

    Phase 11a CI-debt: ``symbol`` + ``asset_class`` plumbed through so
    ``_check_margin`` can call ``BrokerSidecarClient.preview_order`` with
    the actual signature. Defaults to None for legacy callers (the margin
    check then takes the early-WARN branch).
    """
    from app.services.risk_service import EvaluationContext, RiskService

    svc = RiskService(
        db=db,
        redis=cast(Any, redis),
        config=cast(Any, cfg),
        sidecar=cast(Any, client),
    )
    ctx = EvaluationContext(
        account_id=request.account_id,
        broker_id=capability_broker_id(account.gateway_label),
        instrument_id=instrument_id,
        side=cast(Any, request.side),
        qty=qty,
        price=Decimal(request.limit_price) if request.limit_price else None,
        order_type=str(request.order_type),
        time_in_force=str(request.tif),
        request_id=str(uuid4()),
        currency_base=account.currency_base,
        symbol=symbol,
        asset_class=asset_class,
    )
    verdict = await svc.evaluate(ctx, mode="preview")
    log.info(
        "risk.evaluated",
        verdict=verdict.final_verdict,
        kind="preview",
        account_id=str(request.account_id),
        lat_ms=verdict.latency_ms,
        blockers=len(verdict.blockers),
        warnings=len(verdict.warnings),
    )
    return verdict


async def _evaluate_risk_for_place_order(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    client: object,
    request: PlaceOrderRequest,
    account: _Account,
    qty: Decimal,
    request_id: str,
    instrument_id: int | None,
    symbol: str | None = None,
    asset_class: str | None = None,
) -> GateVerdict:
    """Phase 10a D4: gate evaluation for place_order path.

    Same shape as preview evaluator but mode="place_order" so _check_margin
    fails CLOSED on sidecar timeout / UNIMPLEMENTED (per spec §4 H4).

    Phase 10a.5 B2: instrument_id resolved at the call site so it can be
    threaded into the audit row too (single resolve per order).

    Phase 11a CI-debt: ``symbol`` + ``asset_class`` plumbed through so
    ``_check_margin`` can call the sidecar with the correct kwargs.
    """
    from app.services.risk_service import EvaluationContext, RiskService

    svc = RiskService(
        db=db,
        redis=cast(Any, redis),
        config=cast(Any, cfg),
        sidecar=cast(Any, client),
    )
    ctx = EvaluationContext(
        account_id=request.account_id,
        broker_id=capability_broker_id(account.gateway_label),
        instrument_id=instrument_id,
        side=cast(Any, request.side),
        qty=qty,
        price=Decimal(request.limit_price) if request.limit_price else None,
        order_type=str(request.order_type),
        time_in_force=str(request.tif),
        request_id=request_id,
        currency_base=account.currency_base,
        symbol=symbol,
        asset_class=asset_class,
    )
    verdict = await svc.evaluate(ctx, mode="place_order")
    log.info(
        "risk.evaluated",
        verdict=verdict.final_verdict,
        kind="place_order",
        account_id=str(request.account_id),
        lat_ms=verdict.latency_ms,
        blockers=len(verdict.blockers),
        warnings=len(verdict.warnings),
    )
    return verdict


async def _audit_risk_decision(
    *,
    db: AsyncSession,
    account_id: UUID,
    request: PlaceOrderRequest,
    qty: Decimal,
    verdict: GateVerdict,
    request_id: str,
    attempt_kind: str,
    order_id: UUID | None,
    instrument_id: int | None = None,
) -> None:
    """Phase 10a D4: write a risk_decisions audit row.

    Implementation status (Phase 10a Chunk D): only BLOCK verdicts reach
    this helper today (call sites in place_order/modify_order gate on
    `if risk_verdict.final_verdict == "block"`). The spec §6 contract
    requires every gate-reached attempt to leave a row regardless of
    verdict; audit-on-ALLOW/WARN is deferred to Phase 10a.5 — see
    `phase10a_in_progress.md` deviations block.

    Fail-OPEN policy (spec §4): suppress exceptions on the audit write
    so the trade isn't blocked by an audit DB hiccup; the
    `risk_audit_insert_failures_total` metric covers visibility.

    Session isolation (D9-fix): opens a dedicated SessionLocal() session
    so the audit commit doesn't promote/discard pending state on the
    caller's AsyncSession. Today the BLOCK call sites have no
    uncommitted writes before the audit row, but the pattern protects
    against future caller refactors silently corrupting the order's
    transaction.
    """
    # `db` is preserved in the signature for legacy stub-Session callers
    # but unused — we open a dedicated SessionLocal() to avoid mutating
    # the caller's transaction.
    _ = db
    try:
        from app.models.risk import RiskDecision

        async with SessionLocal() as audit_db:
            decision = RiskDecision(
                account_id=account_id,
                # Phase 10a.5 B2: resolved via _resolve_instrument_id at the
                # gate evaluator. None still possible when alias is uncreated
                # and broker get_contract miss in cold path.
                instrument_id=instrument_id,
                # D7: side is lowercased to satisfy the
                # risk_decisions_side_check CHECK constraint (alembic 0036
                # enforces ('buy', 'sell')). PlaceOrderRequest.side is the
                # uppercase OrderSide literal ('BUY'|'SELL').
                side=str(request.side).lower(),
                qty=qty,
                price=Decimal(request.limit_price) if request.limit_price else None,
                order_type=str(request.order_type),
                time_in_force=str(request.tif),
                verdict=verdict.final_verdict,
                blockers=[b.model_dump(mode="json") for b in verdict.blockers],
                warnings=[w.model_dump(mode="json") for w in verdict.warnings],
                latency_ms=verdict.latency_ms,
                attempt_kind=attempt_kind,
                request_id=request_id,
                order_id=order_id,
            )
            audit_db.add(decision)
            await audit_db.commit()
    except Exception as exc:
        log.exception(
            "risk.audit_insert_failed",
            account_id=str(account_id),
            attempt_kind=attempt_kind,
            verdict=verdict.final_verdict,
            error=str(exc),
        )
        with contextlib.suppress(Exception):
            metrics.risk_audit_insert_failures_total.labels(attempt_kind=attempt_kind).inc()


async def _asset_class_for_instrument(
    db: AsyncSession,
    instrument_id: int | None,
) -> str | None:
    """Phase 11a reviewer fix: look up instruments.asset_class for the modify
    path's risk-gate margin check. ``orders`` table doesn't carry asset_class,
    so the canonical source is the resolved instrument row. Returns ``None``
    when instrument_id is unresolved or the row is missing — caller treats as
    "asset_class unavailable" and the margin check falls into the documented
    WARN/BLOCK skip branch in ``risk_service._check_margin``.
    """
    if instrument_id is None:
        return None
    result = await db.execute(
        text("SELECT asset_class::text AS ac FROM instruments WHERE id = :id"),
        {"id": instrument_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return str(row["ac"])


async def _resolve_instrument_id(
    db: AsyncSession,
    *,
    broker_id: str,
    conid: str,
    client: object | None = None,
) -> int | None:
    """Phase 10a.5 B1: conid → instruments.id via read-only alias lookup.

    Cold-path eager-create via ``resolve_or_create`` when ``client`` is given
    and ``find_by_alias`` misses. ``None`` return tells the concentration
    check to skip (with metric increment) rather than block on unresolved.

    The risk gate calls this with ``client=None`` (gate must NOT author
    instruments); the place_order write path passes the broker client so a
    cold alias is created at the same time the order is sent.
    """
    from app.services.quotes.instrument_resolver import InstrumentResolver

    resolver = InstrumentResolver(db)
    instrument_id = await resolver.find_by_alias(source=broker_id, raw_symbol=conid)
    if instrument_id is not None:
        return instrument_id

    if client is None:
        # Preview path: gate must not author instruments.
        metrics.risk_gate_concentration_skipped_unresolved_total.labels(
            reason="alias_miss_preview"
        ).inc()
        return None

    try:
        contract = await client.get_contract(conid=conid)  # type: ignore[attr-defined]
    except Exception:
        # grpc.RpcError, asyncio.TimeoutError, sidecar protocol errors, etc.
        # — fail-open: gate skips concentration, broker still validates.
        metrics.risk_gate_concentration_skipped_unresolved_total.labels(
            reason="contract_fetch_failed"
        ).inc()
        return None

    if contract is None:
        metrics.risk_gate_concentration_skipped_unresolved_total.labels(
            reason="contract_not_found"
        ).inc()
        return None

    result = await resolver.resolve_or_create(
        source=broker_id,
        raw_symbol=conid,
        canonical_id=contract.canonical_id,
        asset_class=contract.asset_class,
        primary_exchange=contract.primary_exchange,
        currency=contract.currency,
    )
    return int(result.id)


async def _audit_risk_decision_with_dedupe(
    *,
    db: AsyncSession,
    redis: RedisLike,
    account_id: UUID,
    request: PlaceOrderRequest,
    qty: Decimal,
    verdict: GateVerdict,
    request_id: str,
    attempt_kind: str,
    order_id: UUID | None,
    instrument_id: int | None = None,
) -> None:
    """Phase 10a.5 A5.1: audit emission with ALLOW-tier 30s SETNX dedupe.

    Volume control (HIGH-4): a chatty client previewing+placing dozens of
    identical orders shouldn't flood risk_decisions with redundant ALLOW
    rows. WARN + BLOCK always emit (operator visibility outweighs volume).
    Dedupe key: (account, conid, side, qty_int) — qty is normalised to
    int because exact-Decimal repeats are the practical case.
    """
    if verdict.final_verdict == "allow":
        dedupe_key = (
            f"risk_audit_dedupe:{account_id}:{request.conid}:{str(request.side).lower()}:{int(qty)}"
        )
        try:
            was_set = await redis.set(dedupe_key, "1", ex=30, nx=True)
        except (Exception,) as exc:  # noqa: B013
            log.warning("risk_audit_dedupe_redis_failed", err=str(exc))
            was_set = True  # fail-open: emit when dedupe lookup fails
        if not was_set:
            with contextlib.suppress(Exception):
                metrics.risk_audit_dedupe_skipped_total.labels(attempt_kind=attempt_kind).inc()
            return
    await _audit_risk_decision(
        db=db,
        account_id=account_id,
        request=request,
        qty=qty,
        verdict=verdict,
        request_id=request_id,
        attempt_kind=attempt_kind,
        order_id=order_id,
        instrument_id=instrument_id,
    )


async def _audit_risk_decision_modify_with_dedupe(
    *,
    db: AsyncSession,
    redis: RedisLike,
    account_id: UUID,
    side: str,
    qty: Decimal,
    limit_price: str | None,
    order_type: str,
    tif: str,
    verdict: GateVerdict,
    request_id: str,
    order_id: UUID,
    conid: str,
    attempt_kind: str,
    instrument_id: int | None = None,
) -> None:
    """Phase 10a.5 A5.1: modify-path mirror of the place-path dedupe helper."""
    if verdict.final_verdict == "allow":
        dedupe_key = f"risk_audit_dedupe:{account_id}:{conid}:{str(side).lower()}:{int(qty)}:modify"
        try:
            was_set = await redis.set(dedupe_key, "1", ex=30, nx=True)
        except (Exception,) as exc:  # noqa: B013
            log.warning("risk_audit_dedupe_redis_failed", err=str(exc))
            was_set = True
        if not was_set:
            with contextlib.suppress(Exception):
                metrics.risk_audit_dedupe_skipped_total.labels(attempt_kind=attempt_kind).inc()
            return
    await _audit_risk_decision_modify(
        db=db,
        account_id=account_id,
        side=side,
        qty=qty,
        limit_price=limit_price,
        order_type=order_type,
        tif=tif,
        verdict=verdict,
        request_id=request_id,
        order_id=order_id,
        instrument_id=instrument_id,
    )


async def _evaluate_risk_for_modify_order(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    client: object,
    account_id: UUID,
    account: _Account,
    side: str,
    qty: Decimal,
    limit_price: str | None,
    order_type: str,
    tif: str,
    request_id: str,
    instrument_id: int | None,
    symbol: str | None = None,
    asset_class: str | None = None,
) -> GateVerdict:
    """Phase 10a D5: gate evaluation for modify_order path.

    Mirrors _evaluate_risk_for_place_order but accepts the immutable fields
    (account_id, side) from the orders row instead of a PlaceOrderRequest,
    since OrderModifyRequest doesn't carry them. mode="place_order" so the
    margin check still fails CLOSED on sidecar timeout (spec §4 H4).

    Phase 10a.5 B2: instrument_id resolved at the call site so it threads
    into the audit row too.
    """
    from app.services.risk_service import EvaluationContext, RiskService

    svc = RiskService(
        db=db,
        redis=cast(Any, redis),
        config=cast(Any, cfg),
        sidecar=cast(Any, client),
    )
    ctx = EvaluationContext(
        account_id=account_id,
        broker_id=capability_broker_id(account.gateway_label),
        instrument_id=instrument_id,
        side=cast(Any, side),
        qty=qty,
        price=Decimal(limit_price) if limit_price else None,
        order_type=order_type,
        time_in_force=tif,
        request_id=request_id,
        currency_base=account.currency_base,
        symbol=symbol,
        asset_class=asset_class,
    )
    verdict = await svc.evaluate(ctx, mode="place_order")
    log.info(
        "risk.evaluated",
        verdict=verdict.final_verdict,
        kind="modify_order",
        account_id=str(account_id),
        lat_ms=verdict.latency_ms,
        blockers=len(verdict.blockers),
        warnings=len(verdict.warnings),
    )
    return verdict


async def _audit_risk_decision_modify(
    *,
    db: AsyncSession,
    account_id: UUID,
    side: str,
    qty: Decimal,
    limit_price: str | None,
    order_type: str,
    tif: str,
    verdict: GateVerdict,
    request_id: str,
    order_id: UUID,
    instrument_id: int | None = None,
) -> None:
    """Phase 10a D5: write a risk_decisions audit row for modify_order.

    Same fail-OPEN + isolated-session semantics as `_audit_risk_decision`
    (spec §4); see that helper's docstring. Only BLOCK reaches this code
    today; audit-on-ALLOW/WARN deferred to Phase 10a.5.
    """
    # `db` is preserved in the signature for legacy stub-Session callers
    # but unused — we open a dedicated SessionLocal() to avoid mutating
    # the caller's transaction.
    _ = db
    try:
        from app.models.risk import RiskDecision

        async with SessionLocal() as audit_db:
            decision = RiskDecision(
                account_id=account_id,
                # Phase 10a.5 B2: resolved via _resolve_instrument_id at the
                # modify-order gate evaluator.
                instrument_id=instrument_id,
                # D7: lowercase side to satisfy risk_decisions_side_check.
                side=side.lower(),
                qty=qty,
                price=Decimal(limit_price) if limit_price else None,
                order_type=order_type,
                time_in_force=tif,
                verdict=verdict.final_verdict,
                blockers=[b.model_dump(mode="json") for b in verdict.blockers],
                warnings=[w.model_dump(mode="json") for w in verdict.warnings],
                latency_ms=verdict.latency_ms,
                attempt_kind="modify_order",
                request_id=request_id,
                order_id=order_id,
            )
            audit_db.add(decision)
            await audit_db.commit()
    except Exception as exc:
        log.exception(
            "risk.audit_insert_failed",
            account_id=str(account_id),
            attempt_kind="modify_order",
            verdict=verdict.final_verdict,
            error=str(exc),
        )
        with contextlib.suppress(Exception):
            metrics.risk_audit_insert_failures_total.labels(attempt_kind="modify_order").inc()


async def place_order(
    *,
    cfg: ConfigService,
    db: AsyncSession,
    redis: RedisLike,
    registry: BrokerRegistry,
    capability: OrderCapabilityService,
    request_data: dict[str, Any],
) -> OrderResponse:
    await _check_kill_switch(cfg)

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
    canonical_qty = canonicalize_qty(request.qty)
    request = request.model_copy(update={"qty": canonical_qty})
    account = await resolve_account(db, request.account_id)
    await validate_pre_dispatch(
        cfg=cfg,
        capability=capability,
        broker_label=account.gateway_label,
        asset_class="STOCK",
        order_type=request.order_type,
        tif=request.tif,
        skip_operational_checks=True,
    )
    client = await registry.get_client(account.gateway_label)
    contract = await _resolve_contract(client, request.conid)
    qty = Decimal(canonical_qty)
    notional_native = await _native_notional(redis, request, contract, qty)
    fx_rate = await _fx_rate(redis, contract.currency, account.currency_base)
    notional = (notional_native * fx_rate).quantize(Decimal("1e-8"))

    # Phase 10a D4: risk gate at station 4 (after capability, before notional /
    # nonce / dispatch). Asymmetric per spec: gate BLOCK -> 422 with structured
    # blockers payload; ALLOW/WARN -> proceed (warnings live on the
    # RiskDecision audit row but don't surface in the response shape today).
    # Phase 10a.5.1 C2.1: isinstance(db, AsyncSession) guard removed.
    # Stub-Session tests monkeypatch _resolve_instrument_id +
    # _evaluate_risk_for_place_order + _audit_risk_decision_with_dedupe.
    risk_request_id = str(uuid4())
    # Phase 10a.5 B2: resolve instrument_id once. place_order is the
    # write path — pass the broker client so a cold alias is created
    # at the same time the order is sent (eager-create).
    instrument_id = await _resolve_instrument_id(
        db,
        broker_id=capability_broker_id(account.gateway_label),
        conid=request.conid,
        client=client,
    )
    risk_verdict = await _evaluate_risk_for_place_order(
        cfg=cfg,
        db=db,
        redis=redis,
        client=client,
        request=request,
        account=account,
        qty=qty,
        request_id=risk_request_id,
        instrument_id=instrument_id,
        symbol=contract.symbol,
        # AssetClass is a Literal[str] at runtime, not an Enum.
        asset_class=str(contract.asset_class),
    )
    # Phase 10a.5 A5.1: audit on every verdict (ALLOW + WARN + BLOCK)
    # for place_order/modify_order. preview_order does NOT audit ALLOW
    # (HIGH-4 volume control). ALLOW emissions are deduped via 30s
    # SETNX keyed by (account, conid, side, qty) to bound volume.
    await _audit_risk_decision_with_dedupe(
        db=db,
        redis=redis,
        account_id=request.account_id,
        request=request,
        qty=qty,
        verdict=risk_verdict,
        request_id=risk_request_id,
        attempt_kind="place_order",
        order_id=None,
        instrument_id=instrument_id,
    )
    if risk_verdict.final_verdict == "block":
        raise PreviewUnavailable(
            422,
            {
                "error": "risk_gate_blocked",
                "blockers": [b.model_dump(mode="json") for b in risk_verdict.blockers],
            },
        )

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
    await db.commit()

    # Phase 10a.5 A4.4: token-bearing risk-counter mutation around dispatch.
    # Decrement PDT + commit BP before SubmitOrder so the gate sees the
    # in-flight value on a concurrent submission; revert both on broker
    # exception, commit-finalize on broker ACK. Phase 10a.5.1 C2.1:
    # isinstance(db, AsyncSession) guard removed — fakeredis handles
    # SET/DECR/EVAL fine in stub-Session tests.
    pdt_token: str | None = None
    bp_token: str | None = None
    try:
        _, pdt_token = await decrement_pdt(redis, request.account_id)
    except (Exception,) as _exc:  # noqa: B013
        log.warning("risk_counter_decrement_pdt_failed", err=str(_exc))
        metrics.risk_counter_cleanup_failures_total.inc()
    try:
        _, bp_token = await commit_bp(redis, request.account_id, notional)
    except (Exception,) as _exc:  # noqa: B013
        log.warning("risk_counter_commit_bp_failed", err=str(_exc))
        metrics.risk_counter_cleanup_failures_total.inc()

    async def _revert_counters() -> None:
        if pdt_token is not None:
            try:
                await revert_pdt(redis, request.account_id, pdt_token)
            except (Exception,) as exc:  # noqa: B013
                log.warning("risk_counter_revert_pdt_failed", err=str(exc))
                metrics.risk_counter_cleanup_failures_total.inc()
        if bp_token is not None:
            try:
                await revert_bp(redis, request.account_id, bp_token)
            except (Exception,) as exc:  # noqa: B013
                log.warning("risk_counter_revert_bp_failed", err=str(exc))
                metrics.risk_counter_cleanup_failures_total.inc()

    async def _finalize_counters() -> None:
        if pdt_token is not None:
            try:
                await commit_pdt(redis, request.account_id, pdt_token)
            except (Exception,) as exc:  # noqa: B013
                log.warning("risk_counter_commit_pdt_failed", err=str(exc))
                metrics.risk_counter_cleanup_failures_total.inc()
        if bp_token is not None:
            try:
                await commit_bp_finalize(redis, request.account_id, bp_token)
            except (Exception,) as exc:  # noqa: B013
                log.warning("risk_counter_finalize_bp_failed", err=str(exc))
                metrics.risk_counter_cleanup_failures_total.inc()

    order_client = as_order_sidecar_client(client)
    try:
        sidecar_result = await order_client.place_order(
            account.account_number,
            str(request.client_order_id),
            request.conid,
            request.side,
            request.order_type,
            request.tif,
            canonical_qty,
            request.limit_price or "",
            request.stop_price or "",
            request.trail_offset or "",
            request.trail_offset_type or "",
            request.trail_limit_offset or "",
            request.expiry_date or "",
        )
    except (BrokerSidecarTimeout, BrokerSidecarUnavailable) as _exc:
        # Phase 9.7 G2: timeout class — broker reachability failed.
        # contextlib.suppress so an internal Prometheus error doesn't shadow
        # the original broker exception (silent-failure-hunter HIGH-3).
        # Phase 10a.5: counters STAY decremented on timeout — broker may
        # still have accepted the order. Reconcile resolves the state.
        with contextlib.suppress(Exception):
            metrics.broker_order_place_total.labels(
                label=account.gateway_label, result="timeout"
            ).inc()
        return _order_response_from_mapping(row, submission_state="pending_unknown")
    except Exception:
        # Phase 9.7 G2: error class — broker rejected or transport blew up.
        # Phase 10a.5 A4.4: explicit broker REJECT — release counters.
        with contextlib.suppress(Exception):
            metrics.broker_order_place_total.labels(
                label=account.gateway_label, result="error"
            ).inc()
        await _revert_counters()
        await db.execute(
            text(
                """
                UPDATE orders
                   SET status = 'rejected',
                       updated_at = now()
                 WHERE id = :id;
                """
            ),
            {"id": row["id"]},
        )
        await db.commit()
        raise

    submitted = await _mark_order_submitted(
        db,
        order_id=row["id"],
        broker_order_id=sidecar_result.broker_order_id,
    )
    await db.commit()
    await _finalize_counters()
    # Phase 9.7 G2: success class — emit AFTER _mark_order_submitted commits.
    metrics.broker_order_place_total.labels(label=account.gateway_label, result="success").inc()

    if await is_kill_switch_active(cfg):
        try:
            await order_client.cancel_order(account.account_number, sidecar_result.broker_order_id)
        except (BrokerSidecarTimeout, BrokerSidecarUnavailable) as _exc:
            pass

    return _order_response_from_mapping(submitted or row, submission_state="submitted")


async def modify_order(
    db: AsyncSession,
    redis: RedisLike,
    config: ConfigService,
    registry: BrokerRegistry,
    capability: OrderCapabilityService,
    *,
    order_id: UUID,
    request: OrderModifyRequest,
) -> dict[str, Any]:
    cached = await _modify_replay_lookup(redis, order_id, request.nonce)
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
                   client_order_id,
                   notional
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
                   AND status NOT IN ('filled', 'cancelled', 'rejected', 'expired', 'inactive')
                 LIMIT 1;
                """
            ),
            {"p": order_id},
        )
        if child_result.scalar_one_or_none() is not None:
            raise PreviewUnavailable(409, {"error": "bracket_parent_partial"})

    if await config.get_bool("broker", "kill_switch_enabled", default=False):
        raise PreviewUnavailable(503, {"error": "kill_switch"})

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

    account = await resolve_account(db, row["account_id"])
    await validate_pre_dispatch(
        cfg=config,
        capability=capability,
        broker_label=account.gateway_label,
        asset_class="STOCK",
        order_type=str(row["order_type"]),
        tif=request.tif,
        skip_operational_checks=True,
    )
    qty_text = canonicalize_qty(request.qty)
    existing_limit_price = str(row["limit_price"]) if row["limit_price"] is not None else None
    new_limit_price = request.limit_price or existing_limit_price
    new_notional = Decimal(qty_text) * Decimal(new_limit_price or "0")

    # Phase 10a D5: hoist broker client fetch above the risk gate so the
    # margin-preview sidecar call inside RiskService reuses the same client
    # the post-DB-update modify dispatch will use. Single fetch, no
    # double round-trip vs the prior 5c layout where client was lazily
    # acquired right before the sidecar modify_order call.
    client = await registry.get_client(account.gateway_label)

    # Phase 10a D5: risk gate at station 4 for modify_order. Mirrors D4 in
    # place_order: BLOCK -> 422 + audit row; ALLOW/WARN -> proceed. Counter
    # decrement on increase-of-notional deferred (consistent with D4
    # deferral). Phase 10a.5.1 C2.1: isinstance(db, AsyncSession) guard
    # removed; stub-Session tests monkeypatch the three risk-gate helpers.
    risk_request_id = str(uuid4())
    # Phase 10a.5 B2: resolve instrument_id once. modify is the write
    # path — pass the broker client for eager-create on cold alias miss.
    instrument_id = await _resolve_instrument_id(
        db,
        broker_id=capability_broker_id(account.gateway_label),
        conid=str(row["conid"]),
        client=client,
    )
    # Phase 11a reviewer fix: pull asset_class from instruments table rather
    # than hard-coding "STOCK". The pre-existing capability check upstream
    # only passes through equities today, but margin-RPC accuracy demands
    # the correct asset_class for any future order types reaching this path
    # (CFD / options / futures). None falls into the documented skip branch
    # in _check_margin which now correctly BLOCKs on place_order/modify.
    instrument_asset_class = await _asset_class_for_instrument(db, instrument_id)
    risk_verdict = await _evaluate_risk_for_modify_order(
        cfg=config,
        db=db,
        redis=redis,
        client=client,
        account_id=row["account_id"],
        account=account,
        side=str(row["side"]),
        qty=Decimal(qty_text),
        limit_price=new_limit_price,
        order_type=str(row["order_type"]),
        tif=request.tif,
        request_id=risk_request_id,
        instrument_id=instrument_id,
        symbol=str(row["symbol"]),
        asset_class=instrument_asset_class,
    )
    # Phase 10a.5 A5.1: audit on every verdict (ALLOW + WARN + BLOCK)
    # for modify_order. ALLOW path is deduped via 30s SETNX.
    await _audit_risk_decision_modify_with_dedupe(
        db=db,
        redis=redis,
        account_id=row["account_id"],
        side=str(row["side"]),
        qty=Decimal(qty_text),
        limit_price=new_limit_price,
        order_type=str(row["order_type"]),
        tif=request.tif,
        verdict=risk_verdict,
        request_id=risk_request_id,
        order_id=order_id,
        conid=str(row["conid"]),
        attempt_kind="modify_order",
        instrument_id=instrument_id,
    )
    if risk_verdict.final_verdict == "block":
        raise PreviewUnavailable(
            422,
            {
                "error": "risk_gate_blocked",
                "blockers": [b.model_dump(mode="json") for b in risk_verdict.blockers],
            },
        )

    # Phase 10a.5 A4.4 (modify path): NO counter mutation here. The original
    # place_order already committed PDT + BP; modify only changes price/qty
    # in-place and does not re-spend a day-trade. BP-delta semantics for a
    # partial-fill + modify-remaining sequence require position-state tracking
    # that the gate doesn't have today — Phase 24 will revisit.
    await _check_trade_policy(
        config,
        account.gateway_label,
        notional=new_notional,
        currency_base=account.currency_base,
        redis=redis,
        mode=account.mode,
    )
    # 5c f4-fix: modify reuses /api/orders/preview which hashes 8 fields
    # (account_id, conid, side, order_type, tif, qty, limit_price, stop_price).
    # Verify with the merged payload — immutable fields from the orders row,
    # mutable fields from the modify request — so hashes align.
    nonce_key = f"nonce:order:{row['account_id']}:{request.nonce}"
    consumed_nonce_value = await redis.execute_command("GETDEL", nonce_key)
    if consumed_nonce_value is None:
        raise PreviewUnavailable(409, {"error": "nonce_mismatch"})
    expected_payload = {
        "account_id": str(row["account_id"]),
        "conid": str(row["conid"]),
        "side": str(row["side"]),
        "order_type": str(row["order_type"]),
        "tif": request.tif,
        "qty": qty_text,
        "limit_price": _canonical_decimal_or_none(request.limit_price),
        "stop_price": _canonical_decimal_or_none(request.stop_price),
    }
    expected_hash = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if _decode_nonce_payload(consumed_nonce_value)["payload_hash"] != expected_hash:
        raise PreviewUnavailable(409, {"error": "nonce_mismatch"})

    prior_mutable = {
        "qty": row["qty"],
        "limit_price": row["limit_price"],
        "stop_price": row["stop_price"],
        "tif": row["tif"],
        "notional": row["notional"],
    }
    await db.execute(
        text(
            """
            UPDATE orders
               SET qty = :qty,
                   limit_price = :limit_price,
                   stop_price = :stop_price,
                   tif = :tif,
                   notional = :notional
             WHERE id = :order_id
            """
        ),
        {
            "order_id": order_id,
            "qty": qty_text,
            "limit_price": new_limit_price,
            "stop_price": request.stop_price,
            "tif": request.tif,
            "notional": new_notional,
        },
    )
    await db.commit()

    # D5: client already fetched above for the risk gate; reuse it here.
    contract = await client.get_contract(str(row["conid"]))
    try:
        modify_result = await as_order_sidecar_client(client).modify_order(
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
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as exc:
        await _restore_modify_baseline(db, order_id=order_id, baseline=prior_mutable)
        await db.commit()
        # Phase 9.7 G2: timeout class for sidecar unreachable / broker reject
        # surfaced as UNKNOWN/INVALID_ARGUMENT/NOT_FOUND. The api/orders.py
        # PUT handler maps these to either 422 (broker_modify_rejected) or
        # 503 (sidecar_unreachable); for the metric we treat the gRPC
        # broker-reject codes as "error" and the rest as "timeout".
        # `or ""` guards against grpc_code=None falling through silently
        # (silent-failure-hunter MED-1).
        grpc_code = getattr(exc, "grpc_code", "") or ""
        metric_result = (
            "error"
            if isinstance(exc, BrokerSidecarUnavailable)
            and grpc_code in {"UNKNOWN", "INVALID_ARGUMENT", "NOT_FOUND"}
            else "timeout"
        )
        # contextlib.suppress so an internal Prometheus error doesn't shadow
        # the PreviewUnavailable we're about to raise (HIGH-3).
        with contextlib.suppress(Exception):
            metrics.broker_order_modify_total.labels(
                label=account.gateway_label, result=metric_result
            ).inc()
        raise PreviewUnavailable(
            503,
            {"error": "sidecar_unavailable"},
            headers={"Retry-After": "1"},
        ) from exc
    except Exception:
        await _restore_modify_baseline(db, order_id=order_id, baseline=prior_mutable)
        await db.commit()
        with contextlib.suppress(Exception):
            metrics.broker_order_modify_total.labels(
                label=account.gateway_label, result="error"
            ).inc()
        raise

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

    current_status_row = await db.execute(
        text("SELECT status::text FROM orders WHERE id = :id"),
        {"id": order_id},
    )
    current_status = current_status_row.scalar_one()
    new_status_row = await db.execute(
        text(
            """
            SELECT CASE
                     WHEN order_status_rank(CAST(:current AS order_status_enum))
                          > order_status_rank('modified'::order_status_enum)
                       THEN CAST(:current AS order_status_enum)
                     ELSE 'modified'::order_status_enum
                   END::text
            """
        ),
        {"current": current_status},
    )
    synthesized_status = str(new_status_row.scalar_one())

    projected = {
        "id": order_id,
        "client_order_id": str(row["client_order_id"]),
        "broker_order_id": modify_result.broker_order_id or str(row["broker_order_id"] or ""),
        "status": synthesized_status,
        "qty": qty_text,
        "limit_price": request.limit_price,
        "stop_price": request.stop_price,
        "tif": request.tif,
    }
    await _modify_replay_store(redis, order_id, request.nonce, projected)
    # Phase 9.7 G2: success class — emit AFTER projected has been replay-stored.
    metrics.broker_order_modify_total.labels(label=account.gateway_label, result="success").inc()
    return projected


async def list_orders(
    *,
    db: AsyncSession,
    cfg: ConfigService,
    status: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> OrderListResponse:
    if status is None:
        query_prefix = """
            SELECT id, account_id, broker_order_id, conid, symbol, side, order_type, tif, qty,
                   limit_price, stop_price, status, filled_qty, avg_fill_price, notional,
                   created_at, updated_at, last_event_at
              FROM orders
             WHERE status NOT IN ('filled', 'cancelled', 'rejected', 'expired', 'inactive')
        """
        params: dict[str, object] = {}
    else:
        query_prefix = """
            SELECT id, account_id, broker_order_id, conid, symbol, side, order_type, tif, qty,
                   limit_price, stop_price, status, filled_qty, avg_fill_price, notional,
                   created_at, updated_at, last_event_at
              FROM orders
             WHERE status = CAST(:status AS order_status_enum)
        """
        params = {"status": status}

    # 5c C7: optional date-range filter on created_at.
    where_suffix = ""
    if from_ts is not None:
        where_suffix += " AND created_at >= :from_ts"
        params["from_ts"] = from_ts
    if to_ts is not None:
        where_suffix += " AND created_at <= :to_ts"
        params["to_ts"] = to_ts

    params["limit"] = 500
    result = await db.execute(
        text(query_prefix + where_suffix + " ORDER BY created_at DESC LIMIT :limit"),
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
            SELECT id, account_id, broker_order_id, conid, symbol, side, order_type, tif, qty,
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
    gateway_label = str(row["gateway_label"])
    client = await registry.get_client(gateway_label)
    try:
        accepted = await _as_cancel_order_client(client).cancel_order(
            str(row["account_number"]),
            str(broker_order_id),
        )
        if not accepted:
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
            # Phase 9.7 G2: broker rejected cancel. suppress any internal
            # Prometheus error so it can't shadow the CancelUnavailable
            # we're about to raise (silent-failure-hunter HIGH-3).
            with contextlib.suppress(Exception):
                metrics.broker_order_cancel_total.labels(label=gateway_label, result="error").inc()
            raise CancelUnavailable(
                422,
                {"error": "broker_cancel_rejected", "detail": "broker rejected cancel"},
            )
    except (BrokerSidecarTimeout, BrokerSidecarUnavailable) as _exc:
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
        # Phase 9.7 G2: sidecar unreachable. suppress per HIGH-3.
        with contextlib.suppress(Exception):
            metrics.broker_order_cancel_total.labels(label=gateway_label, result="timeout").inc()
        raise CancelUnavailable(
            503,
            {"error": "sidecar_unavailable", "detail": "cancel forward failed; retry"},
            {"Retry-After": "1"},
        ) from None
    # Phase 9.7 G2: cancel forward accepted by sidecar.
    metrics.broker_order_cancel_total.labels(label=gateway_label, result="success").inc()
    return CancelOrderResult(status="cancel_requested")


async def _check_rate_limit(redis: RedisLike, user_key: str) -> None:
    key = f"rl:orders-preview:{user_key}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > 10:
        raise PreviewUnavailable(429, {"error": "rate_limited"}, {"Retry-After": "60"})


async def resolve_account(db: AsyncSession, account_id: object) -> _Account:
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
        trail_offset: str = "",
        trail_offset_type: str = "",
        trail_limit_offset: str = "",
        expiry_date: str = "",
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


def as_order_sidecar_client(client: object) -> _OrderSidecarClient:
    return client  # type: ignore[return-value]


async def _resolve_contract(client: _ContractSearchClient, conid: str) -> base.Contract:
    # search_contracts is symbol-name autocomplete (reqMatchingSymbols);
    # it cannot resolve a numeric conid. Use the explicit GetContract RPC
    # (qualifyContractsAsync on the sidecar) so a numeric conid like 265598
    # round-trips correctly.
    try:
        return await client.get_contract(conid)
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as _exc:
        raise
    except Exception as exc:  # contract not found / sidecar logic error
        raise PreviewUnavailable(404, {"error": "contract_not_found", "conid": conid}) from exc


async def _native_notional(
    redis: RedisLike,
    request: PreviewRequest,
    contract: base.Contract,
    qty: Decimal,
    *,
    quote_engine: object | None = None,
) -> Decimal:
    if request.order_type == "LIMIT" and request.limit_price is not None:
        return qty * Decimal(request.limit_price)
    if request.order_type == "STOP" and request.stop_price is not None:
        return qty * Decimal(request.stop_price)
    mid = await _get_market_mid(redis, request.conid, contract=contract, quote_engine=quote_engine)
    return qty * mid * Decimal("1.05")


async def _get_market_mid(
    redis: RedisLike,
    conid: str,
    *,
    contract: base.Contract | None = None,
    quote_engine: object | None = None,
) -> Decimal:
    # Namespace mkt:mid:<conid> distinct from FX-pair fx:mid:<from>:<to> below
    # (architect-review aa2071a6 — collision risk if both used fx:mid:).
    cached = await redis.get(f"mkt:mid:{conid}")
    if cached is not None:
        return Decimal(_redis_text(cached))

    # Phase 9.7: on-demand subscribe — trigger a one-shot quote subscription
    # for the ticker so unheld symbols don't unconditionally return 503.
    if quote_engine is not None and contract is not None:
        mid = await _one_shot_market_mid(redis, conid, contract, quote_engine)
        if mid is not None:
            return mid

    raise PreviewUnavailable(503, {"error": "market_mid_unavailable", "conid": conid})


async def _one_shot_market_mid(
    redis: RedisLike,
    conid: str,
    contract: base.Contract,
    quote_engine: object,
) -> Decimal | None:
    """Derive a canonical_id from ``contract``, call
    :meth:`QuoteEngine.subscribe_one_shot`, compute (bid+ask)/2 from the
    first tick, populate ``mkt:mid:<conid>`` in Redis, and return the mid.

    Returns ``None`` on timeout or if price fields are absent/zero.
    """
    from app.services.quotes.base import canonical_key, country_for_exchange
    from app.services.quotes.engine import QuoteEngine

    if not isinstance(quote_engine, QuoteEngine):
        return None

    country = country_for_exchange(contract.exchange)
    if country is None:
        log.warning(
            "preview.one_shot.unknown_exchange",
            exchange=contract.exchange,
            conid=conid,
        )
        return None

    canonical_id = canonical_key(
        asset_class=contract.asset_class,
        symbol=contract.symbol,
        country=country,
    )

    tick = await quote_engine.subscribe_one_shot(canonical_id, timeout_sec=3.0)
    if tick is None:
        return None

    mid = _mid_from_tick(tick)
    if mid is None or mid <= Decimal("0"):
        return None

    # Cache the mid under the broker conid key so subsequent preview calls
    # for the same symbol in the same session hit the Redis fast path.
    await redis.set(f"mkt:mid:{conid}", str(mid), ex=60)
    log.info(
        "preview.one_shot.ok",
        conid=conid,
        canonical_id=canonical_id,
        mid=str(mid),
    )
    return mid


def _mid_from_tick(tick: object) -> Decimal | None:
    """Compute mid price from a QuoteMessage.

    Prefers (bid + ask) / 2 when both are present and positive.  Falls back
    to ``last`` if only one side is available.  Returns ``None`` when no
    usable price field is present.
    """
    bid_raw = getattr(tick, "bid", None)
    ask_raw = getattr(tick, "ask", None)
    last_raw = getattr(tick, "last", None)

    def _to_dec(v: object) -> Decimal | None:
        if not v:
            return None
        try:
            d = Decimal(str(v))
            return d if d > Decimal("0") else None
        except Exception:
            return None

    bid = _to_dec(bid_raw)
    ask = _to_dec(ask_raw)
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    last = _to_dec(last_raw)
    return bid or ask or last


async def _fx_rate(redis: RedisLike, from_currency: str, to_currency: str) -> Decimal:
    if from_currency == to_currency:
        return Decimal("1")
    pair = f"{from_currency}:{to_currency}"
    cached = await redis.get(f"fx:mid:{pair}")
    if cached is None:
        raise PreviewUnavailable(503, {"error": "fx_rate_unavailable", "pair": pair})
    return Decimal(_redis_text(cached))


async def _notional_today(db: AsyncSession, account_id: object) -> Decimal:
    result = await db.execute(
        text(
            """
            SELECT COALESCE(SUM(notional_filled), 0)::numeric
              FROM orders
             WHERE account_id = :account_id
               AND status NOT IN ('cancelled', 'rejected', 'expired', 'inactive')
               AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC');
            """
        ),
        {"account_id": account_id},
    )
    return Decimal(str(result.scalar_one_or_none() or "0")).quantize(Decimal("1e-8"))


async def _notional_filled_today(db: AsyncSession, account_id: object) -> Decimal:
    return await _notional_today(db, account_id)


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
    return await _notional_today(db, account_id)


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
    qty_str = canonicalize_qty(request.qty)
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
            "qty": qty_str,
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
        conid=row["conid"],
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
    qty_str = canonicalize_qty(request.qty)
    payload = {
        "account_id": str(request.account_id),
        "conid": request.conid,
        "side": request.side,
        "order_type": request.order_type,
        "tif": request.tif,
        "qty": qty_str,
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


async def _modify_replay_lookup(
    redis: RedisLike,
    order_id: UUID,
    nonce: str,
) -> dict[str, Any] | None:
    entry = await redis.get(_modify_replay_key(order_id, nonce))
    if entry is None:
        return None
    try:
        decoded = json.loads(_redis_text(entry))
    except json.JSONDecodeError:
        return None
    return dict(decoded) if isinstance(decoded, dict) else None


async def _modify_replay_store(
    redis: RedisLike,
    order_id: UUID,
    nonce: str,
    response: dict[str, Any],
) -> None:
    await redis.set(
        _modify_replay_key(order_id, nonce),
        json.dumps(response, default=str, sort_keys=True, separators=(",", ":")),
        ex=_MODIFY_REPLAY_TTL_SECONDS,
        nx=True,
    )


def _modify_replay_key(order_id: UUID, nonce: str) -> str:
    return f"order-modify-replay:{order_id}:{nonce}"


async def _restore_modify_baseline(
    db: AsyncSession,
    *,
    order_id: UUID,
    baseline: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            UPDATE orders
               SET qty = :qty,
                   limit_price = :limit_price,
                   stop_price = :stop_price,
                   tif = :tif,
                   notional = :notional,
                   updated_at = now()
             WHERE id = :order_id
            """
        ),
        {
            "order_id": order_id,
            "qty": baseline["qty"],
            "limit_price": baseline["limit_price"],
            "stop_price": baseline["stop_price"],
            "tif": baseline["tif"],
            "notional": baseline["notional"],
        },
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
    capability: OrderCapabilityService,
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
    account = await resolve_account(db, request.account_id)

    # HIGH-2: capability gate + maintenance window check (was missing entirely).
    await validate_pre_dispatch(
        cfg=config,
        capability=capability,
        broker_label=account.gateway_label,
        asset_class="STOCK",
        order_type=request.order_type,
        tif=request.tif,
    )
    parent_qty = canonicalize_qty(request.qty)
    parent_notional = Decimal(parent_qty) * entry
    policy = await get_account_policy(
        config,
        gateway_label=account.gateway_label,
        mode=account.mode,
    )
    if cap_status(parent_notional, policy.max_notional_per_order) == "exceeded":
        raise PreviewUnavailable(422, {"error": "max_notional_exceeded"})
    nonce_consumed = False
    existing = await db.execute(
        text(
            """
            SELECT id, client_order_id, broker_order_id, status::text AS status, oca_group
              FROM orders
             WHERE client_order_id = :client_order_id
               AND account_id = :account_id
            """
        ),
        {"client_order_id": request.client_order_id, "account_id": request.account_id},
    )
    existing_row = existing.mappings().one_or_none()
    if existing_row is not None:
        child_rows_result = await db.execute(
            text(
                """
                SELECT id, broker_order_id, status::text AS status, order_type::text AS order_type
                  FROM orders
                 WHERE parent_order_id = :parent_id
                 ORDER BY created_at, id
                """
            ),
            {"parent_id": existing_row["id"]},
        )
        child_rows = list(child_rows_result.mappings().all())
        expected_children = int(request.stop_price is not None) + int(
            request.target_price is not None
        )
        if len(child_rows) == expected_children:
            return _build_bracket_response_from_db(existing_row, child_rows)
        await _consume_nonce(
            redis,
            request.nonce,
            account_id=request.account_id,
            qty=parent_qty,
            limit_price=request.limit_price,
        )
        nonce_consumed = True
        await db.execute(
            text("DELETE FROM orders WHERE parent_order_id = :parent_id"),
            {"parent_id": existing_row["id"]},
        )
        await db.execute(
            text("DELETE FROM orders WHERE id = :parent_id"),
            {"parent_id": existing_row["id"]},
        )
        await db.commit()

    if not nonce_consumed:
        await _consume_nonce(
            redis,
            request.nonce,
            account_id=request.account_id,
            qty=parent_qty,
            limit_price=request.limit_price,
        )

    parent_id = uuid7()
    sl_id = uuid7() if request.stop_price else None
    tp_id = uuid7() if request.target_price else None
    sl_client_order_id = uuid7() if request.stop_price else None
    tp_client_order_id = uuid7() if request.target_price else None
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
    if request.stop_price and sl_id is not None and sl_client_order_id is not None:
        await db.execute(
            text(
                "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, "
                "side, order_type, tif, qty, stop_price, status, notional, "
                "parent_order_id, oca_group) "
                "VALUES (:id, :a, :coid, :conid, :symbol, :side, 'STOP', :tif, :qty, "
                ":sp, 'pending_submit', :n, :pid, :oca)"
            ),
            {
                "id": sl_id,
                "a": request.account_id,
                "coid": sl_client_order_id,
                "conid": request.conid,
                "symbol": symbol,
                "side": "SELL" if request.side == "BUY" else "BUY",
                "tif": request.tif,
                "qty": parent_qty,
                "sp": request.stop_price,
                "n": Decimal(parent_qty) * Decimal(request.stop_price),
                "pid": parent_id,
                "oca": oca_group,
            },
        )
    if request.target_price and tp_id is not None and tp_client_order_id is not None:
        await db.execute(
            text(
                "INSERT INTO orders (id, account_id, client_order_id, conid, symbol, "
                "side, order_type, tif, qty, limit_price, status, notional, "
                "parent_order_id, oca_group) "
                "VALUES (:id, :a, :coid, :conid, :symbol, :side, 'LIMIT', :tif, :qty, "
                ":tp, 'pending_submit', :n, :pid, :oca)"
            ),
            {
                "id": tp_id,
                "a": request.account_id,
                "coid": tp_client_order_id,
                "conid": request.conid,
                "symbol": symbol,
                "side": "SELL" if request.side == "BUY" else "BUY",
                "tif": request.tif,
                "qty": parent_qty,
                "tp": request.target_price,
                "n": Decimal(parent_qty) * Decimal(request.target_price),
                "pid": parent_id,
                "oca": oca_group,
            },
        )
    await db.commit()

    client = await registry.get_client(account.gateway_label)
    try:
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
                    str(sl_client_order_id),
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
                    str(tp_client_order_id),
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
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as exc:
        log.warning("place_bracket.sidecar_unavailable", parent_id=parent_id, exc=str(exc))
        await db.execute(
            text(
                """
                UPDATE orders
                   SET status = 'rejected',
                       updated_at = NOW()
                 WHERE id = :id
                    OR parent_order_id = :id
                """
            ),
            {"id": parent_id},
        )
        await db.commit()
        raise PreviewUnavailable(
            503,
            {"error": "sidecar_unavailable"},
            headers={"Retry-After": "1"},
        ) from exc

    children: list[dict[str, Any]] = []
    await db.execute(
        text("UPDATE orders SET broker_order_id = :bo, status = 'submitted' WHERE id = :id"),
        {"bo": bracket_result.parent_broker_order_id, "id": parent_id},
    )
    if request.stop_price and bracket_result.stop_loss_broker_order_id and sl_id is not None:
        await db.execute(
            text("UPDATE orders SET broker_order_id = :bo, status = 'submitted' WHERE id = :id"),
            {"bo": bracket_result.stop_loss_broker_order_id, "id": sl_id},
        )
        children.append(
            {
                "id": str(sl_id),
                "leg": "stop_loss",
                "broker_order_id": bracket_result.stop_loss_broker_order_id,
                "status": "submitted",
            }
        )
    if request.target_price and bracket_result.take_profit_broker_order_id and tp_id is not None:
        await db.execute(
            text("UPDATE orders SET broker_order_id = :bo, status = 'submitted' WHERE id = :id"),
            {"bo": bracket_result.take_profit_broker_order_id, "id": tp_id},
        )
        children.append(
            {
                "id": str(tp_id),
                "leg": "take_profit",
                "broker_order_id": bracket_result.take_profit_broker_order_id,
                "status": "submitted",
            }
        )
    await db.commit()

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


def _build_bracket_response_from_db(
    parent: Any,
    children: list[Any],
) -> dict[str, Any]:
    response_children: list[dict[str, Any]] = []
    for child in children:
        order_type = str(child["order_type"])
        response_children.append(
            {
                "id": str(child["id"]),
                "leg": "stop_loss" if order_type == "STOP" else "take_profit",
                "broker_order_id": child["broker_order_id"] or "",
                "status": child["status"],
            }
        )
    return {
        "parent": {
            "id": str(parent["id"]),
            "client_order_id": str(parent["client_order_id"]),
            "broker_order_id": parent["broker_order_id"] or "",
            "status": parent["status"],
        },
        "children": response_children,
        "oca_group": parent["oca_group"] or "",
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
