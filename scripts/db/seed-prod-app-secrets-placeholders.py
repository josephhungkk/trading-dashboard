"""Seed placeholder rows in app_secrets so they show up in the admin UI.

Idempotent: only inserts rows that DON'T already exist. Existing secrets
(with real values you've entered) are left untouched.

Each placeholder value is the literal string 'REPLACE_ME' encrypted via
Fernet at the configured APP_SECRET_KEY, so the row decrypts cleanly and
the admin UI can show + replace it.

Run inside the backend container on VPS:

    docker compose exec -T -w /app backend bash -c \\
      'PYTHONPATH=/app uv run python /tmp/seed-secrets.py'
    # (copy this file to /tmp/seed-secrets.py first via docker cp)

After it runs, every slot below appears in the admin UI with value
'REPLACE_ME' — click each row, paste the real value, save.

Slots seeded (12 rows total):
  - IBKR mTLS triplet (3)   — overwritten by provision-and-publish.ps1
  - Schwab triplet (3)      — app_key/app_secret manual, refresh_token via OAuth
  - Alpaca paper + live (4) — api_key + api_secret per mode (dotted schema)
  - Futu (2)                — rsa_priv_pem (1024-bit) + unlock_pwd_md5

Note: IBKR per-label rows (isa-paper.*, isa-live.*, normal-*.*)  are NOT seeded.
BrokerConfigurer.targets excludes IBKR labels — they connect via mTLS transport
only and never go through configure(). See docs/APP_CONFIG_INVENTORY.md.
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
    # Schwab triplet.
    ("broker", "schwab.app_key"),
    ("broker", "schwab.app_secret"),
    ("broker", "schwab.refresh_token"),
    # Alpaca paper + live. Schema is dotted (alpaca.<mode>.api_*) NOT
    # hyphenated (alpaca-<mode>.api_*); broker_registry_factory.py reads
    # `alpaca.{mode}.api_key` as the legacy fallback. Hyphenated form
    # silently fails the Configure call.
    ("broker", "alpaca.paper.api_key"),
    ("broker", "alpaca.paper.api_secret"),
    ("broker", "alpaca.live.api_key"),
    ("broker", "alpaca.live.api_secret"),
    # Futu: 1024-bit RSA priv key + OpenD unlock-password MD5 hash.
    # Both required; configure() returns "creds missing" if either is absent.
    ("broker", "futu.rsa_priv_pem"),
    ("broker", "futu.unlock_pwd_md5"),
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
