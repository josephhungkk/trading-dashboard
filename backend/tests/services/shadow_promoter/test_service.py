from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.shadow_promoter.service import ShadowPromoterService
from app.services.shadow_promoter.types import ShadowComparisonReport


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one.return_value = value
    r.scalar_one_or_none.return_value = value
    return r


def _first_result(row_dict):
    """CursorResult where .mappings().first() returns a plain dict."""
    r = MagicMock()
    r.mappings.return_value.first.return_value = row_dict
    r.first.return_value = row_dict
    return r


def _all_result(*rows):
    """CursorResult where .mappings().all() returns a list of plain dicts."""
    r = MagicMock()
    r.mappings.return_value.all.return_value = list(rows)
    r.mappings.return_value.first.return_value = rows[0] if rows else None
    r.first.return_value = rows[0] if rows else None
    return r


def _empty_result():
    r = MagicMock()
    r.first.return_value = None
    r.mappings.return_value.first.return_value = None
    r.mappings.return_value.all.return_value = []
    r.scalar_one_or_none.return_value = None
    return r


def _agg_row():
    """Aggregation query row for _aggregate_metrics."""
    r = MagicMock()
    r.mappings.return_value.one.return_value = {
        "sharpe": 1.2,
        "mar": 0.8,
        "max_dd": -0.15,
        "win_rate": 0.55,
        "avg_trade_pnl": 45.0,
        "total_trades": 20,
    }
    return r


@pytest.fixture
def service():
    db_factory = MagicMock()
    supervisor = AsyncMock()
    redis = AsyncMock()
    return ShadowPromoterService(db_factory=db_factory, supervisor=supervisor, redis=redis)


class TestCreateShadow:
    @pytest.mark.asyncio
    async def test_raises_when_live_bot_not_found(self, service):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_result())
        with pytest.raises(ValueError, match="bot_not_found"):
            await service.create_shadow(uuid4(), {}, 30, "admin", db)

    @pytest.mark.asyncio
    async def test_raises_cannot_shadow_a_shadow(self, service):
        live_id = uuid4()
        live_row = {
            "id": str(live_id),
            "name": "MyBot",
            "strategy_file": "s.py",
            "strategy_params": {"fast": 10},
            "is_shadow": True,
            "deleted_at": None,
            "advisor_config": None,
            "strategy_schema": None,
        }
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_first_result(live_row))
        with pytest.raises(ValueError, match="cannot_shadow_a_shadow"):
            await service.create_shadow(live_id, {}, 30, "admin", db)

    @pytest.mark.asyncio
    async def test_creates_shadow_and_returns_id(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        live_row = {
            "id": str(live_id),
            "name": "MyBot",
            "strategy_file": "s.py",
            "strategy_params": {"fast": 10, "slow": 50},
            "is_shadow": False,
            "deleted_at": None,
            "advisor_config": None,
            "strategy_schema": {"fast": "int", "slow": "int"},
        }
        insert_result = MagicMock()
        insert_result.scalar_one.return_value = shadow_id

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _first_result(live_row),  # SELECT live bot
                insert_result,  # INSERT shadow RETURNING id
                _empty_result(),  # SELECT bot_risk_caps
                _empty_result(),  # SELECT bot_accounts
            ]
        )
        db.commit = AsyncMock()

        result = await service.create_shadow(live_id, {"fast": 20}, 30, "admin", db)
        assert result == shadow_id

    @pytest.mark.asyncio
    async def test_merges_override_params_with_live_params(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        live_row = {
            "id": str(live_id),
            "name": "MyBot",
            "strategy_file": "s.py",
            "strategy_params": {"fast": 10, "slow": 50},
            "is_shadow": False,
            "deleted_at": None,
            "advisor_config": None,
            "strategy_schema": {"fast": "int", "slow": "int"},
        }
        insert_result = MagicMock()
        insert_result.scalar_one.return_value = shadow_id

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _first_result(live_row),
                insert_result,
                _empty_result(),
                _empty_result(),
            ]
        )
        db.commit = AsyncMock()

        await service.create_shadow(live_id, {"fast": 20}, 14, "admin", db)
        # Verify INSERT call included merged params: fast=20, slow=50
        call_params = db.execute.call_args_list[1][0][1]
        merged = json.loads(call_params["strategy_params"])
        assert merged["fast"] == 20
        assert merged["slow"] == 50

    @pytest.mark.asyncio
    async def test_shadow_always_created_as_paper_mode(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        live_row = {
            "id": str(live_id),
            "name": "LiveBot",
            "strategy_file": "s.py",
            "strategy_params": {},
            "is_shadow": False,
            "deleted_at": None,
            "advisor_config": None,
            "strategy_schema": {"x": "int"},
        }
        insert_result = MagicMock()
        insert_result.scalar_one.return_value = shadow_id

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _first_result(live_row),
                insert_result,
                _empty_result(),
                _empty_result(),
            ]
        )
        db.commit = AsyncMock()

        await service.create_shadow(live_id, {}, 7, "admin", db)
        insert_call = db.execute.call_args_list[1]
        sql_text = str(insert_call[0][0])
        # mode must be 'paper' in the INSERT SQL
        assert "'paper'" in sql_text


class TestCheckAutoPromoteEligibility:
    @pytest.mark.asyncio
    async def test_always_returns_false(self, service):
        db = AsyncMock()
        result = await service.check_auto_promote_eligibility(uuid4(), db)
        assert result is False


class TestGetComparison:
    @pytest.mark.asyncio
    async def test_returns_report_with_live_bot_id(self, service):
        live_id = uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_result())
        report = await service.get_comparison(live_id, db)
        assert isinstance(report, ShadowComparisonReport)
        assert report.live_bot_id == live_id
        assert report.shadows == []


class TestPromote:
    @pytest.mark.asyncio
    async def test_raises_shadow_not_found(self, service):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_result())
        with pytest.raises(ValueError, match="shadow_not_found"):
            await service.promote(uuid4(), uuid4(), "admin", db)

    @pytest.mark.asyncio
    async def test_raises_shadow_not_owned_by_live(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        other_bot_id = uuid4()
        shadow_row = {
            "id": str(shadow_id),
            "shadow_of": other_bot_id,
            "is_shadow": True,
            "strategy_params": {"fast": 20},
            "shadow_comparison_window_days": 30,
        }
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_first_result(shadow_row))
        with pytest.raises(ValueError, match="shadow_not_owned_by_live_bot"):
            await service.promote(live_id, shadow_id, "admin", db)

    @pytest.mark.asyncio
    async def test_raises_when_bot_is_not_shadow(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        shadow_row = {
            "id": str(shadow_id),
            "shadow_of": live_id,
            "is_shadow": False,
            "strategy_params": {"fast": 20},
            "shadow_comparison_window_days": 30,
        }
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_first_result(shadow_row))
        with pytest.raises(ValueError, match="bot_is_not_a_shadow"):
            await service.promote(live_id, shadow_id, "admin", db)

    @pytest.mark.asyncio
    async def test_promote_succeeds(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        shadow_row = {
            "id": str(shadow_id),
            "shadow_of": live_id,
            "is_shadow": True,
            "strategy_params": {"fast": 20},
            "shadow_comparison_window_days": 7,
        }
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _first_result(shadow_row),  # shadow SELECT
                MagicMock(),  # UPDATE live bot params
                _agg_row(),  # _aggregate_metrics shadow
                _agg_row(),  # _aggregate_metrics live
                MagicMock(),  # INSERT shadow_promotion_events
                MagicMock(),  # UPDATE bots deleted_at
            ]
        )
        db.commit = AsyncMock()

        await service.promote(live_id, shadow_id, "admin", db)
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_promote_increments_failure_metric_on_error(self, service):
        live_id = uuid4()
        shadow_id = uuid4()
        shadow_row = {
            "id": str(shadow_id),
            "shadow_of": live_id,
            "is_shadow": True,
            "strategy_params": {"fast": 20},
            "shadow_comparison_window_days": 7,
        }
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _first_result(shadow_row),
                RuntimeError("db_error"),
            ]
        )

        import app.services.shadow_promoter.metrics as sm

        with patch.object(sm.shadow_promoter_promote_failures_total, "inc") as mock_inc:
            with pytest.raises(RuntimeError):
                await service.promote(live_id, shadow_id, "admin", db)
            mock_inc.assert_called_once()
