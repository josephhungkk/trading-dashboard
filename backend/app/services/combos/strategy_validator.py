from __future__ import annotations

from app.services.combos.types import ComboSpec, LegSpec


class ComboValidationError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate(
    strategy_type: str,
    legs: list[LegSpec],
    underlying_symbol: str,
    underlying_canonical_id: str,
    tif: str,
    account_id: str,
) -> ComboSpec:
    if len({leg.currency for leg in legs}) > 1:
        raise ComboValidationError("currency_mismatch")
    _VALIDATORS[strategy_type](legs)
    return ComboSpec(
        strategy_type=strategy_type,
        underlying_symbol=underlying_symbol,
        underlying_canonical_id=underlying_canonical_id,
        legs=legs,
        tif=tif,
        account_id=account_id,
    )


def _validate_vertical(legs: list[LegSpec]) -> None:
    a, b = legs[0], legs[1]
    if a.expiry != b.expiry:
        raise ComboValidationError("expiry_mismatch")
    if a.put_call != b.put_call:
        raise ComboValidationError("opposite_put_call_required")
    if a.strike == b.strike:
        raise ComboValidationError("same_strike_required")
    if a.side == b.side:
        raise ComboValidationError("opposite_side_required")


def _validate_calendar(legs: list[LegSpec]) -> None:
    a, b = legs[0], legs[1]
    if a.expiry == b.expiry:
        raise ComboValidationError("expiry_mismatch")
    if a.put_call != b.put_call:
        raise ComboValidationError("opposite_put_call_required")
    if a.strike != b.strike:
        raise ComboValidationError("same_strike_required")
    if a.side == b.side:
        raise ComboValidationError("opposite_side_required")


def _validate_diagonal(legs: list[LegSpec]) -> None:
    a, b = legs[0], legs[1]
    if a.expiry == b.expiry and a.strike == b.strike:
        raise ComboValidationError("expiry_mismatch")
    if a.put_call != b.put_call:
        raise ComboValidationError("opposite_put_call_required")
    if a.side == b.side:
        raise ComboValidationError("opposite_side_required")


def _validate_straddle(legs: list[LegSpec]) -> None:
    a, b = legs[0], legs[1]
    if a.expiry != b.expiry:
        raise ComboValidationError("expiry_mismatch")
    if a.strike != b.strike:
        raise ComboValidationError("same_strike_required")
    if a.put_call == b.put_call:
        raise ComboValidationError("opposite_put_call_required")
    if a.side != b.side:
        raise ComboValidationError("opposite_side_required")


def _validate_strangle(legs: list[LegSpec]) -> None:
    a, b = legs[0], legs[1]
    if a.expiry != b.expiry:
        raise ComboValidationError("expiry_mismatch")
    if a.strike == b.strike:
        raise ComboValidationError("same_strike_required")
    if a.put_call == b.put_call:
        raise ComboValidationError("opposite_put_call_required")
    if a.side != b.side:
        raise ComboValidationError("opposite_side_required")


_VALIDATORS = {
    "VERTICAL": _validate_vertical,
    "CALENDAR": _validate_calendar,
    "DIAGONAL": _validate_diagonal,
    "STRADDLE": _validate_straddle,
    "STRANGLE": _validate_strangle,
}
