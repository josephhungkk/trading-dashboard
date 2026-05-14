"""SQLAlchemy ORM models for Phase 12 options tables."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.instruments import Instrument


class BrokerAccount(Base):
    """Minimal broker_accounts mapping for ExerciseElection relationships."""

    __tablename__ = "broker_accounts"
    __table_args__ = {"extend_existing": True}  # noqa: RUF012

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)


class OptionGreeks(Base):
    """Latest greeks snapshot for one option instrument."""

    __tablename__ = "option_greeks"

    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    delta: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    gamma: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    theta: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    vega: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    rho: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    iv: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    iv_rank: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="greeks")


class ExerciseElection(Base):
    """Exercise / lapse election submitted for an option instrument."""

    __tablename__ = "exercise_elections"
    __table_args__ = (
        CheckConstraint(
            "action IN ('EXERCISE', 'DO_NOT_EXERCISE', 'LAPSE')",
            name="exercise_elections_action_check",
        ),
        CheckConstraint(
            "status IN ('submitted', 'confirmed', 'failed')",
            name="exercise_elections_status_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False)
    jwt_subject: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("broker_accounts.id"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("instruments.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="submitted")
    broker_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    account: Mapped[BrokerAccount] = relationship("BrokerAccount")
    instrument: Mapped[Instrument] = relationship("Instrument")
