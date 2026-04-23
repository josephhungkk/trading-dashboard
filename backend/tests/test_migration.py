"""Alembic migration round-trip + CHECK constraint enforcement."""

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.config import settings


def _sync_url() -> str:
    return settings.database_url.replace("+asyncpg", "")


def test_upgrade_head_creates_tables():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _sync_url())
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # idempotent second call must not fail


@pytest.mark.asyncio
async def test_both_tables_present():
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.connect() as conn:
            res = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name IN ('app_config','app_secrets')"
                )
            )
            tables = {r[0] for r in res.all()}
        assert tables == {"app_config", "app_secrets"}
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_check_constraint_value_exclusive():
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        "INSERT INTO app_config (namespace, key, value, value_json, value_type) "
                        "VALUES ('x', 'y', 'both', '{}'::jsonb, 'str')"
                    )
                )
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_check_constraint_value_type_enum():
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        "INSERT INTO app_config (namespace, key, value, value_type) "
                        "VALUES ('x', 'y', 'z', 'FLOAT')"
                    )
                )
    finally:
        await eng.dispose()
