"""Tests for auto-promote veto window (promote_pending CAS + Telegram handler)."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.orchestrator.auto_promote import AutoPromoteEvaluator

pytestmark = pytest.mark.no_db

_VETO_WINDOW_S = 300  # must match the constant in auto_promote.py


def _make_criteria_row(auto_apply: bool = True) -> str:
    import json

    return json.dumps(
        {
            "min_sharpe": 1.0,
            "max_drawdown": 0.2,
            "min_win_rate": 0.5,
            "auto_apply": auto_apply,
        }
    )


@pytest.mark.asyncio
async def test_veto_window_inserts_promote_pending() -> None:
    """When criteria met and auto_apply=True, INSERT promote_pending row before Telegram."""
    live_bot_id = uuid.uuid4()
    shadow_bot_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),  # master_switch
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # no prior success
            MagicMock(scalar_one_or_none=MagicMock(return_value=_make_criteria_row())),
            MagicMock(all=MagicMock(return_value=[(1.5, 0.1, 0.6, 1.2, 5, 14)])),  # metrics
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # no existing pending
            MagicMock(),  # INSERT promote_pending
        ]
    )
    db.commit = AsyncMock()

    telegram = AsyncMock()
    promoter = AsyncMock()
    scheduler = MagicMock()
    scheduler.add_job = MagicMock()

    evaluator = AutoPromoteEvaluator(
        promoter_service=promoter,
        telegram=telegram,
        scheduler=scheduler,
    )
    result = await evaluator.evaluate(live_bot_id, shadow_bot_id, db)

    assert result == "pending_veto_window"
    telegram.send.assert_called_once()
    assert "veto" in telegram.send.call_args[0][0].lower()
    scheduler.add_job.assert_called_once()


@pytest.mark.asyncio
async def test_veto_window_existing_pending_skipped() -> None:
    """If promote_pending row already exists, skip duplicate insert."""
    live_bot_id = uuid.uuid4()
    shadow_bot_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=_make_criteria_row())),
            MagicMock(all=MagicMock(return_value=[(1.5, 0.1, 0.6, 1.2, 5, 14)])),
            MagicMock(
                scalar_one_or_none=MagicMock(return_value=str(uuid.uuid4()))
            ),  # existing pending
        ]
    )
    db.commit = AsyncMock()

    evaluator = AutoPromoteEvaluator(
        promoter_service=AsyncMock(),
        telegram=AsyncMock(),
        scheduler=MagicMock(),
    )
    result = await evaluator.evaluate(live_bot_id, shadow_bot_id, db)
    assert result == "skipped_already_pending"


def _make_db_factory(db: AsyncMock) -> MagicMock:
    """Return a sync callable that acts as an async context manager yielding db."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


@pytest.mark.asyncio
async def test_expiry_promote_executes_on_non_vetoed() -> None:
    """_expiry_promote: if status still promote_pending, CAS to success and promote."""
    live_bot_id = uuid.uuid4()
    shadow_bot_id = uuid.uuid4()
    event_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=1),  # CAS UPDATE promote_pending -> success
        ]
    )
    db.commit = AsyncMock()
    db_for_promote = AsyncMock()
    db_for_promote.execute = AsyncMock()
    db_for_promote.commit = AsyncMock()

    call_count = 0

    def _factory_side_effect() -> MagicMock:
        nonlocal call_count
        call_count += 1
        target_db = db if call_count == 1 else db_for_promote
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=target_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    promoter = AsyncMock()
    telegram = AsyncMock()

    from app.services.orchestrator.auto_promote import _expiry_promote

    await _expiry_promote(
        event_id=event_id,
        live_bot_id=live_bot_id,
        shadow_bot_id=shadow_bot_id,
        promoter=promoter,
        telegram=telegram,
        db_factory=MagicMock(side_effect=_factory_side_effect),
    )
    promoter.promote.assert_called_once()
    args = promoter.promote.call_args[0]
    assert args[0] == live_bot_id
    assert args[1] == shadow_bot_id
    assert args[2] == "auto"


@pytest.mark.asyncio
async def test_expiry_promote_skips_if_vetoed() -> None:
    """_expiry_promote: if status was vetoed (rowcount=0), do NOT promote."""
    live_bot_id = uuid.uuid4()
    shadow_bot_id = uuid.uuid4()
    event_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=0),  # CAS UPDATE returns 0 = vetoed/already-done
        ]
    )
    db.commit = AsyncMock()

    promoter = AsyncMock()
    from app.services.orchestrator.auto_promote import _expiry_promote

    await _expiry_promote(
        event_id=event_id,
        live_bot_id=live_bot_id,
        shadow_bot_id=shadow_bot_id,
        promoter=promoter,
        telegram=AsyncMock(),
        db_factory=_make_db_factory(db),
    )
    promoter.promote.assert_not_called()


@pytest.mark.asyncio
async def test_veto_handler_marks_vetoed() -> None:
    """Veto handler: valid token -> UPDATE status=vetoed, rowcount=1."""
    from app.services.orchestrator.auto_promote import handle_veto_token

    token = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()

    result = await handle_veto_token(token=token, db=db)
    assert result is True


@pytest.mark.asyncio
async def test_veto_handler_expired_token_returns_false() -> None:
    """Veto handler: expired token (rowcount=0) -> returns False."""
    from app.services.orchestrator.auto_promote import handle_veto_token

    token = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    db.commit = AsyncMock()

    result = await handle_veto_token(token=token, db=db)
    assert result is False


@pytest.mark.asyncio
async def test_recovery_sweep_reschedules_pending() -> None:
    """Startup recovery sweep: rows with promote_pending and future expiry get rescheduled."""
    from app.services.orchestrator.auto_promote import recover_pending_veto_windows

    event_id = uuid.uuid4()
    live_bot_id = uuid.uuid4()
    shadow_bot_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=60)

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[
                    (event_id, live_bot_id, shadow_bot_id, expires_at),
                ]
            )
        )
    )

    scheduler = MagicMock()
    scheduler.add_job = MagicMock()

    await recover_pending_veto_windows(
        db=db,
        scheduler=scheduler,
        promoter=AsyncMock(),
        telegram=AsyncMock(),
        db_factory=AsyncMock(),
    )
    scheduler.add_job.assert_called_once()
