import pytest
from pydantic import ValidationError

from app.services.orchestrator.auto_promote import AutoPromoteCriteria, AutoPromoteEvaluator


def test_auto_promote_criteria_valid() -> None:
    c = AutoPromoteCriteria(min_sharpe=0.5, max_drawdown=0.15, min_win_rate=0.5)
    assert c.auto_apply is False
    assert c.min_comparison_days == 14


def test_auto_promote_criteria_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_field"):
        AutoPromoteCriteria(min_sharpe=0.5, max_drawdown=0.15, min_win_rate=0.5, extra_field=1)


def test_auto_promote_criteria_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        AutoPromoteCriteria(min_sharpe=0.5, max_drawdown=0.15)


@pytest.mark.asyncio
async def test_auto_promote_evaluator_skips_when_master_switch_off() -> None:
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value='"false"'))
    )
    promoter_svc = AsyncMock()
    telegram = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=telegram)

    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    result = await evaluator.evaluate(live_id, shadow_id, db)
    assert result == "skipped_master_switch_off"
    promoter_svc.promote.assert_not_called()


@pytest.mark.asyncio
async def test_auto_promote_evaluator_already_promoted_idempotent() -> None:
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    call_results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),
        MagicMock(scalar_one_or_none=MagicMock(return_value="existing_id")),
    ]
    db.execute = AsyncMock(side_effect=call_results)
    promoter_svc = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=AsyncMock())

    result = await evaluator.evaluate(uuid.uuid4(), uuid.uuid4(), db)
    assert result == "skipped_already_promoted"
    promoter_svc.promote.assert_not_called()


@pytest.mark.asyncio
async def test_auto_promote_evaluator_promotes_when_all_criteria_pass() -> None:
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = AsyncMock()
    call_results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        MagicMock(
            scalar_one_or_none=MagicMock(
                return_value=(
                    '{"min_sharpe": 0.5, "max_drawdown": 0.2,'
                    ' "min_win_rate": 0.4, "auto_apply": true}'
                )
            )
        ),
        MagicMock(all=MagicMock(return_value=[(1.2, 0.05, 0.6, 1.1, 100, 14)])),
    ]
    db.execute = AsyncMock(side_effect=call_results)

    promoter_svc = AsyncMock()
    telegram = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=telegram)

    result = await evaluator.evaluate(live_id, shadow_id, db)
    assert result == "promoted"
    promoter_svc.promote.assert_called_once_with(live_id, shadow_id, "auto", db)
    telegram.send.assert_called_once()


@pytest.mark.asyncio
async def test_auto_promote_evaluator_skips_when_criteria_fail() -> None:
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = AsyncMock()
    call_results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        MagicMock(
            scalar_one_or_none=MagicMock(
                return_value=(
                    '{"min_sharpe": 0.5, "max_drawdown": 0.2,'
                    ' "min_win_rate": 0.4, "auto_apply": true}'
                )
            )
        ),
        MagicMock(all=MagicMock(return_value=[(0.3, 0.05, 0.6, 0.8, 100, 14)])),
    ]
    db.execute = AsyncMock(side_effect=call_results)
    promoter_svc = AsyncMock()
    evaluator = AutoPromoteEvaluator(promoter_service=promoter_svc, telegram=AsyncMock())
    result = await evaluator.evaluate(live_id, shadow_id, db)
    assert result == "criteria_not_met"
    promoter_svc.promote.assert_not_called()
