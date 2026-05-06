"""Phase 8b T-F.4 — empirical hard-gate for Futu paper bracket + modify.

Run: FUTU_HOST=10.10.0.2 FUTU_PORT=11111 python scripts/empirical/futu_bracket_modify_paper.py

SDK API path chosen: PATH B — 3 separate orders (entry + stop + take-profit).
Reason: futu-api (as of inspection 2026-05-06) has NO attached_conditional_orders
parameter on place_order and NO native OCO/bracket support in ft.OpenSecTradeContext.
Bracket emulation is handled by the backend OCO orchestrator (cancel-on-fill logic).
This gate confirms that the 3 individual orders can be placed, modified, and cancelled
independently — which is all the backend needs.

Key SDK facts learned during inspection:
- ft.OpenSecTradeContext.place_order params: price, qty, code, trd_side, order_type,
  adjust_limit, trd_env, acc_id, acc_index, remark, time_in_force, fill_outside_rth,
  aux_price, trail_type, trail_value, trail_spread, session, jp_acc_type, position_id
- Cancel is via modify_order(modify_order_op=ft.ModifyOrderOp.CANCEL, ...)
- Modify price is via modify_order(modify_order_op=ft.ModifyOrderOp.NORMAL, ...)
- ft.ModifyOrderOp.CANCEL, .NORMAL are the two relevant ops
- ft.OrderType.STOP and ft.OrderType.NORMAL (LIMIT) both exist
"""

import os
import time

import futu as ft


def check(name: str, value: bool) -> None:
    """Print PASS/FAIL and assert — gate exits non-zero on first failure."""
    print(("PASS " if value else "FAIL ") + name)
    assert value, name


def _place_one(
    ctx: ft.OpenSecTradeContext,
    *,
    price: float,
    qty: int,
    trd_side: str,
    order_type: str,
) -> str:
    """Place a single order and return its order_id string."""
    code, data = ctx.place_order(
        price=price,
        qty=qty,
        code="HK.00700",
        trd_side=trd_side,
        order_type=order_type,
        trd_env=ft.TrdEnv.SIMULATE,
    )
    check(f"place_order RET_OK (price={price}, side={trd_side})", code == ft.RET_OK)
    check(f"place_order returns 1 row (price={price})", len(data) == 1)
    return str(data["order_id"].iloc[0])


def main() -> None:
    ctx = ft.OpenSecTradeContext(
        host=os.getenv("FUTU_HOST", "127.0.0.1"),
        port=int(os.getenv("FUTU_PORT", "11111")),
    )
    try:
        # ------------------------------------------------------------------
        # PATH B: place 3 separate orders to emulate a bracket
        #   ids[0] = entry   — LIMIT BUY  HK.00700 qty=100 @ 10.0
        #   ids[1] = stop    — STOP  SELL HK.00700 qty=100 @ 9.5
        #   ids[2] = tp      — LIMIT SELL HK.00700 qty=100 @ 11.0
        # The backend OCO orchestrator is responsible for linking them (cancel-on-fill).
        # ------------------------------------------------------------------

        entry_id = _place_one(
            ctx,
            price=10.0,
            qty=100,
            trd_side=ft.TrdSide.BUY,
            order_type=ft.OrderType.NORMAL,
        )
        stop_id = _place_one(
            ctx,
            price=9.5,
            qty=100,
            trd_side=ft.TrdSide.SELL,
            order_type=ft.OrderType.STOP,
        )
        tp_id = _place_one(
            ctx,
            price=11.0,
            qty=100,
            trd_side=ft.TrdSide.SELL,
            order_type=ft.OrderType.NORMAL,
        )

        ids = [entry_id, stop_id, tp_id]

        # 1. Confirm we have 3 distinct order IDs
        check("place bracket (PATH B) yields 3 order IDs", len(set(ids)) == 3)

        # 2. All 3 orders appear in order_list_query
        code, orders = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
        check("order_list_query OK", code == ft.RET_OK)
        live_ids = set(orders["order_id"].astype(str).tolist())
        check("all 3 orders in order_list", set(ids).issubset(live_ids))

        # 3. Modify entry price 10.0 → 9.9
        code, _ = ctx.modify_order(
            modify_order_op=ft.ModifyOrderOp.NORMAL,
            order_id=entry_id,
            qty=100,
            price=9.9,
            adjust_limit=0,
            trd_env=ft.TrdEnv.SIMULATE,
        )
        check("modify_order OK", code == ft.RET_OK)

        # 4. Poll up to 5 s for modified price to be reflected
        deadline = time.monotonic() + 5.0
        new_price: float | None = None
        while time.monotonic() < deadline:
            code2, orders2 = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
            if code2 == ft.RET_OK:
                row = orders2[orders2["order_id"].astype(str) == entry_id]
                if not row.empty and float(row.iloc[0]["price"]) == 9.9:
                    new_price = 9.9
                    break
            time.sleep(0.5)
        check("modified price reflected within 5s", new_price == 9.9)

        # 5. Cancel entry; verify children (stop + tp) remain — proves no auto-cascade
        code, _ = ctx.modify_order(
            modify_order_op=ft.ModifyOrderOp.CANCEL,
            order_id=entry_id,
            qty=100,
            price=9.9,
            adjust_limit=0,
            trd_env=ft.TrdEnv.SIMULATE,
        )
        check("cancel entry OK", code == ft.RET_OK)
        time.sleep(1.0)
        code, orders3 = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
        check("order_list after entry cancel OK", code == ft.RET_OK)
        live_after = set(orders3["order_id"].astype(str).tolist())
        children_alive = {stop_id, tp_id} & live_after
        check(
            "cancel of entry does NOT auto-cancel stop/tp (children alive)",
            children_alive == {stop_id, tp_id},
        )

        # 6. Manual sibling cancel — stop and tp must each cancel cleanly
        for label, oid, price in (("stop", stop_id, 9.5), ("tp", tp_id, 11.0)):
            code, _ = ctx.modify_order(
                modify_order_op=ft.ModifyOrderOp.CANCEL,
                order_id=oid,
                qty=100,
                price=price,
                adjust_limit=0,
                trd_env=ft.TrdEnv.SIMULATE,
            )
            check(f"manual cancel of {label} ({oid}) OK", code == ft.RET_OK)

        print("\nAll checks PASSED — Futu paper bracket+modify gate satisfied.")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
