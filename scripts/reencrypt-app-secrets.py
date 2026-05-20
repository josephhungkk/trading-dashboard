#!/usr/bin/env python3
"""
Re-encrypt all app_secrets rows after APP_SECRET_KEY rotation.

Usage:
    APP_SECRET_KEY=<new_key> APP_SECRET_KEY_OLD=<old_key> python scripts/reencrypt-app-secrets.py

Safe to re-run: MultiFernet handles decrypt-with-either-key until cleanup.
"""
from __future__ import annotations

import asyncio
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


async def reencrypt(session: AsyncSession, new_fernet: Fernet, old_fernet: Fernet) -> int:
    result = await session.execute(
        text("SELECT id, namespace, key, value FROM app_secrets")
    )
    rows = result.fetchall()
    count = 0
    for row in rows:
        raw = row.value
        if isinstance(raw, str):
            raw = raw.encode()
        try:
            plaintext = old_fernet.decrypt(raw)
        except InvalidToken:
            try:
                plaintext = new_fernet.decrypt(raw)
                print(f"  {row.namespace}/{row.key}: already using new key — skipping")
                continue
            except InvalidToken:
                print(f"  ERROR: {row.namespace}/{row.key}: cannot decrypt with either key", file=sys.stderr)
                continue
        new_value = new_fernet.encrypt(plaintext).decode()
        await session.execute(
            text("UPDATE app_secrets SET value = :v WHERE id = :id"),
            {"v": new_value, "id": row.id},
        )
        count += 1
        print(f"  re-encrypted {row.namespace}/{row.key}")
    return count


async def main() -> None:
    new_key = os.environ.get("APP_SECRET_KEY")
    old_key = os.environ.get("APP_SECRET_KEY_OLD")
    db_url = os.environ.get("DATABASE_URL")

    if not new_key or not old_key or not db_url:
        print("Usage: APP_SECRET_KEY=<new> APP_SECRET_KEY_OLD=<old> DATABASE_URL=<url> python reencrypt-app-secrets.py")
        sys.exit(1)

    new_fernet = Fernet(new_key.encode())
    old_fernet = Fernet(old_key.encode())

    engine = create_async_engine(db_url, pool_size=1)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            count = await reencrypt(session, new_fernet, old_fernet)
    print(f"\nDone. Re-encrypted {count} row(s).")
    await engine.dispose()


asyncio.run(main())
