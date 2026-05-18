from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ComboOrder(Base):
    __tablename__ = "combo_orders"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "client_combo_id", name="combo_orders_account_client_combo_id_key"
        ),
        CheckConstraint(
            "strategy_type IN ('VERTICAL','CALENDAR','DIAGONAL','STRADDLE','STRANGLE')",
            name="combo_orders_strategy_type_check",
        ),
        CheckConstraint(
            "net_debit_credit_kind IN ('DEBIT','CREDIT')",
            name="combo_orders_net_debit_credit_kind_check",
        ),
        CheckConstraint("tif IN ('DAY','GTC','IOC','FOK')", name="combo_orders_tif_check"),
        CheckConstraint(
            "status IN ("
            "'pending_submit','working','filled','partially_filled',"
            "'cancelled','rejected','legged_out'"
            ")",
            name="combo_orders_status_check",
        ),
        Index("combo_orders_account_status_idx", "account_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    client_combo_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_type: Mapped[str] = mapped_column(Text, nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    underlying_canonical_id: Mapped[str] = mapped_column(Text, nullable=False)
    net_debit_credit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    net_debit_credit_kind: Mapped[str] = mapped_column(Text, nullable=False)
    max_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    max_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    break_even: Mapped[list[Decimal]] = mapped_column(
        ARRAY(Numeric(20, 8)), nullable=False, server_default="{}"
    )
    tif: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    broker_combo_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    legs: Mapped[list[OrderLeg]] = relationship(
        "OrderLeg", back_populates="combo", cascade="all, delete-orphan"
    )


class OrderLeg(Base):
    __tablename__ = "order_legs"
    __table_args__ = (
        UniqueConstraint("combo_id", "leg_idx", name="order_legs_combo_id_leg_idx_key"),
        CheckConstraint("side IN ('buy','sell')", name="order_legs_side_check"),
        CheckConstraint("ratio > 0", name="order_legs_ratio_check"),
        CheckConstraint(
            "position_effect IN ('OPEN','CLOSE')", name="order_legs_position_effect_check"
        ),
        Index("order_legs_combo_idx", "combo_id"),
        Index("order_legs_instrument_idx", "instrument_id"),
        Index(
            "order_legs_broker_idx",
            "broker_order_id",
            postgresql_where=text("broker_order_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    combo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("combo_orders.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    leg_idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    ratio: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    position_effect: Mapped[str] = mapped_column(Text, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    filled_qty: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, server_default="0")
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending_submit")

    combo: Mapped[ComboOrder] = relationship("ComboOrder", back_populates="legs")
