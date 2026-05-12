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


def test_default_capability_map_covers_every_capability() -> None:
    """The shipped default must have at least one entry per AICapability
    value. Missing entries would silently disable the capability for
    operators who haven't customised the config namespace."""
    from app.services.config_defaults import DEFAULT_AI_ROUTER_CAPABILITY_MAP

    enum_values = {c.value for c in AICapability}
    map_keys = set(DEFAULT_AI_ROUTER_CAPABILITY_MAP.keys())
    missing = enum_values - map_keys
    assert not missing, f"capabilities without default: {missing}"


def test_default_capability_map_entries_well_formed() -> None:
    """Every entry must have provider + model keys; entries with extra
    keys are rejected to keep the schema tight."""
    from app.services.config_defaults import DEFAULT_AI_ROUTER_CAPABILITY_MAP

    required = {"provider", "model"}
    for capability, entries in DEFAULT_AI_ROUTER_CAPABILITY_MAP.items():
        assert entries, f"{capability}: empty fallback chain"
        for i, entry in enumerate(entries):
            assert set(entry.keys()) == required, f"{capability}[{i}] has wrong keys: {entry}"
            assert entry["provider"], f"{capability}[{i}] empty provider"
            assert entry["model"], f"{capability}[{i}] empty model"


def test_default_capability_map_local_only_uses_only_local_providers() -> None:
    """CRIT-3 defence-in-depth: even the default config must not list a
    cloud provider under LOCAL_ONLY. Spec invariant."""
    from app.services.ai.capabilities import LOCAL_PROVIDERS
    from app.services.config_defaults import DEFAULT_AI_ROUTER_CAPABILITY_MAP

    for entry in DEFAULT_AI_ROUTER_CAPABILITY_MAP["LOCAL_ONLY"]:
        assert entry["provider"] in LOCAL_PROVIDERS, (
            f"LOCAL_ONLY default lists non-local provider {entry['provider']!r}"
        )
