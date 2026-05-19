"""Tests for BaseStrategy ABC, BarEvent, and FillEvent (Phase 19 Chunk A)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.bot.base import BarEvent, BaseStrategy, FillEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar() -> BarEvent:
    return BarEvent(
        canonical_id="AAPL",
        timeframe="1m",
        open=Decimal("180.00"),
        high=Decimal("181.00"),
        low=Decimal("179.50"),
        close=Decimal("180.75"),
        volume=Decimal("10000"),
        ts=datetime.now(UTC),
    )


def _make_fill() -> FillEvent:
    return FillEvent(
        order_id=uuid4(),
        account_id=uuid4(),
        canonical_id="AAPL",
        side="buy",
        qty=Decimal("10"),
        price=Decimal("180.75"),
        filled_at=datetime.now(UTC),
    )


class _MinimalStrategy(BaseStrategy):
    """Concrete strategy that satisfies the abstract interface."""

    def on_start(self) -> None:
        pass

    def on_bar(self, bar: BarEvent) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bar_event_frozen() -> None:
    """BarEvent must be immutable (frozen dataclass)."""
    bar = _make_bar()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        bar.open = Decimal("999.00")  # type: ignore[misc]


def test_fill_event_frozen() -> None:
    """FillEvent must be immutable (frozen dataclass)."""
    fill = _make_fill()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        fill.qty = Decimal("999")  # type: ignore[misc]


def test_cannot_instantiate_base_strategy() -> None:
    """BaseStrategy cannot be instantiated directly (abstract methods)."""
    with pytest.raises(TypeError):
        BaseStrategy()  # type: ignore[abstract]


def test_concrete_strategy_minimal() -> None:
    """A minimal concrete subclass can be instantiated without error."""
    strat = _MinimalStrategy()
    assert isinstance(strat, BaseStrategy)


def test_on_fill_noop() -> None:
    """on_fill() default implementation returns None without error."""
    strat = _MinimalStrategy()
    result = strat.on_fill(_make_fill())
    assert result is None


def test_on_advisor_reject_noop_does_not_raise() -> None:
    """on_advisor_reject() default noop does not raise."""
    strat = _MinimalStrategy()
    strat.on_advisor_reject(None, None)  # type: ignore[arg-type]


def test_on_advisor_reject_subclass_override_invoked() -> None:
    """Subclass on_advisor_reject is called with the correct args."""
    calls: list = []

    class _Impl(_MinimalStrategy):
        def on_advisor_reject(self, intent, decision) -> None:  # type: ignore[override]
            calls.append((intent, decision))

    s = _Impl()
    s.on_advisor_reject("intent", "decision")
    assert calls == [("intent", "decision")]


def test_on_advisor_reject_weakref_does_not_cause_repr_recursion() -> None:
    """weakref to a strategy should not cause repr recursion issues."""
    import weakref

    s = _MinimalStrategy()
    ref = weakref.ref(s)
    r = repr(ref)
    assert "Traceback" not in r
