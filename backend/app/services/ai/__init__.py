"""Phase 11 — services/ai/ module.

Single boundary between consumers (alerts, telegram, trade ticket, chat,
future Phase 18 scanner + Phase 21 bot-engine) and the LiteLLM proxy.
Anyone who needs an LLM completion imports AICompletionClient from here.
"""

from __future__ import annotations

from app.services.ai.capabilities import AICapability
from app.services.ai.exceptions import (
    AIProxyUnavailableError,
    AITimeoutError,
    AIToolCallingNotSupportedError,
    LocalModelsUnavailableError,
    StructuredOutputFailedError,
)

__all__ = [
    "AICapability",
    "AIProxyUnavailableError",
    "AITimeoutError",
    "AIToolCallingNotSupportedError",
    "LocalModelsUnavailableError",
    "StructuredOutputFailedError",
]
