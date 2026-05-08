"""Tests for sidecar.probe (Phase 4 Task 15).

Plan §15.2: assert exit code matrix using in-process server returning
canned Health responses. We avoid spinning up a real gRPC channel by
injecting a `channel_factory` whose returned object emulates the bits
of grpc.aio.Channel that BrokerStub touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import grpc  # type: ignore[import-untyped]
import grpc.aio  # type: ignore[import-untyped]
import pytest

from sidecar_ibkr._generated.broker.v1 import broker_pb2
from sidecar_ibkr.probe import probe


@dataclass
class FakeUnaryUnary:
    """Stand-in for the unary-unary callable BrokerStub binds to /Health."""

    response: broker_pb2.HealthResponse | None = None
    raise_aio: BaseException | None = None
    raise_other: Exception | None = None

    async def __call__(
        self,
        request: object,
        timeout: float | None = None,  # noqa: ASYNC109 — mirrors gRPC stub signature
    ) -> broker_pb2.HealthResponse:
        del request, timeout
        if self.raise_aio is not None:
            raise self.raise_aio
        if self.raise_other is not None:
            raise self.raise_other
        assert self.response is not None
        return self.response


class FakeChannel:
    """Mimics grpc.aio.Channel's surface: unary_unary + close."""

    def __init__(self, unary: FakeUnaryUnary) -> None:
        self._unary = unary
        self.closed = False

    def unary_unary(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool = False,
    ) -> FakeUnaryUnary:
        del method, request_serializer, response_deserializer, _registered_method
        return self._unary

    def unary_stream(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool = False,
    ) -> FakeUnaryUnary:
        # BrokerStub binds the OrderEvent server-streaming RPC (added in
        # Phase 5b) at __init__ time. Probe never invokes it, but the
        # channel must expose the attribute or BrokerStub(channel) raises
        # AttributeError.
        del method, request_serializer, response_deserializer, _registered_method
        return self._unary

    def stream_stream(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool = False,
    ) -> FakeUnaryUnary:
        # Phase 9.7: BrokerStub now binds StreamQuotes (bidi, Phase 7b.1)
        # at __init__ time. Probe never invokes it, but the channel must
        # expose stream_stream or BrokerStub(channel) raises AttributeError.
        del method, request_serializer, response_deserializer, _registered_method
        return self._unary

    async def close(self) -> None:
        self.closed = True


def _make_factory(unary: FakeUnaryUnary) -> Any:
    def _factory(target: str, creds: object) -> FakeChannel:
        del target, creds
        return FakeChannel(unary)

    return _factory


# ---------- exit code matrix ----------


@pytest.mark.asyncio
async def test_probe_exits_0_when_gateway_connected() -> None:
    captured: list[str] = []
    response = broker_pb2.HealthResponse(
        label="ibgw_live_us",
        gateway_connected=True,
        gateway_version="178",
        sidecar_version="0.4.0",
    )
    code = await probe(
        label="ibgw_live_us",
        host="127.0.0.1",
        port=18001,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=captured.append,
        channel_factory=_make_factory(FakeUnaryUnary(response=response)),
    )
    assert code == 0
    assert any(line.startswith("[ok]") for line in captured)
    assert any("ver=178" in line for line in captured)


@pytest.mark.asyncio
async def test_probe_exits_1_when_gateway_disconnected() -> None:
    captured: list[str] = []
    response = broker_pb2.HealthResponse(
        label="ibgw_live_us",
        gateway_connected=False,
        gateway_version="",
        sidecar_version="0.4.0",
    )
    code = await probe(
        label="ibgw_live_us",
        host="127.0.0.1",
        port=18001,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=captured.append,
        channel_factory=_make_factory(FakeUnaryUnary(response=response)),
    )
    assert code == 1
    assert any(line.startswith("[degraded]") for line in captured)


@pytest.mark.asyncio
async def test_probe_exits_1_on_aio_rpc_error() -> None:
    """Server unreachable / TLS failure / DEADLINE_EXCEEDED → [down] + 1.

    grpc.aio.AioRpcError's real __init__ wants a metadata structure; we
    construct a minimal subclass that bypasses it so the test can raise
    the exact type the production handler catches.
    """
    captured: list[str] = []

    class StubAioRpcError(grpc.aio.AioRpcError):
        def __init__(self) -> None:
            # Skip parent init; it expects a metadata structure we don't have.
            self._code = grpc.StatusCode.DEADLINE_EXCEEDED

        def __str__(self) -> str:
            return "DEADLINE_EXCEEDED"

    code = await probe(
        label="ibgw_live_us",
        host="127.0.0.1",
        port=18001,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=captured.append,
        channel_factory=_make_factory(FakeUnaryUnary(raise_aio=StubAioRpcError())),
    )
    assert code == 1
    assert any(line.startswith("[down]") for line in captured)
    assert any("DEADLINE_EXCEEDED" in line for line in captured)


@pytest.mark.asyncio
async def test_probe_exits_1_on_generic_exception() -> None:
    """Any non-RPC exception still degrades to exit 1, never crashes."""
    captured: list[str] = []
    code = await probe(
        label="ibgw_live_us",
        host="127.0.0.1",
        port=18001,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=captured.append,
        channel_factory=_make_factory(
            FakeUnaryUnary(raise_other=RuntimeError("boom"))
        ),
    )
    assert code == 1
    assert any(line.startswith("[down]") for line in captured)


@pytest.mark.asyncio
async def test_probe_label_appears_in_output() -> None:
    """Label is the watchdog's pivot key — must echo verbatim into the line."""
    captured: list[str] = []
    response = broker_pb2.HealthResponse(
        label="ibgw_paper_hk",
        gateway_connected=True,
        gateway_version="178",
        sidecar_version="0.4.0",
    )
    await probe(
        label="ibgw_paper_hk",
        host="127.0.0.1",
        port=18004,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=captured.append,
        channel_factory=_make_factory(FakeUnaryUnary(response=response)),
    )
    assert any("label=ibgw_paper_hk" in line for line in captured)


@pytest.mark.asyncio
async def test_probe_closes_channel_on_success() -> None:
    """Probe must drain the channel after success — no FD leaks."""
    response = broker_pb2.HealthResponse(
        label="ibgw_live_us", gateway_connected=True, gateway_version="178"
    )
    holder: dict[str, FakeChannel] = {}

    def factory(target: str, creds: object) -> FakeChannel:
        del target, creds
        holder["chan"] = FakeChannel(FakeUnaryUnary(response=response))
        return holder["chan"]

    await probe(
        label="ibgw_live_us",
        host="127.0.0.1",
        port=18001,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=lambda _line: None,
        channel_factory=factory,
    )
    assert "chan" in holder and holder["chan"].closed


@pytest.mark.asyncio
async def test_probe_closes_channel_on_error() -> None:
    """Probe must drain the channel even when the RPC raises."""
    holder: dict[str, FakeChannel] = {}

    def factory(target: str, creds: object) -> FakeChannel:
        del target, creds
        holder["chan"] = FakeChannel(FakeUnaryUnary(raise_other=RuntimeError("boom")))
        return holder["chan"]

    await probe(
        label="ibgw_live_us",
        host="127.0.0.1",
        port=18001,
        client_cert=b"cert",
        client_key=b"key",
        ca=b"ca",
        print_fn=lambda _line: None,
        channel_factory=factory,
    )
    assert "chan" in holder and holder["chan"].closed
