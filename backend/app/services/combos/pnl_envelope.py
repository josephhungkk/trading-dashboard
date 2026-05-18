from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from app.services.combos.types import ComboEnvelope, ComboSpec, LegSpec

_MULTIPLIER = Decimal("100")
_QUANTIZE = Decimal("0.00000001")


def _q(d: Decimal) -> Decimal:
    return d.quantize(_QUANTIZE, rounding=ROUND_HALF_EVEN)


def _mid(leg_idx: int, mids: dict[int, Decimal]) -> Decimal:
    return mids[leg_idx]


def _leg_signed_premium(leg: LegSpec, mid: Decimal) -> Decimal:
    sign = Decimal("1") if leg.side == "sell" else Decimal("-1")
    return sign * mid * leg.qty


def compute_envelope(spec: ComboSpec, mids: dict[int, Decimal]) -> ComboEnvelope:
    dispatch = {
        "VERTICAL": _vertical,
        "CALENDAR": _calendar,
        "DIAGONAL": _diagonal,
        "STRADDLE": _straddle,
        "STRANGLE": _strangle,
    }
    return dispatch[spec.strategy_type](spec, mids)


def combo_native_notional(envelope: ComboEnvelope, multiplier: Decimal = _MULTIPLIER) -> Decimal:
    if envelope.kind == "DEBIT":
        return _q(abs(envelope.net_debit_credit) * multiplier)
    if envelope.max_loss is not None:
        return _q(envelope.max_loss * multiplier)
    return _q(abs(envelope.net_debit_credit) * multiplier)


def _vertical(spec: ComboSpec, mids: dict[int, Decimal]) -> ComboEnvelope:
    legs = spec.legs
    buy_leg = next(leg for leg in legs if leg.side == "buy")
    sell_leg = next(leg for leg in legs if leg.side == "sell")
    net = _q(
        sum((_leg_signed_premium(leg, _mid(i, mids)) for i, leg in enumerate(legs)), Decimal("0"))
    )
    if net < 0:
        kind, nd = "DEBIT", _q(-net)
        spread = _q(abs(buy_leg.strike - sell_leg.strike))
        max_loss = _q(nd * _MULTIPLIER)
        max_profit = _q((spread - nd) * _MULTIPLIER)
        be = [_q(buy_leg.strike + nd)]
    else:
        kind, nd = "CREDIT", _q(net)
        spread = _q(abs(buy_leg.strike - sell_leg.strike))
        max_profit = _q(nd * _MULTIPLIER)
        max_loss = _q((spread - nd) * _MULTIPLIER)
        be = [_q(sell_leg.strike + nd)]
    return ComboEnvelope(
        net_debit_credit=nd,
        kind=kind,
        max_loss=max_loss,
        max_profit=max_profit,
        break_even=be,
    )


def _calendar(spec: ComboSpec, mids: dict[int, Decimal]) -> ComboEnvelope:
    net = _q(
        sum(
            (_leg_signed_premium(leg, _mid(i, mids)) for i, leg in enumerate(spec.legs)),
            Decimal("0"),
        )
    )
    nd = _q(abs(net))
    kind = "DEBIT" if net < 0 else "CREDIT"
    max_loss = _q(nd * _MULTIPLIER)
    return ComboEnvelope(
        net_debit_credit=nd,
        kind=kind,
        max_loss=max_loss,
        max_profit=None,
        break_even=[],
    )


def _diagonal(spec: ComboSpec, mids: dict[int, Decimal]) -> ComboEnvelope:
    return _calendar(spec, mids)


def _straddle(spec: ComboSpec, mids: dict[int, Decimal]) -> ComboEnvelope:
    legs = spec.legs
    net = _q(
        sum((_leg_signed_premium(leg, _mid(i, mids)) for i, leg in enumerate(legs)), Decimal("0"))
    )
    nd = _q(abs(net))
    strike = legs[0].strike
    if net < 0:
        kind = "DEBIT"
        max_loss = _q(nd * _MULTIPLIER)
        max_profit = None
        be = [_q(strike - nd), _q(strike + nd)]
    else:
        kind = "CREDIT"
        max_profit = _q(nd * _MULTIPLIER)
        max_loss = None
        be = []
    return ComboEnvelope(
        net_debit_credit=nd,
        kind=kind,
        max_loss=max_loss,
        max_profit=max_profit,
        break_even=be,
    )


def _strangle(spec: ComboSpec, mids: dict[int, Decimal]) -> ComboEnvelope:
    legs = spec.legs
    net = _q(
        sum((_leg_signed_premium(leg, _mid(i, mids)) for i, leg in enumerate(legs)), Decimal("0"))
    )
    nd = _q(abs(net))
    put_leg = next(leg for leg in legs if leg.put_call == "P")
    call_leg = next(leg for leg in legs if leg.put_call == "C")
    if net < 0:
        kind = "DEBIT"
        max_loss = _q(nd * _MULTIPLIER)
        max_profit = None
        be = [_q(put_leg.strike - nd), _q(call_leg.strike + nd)]
    else:
        kind = "CREDIT"
        max_profit = _q(nd * _MULTIPLIER)
        max_loss = None
        be = []
    return ComboEnvelope(
        net_debit_credit=nd,
        kind=kind,
        max_loss=max_loss,
        max_profit=max_profit,
        break_even=be,
    )
