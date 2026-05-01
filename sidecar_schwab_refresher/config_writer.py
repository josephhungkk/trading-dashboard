"""POST captured auth code to backend admin OAuth callback."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


async def post_oauth_callback(
    *,
    backend_url: str,
    code: str,
    state: str,
    cf_headers: dict[str, str],
    max_retries: int = 3,
) -> dict[str, Any]:
    """POST /api/admin/brokers/schwab/oauth-callback?code=&state=&actor=tier2.

    cf_headers must contain CF-Access-Client-Id + CF-Access-Client-Secret.
    Retries on 5xx with exponential backoff.
    """
    url = f"{backend_url}/api/admin/brokers/schwab/oauth-callback"
    params = {"code": code, "state": state, "actor": "tier2"}
    async with httpx.AsyncClient(timeout=30.0, headers=cf_headers) as http:
        for attempt in range(max_retries + 1):
            try:
                resp = await http.post(url, params=params)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code >= 500 and attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
            except httpx.HTTPError:
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("post_oauth_callback: retries exhausted")
