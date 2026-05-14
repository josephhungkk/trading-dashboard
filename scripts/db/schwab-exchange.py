"""Exchange a Schwab authorization code and print the tokens to stdout.

Reads app_key and app_secret from app_secrets via the backend ConfigService
(same DB as the running backend — no hardcoded credentials).

Usage:
    uv run python scripts/db/schwab-exchange.py '<full-callback-or-cf-access-url>'

The URL argument can be any of:
  - The direct callback:  https://dashboard.kiusinghung.com/api/oauth/schwab/callback?code=...
  - The CF Access redirect: https://kiusinghung.cloudflareaccess.com/cdn-cgi/access/login/...
  - Just the raw code value itself
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Resolve backend root and load .env so backend imports work
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

CALLBACK_URL = "https://dashboard.kiusinghung.com/api/oauth/schwab/callback"
TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"

if len(sys.argv) < 2:
    print("Usage: uv run python scripts/db/schwab-exchange.py '<url-or-code>'", file=sys.stderr)
    sys.exit(1)

raw = sys.argv[1]


def _extract_code(url: str) -> str:
    if not url.startswith("http"):
        return urllib.parse.unquote(url)

    parsed = urllib.parse.urlparse(url)
    outer_qs = urllib.parse.parse_qs(parsed.query)

    # Direct callback URL
    if "code" in outer_qs:
        return urllib.parse.unquote(outer_qs["code"][0])

    # CF Access redirect_url param
    for param in ("redirect_url", "redirect_uri"):
        if param in outer_qs:
            inner = urllib.parse.unquote(outer_qs[param][0])
            inner_qs = urllib.parse.parse_qs(urllib.parse.urlparse(inner).query)
            if "code" in inner_qs:
                return urllib.parse.unquote(inner_qs["code"][0])

    # CF Access JWT meta claim
    if "meta" in outer_qs:
        try:
            payload_b64 = outer_qs["meta"][0].split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            redirect = urllib.parse.unquote(payload.get("redirect_url", ""))
            inner_qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
            if "code" in inner_qs:
                return urllib.parse.unquote(inner_qs["code"][0])
        except Exception as exc:
            print(f"WARNING: JWT meta parse failed: {exc}", file=sys.stderr)

    print("ERROR: could not extract code from URL", file=sys.stderr)
    print(f"  received: {url[:300]}", file=sys.stderr)
    sys.exit(1)


async def _load_credentials() -> tuple[str, str]:
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
        print("ERROR: schwab.app_key / schwab.app_secret not found in app_secrets", file=sys.stderr)
        sys.exit(1)

    return app_key, app_secret


code = _extract_code(raw)
print(f"code (len={len(code)}): {code[:40]}...", file=sys.stderr)

app_key, app_secret = asyncio.run(_load_credentials())

credentials = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
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

print(f"HTTP {resp.status_code}", file=sys.stderr)
if resp.status_code != 200:
    print(f"ERROR: {resp.text}", file=sys.stderr)
    sys.exit(1)

tok = resp.json()
print(f"access_token  = {tok['access_token']}")
print(f"refresh_token = {tok['refresh_token']}")
