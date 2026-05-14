"""Interactive script: complete the Schwab initial OAuth flow and seed tokens into app_secrets.

Runs on the host (WSL/NUC), NOT inside Docker. Does NOT use schwabdev — exchanges
the authorization code directly via requests so it works in headless/WSL environments.

Flow:
  1. Prints the Schwab authorization URL — open it in your browser
  2. After Schwab redirects, CF Access will intercept (302 to cloudflareaccess.com) —
     that's fine. Copy the FULL URL from your address bar (the CF Access redirect URL
     contains the Schwab code embedded in its redirect_url / meta parameters).
  3. Paste the full URL here. The script extracts the code, exchanges it, and seeds
     access_token + refresh_token into app_secrets.

Usage (from anywhere inside /home/joseph/dashboard):

    uv run python scripts/db/schwab-initial-oauth.py

Requirements:
    - uv environment with requests, sqlalchemy, asyncpg, redis, cryptography (backend venv)
    - DATABASE_URL and APP_SECRET_KEY in backend/.env (reads them automatically)
    - Redis running (for ConfigCache invalidation publish)
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Step 0: resolve backend root and load .env
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent / "backend"
ENV_FILE = BACKEND_DIR / ".env"

if not ENV_FILE.exists():
    print(f"ERROR: {ENV_FILE} not found", file=sys.stderr)
    sys.exit(1)

for line in ENV_FILE.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip())

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Credentials — read from app_secrets (never hardcoded)
# ---------------------------------------------------------------------------
CALLBACK_URL = "https://dashboard.kiusinghung.com/api/oauth/schwab/callback"
TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"


async def _load_app_key_secret() -> tuple[str, str]:
    from app.core.config import settings
    from app.core.crypto import get_fernet
    from app.core.db import engine
    from app.services.config import ConfigService
    from app.services.config_cache import ConfigCache
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    cc = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    sc = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    cfg = ConfigService(sf, cc, sc, fernet)
    app_key = await cfg.reveal_secret("broker", "schwab.app_key")
    app_secret = await cfg.reveal_secret("broker", "schwab.app_secret")
    await redis.aclose()
    await engine.dispose()
    if not app_key or not app_secret:
        print("ERROR: schwab.app_key / schwab.app_secret not in app_secrets", file=sys.stderr)
        sys.exit(1)
    return app_key, app_secret


APP_KEY, APP_SECRET = asyncio.run(_load_app_key_secret())

# ---------------------------------------------------------------------------
# Step 1: prompt user
# ---------------------------------------------------------------------------
auth_url = (
    "https://api.schwabapi.com/v1/oauth/authorize"
    f"?response_type=code"
    f"&client_id={APP_KEY}"
    f"&redirect_uri={urllib.parse.quote(CALLBACK_URL, safe='')}"
)

print("=" * 60)
print("Schwab initial OAuth — token seed script")
print("=" * 60)
print()
print("1. Open this URL in your browser:")
print()
print(f"   {auth_url}")
print()
print("2. Log in to Schwab and approve access.")
print()
print("3. Schwab will redirect to your callback URL.")
print("   CF Access will intercept it — that's OK.")
print("   Copy the FULL URL from your address bar")
print("   (starts with https://kiusinghung.cloudflareaccess.com/... OR")
print("    https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=...)")
print()

pasted = input("Paste the full URL here: ").strip()
if not pasted:
    print("ERROR: no URL pasted", file=sys.stderr)
    sys.exit(1)


def _extract_code(url: str) -> str:
    """Extract the Schwab authorization code from any of the redirect shapes."""
    parsed = urllib.parse.urlparse(url)

    # Shape A: direct callback URL — ?code=...&session=...
    if "code=" in url and "cloudflareaccess.com" not in url:
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            return urllib.parse.unquote(qs["code"][0])

    # Shape B: CF Access redirect — code is in redirect_url query param
    outer_qs = urllib.parse.parse_qs(parsed.query)
    for param in ("redirect_url", "redirect_uri"):
        if param in outer_qs:
            inner = urllib.parse.unquote(outer_qs[param][0])
            inner_qs = urllib.parse.parse_qs(urllib.parse.urlparse(inner).query)
            if "code" in inner_qs:
                return urllib.parse.unquote(inner_qs["code"][0])

    # Shape C: code embedded in JWT meta claim (base64 redirect_url field)
    if "meta=" in url:
        for param in ("redirect_url",):
            meta_qs = urllib.parse.parse_qs(parsed.query)
            if "meta" in meta_qs:
                # JWT payload is middle segment
                try:
                    payload_b64 = meta_qs["meta"][0].split(".")[1]
                    # Fix padding
                    payload_b64 += "=" * (-len(payload_b64) % 4)
                    import json
                    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                    redirect = urllib.parse.unquote(payload.get("redirect_url", ""))
                    inner_qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
                    if "code" in inner_qs:
                        return urllib.parse.unquote(inner_qs["code"][0])
                except Exception:
                    pass

    print("ERROR: could not extract code from URL.", file=sys.stderr)
    print("  URL received:", url[:200], file=sys.stderr)
    sys.exit(1)


code = _extract_code(pasted)
print(f"\nExtracted code (len={len(code)}). Exchanging with Schwab...")

# ---------------------------------------------------------------------------
# Step 2: exchange code for tokens
# ---------------------------------------------------------------------------
credentials = base64.b64encode(f"{APP_KEY}:{APP_SECRET}".encode()).decode()
resp = requests.post(
    TOKEN_ENDPOINT,
    headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK_URL,
    },
    timeout=15,
)

print(f"Schwab token endpoint → HTTP {resp.status_code}")
if resp.status_code != 200:
    print(f"ERROR: {resp.text[:400]}", file=sys.stderr)
    sys.exit(1)

tok = resp.json()
access_token: str = tok["access_token"]
refresh_token: str = tok["refresh_token"]
issued_at = datetime.now(UTC)

print(f"access_token:  len={len(access_token)}")
print(f"refresh_token: len={len(refresh_token)}")
print(f"issued_at:     {issued_at.isoformat()}")

# ---------------------------------------------------------------------------
# Step 3: seed into app_secrets
# ---------------------------------------------------------------------------
print("\nSeeding tokens into app_secrets...")


async def seed_tokens() -> None:
    from app.core.config import settings
    from app.core.crypto import get_fernet
    from app.core.db import engine
    from app.services.config import ConfigService
    from app.services.config_cache import ConfigCache
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    cc = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    sc = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    cfg = ConfigService(sf, cc, sc, fernet)

    await cfg.set_secret("broker", "schwab.access_token", access_token, "str")
    await cfg.set_secret("broker", "schwab.refresh_token", refresh_token, "str")
    await cfg.set("broker", "schwab.access_token_issued_at", issued_at.isoformat(), "str")
    await cfg.set("broker", "schwab.refresh_token_issued_at", issued_at.isoformat(), "str")

    print("  broker/schwab.access_token            -> seeded")
    print("  broker/schwab.refresh_token           -> seeded")
    print("  broker/schwab.access_token_issued_at  -> seeded")
    print("  broker/schwab.refresh_token_issued_at -> seeded")

    await redis.publish("config:invalidate:schwab", "1")
    print("  config:invalidate:schwab              -> published")

    await redis.aclose()
    await engine.dispose()


asyncio.run(seed_tokens())

print()
print("Done. Restart the schwab-sidecar to pick up the new tokens:")
print("  docker compose restart schwab-sidecar                                              (dev NUC)")
print("  ssh -p 2222 trader@88.208.197.219 'cd trading-dashboard && docker compose restart schwab-sidecar'  (prod VPS)")
