"""CRIT-code-4: _acquire_trading_stream counter must not inflate on rejected acquires.

Prior to the fix, _acquire_trading_stream called context.abort() then fell
through to increment _TRADING_STREAM_COUNTS. Rejected callers inflated the
counter, permanently blocking future subscriptions after the cap appeared to
be exhausted (even though real subscribers had disconnected and drained the
count back down).
"""

from __future__ import annotations

import os

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

import sidecar_alpaca.handlers as handlers_mod
from sidecar_alpaca.handlers import _TRADING_STREAM_CAP, AlpacaServicer

pytestmark = [pytest.mark.unit]


class _SilentAbortContext:
    """Records aborts without raising — matches grpc.aio's real behaviour."""

    def __init__(self) -> None:
        self.aborted: list[tuple[grpc.StatusCode, str]] = []

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.aborted.append((code, details))


@pytest.fixture(autouse=True)
def _clear_stream_counts() -> None:  # type: ignore[return]
    """Ensure global state is clean before and after each test."""
    handlers_mod._TRADING_STREAM_COUNTS.clear()
    yield
    handlers_mod._TRADING_STREAM_COUNTS.clear()


async def _servicer() -> AlpacaServicer:
    from sidecar_alpaca.auth import AuthCache

    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    return AlpacaServicer(auth_cache=auth)


@pytest.mark.asyncio
async def test_rejected_acquire_does_not_increment_counter() -> None:
    """RESOURCE_EXHAUSTED abort must leave the counter unchanged."""
    servicer = await _servicer()
    account_id = "test-account-counter"

    # Fill the counter to the cap.
    handlers_mod._TRADING_STREAM_COUNTS[account_id] = _TRADING_STREAM_CAP

    ctx = _SilentAbortContext()
    await servicer._acquire_trading_stream(account_id, ctx)  # type: ignore[arg-type]

    assert any(
        code == grpc.StatusCode.RESOURCE_EXHAUSTED for code, _ in ctx.aborted
    ), f"expected RESOURCE_EXHAUSTED abort, got {ctx.aborted}"

    # Counter must be unchanged — rejected acquire must not increment.
    assert handlers_mod._TRADING_STREAM_COUNTS[account_id] == _TRADING_STREAM_CAP, (
        f"counter inflated from {_TRADING_STREAM_CAP} to "
        f"{handlers_mod._TRADING_STREAM_COUNTS[account_id]}"
    )


@pytest.mark.asyncio
async def test_accepted_acquire_increments_counter() -> None:
    """Successful acquire (below cap) must increment the counter by 1."""
    servicer = await _servicer()
    account_id = "test-account-accept"
    initial = _TRADING_STREAM_CAP - 1
    handlers_mod._TRADING_STREAM_COUNTS[account_id] = initial

    ctx = _SilentAbortContext()
    await servicer._acquire_trading_stream(account_id, ctx)  # type: ignore[arg-type]

    assert ctx.aborted == [], f"unexpected abort: {ctx.aborted}"
    assert handlers_mod._TRADING_STREAM_COUNTS[account_id] == initial + 1


@pytest.mark.asyncio
async def test_multiple_rejected_acquires_do_not_inflate() -> None:
    """N rejected acquires must leave the counter at cap, not cap+N."""
    servicer = await _servicer()
    account_id = "test-account-multi-reject"
    handlers_mod._TRADING_STREAM_COUNTS[account_id] = _TRADING_STREAM_CAP

    for _ in range(5):
        ctx = _SilentAbortContext()
        await servicer._acquire_trading_stream(account_id, ctx)  # type: ignore[arg-type]

    assert handlers_mod._TRADING_STREAM_COUNTS[account_id] == _TRADING_STREAM_CAP, (
        "counter inflated after multiple rejected acquires: "
        f"{handlers_mod._TRADING_STREAM_COUNTS[account_id]}"
    )


@pytest.mark.asyncio
async def test_release_after_accepted_acquire_decrements_correctly() -> None:
    """release_trading_stream must undo exactly one accepted acquire."""
    await _servicer()
    account_id = "test-account-release"
    handlers_mod._TRADING_STREAM_COUNTS[account_id] = 2

    AlpacaServicer._release_trading_stream(account_id)

    assert handlers_mod._TRADING_STREAM_COUNTS.get(account_id) == 1
