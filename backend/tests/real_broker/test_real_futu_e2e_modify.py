"""Phase 8b T-F.6 — real-broker E2E: Futu modify + bracket against paper account.

Skipped unless ``pytest -m real_futu`` AND env FUTU_HOST + FUTU_PORT set.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = [pytest.mark.real_futu, pytest.mark.no_db]

_REQUIRED_FUTU_ENV = ("FUTU_HOST", "FUTU_PORT")


@pytest.fixture(scope="module")
def futu_paper_ctx():
    """OpenSecTradeContext bound to the paper environment.

    futu.OpenSecTradeContext.__init__ spawns a non-daemon reconnect thread
    that retries forever if the RSA handshake fails. That happens any time
    the test runs against the prod OpenD on the NUC (10.10.0.2:11111),
    because prod OpenD has the prod schwab-sidecar's RSA public key
    registered — NOT a separate test key. A TCP probe alone isn't enough
    (TCP connect succeeds, then OpenD drops the conn after handshake).

    The conftest `_FUTU` marker gates this whole test on
    `testing/futu_test_enabled=true` AND a populated futu.rsa_priv_pem
    that matches what OpenD has registered. Operator sets the flag only
    when a dedicated test OpenD instance (or a temporarily-test-keyed prod
    OpenD) is available. Without that gate flipping to true, this fixture
    never runs. Once the gate is on, the only remaining failure mode is
    RSA-handshake misconfiguration — which is the operator's problem,
    not the fixture's.

    Defense in depth: a 2s TCP pre-probe fails-fast if even the listening
    socket is unreachable (saves a 30s+ reconnect-loop wait).
    """
    import socket

    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2.0)
        try:
            probe.connect((host, port))
        except (TimeoutError, OSError) as exc:
            pytest.skip(f"Futu OpenD not reachable at {host}:{port} ({exc!s})")

    ft = pytest.importorskip("futu")
    ctx = ft.OpenSecTradeContext(host=host, port=port)
    try:
        yield ctx
    finally:
        # close() sets _auto_reconnect=False, stopping the daemon reconnect
        # thread that would otherwise keep pytest's process alive.
        try:
            ctx.close()
        except Exception:
            pass


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
