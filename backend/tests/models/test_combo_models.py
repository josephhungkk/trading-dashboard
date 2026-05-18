from app.models.combos import ComboOrder, OrderLeg


def test_combo_order_tablename() -> None:
    assert ComboOrder.__tablename__ == "combo_orders"


def test_order_leg_tablename() -> None:
    assert OrderLeg.__tablename__ == "order_legs"


def test_order_leg_has_order_id() -> None:
    assert hasattr(OrderLeg, "order_id")


def test_combo_order_has_legged_out_in_check() -> None:
    checks = [c.sqltext.text for c in ComboOrder.__table__.constraints if hasattr(c, "sqltext")]
    assert any("legged_out" in t for t in checks)
