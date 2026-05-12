"""Phase 11a-A2 Task 21: lifespan wires HeavyBoxWoL + OllamaHealthWatcher."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_lifespan_creates_heavy_wol_singleton() -> None:
    from app.main import app

    assert app.state.heavy_wol is not None


@pytest.mark.asyncio
async def test_lifespan_creates_ollama_health_watcher() -> None:
    from app.main import app

    assert app.state.ollama_health_watcher is not None
