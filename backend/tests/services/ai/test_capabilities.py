"""Phase 11a-A1: capability-map resolution tests."""

from __future__ import annotations

import pytest

from app.services.ai.capabilities import (
    AICapability,
    resolve_models,
)

pytestmark = pytest.mark.no_db


def test_capability_enum_has_eight_values() -> None:
    assert {c.value for c in AICapability} == {
        "LOCAL_ONLY",
        "LONG_CONTEXT",
        "REALTIME_SENTIMENT",
        "STRUCTURED_OUTPUT",
        "BULK_CHEAP",
        "REASONING",
        "NUMERICAL",
        "CODING",
    }


def test_local_only_excludes_cloud_models() -> None:
    capability_map = {
        "LOCAL_ONLY": [
            {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
        ],
    }
    available_providers = {"ollama-nuc", "anthropic", "ollama-heavy"}
    models = resolve_models(
        AICapability.LOCAL_ONLY,
        capability_map=capability_map,
        available_providers=available_providers,
    )
    assert [(m.provider, m.model) for m in models] == [
        ("ollama-nuc", "qwen2.5:7b"),
        ("ollama-heavy", "qwen2.5:32b"),
    ]


def test_force_local_only_overrides_capability_default() -> None:
    """CRIT-3: parser passes force_local_only=True even when capability
    is STRUCTURED_OUTPUT (which would otherwise allow cloud)."""
    capability_map = {
        "STRUCTURED_OUTPUT": [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "ollama-nuc", "model": "qwen2.5:7b"},
        ],
    }
    available_providers = {"anthropic", "ollama-nuc"}
    models = resolve_models(
        AICapability.STRUCTURED_OUTPUT,
        capability_map=capability_map,
        available_providers=available_providers,
        force_local_only=True,
    )
    assert [(m.provider, m.model) for m in models] == [("ollama-nuc", "qwen2.5:7b")]


def test_missing_provider_key_drops_entry() -> None:
    capability_map = {
        "REASONING": [
            {"provider": "anthropic", "model": "claude-opus-4-7"},
            {"provider": "ollama-heavy", "model": "qwen2.5:32b"},
        ],
    }
    available_providers = {"ollama-heavy"}  # anthropic key missing
    models = resolve_models(
        AICapability.REASONING,
        capability_map=capability_map,
        available_providers=available_providers,
    )
    assert [(m.provider, m.model) for m in models] == [("ollama-heavy", "qwen2.5:32b")]


def test_unknown_capability_returns_empty() -> None:
    models = resolve_models(
        AICapability.NUMERICAL,
        capability_map={},
        available_providers={"anthropic"},
    )
    assert models == []
