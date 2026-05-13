"""Phase 11b chunk A: rules CRUD tests with cross-subject 404 defence."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.alerts.exceptions import (
    AlreadyActiveError,
    RuleCrossSubjectError,
    RuleNotFoundError,
)
from app.services.alerts.rules import (
    confirm_rule,
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_predicate,
)

pytestmark = pytest.mark.asyncio


_VALID_PREDICATE = {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 200.0}
_DUMMY_PREDICATE = {"kind": "unknown", "raw_text": "x", "suggestions": []}


async def test_create_returns_pending(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="AAPL above 200",
        original_nl="tell me when AAPL > 200",
        predicate_json=_VALID_PREDICATE,
        parse_status="ok",
    )
    assert rule.status == "pending"
    assert rule.jwt_subject == "user-1"
    assert rule.predicate_json == _VALID_PREDICATE


async def test_get_rule_cross_subject_raises(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_DUMMY_PREDICATE,
        parse_status="failed",
    )
    with pytest.raises(RuleCrossSubjectError):
        await get_rule(session, rule_id=rule.id, jwt_subject="user-2")


async def test_get_rule_unknown_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(RuleNotFoundError):
        await get_rule(session, rule_id=999_999_999, jwt_subject="user-1")


async def test_list_rules_scoped_to_subject(session: AsyncSession) -> None:
    await create_rule(
        session,
        jwt_subject="user-A",
        user_label="a",
        original_nl="a",
        predicate_json=_DUMMY_PREDICATE,
        parse_status="failed",
    )
    await create_rule(
        session,
        jwt_subject="user-B",
        user_label="b",
        original_nl="b",
        predicate_json=_DUMMY_PREDICATE,
        parse_status="failed",
    )
    rules_a = await list_rules(session, jwt_subject="user-A")
    rules_b = await list_rules(session, jwt_subject="user-B")
    assert all(r.jwt_subject == "user-A" for r in rules_a)
    assert all(r.jwt_subject == "user-B" for r in rules_b)


async def test_delete_soft_deletes(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_DUMMY_PREDICATE,
        parse_status="failed",
    )
    await delete_rule(session, rule_id=rule.id, jwt_subject="user-1")
    with pytest.raises(RuleNotFoundError):
        await get_rule(session, rule_id=rule.id, jwt_subject="user-1")


async def test_delete_cross_subject_raises(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_DUMMY_PREDICATE,
        parse_status="failed",
    )
    with pytest.raises(RuleCrossSubjectError):
        await delete_rule(session, rule_id=rule.id, jwt_subject="user-2")


async def test_update_predicate_sets_manual_status(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_DUMMY_PREDICATE,
        parse_status="failed",
    )
    new_pred = {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 100.0}
    updated = await update_predicate(
        session,
        rule_id=rule.id,
        jwt_subject="user-1",
        predicate_json=new_pred,
    )
    assert updated.predicate_json == new_pred
    assert updated.parse_status == "manual"


async def test_confirm_flips_to_active(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_VALID_PREDICATE,
        parse_status="ok",
    )
    confirmed = await confirm_rule(session, rule_id=rule.id, jwt_subject="user-1")
    assert confirmed.status == "active"
    assert confirmed.confirmed_at is not None


async def test_confirm_already_active_raises(session: AsyncSession) -> None:
    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_VALID_PREDICATE,
        parse_status="ok",
    )
    await confirm_rule(session, rule_id=rule.id, jwt_subject="user-1")
    with pytest.raises(AlreadyActiveError):
        await confirm_rule(session, rule_id=rule.id, jwt_subject="user-1")


async def test_confirm_non_pending_raises_not_found(session: AsyncSession) -> None:
    """confirm() must reject non-pending states (dormant/disabled have their own
    chunk-B flip paths and must not be reachable through user confirm)."""
    from sqlalchemy import text

    rule = await create_rule(
        session,
        jwt_subject="user-1",
        user_label="x",
        original_nl="x",
        predicate_json=_VALID_PREDICATE,
        parse_status="ok",
    )
    await session.execute(
        text("UPDATE alerts SET status='dormant' WHERE id=:id"),
        {"id": rule.id},
    )
    await session.commit()
    with pytest.raises(RuleNotFoundError):
        await confirm_rule(session, rule_id=rule.id, jwt_subject="user-1")
