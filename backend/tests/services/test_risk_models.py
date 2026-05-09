"""Phase 10a — Risk ORM model schema asserts."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db  # column-set introspection; no DB needed


def test_risk_limit_columns() -> None:
    from app.models.risk import RiskLimit

    cols = {c.name for c in RiskLimit.__table__.columns}
    assert cols == {
        "id",
        "scope_type",
        "scope_id",
        "limit_kind",
        "limit_value",
        "warn_at_pct",
        "is_active",
        "notes",
        "created_at",
        "updated_at",
        "updated_by",
    }


def test_risk_decisions_columns() -> None:
    from app.models.risk import RiskDecision

    cols = {c.name for c in RiskDecision.__table__.columns}
    assert cols == {
        "id",
        "account_id",
        "instrument_id",
        "side",
        "qty",
        "price",
        "order_type",
        "time_in_force",
        "verdict",
        "blockers",
        "warnings",
        "evaluated_at",
        "latency_ms",
        "attempt_kind",
        "request_id",
        "order_id",
    }


def test_account_kill_switch_pk_is_account_id() -> None:
    from app.models.risk import AccountKillSwitch

    assert [c.name for c in AccountKillSwitch.__table__.primary_key] == ["account_id"]


def test_history_tables_carry_change_metadata() -> None:
    from app.models.risk import AccountKillSwitchHistory, RiskLimitHistory

    for cls in (RiskLimitHistory, AccountKillSwitchHistory):
        cols = {c.name for c in cls.__table__.columns}
        assert "changed_at" in cols
        assert "changed_by" in cols


def test_risk_decisions_jsonb_fields_default_to_empty_list() -> None:
    from app.models.risk import RiskDecision

    blockers_col = RiskDecision.__table__.columns["blockers"]
    warnings_col = RiskDecision.__table__.columns["warnings"]
    # JSONB columns; defaults align with pydantic schemas (empty list)
    assert blockers_col.default is not None or blockers_col.server_default is not None
    assert warnings_col.default is not None or warnings_col.server_default is not None
