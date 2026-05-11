"""Mint a fresh Schwab access token from the long-lived refresh token.

Cross-platform replacement for the inline bash heredoc in
nightly-real-schwab.yml. Schwab access tokens expire after 30 min; the
7-day refresh token exchanges for a fresh one at job-start. If Schwab
rotates the refresh token (empirically rare), we emit a workflow warning
so the operator can re-sync the secret.

Reads from env:
    SCHWAB_APP_KEY
    SCHWAB_APP_SECRET
    SCHWAB_REFRESH_TOKEN

Writes:
    SCHWAB_TEST_ACCESS_TOKEN=<access> to $GITHUB_ENV (the access token
    is masked from logs before any line could echo it).

Usage:
    uv run python scripts/mint_schwab_access_token.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    try:
        app_key = os.environ["SCHWAB_APP_KEY"]
        app_secret = os.environ["SCHWAB_APP_SECRET"]
        refresh = os.environ["SCHWAB_REFRESH_TOKEN"]
    except KeyError as exc:
        print(f"ERROR: required env var missing: {exc}", file=sys.stderr)
        return 1

    basic = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": refresh}
    ).encode()
    req = urllib.request.Request(
        "https://api.schwabapi.com/v1/oauth/token",
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        snippet = e.read().decode()[:300]
        print(f"::error::Schwab token refresh failed: HTTP {e.code} {snippet}")
        return 1

    access = tok["access_token"]
    new_refresh = tok.get("refresh_token", refresh)
    print(f"::add-mask::{access}")
    if new_refresh != refresh:
        print(f"::add-mask::{new_refresh}")
        print(
            "::warning::Schwab rotated the refresh_token; SCHWAB_REFRESH_TOKEN "
            "secret + broker.schwab.refresh_token in app_secrets need re-sync."
        )

    gh_env = os.environ.get("GITHUB_ENV")
    if not gh_env:
        print("ERROR: GITHUB_ENV not set; not running inside Actions?", file=sys.stderr)
        return 1
    with open(gh_env, "a") as fh:
        fh.write(f"SCHWAB_TEST_ACCESS_TOKEN={access}\n")
    print(f"minted access_token (len={len(access)} expires_in={tok.get('expires_in')}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
