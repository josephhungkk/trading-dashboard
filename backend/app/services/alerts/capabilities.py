"""Single-source capability registry via ``app_config[alert_capabilities]``.

No parallel SQL table (HIGH-7) — matches 11a's ``app_config[ai_router]``
pattern. Pubsub invalidation channel: ``app_config:invalidate:alert_capabilities``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAMESPACE = "alert_capabilities"
KEY = "capability_map"

_DEFAULTS: dict[str, dict[str, Any]] = {
    "news_feed": {"available": False, "description": "Phase 18 news ingest"},
    "filings_feed": {"available": False, "description": "Phase 18 SEC filings ingest"},
    "earnings_calendar": {"available": False, "description": "Phase 18 earnings calendar"},
}


async def ensure_seeded(db: AsyncSession) -> None:
    """Insert the default capability map iff the namespace/key row is absent.

    Idempotent: re-runs are no-ops. Matches the ``app_config`` CHECK constraint
    (``value_type='json'`` → ``value_json`` populated, ``value`` NULL).
    """
    existing = (
        await db.execute(
            text("SELECT 1 FROM app_config WHERE namespace = :ns AND key = :k LIMIT 1"),
            {"ns": NAMESPACE, "k": KEY},
        )
    ).first()
    if existing is not None:
        return
    await db.execute(
        text(
            "INSERT INTO app_config (namespace, key, value, value_json, value_type) "
            "VALUES (:ns, :k, NULL, CAST(:v AS jsonb), 'json') "
            "ON CONFLICT (namespace, key) DO NOTHING"
        ),
        {"ns": NAMESPACE, "k": KEY, "v": json.dumps(_DEFAULTS)},
    )
    await db.commit()


async def get_capability_map(db: AsyncSession) -> dict[str, Any]:
    """Return the current capability map, or empty dict if unseeded."""
    row = (
        await db.execute(
            text("SELECT value_json FROM app_config WHERE namespace = :ns AND key = :k"),
            {"ns": NAMESPACE, "k": KEY},
        )
    ).first()
    if row is None or row.value_json is None:
        return {}
    return dict(row.value_json)
