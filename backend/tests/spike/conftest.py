"""Phase 11a-A0 spike: validate LiteLLM accepts request-body api_key
per provider so Option C secret flow is viable. If any provider fails,
that provider falls back to Option A (config-held key) — outcome
recorded in deploy/litellm/secret_routing.md.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterator

import httpx
import pytest

LITELLM_URL = os.environ.get("SPIKE_LITELLM_URL", "http://localhost:4000")
LITELLM_MASTER_KEY = os.environ.get("SPIKE_LITELLM_MASTER_KEY", "sk-spike-master")


def _is_litellm_up(timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", 4000), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture(scope="session")
def litellm_url() -> str:
    if not _is_litellm_up():
        pytest.skip(
            "LiteLLM not reachable on localhost:4000 — start it via "
            "`docker run --rm -p 4000:4000 "
            "-v $PWD/deploy/litellm/config.spike.yaml:/app/config.yaml "
            "-e LITELLM_MASTER_KEY=sk-spike-master "
            "ghcr.io/berriai/litellm:main-latest --config /app/config.yaml`"
        )
    return LITELLM_URL


@pytest.fixture(scope="session")
def litellm_client(litellm_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=litellm_url,
        headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"},
        timeout=60.0,
    ) as client:
        yield client
