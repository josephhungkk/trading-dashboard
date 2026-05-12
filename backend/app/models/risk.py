"""SQLAlchemy ORM for Phase 10a risk engine tables.

Spec: docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md §3.

NOTE: account_id and order_id reference broker_accounts(id) and orders(id)
at the DB layer (enforced by Alembic 0036). The orders ORM model lives in
``app.models.orders``; broker_accounts has no ORM model in this codebase
(Phase 4/5a uses raw SQL), so we omit ORM-level ForeignKey() args on
account_id / instrument_id to avoid metadata resolution errors at import.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# All three enums are created by Alembic 0036; ORM uses create_type=False.
risk_scope_type = ENUM(
    "global",
    "broker",
    "account",
    name="risk_scope_type",
    create_type=False,
)
risk_limit_kind = ENUM(
    "max_daily_loss_currency_base",
    "max_position_concentration_pct",
    "pdt_warn_remaining",
    "min_buying_power_buffer_pct",
    name="risk_limit_kind",
    create_type=False,
)
risk_verdict = ENUM(
    "allow",
    "warn",
    "block",
    name="risk_verdict",
    create_type=False,
)


class RiskLimit(Base):
    __tablename__ = "risk_limits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope_type: Mapped[str] = mapped_column(risk_scope_type, nullable=False)
    scope_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    limit_kind: Mapped[str] = mapped_column(risk_limit_kind, nullable=False)
    limit_value: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    warn_at_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)


class RiskLimitHistory(Base):
    __tablename__ = "risk_limits_history"

    history_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    limit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scope_type: Mapped[str] = mapped_column(risk_scope_type, nullable=False)
    scope_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    limit_kind: Mapped[str] = mapped_column(risk_limit_kind, nullable=False)
    limit_value: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    warn_at_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)


class AccountKillSwitch(Base):
    __tablename__ = "account_kill_switches"
    # [Chunk-A db reviewer M4] mirror DB CHECK constraints in __table_args__
    # so Base.metadata.create_all (used by tests against fresh PG) keeps the
    # invariants Alembic 0036 enforces.
    __table_args__ = (
        CheckConstraint("length(reason) <= 1000", name="account_kill_switches_reason_len_check"),
        CheckConstraint(
            "(is_enabled IS FALSE) OR (enabled_at IS NOT NULL AND enabled_by IS NOT NULL)",
            name="account_kill_switches_enabled_metadata_check",
        ),
    )

    # FK to broker_accounts(id) is at DB layer only — see module docstring.
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    enabled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    enabled_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AccountKillSwitchHistory(Base):
    __tablename__ = "account_kill_switches_history"

    history_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)


class RiskDecision(Base):
    __tablename__ = "risk_decisions"
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="risk_decisions_side_check"),
        CheckConstraint("latency_ms >= 0", name="risk_decisions_latency_check"),
        CheckConstraint(
            "attempt_kind IN ('preview', 'place_order', 'modify_order')",
            name="risk_decisions_attempt_kind_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # FK to broker_accounts at DB layer only.
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    time_in_force: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(risk_verdict, nullable=False)
    blockers: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    warnings: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_kind: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    # FK to orders.id (UUID) at DB layer — populated post-dispatch [M5].
    order_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
