"""Phase 8b T-O.11 — empirical proof that Futu has NO native OCO support.

Run: FUTU_HOST=10.10.0.2 FUTU_PORT=11111 python scripts/empirical/futu_oco_orchestrated_paper.py

This script proves the NEGATIVE INVARIANT:
  Cancelling one order leg in Futu does NOT automatically cancel the sibling.
  Therefore the backend OCO orchestrator (cancel-on-fill logic) is REQUIRED for
  Futu, unlike brokers with native OCO support.

Two-leg OCO bracket under test:
  leg A — LIMIT BUY  HK.00700 qty=100 @ 10.0   (entry/stop-loss leg)
  leg B — LIMIT SELL HK.00700 qty=100 @ 11.0   (take-profit leg)

Assertions:
  1. leg A order_id returned
  2. leg B order_id returned
  3. both legs in order_list
  4. cancel leg A returns RET_OK
  5. leg B unchanged after leg A cancel — no native OCO cascade  ← KEY INVARIANT
  6. manual cancel leg B returns RET_OK
  7. both orders cancelled — clean end state

Key SDK facts (same as futu_bracket_modify_paper.py):
- Cancel is via modify_order(modify_order_op=ft.ModifyOrderOp.CANCEL, ...)
- place_order returns (ret, DataFrame) with column 'order_id'
- Order status for active/open orders: use order_list_query; cancelled rows may
  disappear from the live list, confirming cancellation.
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
    label: str,
) -> str:
    """Place a single limit order and return its order_id string."""
    code, data = ctx.place_order(
        price=price,
        qty=qty,
        code="HK.00700",
        trd_side=trd_side,
        order_type=ft.OrderType.NORMAL,
        trd_env=ft.TrdEnv.SIMULATE,
    )
    check(f"{label} order_id returned", code == ft.RET_OK and len(data) == 1)
    return str(data["order_id"].iloc[0])


def _query_live_ids(ctx: ft.OpenSecTradeContext, *, retries: int = 3) -> set[str]:
    """Return set of order_id strings currently in order_list_query."""
    for attempt in range(retries):
        code, orders = ctx.order_list_query(trd_env=ft.TrdEnv.SIMULATE)
        if code == ft.RET_OK:
            return set(orders["order_id"].astype(str).tolist())
        if attempt < retries - 1:
            time.sleep(1.0)
    raise RuntimeError(f"order_list_query failed after {retries} attempts")


def main() -> None:
    ctx = ft.OpenSecTradeContext(
        host=os.getenv("FUTU_HOST", "127.0.0.1"),
        port=int(os.getenv("FUTU_PORT", "11111")),
    )
    leg_a_id: str | None = None
    leg_b_id: str | None = None
    try:
        # ------------------------------------------------------------------
        # 1. Place leg A — BUY 100 @ 10.0 (stop-loss / entry leg)
        # ------------------------------------------------------------------
        leg_a_id = _place_one(
            ctx,
            price=10.0,
            qty=100,
            trd_side=ft.TrdSide.BUY,
            label="leg A",
        )

        # ------------------------------------------------------------------
        # 2. Place leg B — SELL 100 @ 11.0 (take-profit leg)
        # ------------------------------------------------------------------
        leg_b_id = _place_one(
            ctx,
            price=11.0,
            qty=100,
            trd_side=ft.TrdSide.SELL,
            label="leg B",
        )

        # Sanity: distinct IDs
        check("leg A and leg B have distinct order_ids", leg_a_id != leg_b_id)

        # ------------------------------------------------------------------
        # 3. Both legs appear in order_list_query
        # ------------------------------------------------------------------
        live_ids = _query_live_ids(ctx)
        check(
            "both legs in order_list",
            {leg_a_id, leg_b_id}.issubset(live_ids),
        )

        # ------------------------------------------------------------------
        # 4. Cancel leg A
        # ------------------------------------------------------------------
        code, _ = ctx.modify_order(
            modify_order_op=ft.ModifyOrderOp.CANCEL,
            order_id=leg_a_id,
            qty=100,
            price=10.0,
            adjust_limit=0,
            trd_env=ft.TrdEnv.SIMULATE,
        )
        check("cancel leg A returns RET_OK", code == ft.RET_OK)

        # ------------------------------------------------------------------
        # 5. KEY INVARIANT: leg B must remain alive — no native OCO cascade
        #    Wait 1 s to give any hypothetical cascade time to propagate.
        # ------------------------------------------------------------------
        time.sleep(1.0)
        live_after_cancel_a = _query_live_ids(ctx)
        check(
            "leg B unchanged after leg A cancel — no native OCO cascade",
            leg_b_id in live_after_cancel_a,
        )

        # ------------------------------------------------------------------
        # 6. Manual cancel of leg B
        # ------------------------------------------------------------------
        code, _ = ctx.modify_order(
            modify_order_op=ft.ModifyOrderOp.CANCEL,
            order_id=leg_b_id,
            qty=100,
            price=11.0,
            adjust_limit=0,
            trd_env=ft.TrdEnv.SIMULATE,
        )
        check("cancel leg B returns RET_OK", code == ft.RET_OK)

        # ------------------------------------------------------------------
        # 7. Final state: both orders cancelled (neither in active order list)
        # ------------------------------------------------------------------
        time.sleep(1.0)
        live_final = _query_live_ids(ctx)
        check(
            "both orders cancelled — clean end state",
            leg_a_id not in live_final and leg_b_id not in live_final,
        )

        print(
            "\nAll checks PASSED — Futu has NO native OCO cascade.\n"
            "Backend OCO orchestrator is required and proven necessary."
        )
    except Exception as exc:
        # Best-effort cleanup so paper account is not left with open orders
        for oid, price in ((leg_a_id, 10.0), (leg_b_id, 11.0)):
            if oid is not None:
                try:
                    ctx.modify_order(
                        modify_order_op=ft.ModifyOrderOp.CANCEL,
                        order_id=oid,
                        qty=100,
                        price=price,
                        adjust_limit=0,
                        trd_env=ft.TrdEnv.SIMULATE,
                    )
                except (Exception,) as cleanup_exc:  # noqa: BLE001
                    print(f"cleanup cancel {oid} failed: {cleanup_exc}")
        raise exc
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
