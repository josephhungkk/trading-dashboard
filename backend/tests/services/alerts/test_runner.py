"""Phase 11b chunk-B-close: runner module — process callback + Redis subscriber."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_db

from app.services.alerts.delivery import DeliveryDispatcher, DeliveryOutcome  # noqa: E402
from app.services.alerts.evaluator import AlertsEvaluator  # noqa: E402
from app.services.alerts.runner import (  # noqa: E402
    AlertsBarsRedisSubscriber,
    build_process_callback,
)


class _FakeRow:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, first_row: Any | None = None) -> None:
        self._rows = rows or []
        self._first = first_row

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeDb:
    """Scripts a sequence of execute() responses keyed by ordinal call index."""

    def __init__(self, responses: list[_FakeResult]) -> None:
        self._responses = responses
        self._calls = 0
        self.commits = 0

    async def execute(self, _stmt: Any, _params: dict[str, Any] | None = None) -> Any:
        idx = self._calls
        self._calls += 1
        if idx < len(self._responses):
            return self._responses[idx]
        return _FakeResult()

    async def commit(self) -> None:
        self.commits += 1

    async def __aenter__(self) -> _FakeDb:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _make_session_factory(db: _FakeDb) -> Any:
    factory = MagicMock()
    factory.return_value = db
    return factory


@pytest.mark.asyncio
async def test_process_callback_fires_and_dispatches_on_true_predicate() -> None:
    """Happy-path: predicate evaluates True, fire row + context row + fan_out."""
    rule_row = _FakeRow(
        id=1,
        jwt_subject="u",
        user_label="AAPL > 100",
        predicate_json={"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 100},
        delivery_channels=["in_app"],
        status="active",
    )
    bar_rows = [
        _FakeRow(ts="2026-05-13T12:00:00Z", close=150.0, volume=1000.0),
    ]
    fire_row = _FakeRow(id=42, fired_at=MagicMock(isoformat=lambda: "2026-05-13T12:00:00Z"))
    db = _FakeDb(
        [
            _FakeResult(first_row=rule_row),  # SELECT alerts WHERE id=...
            _FakeResult(rows=bar_rows),  # SELECT bars_1m JOIN aliases
            _FakeResult(first_row=fire_row),  # INSERT alert_fires RETURNING
            _FakeResult(),  # INSERT alert_fire_context
        ]
    )
    dispatcher = MagicMock(spec=DeliveryDispatcher)
    dispatcher.fan_out = AsyncMock(return_value={"in_app": DeliveryOutcome.sent})

    process = build_process_callback(
        session_factory=_make_session_factory(db),
        dispatcher=dispatcher,
    )
    await process({"rule_id": 1, "symbol": "AAPL"})

    dispatcher.fan_out.assert_awaited_once()
    fire_arg = dispatcher.fan_out.await_args.args[0]
    assert fire_arg.alert_id == 1
    assert fire_arg.fire_id == 42
    assert fire_arg.user_label == "AAPL > 100"
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_process_callback_skips_when_rule_inactive() -> None:
    """A rule in dormant/disabled status must NOT evaluate."""
    rule_row = _FakeRow(
        id=1,
        jwt_subject="u",
        user_label="x",
        predicate_json={"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 100},
        delivery_channels=["in_app"],
        status="dormant",
    )
    db = _FakeDb([_FakeResult(first_row=rule_row)])
    dispatcher = MagicMock(spec=DeliveryDispatcher)
    dispatcher.fan_out = AsyncMock()

    process = build_process_callback(
        session_factory=_make_session_factory(db),
        dispatcher=dispatcher,
    )
    await process({"rule_id": 1, "symbol": "AAPL"})

    dispatcher.fan_out.assert_not_called()


@pytest.mark.asyncio
async def test_process_callback_marks_dormancy_on_eval_error() -> None:
    """A malformed predicate raises inside ``evaluate()`` and bumps the
    consecutive_eval_errors counter via mark_eval_error()."""
    rule_row = _FakeRow(
        id=1,
        jwt_subject="u",
        user_label="x",
        predicate_json={"kind": "price_threshold"},  # missing required keys
        delivery_channels=["in_app"],
        status="active",
    )
    bar_rows = [_FakeRow(ts="x", close=10.0, volume=1.0)]
    db = _FakeDb(
        [
            _FakeResult(first_row=rule_row),
            _FakeResult(rows=bar_rows),
            _FakeResult(),  # UPDATE consecutive_eval_errors
        ]
    )
    dispatcher = MagicMock(spec=DeliveryDispatcher)
    dispatcher.fan_out = AsyncMock()

    process = build_process_callback(
        session_factory=_make_session_factory(db),
        dispatcher=dispatcher,
    )
    await process({"rule_id": 1, "symbol": "AAPL"})

    dispatcher.fan_out.assert_not_called()
    # The UPDATE bumping consecutive_eval_errors + commit should have run.
    assert db._calls >= 3
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_process_callback_skips_unparseable_items() -> None:
    """A queue item without a numeric rule_id or string symbol must NOT
    open a DB session."""
    factory = MagicMock()
    process = build_process_callback(
        session_factory=factory,
        dispatcher=MagicMock(spec=DeliveryDispatcher),
    )
    await process({"rule_id": "nope", "symbol": "AAPL"})
    await process({"rule_id": 1, "symbol": 5})
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_bars_redis_subscriber_dispatches_payload_to_evaluator() -> None:
    """The subscriber pulls a Redis pubsub message and feeds the JSON
    payload into ``evaluator._on_bars_1m_notify``."""
    evaluator = AlertsEvaluator(queue_maxsize=10)
    evaluator.index.add(rule_id=1, symbols={"AAPL"})

    payload = json.dumps({"inst_id": 7, "ts": 1715600000.0})

    class _FakePubSub:
        def __init__(self) -> None:
            self._delivered = False

        async def subscribe(self, _channel: str) -> None:
            return None

        async def unsubscribe(self, _channel: str) -> None:
            return None

        async def aclose(self) -> None:
            return None

        async def get_message(self, **_kw: Any) -> Any:
            if self._delivered:
                return None
            self._delivered = True
            return {"type": "message", "channel": "bars_1m_insert", "data": payload}

    fake_redis = MagicMock()
    fake_redis.pubsub = MagicMock(return_value=_FakePubSub())

    def _resolve(inst_id: int) -> str | None:
        return "AAPL" if inst_id == 7 else None

    sub = AlertsBarsRedisSubscriber(redis=fake_redis, evaluator=evaluator, resolve_symbol=_resolve)
    sub.start()
    try:
        # Wait until the rule was enqueued OR a short timeout.
        for _ in range(40):
            if not evaluator._queue.empty():
                break
            await asyncio.sleep(0.01)
    finally:
        await sub.stop()

    assert not evaluator._queue.empty()
    item = evaluator._queue.get_nowait()
    assert item == {"rule_id": 1, "symbol": "AAPL", "ts": 1715600000.0}
