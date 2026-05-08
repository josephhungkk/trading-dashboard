"""Tests for OCA group attachment (OCO adapter) — Phase 8b T-O.8."""

from unittest.mock import MagicMock

import pytest

from sidecar_ibkr.order_builder import attach_oca_group


def test_attach_oca_group_sets_both_fields():
    order = MagicMock()
    attach_oca_group(order, "OCO-abc123def456ghi7890jklm")
    assert order.ocaGroup == "OCO-abc123def456ghi7890jklm"
    assert order.ocaType == 1


def test_attach_oca_group_rejects_too_long():
    order = MagicMock()
    with pytest.raises(ValueError, match="too long"):
        attach_oca_group(order, "x" * 33)


def test_attach_oca_group_rejects_bad_type():
    order = MagicMock()
    with pytest.raises(ValueError, match="must be 1, 2, or 3"):
        attach_oca_group(order, "OCO-1", oca_type=4)


def test_two_orders_with_same_group_id():
    order_a = MagicMock()
    order_b = MagicMock()
    group = "OCO-shared123"
    attach_oca_group(order_a, group)
    attach_oca_group(order_b, group)
    assert order_a.ocaGroup == order_b.ocaGroup
    assert order_a.ocaType == order_b.ocaType == 1
