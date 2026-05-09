"""Phase 10a — Pydantic v2 schema validation tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.no_db


def test_global_scope_rejects_non_null_scope_id() -> None:
    from app.schemas.risk import RiskLimitCreate

    with pytest.raises(ValidationError):
        RiskLimitCreate(
            scope_type="global",
            scope_id="not-null",
            limit_kind="pdt_warn_remaining",
            limit_value=Decimal("1"),
        )


def test_account_scope_requires_scope_id() -> None:
    from app.schemas.risk import RiskLimitCreate

    with pytest.raises(ValidationError):
        RiskLimitCreate(
            scope_type="account",
            scope_id=None,
            limit_kind="pdt_warn_remaining",
            limit_value=Decimal("1"),
        )


def test_warn_at_pct_must_be_in_0_100() -> None:
    from app.schemas.risk import RiskLimitCreate

    with pytest.raises(ValidationError):
        RiskLimitCreate(
            scope_type="global",
            scope_id=None,
            limit_kind="pdt_warn_remaining",
            limit_value=Decimal("1"),
            warn_at_pct=Decimal("101"),
        )


def test_kill_switch_toggle_requires_reason_when_enabling() -> None:
    from app.schemas.risk import AccountKillSwitchToggleRequest

    with pytest.raises(ValidationError):
        AccountKillSwitchToggleRequest(is_enabled=True, reason="")
    # Disabling without reason is fine
    AccountKillSwitchToggleRequest(is_enabled=False, reason="")


def test_gate_verdict_aggregation_shape() -> None:
    from app.schemas.risk import GateVerdict, GateWarningEntry

    v = GateVerdict(
        final_verdict="warn",
        blockers=[],
        warnings=[
            GateWarningEntry(check="bp_buffer", message="below 5%", value=4.0, threshold=5.0)
        ],
        latency_ms=42,
    )
    assert v.final_verdict == "warn"
    assert len(v.warnings) == 1
    assert v.warnings[0].check == "bp_buffer"
