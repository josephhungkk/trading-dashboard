"""HTTP client mirror of the slice of ConfigService the refresher needs.

Auth: CF-Access service-token headers per CLAUDE.md "CI bypass" pattern.
URL: public dashboard URL — service-token→JWT conversion happens at the CF edge.
Namespace is fixed to "broker" with key prefix "schwab.".
"""
from __future__ import annotations

import os

import httpx


class BackendAdminClient:
    NAMESPACE = "broker"
    KEY_PREFIX = "schwab."

    def __init__(
        self,
        *,
        backend_url: str,
        cf_access_client_id: str,
        cf_access_client_secret: str,
    ) -> None:
        self._url = backend_url.rstrip("/")
        self._headers = {
            "CF-Access-Client-Id": cf_access_client_id,
            "CF-Access-Client-Secret": cf_access_client_secret,
        }

    @classmethod
    def from_env(cls) -> "BackendAdminClient":
        return cls(
            backend_url=os.environ.get(
                "BACKEND_ADMIN_URL", "https://dashboard.kiusinghung.com"
            ),
            cf_access_client_id=os.environ["CF_ACCESS_CLIENT_ID"],
            cf_access_client_secret=os.environ["CF_ACCESS_CLIENT_SECRET"],
        )

    def _key(self, suffix: str) -> str:
        return f"{self.KEY_PREFIX}{suffix}"

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        url = f"{self._url}/api/admin/config/{self.NAMESPACE}/{self._key(key)}"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.get(url)
            if resp.status_code == 404:
                return default
            resp.raise_for_status()
            return str(resp.json()["value"])

    async def set_config(self, key: str, value: str, *, value_type: str = "str") -> None:
        url = f"{self._url}/api/admin/config/{self.NAMESPACE}/{self._key(key)}"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.put(url, json={"value": value, "value_type": value_type})
            resp.raise_for_status()

    async def reveal_secret(self, key: str) -> str:
        url = f"{self._url}/api/admin/secrets/{self.NAMESPACE}/{self._key(key)}/reveal"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.post(url)
            resp.raise_for_status()
            return str(resp.json()["value"])

    async def push_tier2_metric(self, last_run_seconds: float) -> None:
        """Backend translates this into SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS.set(...)."""
        url = f"{self._url}/api/admin/metrics/tier2"
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as http:
            resp = await http.post(url, json={"last_run_seconds": last_run_seconds})
            resp.raise_for_status()
