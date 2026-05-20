"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

import os
import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _build_connect_args() -> dict:
    base: dict = {}
    if os.getenv("TEST_DISABLE_STMT_CACHE"):
        base["statement_cache_size"] = 0
    cert_path = settings.pg_ssl_cert_path
    if cert_path:
        ctx = ssl.create_default_context(
            cafile=settings.pg_ssl_ca_path,
        )
        ctx.load_cert_chain(
            certfile=cert_path,
            keyfile=settings.pg_ssl_key_path,
        )
        ctx.check_hostname = False
        base["ssl"] = ctx
    return base


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.postgres_pool_size,
    max_overflow=settings.postgres_max_overflow,
    pool_pre_ping=True,
    connect_args=_build_connect_args(),
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
