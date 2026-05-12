"""Phase 11a-A1: AICapability enum + resolve_models() pure function.

Each consumer asks for completion by capability rather than by exact
model. The router consults app_config:ai_router to map capability →
ordered model list, then walks it with LOCAL_ONLY filter applied and
missing-provider-key entries removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Providers whose endpoint sits inside the WG/LAN — used by LOCAL_ONLY
# privacy floor. Centralising the membership in one constant means the
# router cannot accidentally route a LOCAL_ONLY request to a cloud
# provider by misclassifying.
LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama-nuc", "ollama-heavy"})


class AICapability(StrEnum):
    """Capability tags consumers attach to a CompletionRequest."""

    LOCAL_ONLY = "LOCAL_ONLY"
    LONG_CONTEXT = "LONG_CONTEXT"
    REALTIME_SENTIMENT = "REALTIME_SENTIMENT"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    BULK_CHEAP = "BULK_CHEAP"
    REASONING = "REASONING"
    NUMERICAL = "NUMERICAL"
    CODING = "CODING"


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str


def resolve_models(
    capability: AICapability,
    *,
    capability_map: dict[str, list[dict[str, str]]],
    available_providers: set[str] | frozenset[str],
    force_local_only: bool = False,
) -> list[ResolvedModel]:
    """Return the ordered fallback chain for a capability.

    Args:
        capability: tag from the consumer.
        capability_map: from app_config:ai_router; each value is an
          ordered list of ``{"provider": str, "model": str}`` entries.
        available_providers: set of providers whose api_key is configured.
        force_local_only: CRIT-3 — parser sets this regardless of the
          capability so the rule-NL stays inside the WG.

    Returns:
        Empty list if no entries survive both filters.
    """
    entries = capability_map.get(capability.value, [])
    out: list[ResolvedModel] = []
    enforce_local = force_local_only or capability is AICapability.LOCAL_ONLY
    for entry in entries:
        provider = entry["provider"]
        model = entry["model"]
        if provider not in available_providers:
            continue
        if enforce_local and provider not in LOCAL_PROVIDERS:
            continue
        out.append(ResolvedModel(provider=provider, model=model))
    return out
