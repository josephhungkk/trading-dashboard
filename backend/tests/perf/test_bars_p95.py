from __future__ import annotations

import asyncio
import json
import os
import random
import statistics
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from websockets.asyncio.client import connect

E2E_BACKEND_URL = os.getenv("E2E_BACKEND_URL", "http://localhost:8000").rstrip("/")
E2E_BACKEND_CONFIGURED = "E2E_BACKEND_URL" in os.environ
E2E_WS_URL = E2E_BACKEND_URL.replace("http://", "ws://").replace("https://", "wss://")
E2E_JWT = os.getenv("E2E_JWT", "test-bypass")
ACTIVE_CANONICAL_IDS = [
    item.strip()
    for item in os.getenv("E2E_ACTIVE_CANONICAL_IDS", "AAPL.US,MSFT.US,NVDA.US").split(",")
    if item.strip()
]


def _auth_headers() -> dict[str, str]:
    return {"CF-Access-Jwt-Assertion": E2E_JWT}


def _bars_params(canonical_id: str, cursor: str | None = None) -> dict[str, str | int]:
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    params: dict[str, str | int] = {
        "canonical_id": canonical_id,
        "timeframe": "1m",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "limit": 1000,
    }
    if cursor is not None:
        params["cursor"] = cursor
    return params


async def _backend_reachable() -> bool:
    try:
        async with httpx.AsyncClient(base_url=E2E_BACKEND_URL, timeout=2.0) as client:
            response = await client.get("/health")
            return response.status_code < 500
    except httpx.HTTPError:
        return False


@pytest.mark.asyncio
async def test_bars_p95_under_100ms() -> None:
    if not await _backend_reachable():
        pytest.skip("backend is not reachable on E2E_BACKEND_URL or localhost:8000")

    durations_ms: list[float] = []
    async with httpx.AsyncClient(
        base_url=E2E_BACKEND_URL,
        headers=_auth_headers(),
        timeout=10.0,
    ) as client:
        for _ in range(100):
            canonical_id = random.choice(ACTIVE_CANONICAL_IDS)
            started_at = time.perf_counter()
            response = await client.get("/api/bars", params=_bars_params(canonical_id))
            durations_ms.append((time.perf_counter() - started_at) * 1000.0)
            if response.status_code == 503:
                pytest.skip(
                    "backend at "
                    f"{E2E_BACKEND_URL} returned 503 — broker registry not available "
                    "in this environment"
                )
            assert response.status_code == 200

    p95 = statistics.quantiles(durations_ms, n=20)[18]
    assert p95 <= 100.0, f"p95={p95:.1f}ms exceeds 100ms gate"


@pytest.mark.asyncio
@pytest.mark.skipif(not E2E_BACKEND_CONFIGURED, reason="requires E2E_BACKEND_URL")
async def test_five_year_one_minute_paginated_fetch_under_3s() -> None:
    canonical_id = ACTIVE_CANONICAL_IDS[0]
    end = datetime.now(UTC)
    start = end - timedelta(days=365 * 5)
    cursor: str | None = None
    page_count = 0
    started_at = time.perf_counter()

    async with httpx.AsyncClient(
        base_url=E2E_BACKEND_URL,
        headers=_auth_headers(),
        timeout=30.0,
    ) as client:
        while True:
            params: dict[str, str | int] = {
                "canonical_id": canonical_id,
                "timeframe": "1m",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 10000,
            }
            if cursor is not None:
                params["cursor"] = cursor

            response = await client.get("/api/bars", params=params)
            assert response.status_code == 200
            page = response.json()
            page_count += 1
            cursor = page.get("next_cursor")
            if cursor is None:
                break

    elapsed = time.perf_counter() - started_at
    assert page_count > 0
    assert elapsed <= 3.0, f"5y/1m paginated fetch took {elapsed:.2f}s"


@pytest.mark.asyncio
@pytest.mark.skipif(not E2E_BACKEND_CONFIGURED, reason="requires E2E_BACKEND_URL")
async def test_100_live_tail_subscribers_no_tick_loss_and_rss_under_256mb() -> None:
    # TODO(Task 50): fixture direct bars_1s insert + pg_notify pump and aggregator RSS metric
    # endpoint.
    expected_ticks = int(os.getenv("E2E_EXPECTED_TEST_TICKS", "10"))
    instruments = ACTIVE_CANONICAL_IDS[:50] or ["AAPL.US"]

    async def subscriber(index: int) -> int:
        canonical_id = instruments[index % len(instruments)]
        url = f"{E2E_WS_URL}/ws/bars/{canonical_id}/1s"
        received = 0
        async with connect(url, subprotocols=[f"bearer.{E2E_JWT}"]) as ws:
            await ws.send(
                json.dumps({"op": "subscribe", "canonical_id": canonical_id, "timeframe": "1s"}),
            )
            deadline = time.monotonic() + 30.0
            while received < expected_ticks and time.monotonic() < deadline:
                try:
                    raw_frame = await asyncio.wait_for(
                        ws.recv(),
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                except TimeoutError:
                    break
                frame = json.loads(raw_frame)
                if frame.get("op") == "ping":
                    await ws.send(json.dumps({"op": "pong"}))
                    continue
                if frame.get("canonical_id") == canonical_id:
                    received += 1
        return received

    async with httpx.AsyncClient(
        base_url=E2E_BACKEND_URL,
        headers=_auth_headers(),
        timeout=30.0,
    ) as client:
        # TODO(Task 50): replace test-only endpoint with compose-attached fixture that inserts
        # bars_1s rows and issues pg_notify for each canonical_id.
        pump_response = await client.post(
            "/api/test/bars/pump-live-tail",
            json={"canonical_ids": instruments, "ticks": expected_ticks},
        )
        assert pump_response.status_code == 200

        received_counts = await asyncio.gather(*(subscriber(index) for index in range(100)))
        assert all(count >= expected_ticks for count in received_counts)

        rss_response = await client.get("/api/test/bar-aggregator/rss")
        assert rss_response.status_code == 200
        rss_bytes = int(rss_response.json()["rss_bytes"])
        assert rss_bytes < 256 * 1024 * 1024
