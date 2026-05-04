"""SQLAlchemy ORM models for instruments + symbol_aliases (Phase 7b.1).

Schema lives in alembic/versions/0009_phase7b_instruments_symbol_aliases.py.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AssetClass(enum.StrEnum):
    """Mirrors instrument_asset_class PG enum (alembic 0009).

    Phase 7b.1 wires STOCK/ETF/INDEX/WARRANT/CBBC; FOREX/CRYPTO land in 7b.2;
    OPTION/FUTURE/BOND/MUTUAL_FUND extend via meta JSONB in Phases 12/14/16.
    """

    STOCK = "STOCK"
    ETF = "ETF"
    INDEX = "INDEX"
    WARRANT = "WARRANT"
    CBBC = "CBBC"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"


class Instrument(Base):
    """Canonical security registry — one row per (asset_class, symbol, country).

    `meta` JSONB carries asset-class-specific extensions (option strike/expiry,
    future contract_month, fundamentals like ISIN/CUSIP/sector) without
    requiring further migrations through Phase 16.
    """

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    asset_class: Mapped[AssetClass] = mapped_column(
        SAEnum(AssetClass, name="instrument_asset_class", create_type=False),
        nullable=False,
    )
    primary_exchange: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    aliases: Mapped[list[SymbolAlias]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("instruments_asset_class_idx", "asset_class"),
        Index("instruments_exchange_idx", "primary_exchange"),
    )


class SymbolAlias(Base):
    """Per-source symbol resolution. Composite PK (source, raw_symbol)
    eliminates collision risk by construction (HIGH-5)."""

    __tablename__ = "symbol_aliases"

    source: Mapped[str] = mapped_column(Text, nullable=False)
    raw_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    instrument: Mapped[Instrument] = relationship(back_populates="aliases")

    __table_args__ = (
        PrimaryKeyConstraint("source", "raw_symbol"),
        Index("symbol_aliases_instrument_idx", "instrument_id"),
    )
