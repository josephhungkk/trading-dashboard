import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import REGISTRY

from app.services.advisor.service import AdvisorService
from app.services.advisor.types import AdvisorConfig, AdvisorMode

pytestmark = pytest.mark.no_db


def _service():
    redis = AsyncMock()
    db_factory = MagicMock()
    return AdvisorService(AsyncMock(), redis, db_factory)


@pytest.mark.asyncio
async def test_ensure_semaphore_creates_with_correct_value():
    service = _service()

    sem = await service._ensure_semaphore(
        "bot-1", AdvisorConfig(mode=AdvisorMode.VETO, max_concurrent=3)
    )

    assert sem._value == 3


@pytest.mark.asyncio
async def test_ensure_semaphore_creation_race_safe():
    service = _service()
    config = AdvisorConfig(mode=AdvisorMode.VETO, max_concurrent=2)

    sem_1, sem_2 = await asyncio.gather(
        service._ensure_semaphore("bot-1", config),
        service._ensure_semaphore("bot-1", config),
    )

    assert sem_1 is sem_2
    assert len(service._in_flight) == 1


@pytest.mark.asyncio
async def test_semaphore_resize_drain_and_swap():
    service = _service()
    await service._ensure_semaphore("bot-1", AdvisorConfig(mode=AdvisorMode.VETO, max_concurrent=3))

    await service._resize_semaphore("bot-1", old_max=3, new_max=5, timeout=2.0)

    assert service._in_flight["bot-1"]._value == 5


@pytest.mark.asyncio
async def test_semaphore_resize_deferred_on_timeout():
    service = _service()
    sem = await service._ensure_semaphore(
        "bot-1", AdvisorConfig(mode=AdvisorMode.VETO, max_concurrent=3)
    )
    await sem.acquire()
    before = REGISTRY.get_sample_value("advisor_semaphore_resize_deferred_total") or 0.0

    await service._resize_semaphore("bot-1", old_max=3, new_max=5, timeout=0.01)

    after = REGISTRY.get_sample_value("advisor_semaphore_resize_deferred_total") or 0.0
    assert after == before + 1.0
    assert service._in_flight["bot-1"] is sem
    sem.release()
