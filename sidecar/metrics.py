"""Sidecar metrics.

The Windows sidecar does not expose Prometheus directly yet, but keeping the
collector shape here lets handlers count events without coupling to backend
metrics internals.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Self

try:
    from prometheus_client import Counter
except ModuleNotFoundError:

    class _Value:
        def __init__(self, counter: _FallbackCounter, labels: tuple[tuple[str, str], ...]) -> None:
            self._counter = counter
            self._labels = labels

        def get(self) -> float:
            return self._counter._values[self._labels]

    class _FallbackCounter:
        def __init__(
            self,
            name: str,
            documentation: str,
            labelnames: list[str],
        ) -> None:
            del documentation
            self._name = name
            self._labelnames = tuple(labelnames)
            self._labels: tuple[tuple[str, str], ...] = ()
            self._values: defaultdict[tuple[tuple[str, str], ...], float] = defaultdict(float)
            self._value = _Value(self, self._labels)

        def labels(self, **labels: str) -> Self:
            counter = type(self)(self._name, "", list(self._labelnames))
            counter._values = self._values
            counter._labels = tuple((name, labels[name]) for name in self._labelnames)
            counter._value = _Value(counter, counter._labels)
            return counter

        def inc(self, amount: float = 1.0) -> None:
            self._values[self._labels] += amount

    Counter = _FallbackCounter


broker_order_events_dropped_total = Counter(
    "broker_order_events_dropped_total",
    "Order events dropped by the sidecar stream before delivery.",
    labelnames=["reason"],
)
