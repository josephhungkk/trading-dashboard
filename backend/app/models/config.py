"""SQLAlchemy models for app_config and app_secrets (Phase 2)."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, LargeBinary, PrimaryKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppConfig(Base):
    __tablename__ = "app_config"

    namespace: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
    value_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("namespace", "key"),
        CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_config_value_type_check",
        ),
        CheckConstraint(
            "(value_type = 'json' AND value_json IS NOT NULL AND value IS NULL)"
            " OR "
            "(value_type <> 'json' AND value IS NOT NULL AND value_json IS NULL)",
            name="app_config_value_exclusive",
        ),
        Index("ix_app_config_updated_at", "updated_at", postgresql_using="btree"),
    )


class AppSecret(Base):
    __tablename__ = "app_secrets"

    namespace: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("namespace", "key"),
        CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_secrets_value_type_check",
        ),
        Index("ix_app_secrets_updated_at", "updated_at", postgresql_using="btree"),
    )
