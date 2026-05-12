"""Phase 11a-B: typed exceptions for services/ai/ (LOW-2 — Error suffix
consistent with Phase 10a RiskGateBlockedError style)."""

from __future__ import annotations


class AIError(Exception):
    """Base for all services/ai/ errors."""


class LocalModelsUnavailableError(AIError):
    """LOCAL_ONLY request but no local models reachable (CRIT-3 fail path)."""


class AIProxyUnavailableError(AIError):
    """LiteLLM proxy unreachable after retries."""


class StructuredOutputFailedError(AIError):
    """Model returned non-JSON-schema-conformant output twice in a row."""

    def __init__(self, raw_text: str, schema_error: str) -> None:
        super().__init__(f"structured output failed: {schema_error}")
        self.raw_text = raw_text
        self.schema_error = schema_error


class AITimeoutError(AIError):
    """Request exceeded the configured timeout window."""


class AIToolCallingNotSupportedError(AIError):
    """HIGH-4 forward-compat: tools param present but v0.11.0 rejects it."""
