"""Seed placeholder rows in app_secrets so they show up in the admin UI.

Idempotent: only inserts rows that DON'T already exist. Existing secrets
(with real values you've entered) are left untouched.

Each placeholder value is the literal string 'REPLACE_ME' encrypted via
Fernet at the configured APP_SECRET_KEY, so the row decrypts cleanly and
the admin UI can show + replace it.

Run inside the backend container on VPS:

    ssh -p 2222 trader@88.208.197.219
    cd trading-dashboard
    docker compose cp scripts/db/seed-prod-app-secrets-placeholders.py \
        backend:/tmp/seed-secrets.py
    docker compose exec -T backend uv run python /tmp/seed-secrets.py

After it runs, every slot below appears in the admin UI with value
'REPLACE_ME' — click each row, paste the real value, save.

Slots seeded (19 rows total):
  - IBKR mTLS triplet (3)        — overwritten by provision-and-publish.ps1
  - IBKR per-label IBC creds (8) — 4 labels x (unlock_pwd_md5 + rsa_priv_pem)
  - Schwab triplet (3)           — app_key/app_secret manual, refresh_token via OAuth
  - Alpaca paper + live (4)      — api_key + api_secret per mode
  - Futu RSA priv key (1)        — 1024-bit PEM required (memory:futu_1024_rsa_key.md)
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.crypto import get_fernet
from app.models.config import AppSecret
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache

PLACEHOLDER = "REPLACE_ME"

# (namespace, key) — order matches docs/APP_CONFIG_INVENTORY.md.
SLOTS: list[tuple[str, str]] = [
    # IBKR mTLS triplet (overwritten by provision-and-publish.ps1).
    ("broker", "mtls.client_cert_pem"),
    ("broker", "mtls.client_key_pem"),
    ("broker", "mtls.ca_bundle_pem"),
    # IBKR per-label IBC creds (4 labels x 2 = 8 rows).
    ("broker", "isa-paper.unlock_pwd_md5"),
    ("broker", "isa-paper.rsa_priv_pem"),
    ("broker", "isa-live.unlock_pwd_md5"),
    ("broker", "isa-live.rsa_priv_pem"),
    ("broker", "normal-paper.unlock_pwd_md5"),
    ("broker", "normal-paper.rsa_priv_pem"),
    ("broker", "normal-live.unlock_pwd_md5"),
    ("broker", "normal-live.rsa_priv_pem"),
    # Schwab triplet.
    ("broker", "schwab.app_key"),
    ("broker", "schwab.app_secret"),
    ("broker", "schwab.refresh_token"),
    # Alpaca paper + live.
    ("broker", "alpaca-paper.api_key"),
    ("broker", "alpaca-paper.api_secret"),
    ("broker", "alpaca-live.api_key"),
    ("broker", "alpaca-live.api_secret"),
    # Futu 1024-bit RSA priv key.
    ("broker", "futu.rsa_priv_pem"),
]


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    config_cache = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    secrets_cache = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    cfg = ConfigService(session_factory, config_cache, secrets_cache, fernet)

    async with session_factory() as s:
        existing_rows = await s.execute(
            select(AppSecret.namespace, AppSecret.key).where(
                AppSecret.namespace.in_({ns for ns, _ in SLOTS})
            )
        )
        existing = {(r.namespace, r.key) for r in existing_rows}

    to_insert = [slot for slot in SLOTS if slot not in existing]
    skipped = [slot for slot in SLOTS if slot in existing]

    if skipped:
        print(f"[seed] {len(skipped)} existing rows — skipping:")
        for ns, key in sorted(skipped):
            print(f"  SKIP  {ns}/{key}")
    print(f"[seed] {len(to_insert)} placeholder rows to insert:")
    for ns, key in to_insert:
        await cfg.set_secret(ns, key, PLACEHOLDER, "str")
        print(f"  ADD   {ns}/{key}")

    print(f"[seed] done — {len(to_insert)} inserted, {len(skipped)} skipped")

    await engine.dispose()
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
