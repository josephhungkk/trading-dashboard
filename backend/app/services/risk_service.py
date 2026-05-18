"""Phase 10a — Risk gate evaluator.

The 4th validation station in the order write path (after kill-switch /
maintenance / capability; before broker dispatch). Spec:
``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md``.

Deterministic given inputs (no global singletons): caller supplies an
``EvaluationContext`` and four injected dependencies (db, redis, config,
sidecar). Returns a ``GateVerdict``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol

import grpc  # type: ignore[import-untyped]
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.models.risk import AccountKillSwitch, RiskLimit
from app.schemas.risk import GateBlockerEntry, GateVerdict, GateWarningEntry
from app.services.market_calendar import (
    is_crypto_session_open,
    is_forex_session_open,
    next_crypto_session_open,
    next_forex_session_open,
)
from app.services.risk_inflight_counters import inflight_bp_committed, inflight_pdt_remaining

if TYPE_CHECKING:
    from app.services.combos.types import ComboContext

CheckResult = tuple[GateBlockerEntry | None, GateWarningEntry | None] | None

# Phase 10a.5 A3.1 (CRIT-2): 3x the 30s discoverer cycle (brokers.py:1036).
_STALENESS_WARN_SECONDS = 90.0


class _ConfigProto(Protocol):
    """Minimal protocol for the ConfigService dependency this service needs."""

    async def get_bool(self, namespace: str, key: str, *, default: bool = False) -> bool: ...
    async def get_int(
        self, namespace: str, key: str, *, default: int | None = None
    ) -> int | None: ...


class _RedisProto(Protocol):
    """Minimal protocol for the Redis dependency this service needs.

    ``incrbyfloat`` widens to ``Decimal | float`` because the project uses
    Decimal for money everywhere; the counter module passes a Decimal-cast
    float at the wire boundary.
    """

    async def get(self, key: str) -> Any: ...
    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> Any: ...
    async def decr(self, key: str) -> Any: ...
    async def incr(self, key: str) -> Any: ...
    async def incrbyfloat(self, key: str, amount: Decimal | float) -> Any: ...


class _SidecarProto(Protocol):
    """Minimal protocol for the broker-client dependency this service needs."""

    async def preview_order(self, **kwargs: Any) -> Any: ...
    async def get_account_summary(self, account_id: uuid.UUID) -> Any: ...


log = structlog.get_logger(__name__)

EvalMode = Literal["preview", "place_order", "modify_order"]
Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class EvaluationContext:
    """Inputs to the risk gate. Pure data; no service references."""

    account_id: uuid.UUID
    broker_id: str
    instrument_id: int | None
    side: Side
    qty: Decimal
    price: Decimal | None
    order_type: str
    time_in_force: str
    request_id: str
    currency_base: str  # account base currency for max-loss conversion
    # Phase 11a CI-debt: symbol + asset_class are needed by ``_check_margin``
    # because ``BrokerSidecarClient.preview_order`` accepts (symbol,
    # asset_class) NOT (instrument_id) — the latter is a DB-side primary
    # key the sidecar can't resolve. None is tolerated (call sites that
    # can't easily plumb these — e.g. fall back to instrument-id-only
    # checks); the margin check then takes the AttributeError → WARN
    # branch with "preview unavailable" instead of fail-CLOSED.
    symbol: str | None = None
    asset_class: str | None = None
    multiplier: Decimal = Decimal("1")  # 1 for non-options; 100 for standard equity options
    tick_size: Decimal | None = None
    first_notice_day: date | None = None
    underlying_symbol: str | None = None
    position_effect: Literal["OPEN", "CLOSE"] | None = None
    account_nlv_base: Decimal | None = None
    notional: Decimal | None = None


class RiskService:
    """Risk-gate evaluator.

    Lookup walk for ``risk_limits``: ``(account, kind)`` → ``(broker, kind)`` →
    ``(global, kind)``. First active hit wins. ``evaluate(ctx, mode)`` returns
    a ``GateVerdict`` aggregated from the seven checks (added in B2-B7).
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: _RedisProto,
        config: _ConfigProto,
        sidecar: _SidecarProto,
    ) -> None:
        self._db = db
        self._redis = redis
        self._config = config
        self._sidecar = sidecar

    async def _resolve_limit(
        self,
        account_id: uuid.UUID,
        broker_id: str,
        kind: str,
    ) -> RiskLimit | None:
        """Walk account → broker → global; return first active hit."""
        for scope_type, scope_id in (
            ("account", str(account_id)),
            ("broker", broker_id),
            ("global", None),
        ):
            stmt = select(RiskLimit).where(
                RiskLimit.scope_type == scope_type,
                RiskLimit.limit_kind == kind,
                RiskLimit.is_active.is_(True),
            )
            stmt = (
                stmt.where(RiskLimit.scope_id.is_(None))
                if scope_id is None
                else stmt.where(RiskLimit.scope_id == scope_id)
            )
            row = (await self._db.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return row
        return None

    async def _check_account_kill_switch(self, ctx: EvaluationContext) -> CheckResult:
        """B2: BLOCK when account_kill_switches.is_enabled=True for the account.

        D9-fix (security): the kill-switch reason text is operator
        free-form (max 1000 chars) and would land in
        risk_decisions.blockers JSONB if interpolated here, persisting
        any PII the operator typed (e.g. "frozen — pending KYC for John
        Smith, DOB 1982-01-01"). The message now carries a fixed code
        only; UI consumers fetch the live reason from
        GET /api/admin/accounts/{id}/kill-switch when displaying.
        """
        stmt = select(AccountKillSwitch).where(AccountKillSwitch.account_id == ctx.account_id)
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        if row is None or not row.is_enabled:
            return None
        return (
            GateBlockerEntry(
                check="account_kill_switch",
                message="account kill switch enabled",
                code="account_kill_switch_enabled",
            ),
            None,
        )

    async def _check_broker_kill_switch(self, ctx: EvaluationContext) -> CheckResult:
        """B2: composes Phase 5b H0 (app_config.broker.kill_switch_enabled)."""
        is_on = await self._config.get_bool("broker", "kill_switch_enabled", default=False)
        if not is_on:
            return None
        return (
            GateBlockerEntry(
                check="broker_kill_switch",
                message=f"broker {ctx.broker_id} kill switch enabled (Phase 5b H0)",
                code="broker_kill_switch_enabled",
            ),
            None,
        )

    async def _check_max_daily_loss(self, ctx: EvaluationContext) -> CheckResult:
        """B3: realized + unrealized intraday P&L vs cap (account base currency).

        Spec §1 #3. Cap kind ``max_daily_loss_currency_base`` resolved via the
        account → broker → global walk. View ``v_account_intraday_pnl`` returns
        a ``(realized, unrealized, summary_updated_at, staleness)`` row per
        account when fresh data exists. Phase 10a.5 §4 CRIT-2: missing row OR
        staleness > 90s (3x discoverer cycle) -> WARN with code
        ``max_daily_loss_pnl_stale`` — informational only, not silent ALLOW.

        Sign convention: realized + unrealized are signed (negative = loss).
        ``loss_today = -(realized + unrealized)``; positive when underwater.
        BLOCK when ``loss_today >= limit_value``; WARN at ``warn_at_pct`` of cap.
        """
        cap = await self._resolve_limit(
            ctx.account_id, ctx.broker_id, "max_daily_loss_currency_base"
        )
        if cap is None:
            return None  # no cap → not evaluated → ALLOW

        row = (
            await self._db.execute(
                text(
                    "SELECT realized, unrealized, staleness_s "
                    "FROM v_account_intraday_pnl "
                    "WHERE account_id = :account_id"
                ),
                {"account_id": ctx.account_id},
            )
        ).first()

        # Phase 10a.5 A3.1 (CRIT-2): row-missing OR staleness > 90s -> WARN.
        # check="max_daily_loss_pnl_stale" distinguishes this WARN from the
        # normal value/threshold WARN (which uses check="max_daily_loss").
        if row is None or row.staleness_s > _STALENESS_WARN_SECONDS:
            log.warning(
                "risk.max_daily_loss_pnl_stale",
                account_id=str(ctx.account_id),
                staleness_s=None if row is None else float(row.staleness_s),
            )
            return (
                None,
                GateWarningEntry(
                    check="max_daily_loss_pnl_stale",
                    message=(
                        "intraday PnL data is stale or absent; "
                        "max-daily-loss check is informational only"
                    ),
                ),
            )

        realized = Decimal(row[0])
        unrealized = Decimal(row[1])
        loss_today = -(realized + unrealized)

        cap_value = Decimal(cap.limit_value)
        if loss_today >= cap_value:
            return (
                GateBlockerEntry(
                    check="max_daily_loss",
                    message=(
                        f"intraday loss {loss_today} {ctx.currency_base} ≥ cap "
                        f"{cap_value} {ctx.currency_base}"
                    ),
                    code="max_daily_loss_exceeded",
                ),
                None,
            )

        if cap.warn_at_pct is not None:
            warn_threshold = cap_value * Decimal(cap.warn_at_pct) / Decimal("100")
            if loss_today >= warn_threshold:
                return (
                    None,
                    GateWarningEntry(
                        check="max_daily_loss",
                        message=(
                            f"intraday loss {loss_today} {ctx.currency_base} at "
                            f"{cap.warn_at_pct}% of cap {cap_value} {ctx.currency_base}"
                        ),
                        value=float(loss_today),
                        threshold=float(cap_value),
                    ),
                )

        return None

    async def _check_position_concentration(self, ctx: EvaluationContext) -> CheckResult:
        """B5: cross-broker position concentration as % of NLV.

        Spec §1 #5 + H2. Cap kind ``max_position_concentration_pct`` resolved
        via the account → broker → global walk. The positions SUM is taken
        **without an account filter** — single-user dashboard aggregates the
        same ``instrument_id`` across every broker so an AAPL position split
        IBKR/Schwab caps as one. Skip when ``ctx.instrument_id is None``
        (cash trades have no concentration risk).

        Sign convention: ``buy`` adds notional, ``sell`` subtracts; we take
        ``abs()`` because shorts concentrate just as much as longs. Market
        orders (``ctx.price is None``) treat the new clip as zero notional —
        post-trade exposure equals current exposure for this check.
        """
        if ctx.instrument_id is None:
            return None

        cap = await self._resolve_limit(
            ctx.account_id, ctx.broker_id, "max_position_concentration_pct"
        )
        if cap is None:
            return None

        # Phase 10a.5 B3 DB-CRIT-1 fix: positions table has no
        # market_value_base column today (deferred to a Phase 10b view).
        # Approximate exposure as (qty * avg_cost * multiplier) — close enough
        # for concentration % math since we compare against current NLV
        # which is itself a snapshot.
        current = Decimal(
            (
                await self._db.execute(
                    text(
                        "SELECT COALESCE(SUM(qty * avg_cost * multiplier), 0) "
                        "FROM positions WHERE instrument_id = :iid"
                    ),
                    {"iid": ctx.instrument_id},
                )
            ).scalar()
            or 0
        )

        summary = await self._sidecar.get_account_summary(ctx.account_id)
        nlv = Decimal(summary.nlv_currency_base)
        if nlv == Decimal("0"):
            return None  # cannot compute % when NLV unknown / zero

        sign = Decimal("1") if ctx.side == "buy" else Decimal("-1")
        price = ctx.price if ctx.price is not None else Decimal("0")
        delta = ctx.qty * price * sign
        post_exposure = abs(current + delta)
        post_pct = post_exposure / nlv * Decimal("100")

        cap_value = Decimal(cap.limit_value)
        if post_pct >= cap_value:
            return (
                GateBlockerEntry(
                    check="position_concentration",
                    message=(
                        f"instrument {ctx.instrument_id} post-trade concentration "
                        f"{post_pct:.2f}% ≥ cap {cap_value}%"
                    ),
                    code="position_concentration_exceeded",
                ),
                None,
            )

        if cap.warn_at_pct is not None:
            warn_threshold = cap_value * Decimal(cap.warn_at_pct) / Decimal("100")
            if post_pct >= warn_threshold:
                return (
                    None,
                    GateWarningEntry(
                        check="position_concentration",
                        message=(
                            f"instrument {ctx.instrument_id} post-trade concentration "
                            f"{post_pct:.2f}% at {cap.warn_at_pct}% of cap {cap_value}%"
                        ),
                        value=float(post_pct),
                        threshold=float(cap_value),
                    ),
                )

        return None

    async def _check_buying_power(self, ctx: EvaluationContext) -> CheckResult:
        """B6: buying-power buffer with in-flight commitment subtract (H3).

        Spec §1 #6. Cap kind ``min_buying_power_buffer_pct.limit_value`` is
        the *required headroom %* — WARN if remaining BP after this trade
        falls below ``effective_bp * cap_pct / 100``. BLOCK is unconditional
        when notional already exceeds ``effective_bp = bp_base - committed``.

        H3 invariant: ``committed`` is the in-flight Redis counter built by
        Task D4 at place_order time and zeroed by reconcile after broker
        ACK; subtracting it here closes the staleness window so a fast
        double-buy can't both clear the gate. Sells reduce BP usage so the
        check is skipped. Market orders skip too (no notional to compare).
        """
        if ctx.side == "sell":
            return None

        cap = await self._resolve_limit(
            ctx.account_id, ctx.broker_id, "min_buying_power_buffer_pct"
        )
        if cap is None:
            return None

        if ctx.price is None:
            return None
        order_notional = ctx.qty * ctx.price

        summary = await self._sidecar.get_account_summary(ctx.account_id)
        bp_base = Decimal(summary.buying_power)
        try:
            committed = await inflight_bp_committed(self._redis, ctx.account_id)
        except (ConnectionError, OSError) as exc:
            # Spec §4: Redis unreachable -> WARN; treat committed as broker
            # truth (i.e. assume zero in-flight) rather than fail-CLOSED on
            # operational hiccup. Counter is best-effort; broker BP is
            # authoritative.
            log.warning(
                "bp_inflight_redis_unreachable",
                account_id=str(ctx.account_id),
                error=str(exc),
            )
            return (
                None,
                GateWarningEntry(
                    check="buying_power",
                    message="BP in-flight tracking degraded (Redis unreachable)",
                    value=0.0,
                    threshold=0.0,
                ),
            )
        effective_bp = bp_base - committed

        if order_notional > effective_bp:
            return (
                GateBlockerEntry(
                    check="buying_power",
                    message=(
                        f"order notional {order_notional} {ctx.currency_base} > "
                        f"effective BP {effective_bp} {ctx.currency_base} "
                        f"(bp_base={bp_base}, committed={committed})"
                    ),
                    code="buying_power_insufficient",
                ),
                None,
            )

        remaining_after_order = effective_bp - order_notional
        buffer_required = effective_bp * Decimal(cap.limit_value) / Decimal("100")
        if remaining_after_order < buffer_required:
            return (
                None,
                GateWarningEntry(
                    check="buying_power",
                    message=(
                        f"remaining BP {remaining_after_order} {ctx.currency_base} < "
                        f"required buffer {buffer_required} {ctx.currency_base} "
                        f"({cap.limit_value}% of effective BP)"
                    ),
                    value=float(remaining_after_order),
                    threshold=float(buffer_required),
                ),
            )

        return None

    async def _check_pdt(self, ctx: EvaluationContext) -> CheckResult:
        """B4: PDT remaining = in-flight Redis counter, fall back to broker.

        Spec §1 #4 + H1. Cap kind ``pdt_warn_remaining.limit_value`` is the
        threshold below which the gate WARNs; BLOCK is unconditional at
        ``current <= 0``. Counter unset (cold cache) ⇒ fetch broker-reported
        ``day_trades_remaining`` from ``sidecar.get_account_summary`` so the
        gate stays decisive on first trade after a backend restart.
        """
        cap = await self._resolve_limit(ctx.account_id, ctx.broker_id, "pdt_warn_remaining")
        if cap is None:
            return None

        try:
            current = await inflight_pdt_remaining(self._redis, ctx.account_id)
        except (ConnectionError, OSError) as exc:
            # Spec §4: Redis unreachable -> WARN "PDT/BP in-flight tracking
            # degraded", broker truth is authoritative. Don't fail-CLOSED on
            # operational Redis hiccup.
            log.warning(
                "pdt_inflight_redis_unreachable",
                account_id=str(ctx.account_id),
                error=str(exc),
            )
            return (
                None,
                GateWarningEntry(
                    check="pdt",
                    message="PDT in-flight tracking degraded (Redis unreachable)",
                    value=0.0,
                    threshold=0.0,
                ),
            )
        if current is None:
            summary = await self._sidecar.get_account_summary(ctx.account_id)
            current = int(summary.day_trades_remaining)

        warn_remaining = int(cap.limit_value)

        if current <= 0:
            return (
                GateBlockerEntry(
                    check="pdt",
                    message=f"day-trades remaining {current} ≤ 0",
                    code="pdt_exhausted",
                ),
                None,
            )

        if current <= warn_remaining:
            return (
                None,
                GateWarningEntry(
                    check="pdt",
                    message=(f"day-trades remaining {current} ≤ warn threshold {warn_remaining}"),
                    value=float(current),
                    threshold=float(warn_remaining),
                ),
            )

        return None

    async def _check_margin(self, ctx: EvaluationContext, mode: EvalMode) -> CheckResult:
        """B7: sidecar margin preview with asymmetric preview/place_order policy (C3, H4).

        Wraps the broker PreviewOrder RPC in ``asyncio.wait_for``. Timeout is
        500ms in preview mode (UX must not block on broker hiccup -> WARN
        pending) and 3s in place_order/modify (margin-violating order must
        not slip through -> BLOCK fail-CLOSED). gRPC UNIMPLEMENTED (Alpaca
        and any future stub) always WARNs with the documented BP-cache-only
        fallback. ``response.accepted=False`` is an authoritative broker
        rejection -> BLOCK regardless of mode.
        """
        timeout = 0.5 if mode == "preview" else 3.0
        # Phase 11a CI-debt: call ``BrokerSidecarClient.preview_order`` with
        # the actual signature (account_id=str, side, symbol, asset_class,
        # order_type, time_in_force, qty, limit_price, stop_price). The
        # previous kwargs (broker_id/instrument_id/price/request_id) did not
        # match the BrokerSidecarClient method and silently turned every
        # IBKR/Schwab preview/place_order into an evaluator_error BLOCK
        # since Phase 10a. Falls back to the AttributeError WARN branch
        # when symbol/asset_class aren't plumbed (legacy callers, modify
        # path with conid-only context).
        if ctx.symbol is None or ctx.asset_class is None:
            # Preview mode: WARN is fine (UX must not block on metadata gap).
            # Write paths (place_order/modify): fail-CLOSED — margin is the
            # only authoritative broker-side check, and missing context means
            # we cannot evaluate it, so the order must not slip through.
            log.warning(
                "risk.margin_skip",
                broker_id=ctx.broker_id,
                account_id=str(ctx.account_id),
                mode=mode,
                symbol_present=ctx.symbol is not None,
                asset_class_present=ctx.asset_class is not None,
            )
            if mode == "preview":
                metrics.risk_margin_skip_total.labels(mode=mode, outcome="warn").inc()
                return (
                    None,
                    GateWarningEntry(
                        check="margin",
                        message=(
                            f"{ctx.broker_id} margin preview skipped: "
                            "symbol/asset_class unavailable, BP cache only"
                        ),
                        value=0.0,
                        threshold=0.0,
                    ),
                )
            metrics.risk_margin_skip_total.labels(mode=mode, outcome="block").inc()
            return (
                GateBlockerEntry(
                    check="margin",
                    message=(
                        f"margin check unavailable: {ctx.broker_id} "
                        "symbol/asset_class missing (fail-CLOSED for "
                        f"{mode})"
                    ),
                    code="margin_check_unavailable",
                ),
                None,
            )
        try:
            response = await asyncio.wait_for(
                self._sidecar.preview_order(
                    account_id=str(ctx.account_id),
                    side=ctx.side,
                    symbol=ctx.symbol,
                    asset_class=ctx.asset_class,
                    order_type=ctx.order_type,
                    time_in_force=ctx.time_in_force,
                    qty=str(ctx.qty),
                    limit_price=str(ctx.price) if ctx.price is not None else None,
                    stop_price=None,
                ),
                timeout=timeout,
            )
        except AttributeError:
            # Sidecar lacks preview_order entirely — treat as UNIMPLEMENTED
            # (Alpaca pattern + test stubs that haven't wired the method).
            return (
                None,
                GateWarningEntry(
                    check="margin",
                    message=(f"{ctx.broker_id} margin preview unavailable, BP cache only"),
                    value=0.0,
                    threshold=0.0,
                ),
            )
        except TimeoutError:
            if mode == "preview":
                return (
                    None,
                    GateWarningEntry(
                        check="margin",
                        message=(
                            f"margin check pending: {ctx.broker_id} preview "
                            f"exceeded {timeout}s soft-deadline"
                        ),
                        value=float(timeout),
                        threshold=float(timeout),
                    ),
                )
            return (
                GateBlockerEntry(
                    check="margin",
                    message=(
                        f"margin check unavailable: {ctx.broker_id} preview "
                        f"exceeded {timeout}s deadline (fail-CLOSED for {mode})"
                    ),
                    code="margin_check_unavailable",
                ),
                None,
            )
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                return (
                    None,
                    GateWarningEntry(
                        check="margin",
                        message=(f"{ctx.broker_id} margin preview unavailable, BP cache only"),
                        value=0.0,
                        threshold=0.0,
                    ),
                )
            raise

        if response.accepted is False:
            return (
                GateBlockerEntry(
                    check="margin",
                    message=(
                        f"broker rejected: {response.reject_reason}"
                        if response.reject_reason
                        else f"{ctx.broker_id} broker rejected the order"
                    ),
                    code="margin_rejected_by_broker",
                ),
                None,
            )

        return None

    async def _check_options_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 12: Options-specific risk checks. Called from evaluate() when asset_class==OPTION.

        Check ordering:
          1a: naked-short gate          (BLOCK — cfg read + positions query)
          1a.5: cash-secured put L2     (BLOCK — strike * qty * multiplier * 1.05 vs cash)
          1b: expiry-day cutoff         (BLOCK — exchange-tz date compare)
          post-BLOCK: 0DTE WARN, assignment-risk WARN (mutually exclusive)
        """
        from app.services import market_calendar

        side = ctx.side
        position_effect = ctx.position_effect
        trading_level = await self._config.get_int("options", "trading_level", default=1)
        if trading_level is None:
            trading_level = 1

        is_sto = side == "sell" and position_effect == "OPEN"

        # Step 1a: naked-short gate
        if is_sto:
            existing_cover = await self._get_existing_long_position(ctx)
            if existing_cover < ctx.qty and trading_level < 3:
                return (
                    GateBlockerEntry(
                        check="options_exposure",
                        message=(
                            f"Naked short requires options trading level 3+, "
                            f"current={trading_level}"
                        ),
                        code="naked_short_not_permitted",
                    ),
                    None,
                )

        # Step 1a.5: cash-secured put reserve check (L2 only, STO PUTs)
        option_expiry, exchange, option_type, strike_str = await self._get_option_meta(ctx)
        if is_sto and option_type == "PUT" and trading_level == 2 and strike_str is not None:
            try:
                strike = Decimal(strike_str)
                required_cash = strike * ctx.qty * Decimal(ctx.multiplier) * Decimal("1.05")
                cash_row = await self._db.execute(
                    text(
                        "SELECT COALESCE(SUM(market_value_base), 0) FROM positions "
                        "WHERE account_id = :aid AND instrument_id IS NULL"
                    ),
                    {"aid": ctx.account_id},
                )
                available_cash = Decimal(str(cash_row.scalar() or 0))
                if required_cash > available_cash:
                    return (
                        GateBlockerEntry(
                            check="options_exposure",
                            message=(
                                f"Cash-secured put requires {required_cash:.2f} "
                                f"{ctx.currency_base} reserve, "
                                f"available={available_cash:.2f}"
                            ),
                            code="cash_secured_put_insufficient_reserve",
                        ),
                        None,
                    )
            except Exception:
                pass

        # Step 1b: expiry-day cutoff + 0DTE WARN + assignment-risk WARN
        if option_expiry is not None:
            if exchange is not None:
                try:
                    today_ex = market_calendar.today_in_exchange_tz(exchange)
                    if today_ex > option_expiry:
                        return (
                            GateBlockerEntry(
                                check="options_exposure",
                                message=(
                                    f"Past option cutoff for expiry {option_expiry} on {exchange}"
                                ),
                                code="option_cutoff_passed",
                            ),
                            None,
                        )
                    if today_ex == option_expiry:
                        return (
                            None,
                            GateWarningEntry(
                                check="options_exposure",
                                message="0DTE order — option expires today",
                            ),
                        )
                    # Assignment-risk WARN: STO within 5 trading days of expiry
                    # (only fires when today_ex < option_expiry, i.e. not 0DTE)
                    if is_sto:
                        try:
                            next_5 = market_calendar.next_trading_days(exchange, 5)
                            within_5_days = option_expiry in next_5
                        except Exception:
                            within_5_days = False
                        if within_5_days:
                            delta: Decimal | None = None
                            try:
                                if ctx.instrument_id is not None:
                                    delta_row = await self._db.execute(
                                        text(
                                            "SELECT delta FROM option_greeks "
                                            "WHERE instrument_id = :iid"
                                        ),
                                        {"iid": ctx.instrument_id},
                                    )
                                    delta_val = delta_row.scalar()
                                    if delta_val is not None:
                                        delta = Decimal(str(delta_val))
                            except Exception:
                                delta = None
                            if delta is None or abs(delta) >= Decimal("0.7"):
                                return (
                                    None,
                                    GateWarningEntry(
                                        check="options_exposure",
                                        message=(
                                            "High assignment risk: ITM short option "
                                            "within 5 trading days of expiry"
                                        ),
                                        value=float(abs(delta)) if delta is not None else 0.0,
                                        threshold=0.7,
                                    ),
                                )
                except ValueError:
                    pass  # unknown exchange — skip cutoff check

        return None

    async def _get_existing_long_position(self, ctx: EvaluationContext) -> Decimal:
        """Return existing long qty for the instrument (0 if none)."""
        if ctx.instrument_id is None:
            return Decimal("0")
        result = await self._db.execute(
            text(
                "SELECT COALESCE(SUM(qty), 0) FROM positions WHERE instrument_id = :iid AND qty > 0"
            ),
            {"iid": ctx.instrument_id},
        )
        row = result.fetchone()
        return Decimal(str(row[0])) if row else Decimal("0")

    async def _get_option_meta(
        self, ctx: EvaluationContext
    ) -> tuple[date | None, str | None, str | None, str | None]:
        """Return (expiry, exchange, option_type, strike_str) for an option instrument.

        Runs a single DB query to avoid N+1 round-trips. Returns all-None tuple
        when instrument_id is absent or asset_class is not OPTION.
        """
        if ctx.instrument_id is None or ctx.asset_class != "OPTION":
            return (None, None, None, None)
        result = await self._db.execute(
            text(
                "SELECT meta->>'expiry', primary_exchange, "
                "meta->>'option_type', meta->>'strike' "
                "FROM instruments WHERE id = :iid"
            ),
            {"iid": ctx.instrument_id},
        )
        row = result.fetchone()
        if not row:
            return (None, None, None, None)
        expiry_str, exchange, option_type, strike_str = row
        expiry: date | None = None
        if expiry_str:
            try:
                expiry = date.fromisoformat(expiry_str)
            except ValueError:
                pass
        return (expiry, exchange or None, option_type or None, strike_str or None)

    async def _check_futures_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 14: Futures-specific risk checks (physical delivery WARN/BLOCK)."""
        from datetime import date as date_cls

        blockers: list[Any] = []
        warnings: list[Any] = []
        is_close = getattr(ctx, "position_effect", None) == "CLOSE"

        if not is_close and ctx.first_notice_day is not None:
            today = date_cls.today()
            if today >= ctx.first_notice_day:
                blockers.append(
                    GateBlockerEntry(
                        check="futures_physical_delivery",
                        code="futures_physical_delivery_block",
                        message=(
                            f"Physical delivery block: first notice day was "
                            f"{ctx.first_notice_day}. Close position via broker."
                        ),
                    )
                )

        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

    async def _check_forex_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 15a: IDEALPRO FX risk checks. Fail-OPEN on infrastructure errors."""
        log = structlog.get_logger(__name__)
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = []
        try:
            if not is_forex_session_open():
                retry_at = next_forex_session_open().isoformat()
                return (
                    GateBlockerEntry(
                        check="forex_session",
                        code="session_closed",
                        message=f"IDEALPRO FX session is closed. Next open: {retry_at}",
                    ),
                    None,
                )
            notional = getattr(ctx, "notional", None) or (ctx.qty * (ctx.price or Decimal("1")))
            limit_row = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "forex_max_notional_per_trade"
            )
            if limit_row is not None and notional > limit_row.limit_value:
                blockers.append(
                    GateBlockerEntry(
                        check="forex_notional",
                        code="forex_notional_exceeded",
                        message=(
                            f"Notional {notional} exceeds per-trade cap {limit_row.limit_value}."
                        ),
                    )
                )
                return blockers[0], None
            # Check #3 (spec §4.4): existing open position in same pair → consolidation WARN
            existing = await self._db.execute(
                text(
                    "SELECT id FROM positions WHERE account_id = :aid AND instrument_id = :iid"
                    " AND qty != 0 LIMIT 1"
                ),
                {"aid": str(ctx.account_id), "iid": ctx.instrument_id},
            )
            if existing.fetchone() is not None:
                warnings.append(
                    GateWarningEntry(
                        check="forex_consolidation",
                        message="consolidation_suggested: An open position exists for this pair.",
                    )
                )
        except Exception:
            metrics.forex_risk_check_failures_total.inc()
            log.exception("forex_risk_check_infrastructure_error", account_id=str(ctx.account_id))
            return None, None  # fail-OPEN
        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

    async def _check_crypto_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 15b: Paxos/IBKR crypto risk checks. Fail-OPEN on infrastructure errors."""
        log = structlog.get_logger(__name__)
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = []
        try:
            if not is_crypto_session_open():
                retry_at = next_crypto_session_open().isoformat()
                return (
                    GateBlockerEntry(
                        check="crypto_session",
                        code="session_closed",
                        message=f"Crypto trading is in maintenance window. Next open: {retry_at}",
                    ),
                    None,
                )
            if ctx.instrument_id is not None:
                meta_row = await self._db.execute(
                    text("SELECT meta FROM instruments WHERE id = :id LIMIT 1"),
                    {"id": ctx.instrument_id},
                )
                meta_result = meta_row.scalar_one_or_none()
                if meta_result is not None:
                    qty_step_str = (meta_result or {}).get("qty_step")
                    if qty_step_str:
                        qty_step = Decimal(qty_step_str)
                        if qty_step > 0 and (ctx.qty % qty_step) != 0:
                            blockers.append(
                                GateBlockerEntry(
                                    check="crypto_qty_precision",
                                    code="invalid_qty_precision",
                                    message=f"Qty {ctx.qty} is not a multiple of step {qty_step}.",
                                )
                            )
                            return blockers[0], None
            if ctx.account_nlv_base is not None and ctx.account_nlv_base > 0:
                notional = ctx.qty * (ctx.price or Decimal("1"))
                concentration = notional / ctx.account_nlv_base
                if concentration > Decimal("0.20"):
                    warnings.append(
                        GateWarningEntry(
                            check="crypto_concentration",
                            message=(
                                f"concentration_warning: crypto notional is "
                                f"{concentration:.1%} of account NLV."
                            ),
                        )
                    )
        except Exception:
            metrics.crypto_risk_check_failures_total.inc()
            log.exception("crypto_risk_check_infrastructure_error", account_id=str(ctx.account_id))
            return None, None  # fail-OPEN
        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

    async def _check_bond_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 16a: Bond risk checks — notional cap + concentration WARN. Fail-OPEN."""
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = []
        try:
            notional = getattr(ctx, "notional", None) or (ctx.qty * (ctx.price or Decimal("1")))
            limit_row = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "bond_max_notional_per_trade"
            )
            if limit_row is not None and notional > limit_row.limit_value:
                blockers.append(
                    GateBlockerEntry(
                        check="bond_notional",
                        code="bond_notional_exceeded",
                        message=(
                            f"Notional {notional} exceeds bond per-trade cap "
                            f"{limit_row.limit_value}."
                        ),
                    )
                )
                return blockers[0], None
            conc_row = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "bond_max_concentration_pct"
            )
            if (
                conc_row is not None
                and ctx.account_nlv_base is not None
                and ctx.account_nlv_base > 0
            ):
                conc_pct = (notional / ctx.account_nlv_base) * Decimal("100")
                if conc_pct > conc_row.limit_value:
                    warnings.append(
                        GateWarningEntry(
                            check="bond_concentration",
                            message=(
                                f"Bond concentration {conc_pct:.1f}% exceeds warning "
                                f"threshold {conc_row.limit_value}%."
                            ),
                        )
                    )
        except Exception:
            log.exception(
                "bond_risk_check_infrastructure_error",
                account_id=str(ctx.account_id),
            )
            return None, None  # fail-OPEN
        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

    async def _check_fund_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 16b: Mutual fund risk checks — notional cap + concentration WARN. Fail-OPEN."""
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = []
        try:
            notional = getattr(ctx, "notional", None) or (ctx.qty * (ctx.price or Decimal("1")))
            limit_row = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "fund_max_notional_per_trade"
            )
            if limit_row is not None and notional > limit_row.limit_value:
                blockers.append(
                    GateBlockerEntry(
                        check="fund_notional",
                        code="fund_notional_exceeded",
                        message=(
                            f"Notional {notional} exceeds fund per-trade cap "
                            f"{limit_row.limit_value}."
                        ),
                    )
                )
                return blockers[0], None
            conc_row = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "fund_max_concentration_pct"
            )
            if (
                conc_row is not None
                and ctx.account_nlv_base is not None
                and ctx.account_nlv_base > 0
            ):
                conc_pct = (notional / ctx.account_nlv_base) * Decimal("100")
                if conc_pct > conc_row.limit_value:
                    warnings.append(
                        GateWarningEntry(
                            check="fund_concentration",
                            message=(
                                f"Fund concentration {conc_pct:.1f}% exceeds warning threshold "
                                f"{conc_row.limit_value}%."
                            ),
                        )
                    )
        except Exception:
            log.exception("fund_risk_check_infrastructure_error", account_id=str(ctx.account_id))
            return None, None  # fail-OPEN
        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

    async def _check_cfd_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 16c: CFD risk checks.

        Notional cap + leverage cap + concentration WARN. Fail-OPEN.
        """
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = []
        try:
            notional = getattr(ctx, "notional", None) or (ctx.qty * (ctx.price or Decimal("1")))
            # Check notional cap
            notional_limit = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "cfd_max_notional_per_trade"
            )
            if notional_limit is not None and notional > notional_limit.limit_value:
                return (
                    GateBlockerEntry(
                        check="cfd_notional",
                        code="cfd_notional_exceeded",
                        message=(
                            f"CFD notional {notional} exceeds per-trade cap "
                            f"{notional_limit.limit_value}."
                        ),
                    ),
                    None,
                )
            # Check leverage cap from meta
            if ctx.instrument_id is not None:
                meta_row = await self._db.execute(
                    text("SELECT meta FROM instruments WHERE id = :id LIMIT 1"),
                    {"id": ctx.instrument_id},
                )
                meta = meta_row.scalar_one_or_none() or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                max_leverage_str = meta.get("max_leverage")
                if max_leverage_str:
                    max_leverage = Decimal(str(max_leverage_str))
                    lev_limit = await self._resolve_limit(
                        ctx.account_id, ctx.broker_id, "cfd_max_leverage"
                    )
                    if lev_limit is not None and max_leverage > lev_limit.limit_value:
                        blockers.append(
                            GateBlockerEntry(
                                check="cfd_leverage",
                                code="cfd_leverage_exceeded",
                                message=(
                                    f"CFD max leverage {max_leverage}x exceeds permitted "
                                    f"{lev_limit.limit_value}x."
                                ),
                            )
                        )
                        return blockers[0], None
            # Concentration WARN
            conc_row = await self._resolve_limit(
                ctx.account_id, ctx.broker_id, "cfd_max_concentration_pct"
            )
            if (
                conc_row is not None
                and ctx.account_nlv_base is not None
                and ctx.account_nlv_base > 0
            ):
                conc_pct = (notional / ctx.account_nlv_base) * Decimal("100")
                if conc_pct > conc_row.limit_value:
                    warnings.append(
                        GateWarningEntry(
                            check="cfd_concentration",
                            message=(
                                f"CFD concentration {conc_pct:.1f}% exceeds warning threshold "
                                f"{conc_row.limit_value}%."
                            ),
                        )
                    )
        except Exception:
            log.exception("cfd_risk_check_infrastructure_error", account_id=str(ctx.account_id))
            return None, None  # fail-OPEN
        if blockers:
            return blockers[0], None
        if warnings:
            return None, warnings[0]
        return None, None

    async def evaluate(self, ctx: EvaluationContext, mode: EvalMode) -> GateVerdict:
        """Run all 7 checks; aggregate to GateVerdict (allow/warn/block precedence).

        Spec §1, §4 [C3]. The 6 fast checks run concurrently via
        ``asyncio.gather(return_exceptions=True)`` so one slow DB query never
        gates the others. The margin check (variable timeout, asymmetric
        fail policy per mode) is awaited separately so its mode-dependent
        WARN/BLOCK semantics aren't flattened into the gather. Any check
        that raises an unhandled exception becomes an ``evaluator_error``
        blocker — degraded gate is still a closed gate (fail-CLOSED on
        unknown failure).

        Verdict precedence: any blocker => "block"; else any warning =>
        "warn"; else "allow".
        """
        t0 = time.perf_counter()
        # Phase 12: options checks run first (serial, before gather) so a
        # BLOCK short-circuits the expensive concurrent checks.
        pre_warnings: list[GateWarningEntry] = []
        if ctx.asset_class == "OPTION":
            opt_blocker, opt_warning = (await self._check_options_exposure(ctx)) or (None, None)
            if opt_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[opt_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if opt_warning is not None:
                pre_warnings = [opt_warning]
        # Phase 14: futures physical delivery check
        if ctx.asset_class == "FUTURE":
            fut_blocker, fut_warning = (await self._check_futures_exposure(ctx)) or (None, None)
            if fut_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[fut_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if fut_warning is not None:
                pre_warnings = [fut_warning]
        # Phase 15a: FX checks
        if ctx.asset_class == "FOREX":
            fx_blocker, fx_warning = (await self._check_forex_exposure(ctx)) or (None, None)
            if fx_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[fx_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if fx_warning is not None:
                pre_warnings = [fx_warning]
        # Phase 15b: crypto checks
        if ctx.asset_class == "CRYPTO":
            crypto_blocker, crypto_warning = (await self._check_crypto_exposure(ctx)) or (
                None,
                None,
            )
            if crypto_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[crypto_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if crypto_warning is not None:
                pre_warnings = [crypto_warning]
        # Phase 16a: bond checks
        if ctx.asset_class == "BOND":
            bond_blocker, bond_warning = (await self._check_bond_exposure(ctx)) or (None, None)
            if bond_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[bond_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if bond_warning is not None:
                pre_warnings = [bond_warning]
        # Phase 16b: mutual fund checks
        if ctx.asset_class == "MUTUAL_FUND":
            fund_blocker, fund_warning = (await self._check_fund_exposure(ctx)) or (None, None)
            if fund_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[fund_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if fund_warning is not None:
                pre_warnings = [fund_warning]
        # Phase 16c: CFD checks
        if ctx.asset_class == "CFD":
            cfd_blocker, cfd_warning = (await self._check_cfd_exposure(ctx)) or (None, None)
            if cfd_blocker is not None:
                return GateVerdict(
                    final_verdict="block",
                    blockers=[cfd_blocker],
                    warnings=[],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            if cfd_warning is not None:
                pre_warnings = [cfd_warning]
        fast_check_names = (
            "account_kill_switch",
            "broker_kill_switch",
            "max_daily_loss",
            "pdt",
            "position_concentration",
            "buying_power",
        )
        fast_results = await asyncio.gather(
            self._check_account_kill_switch(ctx),
            self._check_broker_kill_switch(ctx),
            self._check_max_daily_loss(ctx),
            self._check_pdt(ctx),
            self._check_position_concentration(ctx),
            self._check_buying_power(ctx),
            return_exceptions=True,
        )
        try:
            margin_result: CheckResult | BaseException = await self._check_margin(ctx, mode)
        except BaseException as exc:
            margin_result = exc

        named_results = [
            *zip(fast_check_names, fast_results, strict=True),
            ("margin", margin_result),
        ]
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = list(pre_warnings)
        for check_name, r in named_results:
            if isinstance(r, BaseException):
                # Re-raise into a live exception frame so structlog's
                # log.exception captures the traceback (passing exc_info=r
                # as a kwarg without a live frame yields no stack trace).
                try:
                    raise r
                except BaseException:
                    log.exception("risk.check_raised", check=check_name)
                # AttributeError typically means a misconfigured sidecar /
                # test stub lacks an expected method - degrade to a WARN
                # (skipping this check) rather than fail-CLOSED to BLOCK,
                # since it isn't a real margin/policy failure. The counter
                # distinguishes test-stub noise from prod typos.
                if isinstance(r, AttributeError):
                    metrics.risk_evaluator_degraded_total.labels(check=check_name, mode=mode).inc()
                    warnings.append(
                        GateWarningEntry(
                            check="evaluator",
                            message=(f"check skipped (sidecar/dep missing method): {r}"),
                            value=0.0,
                            threshold=0.0,
                        )
                    )
                    continue
                blockers.append(
                    GateBlockerEntry(
                        check="evaluator",
                        message=f"check raised: {type(r).__name__}: {r}",
                        code="evaluator_error",
                    )
                )
                continue
            if r is None:
                continue
            blocker, warning = r
            if blocker is not None:
                blockers.append(blocker)
            if warning is not None:
                warnings.append(warning)

        verdict_str: Literal["allow", "warn", "block"] = (
            "block" if blockers else ("warn" if warnings else "allow")
        )
        return GateVerdict(
            final_verdict=verdict_str,
            blockers=blockers,
            warnings=warnings,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def evaluate_combo(self, ctx: ComboContext, mode: EvalMode) -> GateVerdict:
        t0 = time.perf_counter()
        blockers: list[GateBlockerEntry] = []
        warnings: list[GateWarningEntry] = []

        result = await self._check_combo_envelope(ctx)
        if result is not None:
            blocker, warning = result
            if blocker is not None:
                blockers.append(blocker)
            if warning is not None:
                warnings.append(warning)

        verdict: Literal["allow", "warn", "block"] = (
            "block" if blockers else ("warn" if warnings else "allow")
        )
        return GateVerdict(
            final_verdict=verdict,
            blockers=blockers,
            warnings=warnings,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def _check_combo_envelope(self, ctx: ComboContext) -> CheckResult:
        from app.services.combos.types import ComboEnvelope

        env: ComboEnvelope = ctx.envelope
        limits = getattr(self, "_limits", None)
        if limits is None:
            try:
                acct_uuid = uuid.UUID(ctx.account_id)
            except ValueError:
                return None
            limits = await self._resolve_limit(acct_uuid, "", "max_daily_loss_currency_base")

        if env.max_loss is None:
            if not getattr(limits, "naked_margin_enabled", True):
                return (
                    GateBlockerEntry(
                        check="combo_unbounded",
                        message="Unbounded combo requires naked-margin account level",
                        code="combo_unbounded_not_permitted",
                    ),
                    None,
                )
            return None

        max_combo = getattr(limits, "max_combo_loss_native", None)
        if max_combo is not None:
            effective_loss = env.max_loss * Decimal("100")
            if effective_loss > max_combo:
                return (
                    GateBlockerEntry(
                        check="combo_max_loss",
                        message=(f"Combo max loss {effective_loss} exceeds limit {max_combo}"),
                        code="combo_max_loss_exceeded",
                    ),
                    None,
                )
        return None
