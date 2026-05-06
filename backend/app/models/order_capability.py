"""SQLAlchemy models for broker order capability tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderType(Base):
    __tablename__ = "order_types"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class TimeInForce(Base):
    __tablename__ = "time_in_force"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    requires_expiry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class BrokerOrderCapability(Base):
    __tablename__ = "broker_order_capability"

    broker_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_type: Mapped[str] = mapped_column(
        String(32), ForeignKey("order_types.code", ondelete="RESTRICT"), primary_key=True
    )
    time_in_force: Mapped[str] = mapped_column(
        String(16), ForeignKey("time_in_force.code", ondelete="RESTRICT"), primary_key=True
    )
    is_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            r"notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256",
            name="broker_order_capability_notes_printable_ascii",
        ),
        CheckConstraint(
            "broker_id IN ('ibkr', 'futu', 'schwab', 'alpaca')",
            name="broker_order_capability_broker_id_enum",
        ),
        Index(
            "ix_broker_order_capability_supported",
            "broker_id",
            postgresql_where=text("is_supported = TRUE"),
        ),
    )
