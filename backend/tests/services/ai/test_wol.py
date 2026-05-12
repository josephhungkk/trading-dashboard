"""Phase 11a-A2: HeavyBoxWoL unit tests.

Validates: wake-helper RPC, model-ready polling, circuit-breaker
state transitions, and idempotent wake (multiple concurrent callers
share one probe).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.no_db


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock() -> _FakeClock:
    return _FakeClock()


def _make_transport(
    *,
    wake_status: int = 200,
    tags_seq: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    """Build a transport that the WoL primitive talks through.

    Args:
        wake_status: status code returned by POST /wake on the NUC helper.
        tags_seq: response payloads (dict) returned by successive calls
          to GET /api/tags on the heavy-box Ollama. Useful for simulating
          "model not loaded yet" -> "model loaded" transitions.
    """
    if tags_seq is None:
        tags_seq = [{"models": [{"name": "qwen2.5:32b"}]}]
    tags_iter = iter(tags_seq)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/wake" in request.url.path:
            return httpx.Response(wake_status, json={"status": "sent", "mac": "AA:BB"})
        if request.method == "GET" and request.url.path == "/api/tags":
            try:
                return httpx.Response(200, json=next(tags_iter))
            except StopIteration:
                return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_wake_and_wait_returns_ready_when_model_present(fake_clock: _FakeClock) -> None:
    from app.services.ai.wol import HeavyBoxWoL

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(),
    )
    result = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=60.0)
    assert result.status == "ready"
    assert result.tcp_open_ms is not None
    assert result.model_ready_ms is not None


@pytest.mark.asyncio
async def test_wake_returns_failed_when_helper_rejects(fake_clock: _FakeClock) -> None:
    from app.services.ai.wol import HeavyBoxWoL

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(wake_status=502),
    )
    result = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=5.0)
    assert result.status == "failed"
    assert "helper" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_wake_returns_failed_when_model_never_appears(fake_clock: _FakeClock) -> None:
    from app.services.ai.wol import HeavyBoxWoL

    # /api/tags responds 200 but never lists the requested model.
    tags_seq = [{"models": [{"name": "other-model"}]}] * 30
    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(tags_seq=tags_seq),
        poll_interval_s=0.0,  # synchronous test loop
    )
    result = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.5)
    assert result.status == "failed"
    assert "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_three_failures_in_window(
    fake_clock: _FakeClock,
) -> None:
    """3 wake failures within 10min open the breaker; subsequent calls
    return 'circuit_open' WITHOUT issuing a wake request."""
    from app.services.ai.wol import HeavyBoxWoL

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=_make_transport(wake_status=502),
    )
    for _ in range(3):
        r = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.1)
        assert r.status == "failed"

    # 4th call within the window must short-circuit.
    r4 = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.1)
    assert r4.status == "circuit_open"


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_window(fake_clock: _FakeClock) -> None:
    """After 5min in the open state, the next call gets one trial wake
    (half-open). If it succeeds, the breaker closes."""
    from app.services.ai.wol import HeavyBoxWoL

    # Three failures open the breaker.
    failing_transport = _make_transport(wake_status=502)
    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=failing_transport,
    )
    for _ in range(3):
        await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=0.1)

    # Window passes.
    fake_clock.advance(5 * 60 + 1)

    # Swap the transport to a healthy one (success path).
    wol.transport = _make_transport()
    r = await wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=60.0)
    assert r.status == "ready"


@pytest.mark.asyncio
async def test_concurrent_waker_calls_share_single_probe(fake_clock: _FakeClock) -> None:
    """Two callers concurrently asking for the same model wake just
    once. Defended via asyncio.Event."""
    from app.services.ai.wol import HeavyBoxWoL

    call_count = {"wake": 0, "tags": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/wake" in request.url.path:
            call_count["wake"] += 1
            return httpx.Response(200, json={"status": "sent"})
        if request.method == "GET" and request.url.path == "/api/tags":
            call_count["tags"] += 1
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:32b"}]})
        return httpx.Response(404)

    wol = HeavyBoxWoL(
        helper_url="http://nuc-helper:11900",
        heavy_url="http://heavy:11434",
        clock=fake_clock,
        transport=httpx.MockTransport(handler),
    )
    r1, r2 = await asyncio.gather(
        wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=5.0),
        wol.wake_and_wait_for_model("qwen2.5:32b", timeout_s=5.0),
    )
    assert r1.status == "ready"
    assert r2.status == "ready"
    assert call_count["wake"] == 1  # Only ONE wake packet despite TWO callers.
