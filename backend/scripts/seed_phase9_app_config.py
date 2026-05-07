"""Idempotent seeder for Phase 9 app_config keys (charts namespace).

Run on first deploy of v0.9.0 (or any environment that lacks the Phase 9
charting configuration). Safe to re-run — `ON CONFLICT (namespace, key) DO
NOTHING` makes each insert a no-op when the row already exists.

Schema reference (per `0001_app_config_and_secrets.py`):
  app_config(namespace, key) PK
    + value         TEXT       (used when value_type IN 'str','int','bool')
    + value_json    JSONB      (used when value_type = 'json')
    + value_type    TEXT       CHECK IN ('str','int','bool','json')
    + value-exclusive CHECK    (exactly one of value/value_json non-null)

Keys seeded (per spec §3 lines 376-389):
  - bar_source_priority.{equity_us, equity_hk, crypto, fx} : json arrays
  - bar_pre_warm_window_days  : int (30 default)
  - bar_active_set_recency_days : int (30 default)
  - chart_layout_schema_version : int (1)
  - enabled : bool (true) — kill-switch
"""

from __future__ import annotations

import json
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SEEDS: Final[tuple[tuple[str, str, str | None, str | None], ...]] = (
    (
        "bar_source_priority.equity_us",
        "json",
        None,
        json.dumps(["schwab", "alpaca", "ibkr"]),
    ),
    (
        "bar_source_priority.equity_hk",
        "json",
        None,
        json.dumps(["futu", "ibkr"]),
    ),
    (
        "bar_source_priority.crypto",
        "json",
        None,
        json.dumps(["alpaca"]),
    ),
    (
        "bar_source_priority.fx",
        "json",
        None,
        json.dumps(["ibkr"]),
    ),
    ("bar_pre_warm_window_days", "int", "30", None),
    ("bar_active_set_recency_days", "int", "30", None),
    ("chart_layout_schema_version", "int", "1", None),
    ("enabled", "bool", "true", None),
)


async def seed_phase9_app_config(session: AsyncSession) -> None:
    """Insert Phase 9 charts-namespace app_config keys; idempotent on re-run."""
    for key, vtype, value, value_json in _SEEDS:
        await session.execute(
            text(
                """
                INSERT INTO app_config (namespace, key, value_type, value, value_json)
                VALUES ('charts', :k, :t, :v, CAST(:j AS JSONB))
                ON CONFLICT (namespace, key) DO NOTHING
                """
            ),
            {"k": key, "t": vtype, "v": value, "j": value_json},
        )
    await session.commit()
