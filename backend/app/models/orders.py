"""SQLAlchemy models for orders and order_events."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, String, Text, func

# NOTE: orders.account_id and order_events.account_id reference
# broker_accounts(id) at the DB layer (enforced by alembic 0004), but no
# BrokerAccount(Base) ORM model exists in this codebase — Phase 4/5a uses
# raw text() SQL for that table. We omit the ORM-level ForeignKey() arg
# on those columns to avoid sqlalchemy.exc.NoReferencedTableError at
# metadata resolution time. The DB-level constraint is the source of
# truth.
from sqlalchemy.dialects.postgresql import ENUM, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import uuid7
from app.models.base import Base

order_side_enum = ENUM("BUY", "SELL", name="order_side_enum", create_type=False)
order_type_enum = ENUM("MARKET", "LIMIT", "STOP", name="order_type_enum", create_type=False)
order_tif_enum = ENUM("DAY", "GTC", name="order_tif_enum", create_type=False)
order_status_enum = ENUM(
    "pending_submit",
    "submitted",
    "partial",
    "filled",
    "cancelled",
    "rejected",
    "expired",
    "inactive",
    name="order_status_enum",
    create_type=False,
)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    client_order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conid: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(order_side_enum, nullable=False)
    order_type: Mapped[str] = mapped_column(order_type_enum, nullable=False)
    tif: Mapped[str] = mapped_column(order_tif_enum, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(
        order_status_enum, nullable=False, server_default="pending_submit"
    )
    filled_qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default="0")
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    notional: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    notional_filled: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default="0"
    )
    position_effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_event_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    events: Mapped[list[OrderEvent]] = relationship(
        "OrderEvent",
        back_populates="order",
        lazy="selectin",
        order_by="desc(OrderEvent.broker_event_at)",
    )

    __table_args__ = (
        CheckConstraint(
            "(order_type = 'MARKET' AND limit_price IS NULL AND stop_price IS NULL) OR "
            "(order_type = 'LIMIT' AND limit_price IS NOT NULL AND stop_price IS NULL) OR "
            "(order_type = 'STOP' AND limit_price IS NULL AND stop_price IS NOT NULL)"
        ),
        CheckConstraint("filled_qty >= 0 AND filled_qty <= qty"),
        CheckConstraint("qty > 0"),
        Index("uq_orders_account_client_order_id", "account_id", "client_order_id", unique=True),
        Index(
            "uq_orders_account_broker_order_id",
            "account_id",
            "broker_order_id",
            unique=True,
            postgresql_where=broker_order_id.is_not(None),
        ),
        Index(
            "ix_orders_account_status",
            "account_id",
            "status",
            postgresql_where=status.in_(("pending_submit", "submitted", "partial")),
        ),
        Index("ix_orders_account_created", "account_id", created_at.desc()),
        Index(
            "ix_orders_pending_submit_watchdog",
            "created_at",
            postgresql_where=status == "pending_submit",
        ),
    )

    def __repr__(self) -> str:
        return f"Order(id={self.id}, symbol={self.symbol!r}, side={self.side}, qty={self.qty})"


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(order_status_enum, nullable=False)
    filled_qty: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    broker_event_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    raw_payload: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)

    order: Mapped[Order | None] = relationship("Order", back_populates="events")

    __table_args__ = (
        Index("ix_order_events_order_id", "order_id", broker_event_at.desc()),
        Index("ix_order_events_account", "account_id", broker_event_at.desc()),
    )

    def __repr__(self) -> str:
        return (
            f"OrderEvent(id={self.id}, status={self.status}, "
            f"broker_event_at={self.broker_event_at})"
        )
