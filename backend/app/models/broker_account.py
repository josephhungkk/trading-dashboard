"""SQLAlchemy ORM model for broker_accounts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

broker_id_enum = ENUM(
    "ibkr",
    "futu",
    "schwab",
    "alpaca",
    name="broker_id_enum",
    create_type=False,
)
trading_mode_enum = ENUM(
    "live",
    "paper",
    name="trading_mode_enum",
    create_type=False,
)


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    broker_id: Mapped[str] = mapped_column(broker_id_enum, nullable=False)
    account_number: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(trading_mode_enum, nullable=False)
    gateway_label: Mapped[str] = mapped_column(Text, nullable=False)
    currency_base: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_via: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_nlv: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    last_nlv_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    last_nlv_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("broker_id", "account_number", name="broker_accounts_natural_uq"),
    )
