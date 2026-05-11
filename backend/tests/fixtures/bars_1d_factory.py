"""Phase 10b.1 test fixture — bars_1d row builder + golden AAPL values.

Provides ``build_bars_1d_rows`` (returns the same (bar_date, high, low, close)
tuples that ``VolatilityService._load_bars`` produces, so the service can be
unit-tested by injecting rows directly) and ``GOLDEN_AAPL_*`` (pinned
realized_vol + ATR golden values computed offline).

We do not insert into bars_1d directly because bars_1d is a TimescaleDB
continuous aggregate (read-only materialized view over bars_1m) added in
alembic 0038. Tests that need round-trip SQL coverage write to bars_1m
and call ``refresh_continuous_aggregate``; tests that only need to verify
the vol/ATR math inject pre-built rows via this builder.

Golden values: AAPL daily closes 2025-12-01 .. 2025-12-19 (15 trading days):
  closes = [
      "190.00", "191.50", "189.75", "192.00", "194.25",
      "193.50", "195.00", "196.75", "198.50", "197.00",
      "199.25", "201.00", "200.50", "202.75", "204.50",
  ]
  realized_vol14_annualized = "0.11501257" (≈ 11.5% annualized)
  atr14 = "2.17857143"

The values above were computed by the same math.log + math.sqrt(252) path
the service uses. If you change the closes list, recompute and update both
the constants and the test.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

GOLDEN_AAPL_CLOSES: list[Decimal] = [
    Decimal("190.00"),
    Decimal("191.50"),
    Decimal("189.75"),
    Decimal("192.00"),
    Decimal("194.25"),
    Decimal("193.50"),
    Decimal("195.00"),
    Decimal("196.75"),
    Decimal("198.50"),
    Decimal("197.00"),
    Decimal("199.25"),
    Decimal("201.00"),
    Decimal("200.50"),
    Decimal("202.75"),
    Decimal("204.50"),
]
GOLDEN_AAPL_START_DATE: date = date(2025, 12, 1)
GOLDEN_AAPL_VOL14_ANNUALIZED: Decimal = Decimal("0.11501257")
GOLDEN_AAPL_ATR14: Decimal = Decimal("2.17857143")


def build_bars_1d_rows(
    closes: Sequence[Decimal],
    start_date: date,
    *,
    high_offset: Decimal = Decimal("0.50"),
    low_offset: Decimal = Decimal("0.50"),
) -> list[tuple[date, Decimal, Decimal, Decimal]]:
    """Return (bar_date, high, low, close) tuples shaped like ``_load_bars``.

    Matches the in-service contract: oldest first, high = close + offset,
    low = close - offset.
    """
    return [
        (
            start_date + timedelta(days=i),
            close + high_offset,
            close - low_offset,
            close,
        )
        for i, close in enumerate(closes)
    ]
