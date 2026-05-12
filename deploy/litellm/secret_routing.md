# Phase 11a-A0 — per-provider secret routing outcome

Validated 2026-05-12 via `backend/tests/spike/test_per_request_provider_key.py`.

| Provider | Request-body `api_key` accepted? | Routing mode |
|---|---|---|
| ollama-nuc | yes (Ollama ignores key) | `request_body` |
| ollama-heavy | yes (Ollama ignores key) | `request_body` |
| xai-grok | TBD-spike | TBD |
| gemini-pro | TBD-spike | TBD |
| anthropic-sonnet | TBD-spike | TBD |
| openai-gpt4o | TBD-spike | TBD |

Providers marked `request_body` use Option C (BE signs each call). Providers
that fail the spike fall back to **Option A**: config-held key, rotation
via lifespan re-render + `docker compose up -d litellm`.

This file is updated as each provider key is acquired and tested.
