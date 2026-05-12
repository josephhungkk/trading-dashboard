"""Phase 11a-A0 spike — for each provider, verify LiteLLM accepts a
request-body api_key and forwards it. Failures here mean that provider
falls back to Option A (config-held key) in production.

Skipped unless SPIKE_PROVIDER_KEYS env is set with comma-separated
provider names that have valid keys available. Example::

    SPIKE_PROVIDER_KEYS=xai,anthropic \\
    SPIKE_KEY_xai=xai-xxxxx \\
    SPIKE_KEY_anthropic=sk-ant-xxxxx \\
    pytest backend/tests/spike/test_per_request_provider_key.py -v
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.no_db

PROVIDERS_TO_TEST = [
    p.strip() for p in os.environ.get("SPIKE_PROVIDER_KEYS", "").split(",") if p.strip()
]

PROVIDER_TO_MODEL = {
    "ollama-nuc": "ollama-nuc",
    "ollama-heavy": "ollama-heavy",
    "xai": "xai-grok",
    "gemini": "gemini-pro",
    "anthropic": "anthropic-sonnet",
    "openai": "openai-gpt4o",
}


@pytest.mark.parametrize("provider", PROVIDERS_TO_TEST or ["__skip__"])
def test_provider_accepts_request_body_api_key(litellm_client: httpx.Client, provider: str) -> None:
    if provider == "__skip__":
        pytest.skip("Set SPIKE_PROVIDER_KEYS to enable")
    if provider not in PROVIDER_TO_MODEL:
        pytest.fail(f"Unknown provider {provider!r}")

    key_env = f"SPIKE_KEY_{provider}"
    provider_key = os.environ.get(key_env)
    if not provider_key:
        pytest.skip(f"{key_env} not set")

    body = {
        "model": PROVIDER_TO_MODEL[provider],
        "messages": [{"role": "user", "content": "Reply with the single word 'ok'."}],
        "max_tokens": 10,
        "api_key": provider_key,
    }
    resp = litellm_client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200, f"{provider}: {resp.status_code} {resp.text[:200]}"
    payload = resp.json()
    assert payload.get("choices"), f"{provider}: no choices in {payload}"
