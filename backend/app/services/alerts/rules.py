"""Phase 11b chunk A: CRUD layer for the ``alerts`` table.

Cross-subject access raises ``RuleCrossSubjectError`` which the API maps
to 404 with an identical body to ``RuleNotFoundError`` — the existence-
oracle defence matching 11a's /api/ai/jobs/{id} GET/DELETE.

All writes call ``predicates.validate_schema()`` first so we never
persist a predicate the evaluator can't dispatch on; ``referenced_capabilities``
populates the GIN-indexed JSONB column read by chunk B's capability-flip
handler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.alerts.exceptions import (
    AlreadyActiveError,
    RuleCrossSubjectError,
    RuleNotFoundError,
)
from app.services.alerts.predicates import referenced_capabilities, validate_schema


@dataclass(slots=True)
class AlertRule:
    id: int
    jwt_subject: str
    user_label: str
    original_nl: str
    predicate_json: dict[str, Any]
    requires_capabilities: list[dict[str, Any]]
    parse_status: str
    parse_metadata: dict[str, Any] | None
    delivery_channels: list[str]
    tick_subscribed: bool
    status: str
    dormancy_reason: str | None
    consecutive_eval_errors: int
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    deleted_at: datetime | None


_COLUMNS = (
    "id, jwt_subject, user_label, original_nl, predicate_json, requires_capabilities, "
    "parse_status, parse_metadata, delivery_channels, tick_subscribed, status, "
    "dormancy_reason, consecutive_eval_errors, created_at, updated_at, confirmed_at, "
    "deleted_at"
)


def _row_to_rule(row: Any) -> AlertRule:
    return AlertRule(
        id=row.id,
        jwt_subject=row.jwt_subject,
        user_label=row.user_label,
        original_nl=row.original_nl,
        predicate_json=row.predicate_json,
        requires_capabilities=row.requires_capabilities,
        parse_status=row.parse_status,
        parse_metadata=row.parse_metadata,
        delivery_channels=row.delivery_channels,
        tick_subscribed=row.tick_subscribed,
        status=row.status,
        dormancy_reason=row.dormancy_reason,
        consecutive_eval_errors=row.consecutive_eval_errors,
        created_at=row.created_at,
        updated_at=row.updated_at,
        confirmed_at=row.confirmed_at,
        deleted_at=row.deleted_at,
    )


async def create_rule(
    db: AsyncSession,
    *,
    jwt_subject: str,
    user_label: str,
    original_nl: str,
    predicate_json: dict[str, Any],
    parse_status: str,
    parse_metadata: dict[str, Any] | None = None,
    delivery_channels: list[str] | None = None,
    tick_subscribed: bool = False,
) -> AlertRule:
    """INSERT a rule in 'pending' status. Validates predicate first."""
    validate_schema(predicate_json)
    caps = referenced_capabilities(predicate_json)
    row = (
        await db.execute(
            text(
                f"INSERT INTO alerts (jwt_subject, user_label, original_nl, predicate_json, "
                f"requires_capabilities, parse_status, parse_metadata, delivery_channels, "
                f"tick_subscribed, status) "
                f"VALUES (:s, :l, :nl, CAST(:p AS jsonb), CAST(:c AS jsonb), :ps, "
                f"CAST(:pm AS jsonb), CAST(:dc AS jsonb), :ts, 'pending') "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "s": jwt_subject,
                "l": user_label,
                "nl": original_nl,
                "p": json.dumps(predicate_json),
                "c": json.dumps(caps),
                "ps": parse_status,
                "pm": json.dumps(parse_metadata) if parse_metadata else None,
                "dc": json.dumps(delivery_channels or ["in_app"]),
                "ts": tick_subscribed,
            },
        )
    ).one()
    await db.commit()
    return _row_to_rule(row)


async def get_rule(db: AsyncSession, *, rule_id: int, jwt_subject: str) -> AlertRule:
    """Fetch one rule by id. Raises RuleNotFoundError if not found
    or already soft-deleted; RuleCrossSubjectError if jwt_subject mismatch."""
    row = (
        await db.execute(
            text(f"SELECT {_COLUMNS} FROM alerts WHERE id = :id AND status != 'deleted'"),
            {"id": rule_id},
        )
    ).first()
    if row is None:
        raise RuleNotFoundError(rule_id)
    if row.jwt_subject != jwt_subject:
        raise RuleCrossSubjectError(rule_id)
    return _row_to_rule(row)


async def list_rules(db: AsyncSession, *, jwt_subject: str) -> list[AlertRule]:
    """Return non-deleted rules for the subject, newest-first."""
    rows = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM alerts WHERE jwt_subject = :s "
                f"AND status != 'deleted' ORDER BY created_at DESC"
            ),
            {"s": jwt_subject},
        )
    ).all()
    return [_row_to_rule(r) for r in rows]


async def delete_rule(db: AsyncSession, *, rule_id: int, jwt_subject: str) -> None:
    """Soft-delete (sets status='deleted', deleted_at=now). Cross-subject raises."""
    await get_rule(db, rule_id=rule_id, jwt_subject=jwt_subject)
    await db.execute(
        text("UPDATE alerts SET status='deleted', deleted_at=:t WHERE id=:id"),
        {"id": rule_id, "t": datetime.now(UTC)},
    )
    await db.commit()


async def update_predicate(
    db: AsyncSession,
    *,
    rule_id: int,
    jwt_subject: str,
    predicate_json: dict[str, Any],
) -> AlertRule:
    """PUT-edit a rule's predicate. Validates predicate. Sets parse_status='manual'.
    Cross-subject raises RuleCrossSubjectError."""
    validate_schema(predicate_json)
    await get_rule(db, rule_id=rule_id, jwt_subject=jwt_subject)
    caps = referenced_capabilities(predicate_json)
    row = (
        await db.execute(
            text(
                f"UPDATE alerts SET predicate_json=CAST(:p AS jsonb), "
                f"requires_capabilities=CAST(:c AS jsonb), parse_status='manual', "
                f"updated_at=now() WHERE id=:id RETURNING {_COLUMNS}"
            ),
            {
                "id": rule_id,
                "p": json.dumps(predicate_json),
                "c": json.dumps(caps),
            },
        )
    ).one()
    await db.commit()
    return _row_to_rule(row)


async def confirm_rule(db: AsyncSession, *, rule_id: int, jwt_subject: str) -> AlertRule:
    """Flip status pending → active. Raises AlreadyActiveError if already active.
    Cross-subject raises RuleCrossSubjectError."""
    rule = await get_rule(db, rule_id=rule_id, jwt_subject=jwt_subject)
    if rule.status == "active":
        raise AlreadyActiveError(rule_id)
    row = (
        await db.execute(
            text(
                f"UPDATE alerts SET status='active', confirmed_at=now(), "
                f"updated_at=now() WHERE id=:id RETURNING {_COLUMNS}"
            ),
            {"id": rule_id},
        )
    ).one()
    await db.commit()
    return _row_to_rule(row)
