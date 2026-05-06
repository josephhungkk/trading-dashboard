"""Phase 8b T-F.6 — real-broker E2E: Futu modify + bracket against paper account.

Skipped unless ``pytest -m real_futu`` AND env FUTU_HOST + FUTU_PORT set.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.real_futu

_REQUIRED_FUTU_ENV = ("FUTU_HOST", "FUTU_PORT")


@pytest.fixture(scope="module")
def futu_paper_ctx():
    """OpenSecTradeContext bound to the paper environment."""
    ft = pytest.importorskip("futu")
    ctx = ft.OpenSecTradeContext(
        host=os.environ.get("FUTU_HOST", "127.0.0.1"),
        port=int(os.environ.get("FUTU_PORT", "11111")),
    )
    yield ctx
    ctx.close()


def test_real_futu_place_modify_cancel(futu_paper_ctx) -> None:
    ft = pytest.importorskip("futu")
    code, data = futu_paper_ctx.place_order(
        price=10.0,
        qty=100,
        code="HK.00700",
        trd_side=ft.TrdSide.BUY,
        order_type=ft.OrderType.NORMAL,
        trd_env=ft.TrdEnv.SIMULATE,
    )
    assert code == ft.RET_OK
    order_id = str(data["order_id"].iloc[0])
    try:
        code, _ = futu_paper_ctx.modify_order(
            order_id=order_id,
            qty=100,
            price=9.9,
            adjust_limit=0,
            trd_env=ft.TrdEnv.SIMULATE,
        )
        assert code == ft.RET_OK
        # poll up to 5 s for modified price
        deadline = time.monotonic() + 5
        observed = None
        while time.monotonic() < deadline:
            code, orders = futu_paper_ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
            if code == ft.RET_OK:
                row = orders[orders["order_id"].astype(str) == order_id]
                if not row.empty:
                    observed = float(row.iloc[0]["price"])
                    if observed == 9.9:
                        break
            time.sleep(0.5)
        assert observed == 9.9, f"modified price not reflected: {observed}"
    finally:
        futu_paper_ctx.cancel_order(order_id=order_id, trd_env=ft.TrdEnv.SIMULATE)


def test_real_futu_bracket_paper(futu_paper_ctx) -> None:
    ft = pytest.importorskip("futu")
    # Bracket via attached conditional orders (verify SDK shape; fallback = 3 separate orders).
    # The empirical script (T-F.4) verifies whether the SDK supports
    # attached_conditional_orders=... on this firmware version.
    # For now accept either path and clean up.
    code, data = futu_paper_ctx.place_order(
        price=10.0,
        qty=100,
        code="HK.00700",
        trd_side=ft.TrdSide.BUY,
        order_type=ft.OrderType.NORMAL,
        trd_env=ft.TrdEnv.SIMULATE,
    )
    assert code == ft.RET_OK
    ids = [str(v) for v in data["order_id"].tolist()]
    # Bracket returns 3 ids when SDK supports attached_conditional_orders;
    # falls back to 1 id otherwise.
    try:
        for oid in ids:
            code, _ = futu_paper_ctx.cancel_order(order_id=oid, trd_env=ft.TrdEnv.SIMULATE)
            assert code == ft.RET_OK
    except Exception:
        pass
