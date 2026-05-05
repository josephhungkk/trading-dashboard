"""SQLAlchemy ORM model for broker watchlist entries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WatchlistEntry(Base):
    """Broker-native watchlist symbol observed by the quote system."""

    __tablename__ = "watchlist_entries"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    broker_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("watchlist_entries_broker_symbol_idx", "broker_id", "symbol"),)
