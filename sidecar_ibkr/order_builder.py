"""Pure order-builder for ib_async.Order — no proto/gRPC imports.

Extracted from IbkrHandler._build_ib_order so unit tests can import it
without triggering the gRPC generated-code sys.modules requirements.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


# MED-sec-4: exchange-aware GTD timezone map.
# Fallback is US/Eastern to preserve prior behaviour for unrecognised exchanges.
_EXCHANGE_TZ = {
    "NYSE": "US/Eastern",
    "NASDAQ": "US/Eastern",
    "ARCA": "US/Eastern",
    "AMEX": "US/Eastern",
    "HKEX": "Asia/Hong_Kong",
    "SEHK": "Asia/Hong_Kong",
    "LSE": "Europe/London",
}

# Legacy constant retained for backward-compat with callers that use it directly.
_IBKR_GTD_EOD_TIME = "23:59:59 US/Eastern"


def _gtd_string(expiry_date: str, exchange: str = "NYSE") -> str:
    """Return the IBKR goodTillDate string for expiry_date on exchange.

    Format: "YYYYMMDD HH:MM:SS TZ"  (TWS API §5.8.3 goodTillDate field).
    Timezone is derived from exchange; falls back to US/Eastern for unknown codes.
    """
    tz = _EXCHANGE_TZ.get(exchange.upper(), "US/Eastern")
    yyyymmdd = expiry_date.replace("-", "")
    return f"{yyyymmdd} 23:59:59 {tz}"


class PlaceOrderRequestLike(Protocol):
    order_type: str
    tif: str
    limit_price: object
    stop_price: object
    trail_offset: object
    trail_offset_type: str
    trail_limit_offset: object
    expiry_date: object


class OrderLike(Protocol):
    action: str
    totalQuantity: float
    orderType: str
    tif: str
    lmtPrice: float
    auxPrice: float
    trailingPercent: float
    goodTillDate: str
    ocaGroup: str
    ocaType: int


def _set_market(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "MKT"


def _set_limit(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "LMT"
    order.lmtPrice = float(request.limit_price)


def _set_stop(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "STP"
    order.auxPrice = float(request.stop_price)


def _set_stop_limit(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "STP LMT"
    order.lmtPrice = float(request.limit_price)
    order.auxPrice = float(request.stop_price)


def _set_trail(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "TRAIL"
    if request.trail_offset_type == "PERCENT":
        order.trailingPercent = float(request.trail_offset)
    else:
        order.auxPrice = float(request.trail_offset)


def _set_trail_limit(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "TRAIL LIMIT"
    order.lmtPrice = float(request.trail_limit_offset)
    if request.trail_offset_type == "PERCENT":
        order.trailingPercent = float(request.trail_offset)
    else:
        order.auxPrice = float(request.trail_offset)


def _set_moc(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "MOC"


def _set_moo(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    # MED-code-5: TWS API docs confirm MOO is submitted as MKT + tif=OPG
    # (not orderType="MOO" directly). "OPG" is the canonical TWS market-on-open
    # form per https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/.
    # TODO: empirically verify on paper account; update if TWS rejects OPG form.
    order.orderType = "MKT"
    order.tif = "OPG"


def _set_loc(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    order.orderType = "LOC"
    order.lmtPrice = float(request.limit_price)


def _set_loo(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    # MED-code-5: same OPG pattern as MOO but with LMT instead of MKT.
    # TODO: empirically verify on paper account; update if TWS rejects OPG form.
    order.orderType = "LMT"
    order.lmtPrice = float(request.limit_price)
    order.tif = "OPG"


_ORDER_TYPE_SETTERS: dict[str, Callable[[OrderLike, PlaceOrderRequestLike], None]] = {
    "MARKET": _set_market,
    "LIMIT": _set_limit,
    "STOP": _set_stop,
    "STOP_LIMIT": _set_stop_limit,
    "TRAIL": _set_trail,
    "TRAIL_LIMIT": _set_trail_limit,
    "MOC": _set_moc,
    "MOO": _set_moo,
    "LOC": _set_loc,
    "LOO": _set_loo,
}


def _format_ibkr_gtd(expiry: object) -> str:
    if hasattr(expiry, "strftime"):
        yyyymmdd = expiry.strftime("%Y%m%d")
    else:
        yyyymmdd = str(expiry).replace("-", "")
    return f"{yyyymmdd} {_IBKR_GTD_EOD_TIME}"


def _set_tif_modifiers(order: OrderLike, request: PlaceOrderRequestLike) -> None:
    if request.order_type in ("MOO", "LOO") or not request.tif:
        return

    if request.tif == "GTD":
        order.tif = "GTD"
        order.goodTillDate = _format_ibkr_gtd(request.expiry_date)
        return

    order.tif = request.tif


def build_ib_order(request: PlaceOrderRequestLike, side: str, qty: float) -> OrderLike:
    """Construct an ib_async.Order from a PlaceOrderRequest-shaped object.

    Parameters
    ----------
    request:
        Any object with attributes matching PlaceOrderRequest fields. GTD currently
        assumes US/Eastern; HKEX/LSE GTD will be addressed in Phase 8c via
        market_calendar.eod_for_exchange().
    side:
        "BUY" or "SELL".
    qty:
        Total quantity as float.

    Returns
    -------
    ib_async.Order
    """
    from ib_async import Order  # type: ignore[import-untyped]

    order: OrderLike = Order()
    order.action = side
    order.totalQuantity = qty

    try:
        _ORDER_TYPE_SETTERS[request.order_type](order, request)
    except KeyError as exc:
        raise ValueError(f"Unsupported order_type: {request.order_type}") from exc

    _set_tif_modifiers(order, request)

    return order


def attach_oca_group(order: OrderLike, group_id: str, oca_type: int = 1) -> None:
    """Attach OCA group identity to an ib_async Order for cancel-on-fill semantics.

    Parameters
    ----------
    order:
        An ib_async.Order instance (or any object with ocaGroup/ocaType attributes).
    group_id:
        OCA group identifier — max 32 chars per TWS API constraint.
    oca_type:
        1 = cancel all remaining on fill (OCO default)
        2 = reduce all sizes proportionally
        3 = reduce all sizes with overfill protection

    Raises
    ------
    ValueError
        If group_id exceeds 32 chars or oca_type is not 1, 2, or 3.
    """
    if len(group_id) > 32:
        raise ValueError(f"oca_group_id too long: {len(group_id)} > 32")
    if oca_type not in (1, 2, 3):
        raise ValueError(f"oca_type must be 1, 2, or 3 (got {oca_type})")
    order.ocaGroup = group_id
    order.ocaType = oca_type
