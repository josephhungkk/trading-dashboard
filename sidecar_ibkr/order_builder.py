"""Pure order-builder for ib_async.Order — no proto/gRPC imports.

Extracted from IbkrHandler._build_ib_order so unit tests can import it
without triggering the gRPC generated-code sys.modules requirements.
"""

from __future__ import annotations

from typing import Any


def build_ib_order(request: Any, side: str, qty: float) -> Any:  # noqa: C901
    """Construct an ib_async.Order from a PlaceOrderRequest-shaped object.

    Parameters
    ----------
    request:
        Any object with attributes matching PlaceOrderRequest fields.
    side:
        "BUY" or "SELL".
    qty:
        Total quantity as float.

    Returns
    -------
    ib_async.Order
    """
    from ib_async import Order  # type: ignore[import-untyped]

    order_type: str = request.order_type

    order: Any = Order()
    order.action = side
    order.totalQuantity = qty

    # --- order type -------------------------------------------------
    if order_type == "MARKET":
        order.orderType = "MKT"
    elif order_type == "LIMIT":
        order.orderType = "LMT"
        order.lmtPrice = float(request.limit_price)
    elif order_type == "STOP":
        order.orderType = "STP"
        order.auxPrice = float(request.stop_price)
    elif order_type == "STOP_LIMIT":
        order.orderType = "STP LMT"
        order.lmtPrice = float(request.limit_price)
        order.auxPrice = float(request.stop_price)
    elif order_type == "TRAIL":
        order.orderType = "TRAIL"
        if request.trail_offset_type == "PERCENT":
            order.trailingPercent = float(request.trail_offset)
        else:
            order.auxPrice = float(request.trail_offset)
    elif order_type == "TRAIL_LIMIT":
        order.orderType = "TRAIL LIMIT"
        order.lmtPrice = float(request.trail_limit_offset)
        if request.trail_offset_type == "PERCENT":
            order.trailingPercent = float(request.trail_offset)
        else:
            order.auxPrice = float(request.trail_offset)
    elif order_type == "MOC":
        order.orderType = "MOC"
    elif order_type == "MOO":
        order.orderType = "MKT"
        order.tif = "OPG"
    elif order_type == "LOC":
        order.orderType = "LOC"
        order.lmtPrice = float(request.limit_price)
    elif order_type == "LOO":
        order.orderType = "LMT"
        order.lmtPrice = float(request.limit_price)
        order.tif = "OPG"
    else:
        raise ValueError(f"Unsupported order_type: {order_type}")

    # --- time-in-force (skip if already set by MOO/LOO above) ------
    if order_type not in ("MOO", "LOO") and request.tif:
        tif: str = request.tif
        if tif == "GTD":
            order.tif = "GTD"
            expiry = request.expiry_date
            if hasattr(expiry, "strftime"):
                yyyymmdd: str = expiry.strftime("%Y%m%d")
            else:
                yyyymmdd = expiry.replace("-", "")
            order.goodTillDate = f"{yyyymmdd} 23:59:59 US/Eastern"
        else:
            order.tif = tif

    return order


def attach_oca_group(order: Any, group_id: str, oca_type: int = 1) -> None:
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
